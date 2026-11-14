"""Independent full capture for controlled Script ID, caller and live run."""
import json
import hashlib
from pathlib import Path
import sqlite3
import re
import unittest
import asyncio

import test_mixed_fixture_census_native as census_fixture
from batch_readback import Edit, RowChange, capture_tree, compare, discovery_coverage, native_location_snapshot, native_empty_auth_snapshot
from mixed_fixture_census import collect_native, consumer_evidence, census_coverage


class ControlledScriptCensus(census_fixture.NativeMixedFixtureCensusTest):
    async def test_own_script_caller_census_a91(self):
        from homeassistant.helpers import area_registry, floor_registry, label_registry
        runtime, source_mapping, source_uuids, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        source_old, source_new = next(iter(source_mapping.items()))
        from homeassistant.core import Context
        path = root / 'mixed_scripts.yaml'
        path.write_text(path.read_text().replace('  sequence:\n', '  sequence:\n'
            '  - delay: "{{ 120 if long | default(false) else 0 }}"\n', 1)
            + '\nstatic_caller:\n  sequence:\n  - action: script.mixed_source\n')
        await hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        await self._flush_documents()
        script = next(e for e in hass.data['script'].entities if e.unique_id == 'mixed_source')
        script_old, script_new = script.entity_id, 'script.census_script_renamed'
        script_uuid = self.f.entity.async_get(script_old).id
        mapping = {**source_mapping, script_old: script_new}
        managers = []
        for module, member in ((floor_registry, 'floors'), (label_registry, 'labels'), (area_registry, 'areas')):
            manager = hass.data.get(module.DATA_REGISTRY)
            if manager is None:
                await module.async_load(hass)
                manager = hass.data[module.DATA_REGISTRY]
            elif not hasattr(manager, member):
                await manager.async_load()
            managers.append(manager)
        floors, labels, areas = managers
        floor = floors.async_create('Independent floor', level=2)
        label = labels.async_create('Independent label', color='blue')
        area = areas.async_create('Independent area', floor_id=floor.floor_id, labels={label.label_id})
        self.f.entity.async_update_entity(source_old, area_id=area.id, labels={label.label_id})
        for manager in managers:
            await manager._store.async_save(manager._data_to_save())
        await hass.auth._store._store.async_save(hass.auth._store._data_to_save())
        output = Path('/qa/script-census')
        output.mkdir()
        evidence = {'apply_attempted': False, 'inverse_attempted': False, 'snapshots': {}, 'mapping': mapping}
        def save():
            (output / 'result.json').write_text(json.dumps(evidence, sort_keys=True, default=str))
        def recorder_snapshot(label):
            source = root / 'fixture.db'
            destination = output / (label + '-recorder.sqlite')
            if not source.is_file() or destination.exists():
                raise RuntimeError('recorder_snapshot_source_or_destination_invalid')
            original = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
            try:
                copy = sqlite3.connect(destination)
                try:
                    original.backup(copy)
                finally:
                    copy.close()
            finally:
                original.close()
            check = sqlite3.connect(destination.as_uri() + '?mode=ro', uri=True)
            try:
                integrity = check.execute('PRAGMA quick_check').fetchone()[0]
                state_count = check.execute('SELECT COUNT(*) FROM states').fetchone()[0]
                entities = [row[0] for row in check.execute('SELECT entity_id FROM states_meta ORDER BY entity_id')]
            finally:
                check.close()
            if integrity != 'ok' or state_count < 1:
                raise RuntimeError('recorder_snapshot_empty_or_corrupt')
            return {'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
                    'bytes': destination.stat().st_size, 'states': state_count,
                    'entity_ids': entities, 'integrity': integrity}
        def recorder_retained(original, observed, *, forward):
            # Compare all captured rows of Recorder's state and statistics
            # tables by their native primary keys. New history may be appended;
            # no original row may disappear or be changed.
            findings, counts = [], {}
            first = sqlite3.connect(original.as_uri() + '?mode=ro', uri=True)
            second = sqlite3.connect(observed.as_uri() + '?mode=ro', uri=True)
            try:
                names = [tuple(row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"))
                    for db in (first, second)]
                if (names[0] != names[1] or not names[0]
                        or not all(re.fullmatch(r'[a-z0-9_]+', name) for name in names[0])
                        or not {'states', 'states_meta', 'state_attributes', 'statistics_meta',
                                'statistics', 'statistics_short_term'} <= set(names[0])):
                    return {'counts': {}, 'findings': [['recorder', 'table_inventory_changed_or_incomplete']]}
                for table in names[0]:
                    schemas = [db.execute('PRAGMA table_info(' + table + ')').fetchall() for db in (first, second)]
                    if not schemas[0] or schemas[0] != schemas[1]:
                        findings.append([table, 'schema_changed_or_missing']); continue
                    keys = [row[1] for row in schemas[0] if row[5]]
                    if len(keys) != 1:
                        findings.append([table, 'primary_key_unqualified']); continue
                    index = next(i for i, row in enumerate(schemas[0]) if row[1] == keys[0])
                    rows = [[tuple(row) for row in db.execute('SELECT * FROM ' + table)] for db in (first, second)]
                    after_by_id = {row[index]: row for row in rows[1]}
                    counts[table] = [len(rows[0]), len(rows[1])]
                    if len(after_by_id) != len(rows[1]):
                        findings.append([table, 'duplicate_primary_key']); continue
                    columns = [row[1] for row in schemas[0]]
                    latest = {}
                    if table == 'states':
                        metadata = columns.index('metadata_id')
                        for prior in rows[0]:
                            latest[prior[metadata]] = max(latest.get(prior[metadata], prior[index]), prior[index])
                    for row in rows[0]:
                        actual = after_by_id.get(row[index])
                        if actual is None:
                            findings.append([table, 'original_history_missing']); break
                        expected = list(row)
                        if table == 'states_meta' and forward:
                            name_index = columns.index('entity_id')
                            expected[name_index] = mapping.get(row[name_index], row[name_index])
                        if table == 'states':
                            # Core finalizes the previously current row when a
                            # new state event arrives. A NULL last_reported can
                            # become its exact existing last_updated timestamp.
                            reported, updated = columns.index('last_reported_ts'), columns.index('last_updated_ts')
                            if (row[index] == latest[row[metadata]] and row[reported] is None
                                    and actual[reported] == row[updated]):
                                expected[reported] = row[updated]
                        if tuple(expected) != actual:
                            findings.append([table, 'original_history_changed']); break
            finally:
                first.close(); second.close()
            return {'counts': counts, 'findings': findings}
        async def capture(label, source_direction):
            current = await collect_native(hass, root, self.producer, source_direction,
                script_service='static_caller', selected_script=(script_new if source_new in source_direction else script_old).split('.')[1])
            await self._flush_documents()
            from homeassistant.components import recorder
            await recorder.get_instance(hass).async_block_till_done()
            recorder_copy = await hass.async_add_executor_job(recorder_snapshot, label)
            evidence.setdefault('recorder', {})[label] = recorder_copy
            save()
            graph = self.module('yaml_plan').plan_yaml(str(root), source_direction)
            row = self.f.entity.async_get(source_old) or self.f.entity.async_get(source_new)
            proposal = {'entities': [{'entity_id': source_old, 'proposed_entity_id': source_new,
                                      'device_id': row.device_id}], 'devices': [], 'statistics': []}
            discovery = await self.module('discovery').async_discover(hass, proposal, guard=lambda: None)
            families, external = discovery_coverage(discovery)
            families, external_consumers = census_coverage(current, families)
            destination = output / label
            info = capture_tree(root, destination, inventory_files=[(r.path, r.sha256) for r in graph.files],
                families=families, external_consumers=external_consumers,
                external_inventory_complete=current['external']['empty_in_bounded_fixture'],
                recorder_databases=('fixture.db',), native_locations=native_location_snapshot(hass, root),
                native_auth=native_empty_auth_snapshot(hass, root))
            (output / (label + '-census.json')).write_text(json.dumps(current, sort_keys=True, default=str))
            (output / (label + '-inventory.json')).write_text(json.dumps(info, sort_keys=True, default=str))
            evidence['snapshots'][label] = {'complete': current['complete'], 'unverified': current['unverified'],
                'external': external, 'auth': native_empty_auth_snapshot(hass, root),
                'locations': native_location_snapshot(hass, root)}
            save()
            return current, destination, info
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        before = after = restored = None
        try:
            before = await capture('before', source_mapping)
            await hass.services.async_call('script', 'mixed_source', {'long': True}, blocking=False)
            for _ in range(1000):
                if script.script.is_running:
                    break
                await asyncio.sleep(.001)
            evidence['active_before_prepare'] = script.script.is_running; save()
            review = await runtime.native_yaml_prepare(hass, mapping, actor_id='native-admin',
                guard=lambda: None, refresh=refresh)
            evidence['review'] = review; save()
            evidence['apply_attempted'] = True; save()
            with census_fixture.mixed.native_diagnostics(self.module, sink=evidence):
                evidence['apply'] = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                    actor_id='native-admin', guard=lambda: None, refresh=refresh)
            save()
            if evidence['apply'].get('state') != 'applied':
                raise RuntimeError('apply did not complete: ' + str(evidence['apply'].get('state')))
            after = await capture('after', {source_new: source_old})
            evidence['inspect'] = await runtime.recovery_inspect(hass, review['operation_id'], review['fingerprint'],
                guard=lambda: None, refresh=refresh); save()
            if evidence['inspect'].get('eligible') is True:
                evidence['inverse_attempted'] = True; save()
                evidence['inverse'] = await runtime.native_yaml_reverse(hass, review['operation_id'], review['fingerprint'],
                    guard=lambda: None, refresh=refresh); save()
                restored = await capture('restored', source_mapping)
                evidence['restored_inspect'] = await runtime.recovery_inspect(hass, review['operation_id'], review['fingerprint'],
                    guard=lambda: None, refresh=refresh); save()
            if before is not None and after is not None and restored is not None:
                edits = [Edit(path, source_old.encode(), source_new.encode(),
                              (before[1] / path).read_bytes().count(source_old.encode()))
                         for path in ('template.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml')]
                script_bytes = (before[1] / 'mixed_scripts.yaml').read_bytes()
                edits.extend((Edit('mixed_scripts.yaml', b'mixed_source:', b'census_script_renamed:', 1),
                    Edit('mixed_scripts.yaml', script_old.encode(), script_new.encode(), script_bytes.count(script_old.encode()))))
                original_row = next(row for row in json.loads((before[1] / '.storage/core.entity_registry').read_text())['data']['entities'] if row['id'] == script_uuid)
                old_key, new_key = script_old.split('.')[1], script_new.split('.')[1]
                rows = [RowChange('.storage/core.entity_registry', script_uuid, 'unique_id', old_key, new_key),
                    RowChange('.storage/core.entity_registry', script_uuid, 'previous_unique_id', original_row['previous_unique_id'], old_key, new_key)]
                if original_row.get('original_name') == old_key and original_row.get('name') is None:
                    rows.extend((RowChange('.storage/core.entity_registry', script_uuid, 'original_name', old_key, new_key),
                        RowChange('.storage/core.entity_registry', script_uuid, 'name', None, old_key)))
                from homeassistant.const import __version__
                if __version__ in ('2026.10.2', '2026.10.4'):
                    rows.append(RowChange('.storage/core.entity_registry', script_uuid, 'suggested_object_id', old_key, new_key))
                maps = [(source_uuids[source_old], source_old, source_new), (script_uuid, script_old, script_new)]
                for label, observed, reverse in (('forward', after, False), ('restore', restored, True)):
                    inventory, checks, bindings = consumer_evidence(before[0], observed[0],
                        script_renames={'mixed_source': 'census_script_renamed'} if not reverse else {})
                    evidence[label + '_comparison'] = compare(before[1], observed[1], mappings=maps,
                        edits=edits, row_changes=rows, reverse=reverse, captures=(before[2], observed[2]),
                        consumer_inventory=inventory, consumer_inventory_complete=True,
                        consumer_checks=checks, source_consumers=bindings)
                    save()
            if before is not None and after is not None and restored is not None:
                for label in ('after', 'restored'):
                    evidence['recorder_' + label + '_retention'] = await hass.async_add_executor_job(
                        lambda label=label: recorder_retained(output / 'before-recorder.sqlite',
                            output / (label + '-recorder.sqlite'), forward=(label == 'after')))
                    save()
            self.assertTrue(evidence['active_before_prepare'])
            self.assertEqual(evidence['apply']['state'], 'applied')
            self.assertTrue(evidence['inspect']['eligible'])
            self.assertEqual(evidence['inverse']['state'], 'rolled_back')
            self.assertEqual(evidence['restored_inspect']['state'], 'rolled_back')
            self.assertEqual(evidence['restored_inspect']['reason'], 'restored_verified')
            self.assertIn(source_old, evidence['recorder']['before']['entity_ids'])
            self.assertIn(source_new, evidence['recorder']['after']['entity_ids'])
            self.assertIn(source_old, evidence['recorder']['restored']['entity_ids'])
            for label in ('after', 'restored'):
                self.assertEqual(evidence['recorder_' + label + '_retention']['findings'], [])
            for label in ('forward', 'restore'):
                result = evidence[label + '_comparison']
                self.assertEqual(result['findings'], [])
                self.assertTrue(result['complete'], result['unverified'])
        except BaseException as error:
            evidence['primary_error'] = {'type': type(error).__name__, 'message': str(error)}; save()
            if evidence['apply_attempted'] and not evidence['inverse_attempted']:
                try:
                    evidence['exception_inspect'] = await runtime.recovery_inspect(hass,
                        review['operation_id'], review['fingerprint'], guard=lambda: None, refresh=refresh)
                except BaseException as inspect_error:
                    evidence['exception_inspect_error'] = {'type': type(inspect_error).__name__, 'message': str(inspect_error)}
                save()
            raise
        finally:
            final_error = None
            try:
                evidence.setdefault('recorder', {})['final'] = await hass.async_add_executor_job(recorder_snapshot, 'final')
            except BaseException as error:
                final_error = error
                evidence['final_recorder_error'] = {'type': type(error).__name__, 'message': str(error)}
            save()
            try:
                if not (output / 'final').exists():
                    # Final after any exception is read-only: no stimulus,
                    # store flush, retry, or implicit restore of uncertain work.
                    info = capture_tree(root, output / 'final', inventory_files=[],
                        families={'final': 'UNKNOWN'}, external_consumers={},
                        recorder_databases=('fixture.db',),
                        native_locations=native_location_snapshot(hass, root),
                        native_auth=native_empty_auth_snapshot(hass, root))
                    (output / 'final-inventory.json').write_text(json.dumps(info, sort_keys=True, default=str))
            except BaseException as error:
                if final_error is None:
                    final_error = error
                evidence['final_error'] = {'type': type(error).__name__, 'message': str(error)}
            save()
            if final_error is not None and 'primary_error' not in evidence:
                raise final_error
