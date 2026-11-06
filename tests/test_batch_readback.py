import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from batch_readback import ConsumerCheck, Edit, JSONStoreEdit, REQUIRED_FAMILIES, RowChange, capture_tree, compare, discovery_coverage, native_empty_auth_snapshot


class BatchReadbackTest(unittest.TestCase):
    def test_explicit_dashboard_store_edit_preserves_all_fields_across_reserialization(self):
        path = '.storage/lovelace'
        original = {'key': 'lovelace', 'data': {'config': {'entities': [self.old], 'foreign': {'flag': True}}}}
        changed = {'key': 'lovelace', 'data': {'config': {'entities': [self.new], 'foreign': {'flag': True}}}}
        (self.original / path).write_text(json.dumps(original, indent=2))
        (self.after / path).write_text(json.dumps(changed, separators=(',', ':')))
        edit = JSONStoreEdit(path, self.old.encode(), self.new.encode(), 1)
        def check(edits):
            return compare(self.original, self.after, old=self.old, new=self.new, uuid=self.uuid,
                           edits=[self.edit, *edits])['findings']
        self.assertNotIn(('unexpected_change', path), check([edit]))
        self.assertIn(('unexpected_change', path), check([]))
        changed['data']['config']['foreign']['flag'] = 1
        (self.after / path).write_text(json.dumps(changed))
        self.assertIn(('unexpected_change', path), check([edit]))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.original = Path(self.tmp.name) / 'original'
        self.after = Path(self.tmp.name) / 'after'
        for root in (self.original, self.after):
            (root / '.storage').mkdir(parents=True)
            (root / 'includes').mkdir()
        self.old = 'sensor.source'
        self.new = 'sensor.target'
        self.uuid = 'source-uuid'
        self.edit = Edit('includes/template.yaml', self.old.encode(), self.new.encode(), 1)
        foreign = {'id': 'other', 'entity_id': 'light.other', 'disabled_by': 'user'}
        own = {'id': self.uuid, 'entity_id': self.old, 'modified_at': '2026-01-01T00:00:00+00:00', 'area_id': 'room'}
        self.registry(self.original, [foreign, own])
        self.registry(self.after, [dict(own, entity_id=self.new, modified_at='2026-01-01T00:00:01+00:00'), foreign])
        for root, reference in ((self.original, self.old), (self.after, self.new)):
            (root / 'includes/template.yaml').write_text('state: "' + reference + '"\n')
            (root / '.storage/core.device_registry').write_text('{"data":{"devices":[]}}')
            (root / '.storage/core.area_registry').write_text('{"data":{"areas":[]}}')
            (root / '.storage/core.floor_registry').write_text('{"data":{"floors":[]}}')
            (root / '.storage/core.label_registry').write_text('{"data":{"labels":[]}}')

    def locations(self, root):
        result = {}
        for family in ('area_registry', 'floor_registry', 'label_registry'):
            raw = (root / ('.storage/core.' + family)).read_bytes()
            result[family] = {'status': 'COMPLETE', 'data': json.loads(raw)['data'],
                              'disk_sha256': hashlib.sha256(raw).hexdigest()}
        return result

    def registry(self, root, rows):
        (root / '.storage/core.entity_registry').write_text(json.dumps({'version': 1, 'data': {'entities': rows}}))

    def test_private_empty_auth_is_bound_without_disclosing_or_ignoring_edits(self):
        def auth(root, name):
            data = {'users': [], 'credentials': [], 'refresh_tokens': [], 'groups': [
                {'id': 'system-admin', 'name': name}, {'id': 'system-users', 'name': 'Users'},
                {'id': 'system-read-only', 'name': 'Read only'}]}
            path = root / '.storage/auth'
            path.write_text(json.dumps({'version': 1, 'key': 'auth', 'data': data}))
            store = SimpleNamespace(key='auth', version=1, path=str(path))
            manager = SimpleNamespace(_loaded=True, _store=store, _data_to_save=lambda: data)
            return SimpleNamespace(auth=SimpleNamespace(_store=manager))
        before_hass = auth(self.original, 'PRIVATE_MARKER_BEFORE')
        after_hass = auth(self.after, 'PRIVATE_MARKER_AFTER')
        before_auth = native_empty_auth_snapshot(before_hass, self.original)
        after_auth = native_empty_auth_snapshot(after_hass, self.after)
        self.assertEqual(before_auth['status'], 'COMPLETE')
        self.assertEqual(after_auth['status'], 'COMPLETE')
        before_hass.data = {'http.auth': 'unbound'}
        self.assertEqual(native_empty_auth_snapshot(before_hass, self.original)['status'],
                         'UNKNOWN:private_auth_unverified')
        before_hass.data = {}
        base = Path(self.tmp.name)
        def capture(root, label, authority):
            return capture_tree(root, base / label, inventory_files=[],
                families={name: 'COMPLETE' for name in REQUIRED_FAMILIES}, external_consumers={},
                external_inventory_complete=True, native_locations=self.locations(root), native_auth=authority)
        first = capture(self.original, 'private-original', before_auth)
        second = capture(self.after, 'private-after', after_auth)
        self.assertNotIn('PRIVATE_MARKER', json.dumps([first, second]))
        self.assertFalse((base / 'private-original/.storage/auth').exists())
        report = compare(base / 'private-original', base / 'private-after', old=self.old, new=self.new,
                         uuid=self.uuid, edits=[self.edit], captures=(first, second))
        self.assertIn(('private_authority_changed', '.storage/auth'), report['findings'])
        data = after_hass.auth._store._data_to_save()
        data['groups'][0]['name'] = 'sensor.source'
        (self.after / '.storage/auth').write_text(json.dumps({'version': 1, 'key': 'auth', 'data': data}))
        self.assertEqual(native_empty_auth_snapshot(after_hass, self.after)['status'],
                         'UNKNOWN:private_auth_unverified')

    def read(self, **kw):
        return compare(self.original, self.after, old=self.old, new=self.new,
                       uuid=self.uuid, edits=[self.edit], **kw)

    def test_expected_changes_and_same_original_reverse(self):
        report = self.read()
        self.assertEqual([], report['findings'])
        self.assertFalse(report['complete'])
        (self.after / 'includes/template.yaml').write_bytes((self.original / 'includes/template.yaml').read_bytes())
        self.registry(self.after, [
            {'id': self.uuid, 'entity_id': self.old, 'modified_at': '2026-01-01T00:00:02+00:00', 'area_id': 'room'},
            {'id': 'other', 'entity_id': 'light.other', 'disabled_by': 'user'}])
        self.assertEqual([], self.read(reverse=True)['findings'])

    def test_omission_and_foreign_change_outside_edits(self):
        (self.original / 'includes/automation.yaml').write_text('entity_id: sensor.source\n')
        (self.after / 'includes/automation.yaml').write_text('entity_id: sensor.source\n')
        (self.after / '.storage/core.area_registry').write_text('{"data":{"areas":[{"name":"foreign edit"}]}}')
        kinds = {kind for kind, _ in self.read()['findings']}
        self.assertEqual({'remaining_reference', 'unexpected_change'}, kinds)

    def test_registry_foreign_and_fields_are_checked(self):
        self.registry(self.after, [
            {'id': self.uuid, 'entity_id': self.new, 'modified_at': '2026-01-01T00:00:01+00:00', 'area_id': 'room'},
            {'id': 'other', 'entity_id': 'light.other', 'disabled_by': None}])
        self.assertIn(('registry_changed', '.storage/core.entity_registry'), self.read()['findings'])

    def test_new_file_and_opaque_are_not_hidden(self):
        (self.after / 'outside.dat').write_bytes(b'\x00\xff')
        report = self.read()
        self.assertIn('outside.dat', report['opaque_files'])
        self.assertIn('file_membership', {kind for kind, _ in report['findings']})
        self.assertFalse(report['complete'])

    def test_symlink_refused(self):
        (self.after / 'escape').symlink_to(self.original, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.read()

    def test_batch_two_entities_and_unrelated_disabled_row(self):
        other = {'id': 'second', 'entity_id': 'switch.old', 'modified_at': '2026-01-01T00:00:00+00:00', 'disabled_by': 'user'}
        original_rows = json.loads((self.original / '.storage/core.entity_registry').read_text())['data']['entities']
        after_rows = json.loads((self.after / '.storage/core.entity_registry').read_text())['data']['entities']
        self.registry(self.original, original_rows + [other])
        self.registry(self.after, [dict(other, entity_id='switch.new', modified_at='2026-01-01T00:00:01+00:00')] + after_rows)
        (self.original / 'includes/template.yaml').write_text('sensor.source switch.old')
        (self.after / 'includes/template.yaml').write_text('sensor.target switch.new')
        result = compare(self.original, self.after,
                         mappings=[(self.uuid, self.old, self.new), ('second', 'switch.old', 'switch.new')],
                         edits=[self.edit, Edit('includes/template.yaml', b'switch.old', b'switch.new', 1)])
        self.assertEqual([], result['findings'])
        with self.assertRaises(ValueError):
            compare(self.original, self.after, mappings=[(self.uuid, self.old, 'light.bad')])

    def test_native_timestamp_regression_detected(self):
        document = json.loads((self.after / '.storage/core.entity_registry').read_text())
        document['data']['entities'][0]['modified_at'] = '2025-12-31T23:59:59+00:00'
        (self.after / '.storage/core.entity_registry').write_text(json.dumps(document))
        self.assertIn(('registry_changed', '.storage/core.entity_registry'), self.read()['findings'])

    def test_expected_own_names_and_device_name_with_foreign_unchanged(self):
        device = '.storage/core.device_registry'
        old_device = {'id': 'dev-1', 'name_by_user': 'Old', 'modified_at': '2026-01-01T00:00:00+00:00'}
        new_device = dict(old_device, name_by_user='New', modified_at='2026-01-01T00:00:01+00:00')
        foreign = {'id': 'dev-2', 'name_by_user': 'Other', 'disabled_by': 'user'}
        (self.original / device).write_text(json.dumps({'data': {'devices': [foreign, old_device]}}))
        (self.after / device).write_text(json.dumps({'data': {'devices': [new_device, foreign]}}))
        for root, value in ((self.original, 'Original'), (self.after, 'Renamed')):
            doc = json.loads((root / '.storage/core.entity_registry').read_text())
            row = next(r for r in doc['data']['entities'] if r['id'] == self.uuid)
            row['name'] = value
            row['unique_id'] = 'old_unique' if value == 'Original' else 'new_unique'
            (root / '.storage/core.entity_registry').write_text(json.dumps(doc))
        changes = [RowChange('.storage/core.entity_registry', self.uuid, 'name', 'Original', 'Renamed'),
                   RowChange('.storage/core.entity_registry', self.uuid, 'unique_id', 'old_unique', 'new_unique'),
                   RowChange(device, 'dev-1', 'name_by_user', 'Old', 'New')]
        args = dict(old=self.old, new=self.new, uuid=self.uuid, edits=[self.edit], row_changes=changes)
        self.assertEqual([], compare(self.original, self.after, **args)['findings'])
        import shutil
        restored = Path(self.tmp.name) / 'restored'
        shutil.copytree(self.original, restored)
        restored_device = json.loads((restored / device).read_text())
        restored_device['data']['devices'][1]['modified_at'] = '2026-01-01T00:00:02+00:00'
        (restored / device).write_text(json.dumps(restored_device))
        restored_entities = json.loads((restored / '.storage/core.entity_registry').read_text())
        next(r for r in restored_entities['data']['entities'] if r['id'] == self.uuid)['modified_at'] = '2026-01-01T00:00:02+00:00'
        (restored / '.storage/core.entity_registry').write_text(json.dumps(restored_entities))
        self.assertEqual([], compare(self.original, restored, reverse=True, **args)['findings'])
        foreign['disabled_by'] = None
        (self.after / device).write_text(json.dumps({'data': {'devices': [new_device, foreign]}}))
        self.assertIn(('registry_changed', device), compare(self.original, self.after, **args)['findings'])

    def test_own_fields_require_exact_type_and_original(self):
        for root, value in ((self.original, False), (self.after, 1)):
            doc = json.loads((root / '.storage/core.entity_registry').read_text())
            next(r for r in doc['data']['entities'] if r['id'] == self.uuid)['hidden'] = value
            (root / '.storage/core.entity_registry').write_text(json.dumps(doc))
        changes = [RowChange('.storage/core.entity_registry', self.uuid, 'hidden', 0, 1)]
        with self.assertRaises(ValueError):
            compare(self.original, self.after, old=self.old, new=self.new, uuid=self.uuid,
                    edits=[self.edit], row_changes=changes)

    def test_names_only_has_no_entity_mapping(self):
        import shutil
        names_after = Path(self.tmp.name) / 'names-after'
        shutil.copytree(self.original, names_after)
        registry = names_after / '.storage/core.entity_registry'
        doc = json.loads(registry.read_text())
        next(r for r in doc['data']['entities'] if r['id'] == self.uuid)['name'] = 'New'
        registry.write_text(json.dumps(doc))
        original = json.loads((self.original / '.storage/core.entity_registry').read_text())
        next(r for r in original['data']['entities'] if r['id'] == self.uuid)['name'] = 'Old'
        (self.original / '.storage/core.entity_registry').write_text(json.dumps(original))
        change = RowChange('.storage/core.entity_registry', self.uuid, 'name', 'Old', 'New')
        self.assertEqual([], compare(self.original, names_after, mappings=[], row_changes=[change])['findings'])

    def test_script_native_previous_unique_id_tracks_inverse_predecessor(self):
        import shutil
        old, new, uuid = self.old, self.new, self.uuid
        original = self.original / '.storage/core.entity_registry'
        observed = self.after / '.storage/core.entity_registry'
        before_doc, after_doc = json.loads(original.read_text()), json.loads(observed.read_text())
        next(r for r in before_doc['data']['entities'] if r['id'] == uuid)['previous_unique_id'] = None
        next(r for r in after_doc['data']['entities'] if r['id'] == uuid)['previous_unique_id'] = 'old_key'
        original.write_text(json.dumps(before_doc))
        observed.write_text(json.dumps(after_doc))
        change = RowChange('.storage/core.entity_registry', uuid, 'previous_unique_id', None, 'old_key', 'new_key')
        args = dict(old=old, new=new, uuid=uuid, edits=[self.edit], row_changes=[change])
        self.assertEqual([], compare(self.original, self.after, **args)['findings'])
        restored = Path(self.tmp.name) / 'previous-restored'
        shutil.copytree(self.original, restored)
        doc = json.loads((restored / '.storage/core.entity_registry').read_text())
        next(r for r in doc['data']['entities'] if r['id'] == uuid)['previous_unique_id'] = 'new_key'
        (restored / '.storage/core.entity_registry').write_text(json.dumps(doc))
        self.assertEqual([], compare(self.original, restored, reverse=True, **args)['findings'])
        doc['data']['entities'][-1]['previous_unique_id'] = 'foreign'
        (restored / '.storage/core.entity_registry').write_text(json.dumps(doc))
        with self.assertRaises(ValueError):
            compare(self.original, restored, reverse=True, **args)

    def test_full_recognized_capture_semantic_outcome_and_external_gaps(self):
        source = Path(self.tmp.name)
        # Not in discovery graph: it must still be captured and compared.
        (self.original / 'includes/outside.yaml').write_text('entity_id: sensor.source\n')
        (self.after / 'includes/outside.yaml').write_text('entity_id: sensor.target\n')
        edits = [self.edit, Edit('includes/outside.yaml', b'sensor.source', b'sensor.target', 1)]
        def snap(root, name, external=True):
            graph = [('includes/template.yaml', hashlib.sha256((root / 'includes/template.yaml').read_bytes()).hexdigest())]
            result = capture_tree(root, source / name, inventory_files=graph,
                                  families={name: 'COMPLETE' for name in REQUIRED_FAMILIES},
                                  external_consumers={}, external_inventory_complete=external,
                                  native_locations=self.locations(root))
            return result
        b, a = snap(self.original, 'full-before'), snap(self.after, 'full-after')
        check = ConsumerCheck('automation', 'off', 'on', 'on')
        args = dict(old=self.old, new=self.new, uuid=self.uuid, edits=edits,
                    captures=(b, a), consumer_checks=[check],
                    consumer_inventory={'automation': 'SUPPORTED'}, consumer_inventory_complete=True)
        report = compare(source / 'full-before', source / 'full-after', **args)
        self.assertTrue(report['complete'])
        self.assertIn('includes/outside.yaml', b['files'])
        args['consumer_checks'] = [ConsumerCheck('automation', 'off', 'on', 'off')]
        report = compare(source / 'full-before', source / 'full-after', **args)
        self.assertIn(('consumer_outcome', 'automation'), report['findings'])
        self.assertFalse(report['complete'])
        missing_external = snap(self.original, 'missing-external', external=False)
        self.assertIn(('external_inventory', 'not_enumerated'), missing_external['gaps'])
        self.assertFalse(missing_external['complete'])

    def test_dynamic_template_requires_named_observed_consumer(self):
        for root, ref in ((self.original, self.old), (self.after, self.new)):
            (root / 'includes/template.yaml').write_text("state: \"{{ states('" + ref + "') }}\"\n")
        base = Path(self.tmp.name)
        def capture(root, name):
            return capture_tree(root, base / name, inventory_files=[],
                families={name: 'COMPLETE' for name in REQUIRED_FAMILIES},
                external_consumers={}, external_inventory_complete=True, native_locations=self.locations(root))
        b, a = capture(self.original, 'sem-before'), capture(self.after, 'sem-after')
        check = ConsumerCheck('template', '26', '26', '26')
        args = dict(old=self.old, new=self.new, uuid=self.uuid, edits=[self.edit], captures=(b, a),
                    consumer_checks=[check], consumer_inventory={'template': 'SUPPORTED'},
                    consumer_inventory_complete=True)
        report = compare(base / 'sem-before', base / 'sem-after', **args)
        self.assertIn(('includes/template.yaml', 'template_consumer_unverified'), report['unverified'])
        self.assertFalse(report['complete'])
        args['source_consumers'] = {'includes/template.yaml': ['template']}
        self.assertTrue(compare(base / 'sem-before', base / 'sem-after', **args)['complete'])

    def test_custom_dashboard_without_literal_reference_is_not_certified(self):
        name = '.storage/lovelace.foreign'
        for root in (self.original, self.after):
            (root / name).write_text('{"views":[{"cards":[{"type":"custom:unknown-card"}]}]}')
        base = Path(self.tmp.name)
        def capture(root, dest):
            return capture_tree(root, base / dest, inventory_files=[],
                families={name: 'COMPLETE' for name in REQUIRED_FAMILIES},
                external_consumers={}, external_inventory_complete=True, native_locations=self.locations(root))
        before, after = capture(self.original, 'custom-before'), capture(self.after, 'custom-after')
        args = dict(old=self.old, new=self.new, uuid=self.uuid, edits=[self.edit], captures=(before, after),
                    consumer_checks=[], consumer_inventory={}, consumer_inventory_complete=True)
        report = compare(base / 'custom-before', base / 'custom-after', **args)
        self.assertIn((name, 'custom_dashboard_consumer_unverified'), report['unverified'])
        self.assertFalse(report['complete'])

    def test_arbitrary_dashboard_route_and_encoded_custom_card_are_unverified(self):
        for root in (self.original, self.after):
            (root / 'includes/plan.yaml').write_text('type: custom:map-card\n')
            (root / '.storage/lovelace.encoded').write_text('{"type":"custom\\u003amap-card"}')
        base = Path(self.tmp.name)
        def capture(root, dest):
            return capture_tree(root, base / dest, inventory_files=[],
                families={name: 'COMPLETE' for name in REQUIRED_FAMILIES},
                external_consumers={}, external_inventory_complete=True, native_locations=self.locations(root))
        before, after = capture(self.original, 'cards-before'), capture(self.after, 'cards-after')
        report = compare(base / 'cards-before', base / 'cards-after', old=self.old, new=self.new,
                         uuid=self.uuid, edits=[self.edit], captures=(before, after), consumer_checks=[],
                         consumer_inventory={}, consumer_inventory_complete=True)
        self.assertIn(('includes/plan.yaml', 'custom_dashboard_consumer_unverified'), report['unverified'])
        self.assertIn(('.storage/lovelace.encoded', 'custom_dashboard_consumer_unverified'), report['unverified'])
        self.assertFalse(report['complete'])

    def test_external_consumer_requires_actual_observation(self):
        base = Path(self.tmp.name)
        def capture(root, name):
            return capture_tree(root, base / name, inventory_files=[],
                families={name: 'COMPLETE' for name in REQUIRED_FAMILIES},
                external_consumers={'mqtt': 'COMPLETE'}, external_inventory_complete=True,
                native_locations=self.locations(root))
        b, a = capture(self.original, 'ext-before'), capture(self.after, 'ext-after')
        args = dict(old=self.old, new=self.new, uuid=self.uuid, edits=[self.edit], captures=(b, a),
                    consumer_checks=[], consumer_inventory={}, consumer_inventory_complete=True)
        report = compare(base / 'ext-before', base / 'ext-after', **args)
        self.assertIn(('external:mqtt', 'behavior_not_observed'), report['unverified'])
        self.assertFalse(report['complete'])
        args.update(consumer_checks=[ConsumerCheck('mqtt', '1', '2', '2')],
                    consumer_inventory={'mqtt': 'SUPPORTED'})
        self.assertTrue(compare(base / 'ext-before', base / 'ext-after', **args)['complete'])
        args['captures'] = (b, dict(a, external_consumers={}))
        report = compare(base / 'ext-before', base / 'ext-after', **args)
        self.assertIn(('external_inventory_changed', ['mqtt']), report['findings'])

    def test_empty_directory_membership_outside_graph_is_compared(self):
        (self.after / 'new-consumer-dir').mkdir()
        base = Path(self.tmp.name)
        def capture(root, name):
            return capture_tree(root, base / name, inventory_files=[],
                families={name: 'COMPLETE' for name in REQUIRED_FAMILIES},
                external_consumers={}, external_inventory_complete=True, native_locations=self.locations(root))
        b, a = capture(self.original, 'dirs-before'), capture(self.after, 'dirs-after')
        report = compare(base / 'dirs-before', base / 'dirs-after', old=self.old, new=self.new,
                         uuid=self.uuid, edits=[self.edit], captures=(b, a), consumer_checks=[],
                         consumer_inventory={}, consumer_inventory_complete=True)
        self.assertIn(('directory_membership', ['new-consumer-dir']), report['findings'])

    def test_areas_floors_labels_and_dashboard_outside_selection(self):
        docs = {'.storage/core.floor_registry': {'data': {'floors': [{'floor_id': 'upper', 'name': 'Upper'}]}},
                '.storage/core.label_registry': {'data': {'labels': [{'label_id': 'tag', 'name': 'Tag'}]}},
                '.storage/core.area_registry': {'data': {'areas': [{'area_id': 'room', 'floor_id': 'upper', 'labels': ['tag']}]}}}
        for root in (self.original, self.after):
            for name, document in docs.items():
                (root / name).write_text(json.dumps(document))
        dashboard = '.storage/lovelace.map'
        (self.original / dashboard).write_text('{"entity":"sensor.source"}')
        (self.after / dashboard).write_text('{"entity":"sensor.target"}')
        edits = [self.edit, Edit(dashboard, b'sensor.source', b'sensor.target', 1)]
        args = dict(old=self.old, new=self.new, uuid=self.uuid, edits=edits)
        self.assertEqual([], compare(self.original, self.after, **args)['findings'])
        for name in docs:
            path = self.after / name
            saved = path.read_text()
            path.write_text('{"data": {"foreign": true}}')
            self.assertIn(('unexpected_change', name), compare(self.original, self.after, **args)['findings'])
            path.write_text(saved)
        (self.after / dashboard).write_text('{"entity":"sensor.source"}')
        kinds = {kind for kind, _ in compare(self.original, self.after, **args)['findings']}
        self.assertEqual({'unexpected_change', 'remaining_reference'}, kinds)

    def test_native_area_reference_change_and_foreign_fields(self):
        area_path = '.storage/core.area_registry'
        original = {'id': 'room', 'name': 'Room', 'temperature_entity_id': self.old,
                    'labels': ['b', 'a'], 'modified_at': '2026-01-01T00:00:00+00:00'}
        updated = dict(original, temperature_entity_id=self.new, labels=['a', 'b'],
                       modified_at='2026-01-01T00:00:01+00:00')
        for root, row in ((self.original, original), (self.after, updated)):
            (root / area_path).write_text(json.dumps({'data': {'areas': [row]}}))
        base = Path(self.tmp.name)
        def capture(root, name):
            return capture_tree(root, base / name, inventory_files=[],
                families={name: 'COMPLETE' for name in REQUIRED_FAMILIES},
                external_consumers={}, external_inventory_complete=True, native_locations=self.locations(root))
        # Native location snapshots normalize only unordered aliases/labels.
        from batch_readback import _location_normalize
        old_locations = self.locations
        self.locations = lambda root: {k: dict(v, data=_location_normalize(v['data']))
                                      for k, v in old_locations(root).items()}
        b, a = capture(self.original, 'geo-before'), capture(self.after, 'geo-after')
        change = RowChange(area_path, 'room', 'temperature_entity_id', self.old, self.new)
        report = compare(base / 'geo-before', base / 'geo-after', old=self.old, new=self.new,
                         uuid=self.uuid, edits=[self.edit], row_changes=[change], captures=(b, a),
                         consumer_checks=[], consumer_inventory={}, consumer_inventory_complete=True)
        self.assertEqual([], report['findings'])
        self.assertTrue(report['complete'])
        import copy
        damaged = copy.deepcopy(a)
        damaged['native_locations']['area_registry']['data']['areas'][0]['name'] = 'foreign'
        with self.assertRaises(ValueError):
            compare(base / 'geo-before', base / 'geo-after', old=self.old, new=self.new,
                    uuid=self.uuid, edits=[self.edit], row_changes=[change], captures=(b, damaged),
                    consumer_checks=[], consumer_inventory={}, consumer_inventory_complete=True)

    def test_escaped_json_reference_outside_edit_is_detected(self):
        escaped = '{"entity":"sensor.\\u0073ource"}'
        for root in (self.original, self.after):
            (root / '.storage/lovelace.hidden').write_text(escaped)
        report = self.read()
        self.assertIn(('remaining_reference', ['.storage/lovelace.hidden']), report['findings'])

    def test_ambiguous_json_and_escaped_yaml_are_unverified(self):
        for root in (self.original, self.after):
            (root / '.storage/lovelace.bad').write_text('{"x":1,"x":2}')
            (root / 'includes/escaped.yaml').write_text('entity_id: "sensor.\\u0073ource"\n')
        report = self.read()
        self.assertIn(('.storage/lovelace.bad', 'json_semantics_unverified'), report['unverified'])
        self.assertIn(('includes/escaped.yaml', 'yaml_escaped_scalar_unverified'), report['unverified'])
        self.assertFalse(report['complete'])

    def test_bad_original_never_becomes_verified_by_new_content(self):
        name = '.storage/lovelace.bad'
        (self.original / name).write_text('{"x":1,"x":2}')
        (self.after / name).write_text('{"x":2}')
        report = self.read()
        self.assertIn((name, 'original_json_semantics_unverified'), report['unverified'])
        self.assertFalse(report['complete'])

    def test_capture_reports_unknown_source_and_rejects_stale_inventory(self):
        (self.original / 'external.py').write_text('ENTITY = "sensor.source"')
        (self.original / 'thirdparty.db').write_bytes(b'\0')
        dest = Path(self.tmp.name) / 'snapshot'
        capture = capture_tree(self.original, dest, inventory_files=[], families={'yaml': 'COMPLETE'},
                               external_consumers={'mqtt': 'UNKNOWN'}, external_inventory_complete=True)
        self.assertIn(('external.py', 'executable_consumer_unverified'), capture['gaps'])
        self.assertIn(('thirdparty.db', 'database_consumer_unverified'), capture['gaps'])
        self.assertIn(('external:mqtt', 'UNKNOWN'), capture['gaps'])
        with self.assertRaises(ValueError):
            capture_tree(self.original, Path(self.tmp.name) / 'stale',
                         inventory_files=[('includes/template.yaml', '0' * 64)],
                         families={'yaml': 'COMPLETE'}, external_consumers={}, external_inventory_complete=True)

    def test_only_recognized_inactive_backup_is_separate(self):
        backup = self.original / '.storage/ha_entity_renamer_backups'
        backup.mkdir()
        recognized = 'yaml_' + 'a' * 64 + '-' + 'b' * 64 + '.yaml-backup'
        (backup / recognized).write_text('entity_id: sensor.source')
        (backup / 'unknown.yaml').write_text('entity_id: sensor.source')
        result = capture_tree(self.original, Path(self.tmp.name) / 'snap-backup', inventory_files=[],
                              families={'yaml': 'COMPLETE'}, external_consumers={}, external_inventory_complete=True)
        self.assertIn(('.storage/ha_entity_renamer_backups/' + recognized, 'renamer_backup_not_active_source'), result['separate'])
        self.assertIn('.storage/ha_entity_renamer_backups/unknown.yaml', result['files'])
        pending = self.original / '.storage/ha_entity_renamer_pending'
        pending.mkdir()
        later = capture_tree(self.original, Path(self.tmp.name) / 'snap-pending', inventory_files=[],
                             families={'yaml': 'COMPLETE'}, external_consumers={}, external_inventory_complete=True)
        self.assertNotIn('.storage/ha_entity_renamer_pending', later['directories'])
        (pending / 'foreign.yaml').write_text('entity_id: sensor.source')
        with_file = capture_tree(self.original, Path(self.tmp.name) / 'snap-pending-file', inventory_files=[],
                                 families={'yaml': 'COMPLETE'}, external_consumers={}, external_inventory_complete=True)
        self.assertIn('.storage/ha_entity_renamer_pending/foreign.yaml', with_file['files'])

    def test_discovery_unknowns_and_blockers_are_never_promoted(self):
        row = lambda name, status, reason='', blockers=(): SimpleNamespace(
            name=name, status=status, reason=reason, blockers=blockers)
        families, external = discovery_coverage(SimpleNamespace(families=(
            row('entity_registry', 'COMPLETE'), row('yaml_sources', 'COMPLETE', blockers=('dynamic',)),
            row('external', 'UNKNOWN', 'source_inventory_unavailable'))))
        self.assertEqual('COMPLETE', families['entity_registry'])
        self.assertEqual('COMPLETE:dynamic', families['yaml_graph'])
        self.assertEqual('UNKNOWN:source_inventory_unavailable', external)
        with self.assertRaises(ValueError):
            discovery_coverage(SimpleNamespace(families=(row('entity_registry', 'COMPLETE'),
                                                          row('entity_registry', 'UNKNOWN'))))


if __name__ == '__main__':
    unittest.main()
