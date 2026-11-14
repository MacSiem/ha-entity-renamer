"""One helper scope dispatches only typed, sealed PC and Template changes."""
import unittest
import json
import sys
from types import ModuleType
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
from custom_components.ha_entity_renamer.controlled_powercalc_helper import ControlledMixedHelperAdapter
from custom_components.ha_entity_renamer.helper_transaction import PreparedHelper
from custom_components.ha_entity_renamer.powercalc_group_metadata import PreparedPowercalcGroupMetadata
from custom_components.ha_entity_renamer.transaction import Refused
from custom_components.ha_entity_renamer.native_powercalc_accepted_consumers import _literal_documents
from custom_components.ha_entity_renamer.powercalc_runtime_observer import PowercalcRuntimeCapture, selected_powercalc_reference


class MixedHelperTests(unittest.IsolatedAsyncioTestCase):
    async def test_template_live_issue_fence_is_scoped_to_selected_entry(self):
        from custom_components.ha_entity_renamer.controlled_template_entry_witness import _check_live_issue
        helpers, ir = ModuleType('homeassistant.helpers'), ModuleType('homeassistant.helpers.issue_registry')
        class Registry:
            def __init__(self): self.issues = {('http', 'deprecated_yaml'): object()}
        ir.DATA_REGISTRY, ir.IssueRegistry = 'issues', Registry
        helpers.issue_registry = ir
        manager = Registry()
        hass = SimpleNamespace(data={'issues': manager})
        with patch.dict(sys.modules, {'homeassistant.helpers': helpers,
                                      'homeassistant.helpers.issue_registry': ir}):
            _check_live_issue(hass, 'selected')
            manager.issues[('template', 'composite_device_id_other')] = object()
            _check_live_issue(hass, 'selected')
            manager.issues[('template', 'composite_device_id_selected')] = object()
            with self.assertRaises(Refused):
                _check_live_issue(hass, 'selected')

    async def test_typed_dispatch_and_context_cover_both_directions(self):
        class Adapter:
            def __init__(self, name): self.name, self.events = name, []
            def bind_controlled_powercalc(self, handoff): self.events.append(('handoff', handoff))
            def bind_phase_context(self, ctx): self.events.append(('bind', ctx))
            async def inspect(self, step): self.events.append(('inspect', step)); return self.name
            async def apply(self, step, guard): self.events.append(('apply', step)); return self.name
            async def compensate(self, step, guard): self.events.append(('inverse', step)); return self.name
            async def verify(self, plan): return plan == 'plan'
            async def verify_rollback(self, plan): return plan == 'plan'
        pc, template = Adapter('pc'), Adapter('template')
        pc.handoff = object()
        group = object.__new__(PreparedPowercalcGroupMetadata)
        helper = PreparedHelper({'domain': 'template'}, {}, {})
        values = {'pc': group, 'template': helper, 'foreign': object()}
        mux = ControlledMixedHelperAdapter(values.__getitem__, pc, template)
        self.assertEqual(template.events, [('handoff', pc.handoff)])
        mux.bind_phase_context('ctx')
        for key in ('pc', 'template'):
            step = SimpleNamespace(prepared_ref=key)
            self.assertEqual(await mux.inspect(step), key)
            self.assertEqual(await mux.apply(step, lambda: None), key)
            self.assertEqual(await mux.compensate(step, lambda: None), key)
        with self.assertRaises(Refused):
            await mux.inspect(SimpleNamespace(prepared_ref='foreign'))
        self.assertTrue(await mux.verify('plan')); self.assertTrue(await mux.verify_rollback('plan'))

    async def test_template_helper_checks_shared_population_lifecycle(self):
        from custom_components.ha_entity_renamer.controlled_template_entry_adapter import ControlledEntryReceiptAdapter
        from custom_components.ha_entity_renamer.controlled_powercalc_handoff import ControlledPowercalcHandoff
        adapter = object.__new__(ControlledEntryReceiptAdapter)
        chain = object()
        adapter.chain = chain
        owned = object.__new__(ControlledPowercalcHandoff)
        owned.chain = chain
        adapter.bind_controlled_powercalc(owned)
        self.assertIs(adapter._controlled_handoff, owned)
        foreign = object.__new__(ControlledPowercalcHandoff)
        foreign.chain = object()
        with self.assertRaises(Refused):
            adapter.bind_controlled_powercalc(foreign)
        self.assertIs(adapter._controlled_handoff, owned)
        calls = []
        adapter.authorization_guard = lambda: calls.append('auth')
        handoff = SimpleNamespace(receipt=None, setup_reference=None,
            require_inactive=lambda: calls.append('inactive'), require_active=lambda: calls.append('active'))
        adapter._controlled_handoff = handoff
        adapter._guard()
        handoff.receipt = ('pc-intent', 'digest')
        adapter._guard()
        handoff.setup_reference = ('pc-setup', 'digest')
        adapter._guard()
        self.assertEqual(calls, ['auth', 'auth', 'inactive', 'auth', 'active'])
        handoff.setup_reference = None
        handoff.require_inactive = lambda: (_ for _ in ()).throw(Refused('foreign activation'))
        with self.assertRaises(Refused):
            adapter._guard()


class MixedInverseReceiptTests(unittest.TestCase):
    def test_source_image_rejects_foreign_edits_for_both_directions(self):
        from custom_components.ha_entity_renamer.native_powercalc_accepted_consumers import _source_image
        from custom_components.ha_entity_renamer.powercalc_candidate_sources import CandidatePowercalcSources
        from custom_components.ha_entity_renamer.yaml_source_binding import OriginalSourceEvidence
        keys = ('core.entity_registry', 'core.device_registry', 'core.config_entries')
        original = {'core.entity_registry': {'data': {'entities': []}},
                    'core.device_registry': {'data': {'devices': []}},
                    'core.config_entries': {'data': {'entries': [
                        {'entry_id': 'template', 'options': {'state': 'old'}, 'modified_at': 't0'},
                        {'entry_id': 'pc', 'data': {'energy': 'old'}, 'modified_at': 'p0'},
                        {'entry_id': 'foreign', 'data': {'untouched': True}, 'modified_at': 'f0'}]}}}
        proposed = deepcopy(original)
        proposed['core.config_entries']['data']['entries'][0]['options']['state'] = 'new'
        proposed['core.config_entries']['data']['entries'][1]['data']['energy'] = 'new'
        raw = lambda doc: json.dumps(doc, sort_keys=True).encode()
        planned = SimpleNamespace(energy=None, documents=tuple(
            (key, raw(original[key]), raw(proposed[key])) for key in keys))
        original_source = SimpleNamespace(native_json='old', closed_graph_json='old')
        candidate_source = SimpleNamespace(native_json='new', closed_graph_json='new')
        candidate = CandidatePowercalcSources(b'original', 'digest', (), planned, candidate_source)
        evidence = OriginalSourceEvidence(None, b'original', (), powercalc_sources=original_source,
                                          powercalc_candidate_sources=candidate)
        receipt = SimpleNamespace(version='2026.10.4', source_files=(('configuration.yaml', 'oldhash'),),
                                  proposed_files=(('configuration.yaml', 'newhash'),))
        chain = SimpleNamespace(original_receipt=receipt,
            baseline=SimpleNamespace(files=tuple(SimpleNamespace(key=key, raw=raw(original[key])) for key in keys)),
            plan=SimpleNamespace(steps=tuple(SimpleNamespace(adapter='helpers') for _ in range(2))),
            changes=tuple(SimpleNamespace(before={'identity': {'entry_id': key}}) for key in ('template', 'pc')),
            _setup_history=[])
        # The candidate seal is tested independently; exercise the actual full
        # source-image comparison and foreign-file fence with deterministic inputs.
        with patch('custom_components.ha_entity_renamer.native_powercalc_accepted_consumers._envelope',
                   side_effect=lambda value, *_: json.loads(value)), \
             patch.object(CandidatePowercalcSources, 'assert_original'):
            for docs, digest, position in ((original, 'oldhash', 'original'), (proposed, 'newhash', 'proposed')):
                def state(value, file_digest=digest):
                    return SimpleNamespace(sources=lambda: {'configuration.yaml': [file_digest]},
                                           documents=lambda: value)
                self.assertEqual(_source_image(chain, evidence, state(deepcopy(docs)))[0], position)
                for mutate in ('field', 'delete', 'add', 'order', 'file'):
                    foreign = deepcopy(docs)
                    rows = foreign['core.config_entries']['data']['entries']
                    if mutate == 'field': rows[2]['data']['untouched'] = False
                    elif mutate == 'delete': rows.pop()
                    elif mutate == 'add': rows.append({'entry_id': 'intruder'})
                    elif mutate == 'order': rows.reverse()
                    with self.assertRaises(ValueError, msg=(position, mutate)):
                        _source_image(chain, evidence, state(foreign, 'foreignhash' if mutate == 'file' else digest))

    def test_source_image_dashboard_requires_same_receipted_head(self):
        from custom_components.ha_entity_renamer.native_powercalc_accepted_consumers import _source_image
        from custom_components.ha_entity_renamer.powercalc_candidate_sources import CandidatePowercalcSources
        from custom_components.ha_entity_renamer.yaml_source_binding import OriginalSourceEvidence
        from custom_components.ha_entity_renamer.accepted_input_state import AcceptedInputState
        keys = ('core.entity_registry', 'core.device_registry', 'core.config_entries')
        docs = {key: {'data': {}} for key in keys}
        raw = lambda doc: json.dumps(doc, sort_keys=True).encode()
        planned = SimpleNamespace(energy=None, documents=tuple(
            (key, raw(docs[key]), raw(docs[key])) for key in keys))
        original = SimpleNamespace(native_json='old', closed_graph_json='old')
        candidate = CandidatePowercalcSources(b'original', 'digest', (), planned,
            SimpleNamespace(native_json='new', closed_graph_json='new'))
        evidence = OriginalSourceEvidence(None, b'original', (), powercalc_sources=original,
                                          powercalc_candidate_sources=candidate)
        path = '.storage/lovelace.selected'
        receipt = SimpleNamespace(version='2026.10.4',
            source_files=(('configuration.yaml', 'old'), (path, 'dashboard-before'),
                          ('.storage/lovelace.foreign', 'foreign')),
            proposed_files=(('configuration.yaml', 'new'),))
        change = SimpleNamespace(before=SimpleNamespace(dashboard=SimpleNamespace(key='lovelace.selected')))
        chain = SimpleNamespace(original_receipt=receipt,
            baseline=SimpleNamespace(files=tuple(SimpleNamespace(key=k, raw=raw(docs[k])) for k in keys)),
            plan=SimpleNamespace(steps=(SimpleNamespace(adapter='dashboard'),)),
            _original_change=lambda _: change, _setup_history=[])
        def state(yaml, dashboard, foreign='foreign', head='head'):
            return AcceptedInputState(b'original', head, {
                'configuration.yaml': [yaml], path: [dashboard],
                '.storage/lovelace.foreign': [foreign]}, deepcopy(docs))
        with patch('custom_components.ha_entity_renamer.native_powercalc_accepted_consumers._envelope',
                   side_effect=lambda value, *_: json.loads(value)), \
             patch('custom_components.ha_entity_renamer.native_powercalc_accepted_consumers._literal_documents',
                   return_value=True), patch.object(CandidatePowercalcSources, 'assert_original'):
            for yaml, dashboard, position in (('new', 'dashboard-after', 'proposed'),
                                               ('old', 'dashboard-restored', 'original')):
                accepted = state(yaml, dashboard)
                chain.reconstruct_inputs = lambda: accepted
                self.assertEqual(_source_image(chain, evidence, accepted)[0], position)
                with self.assertRaisesRegex(ValueError, 'accepted_dashboard_head_changed'):
                    _source_image(chain, evidence, state(yaml, 'unreceipted'))
                foreign = state(yaml, dashboard, foreign='tampered')
                chain.reconstruct_inputs = lambda: foreign
                with self.assertRaisesRegex(ValueError, 'accepted_image_unverified'):
                    _source_image(chain, evidence, foreign)
            receipt.source_files = (('configuration.yaml', 'old'),)
            chain.reconstruct_inputs = lambda: accepted
            with self.assertRaisesRegex(ValueError, 'accepted_dashboard_source_missing'):
                _source_image(chain, evidence, accepted)

    def test_only_receipted_script_reverse_field_is_accepted(self):
        old = {'id': 'u', 'entity_id': 'script.old', 'platform': 'script',
               'unique_id': 'old', 'previous_unique_id': None, 'modified_at': 'a'}
        new = {**old, 'entity_id': 'script.new', 'unique_id': 'new', 'previous_unique_id': 'old'}
        reverse = {**old, 'previous_unique_id': 'new', 'modified_at': 'b'}
        docs = {'core.entity_registry': {'data': {'entities': [old]}},
                'core.device_registry': {'data': {'devices': []}},
                'core.config_entries': {'data': {'entries': []}}}
        accepted = deepcopy(docs)
        accepted['core.entity_registry']['data']['entities'][0] = reverse
        change = SimpleNamespace(kind='entity', identity='u', before=old, after=new)
        receipt = {'kind': 'registry_receipt', 'binding': {'direction': 'rollback'},
                   'mutation': {'kind': 'entity', 'identity': 'u', 'after_json': json.dumps(reverse)}}
        chain = SimpleNamespace(plan=SimpleNamespace(steps=(SimpleNamespace(adapter='registry'),)),
                                changes=(change,), _setup_history=[('receipt', receipt)])
        self.assertTrue(_literal_documents(docs, accepted, docs, chain))
        foreign = deepcopy(accepted)
        foreign['core.entity_registry']['data']['entities'][0]['platform'] = 'automation'
        self.assertFalse(_literal_documents(docs, foreign, docs, chain))
        receipt['binding']['direction'] = 'forward'
        self.assertFalse(_literal_documents(docs, accepted, docs, chain))

    def test_powercalc_routing_requires_a_selected_native_relation(self):
        capture = PowercalcRuntimeCapture('2026.10.4', json.dumps({'objects': [
            {'fields': {'_source_entity': {'entity_id': 'light.used'}}}]}).encode(), (), b'')
        rows = [{'entity_id': 'sensor.group', 'platform': 'powercalc'},
                {'entity_id': 'light.used', 'platform': 'mqtt'}]
        self.assertTrue(selected_powercalc_reference(capture, {'light.used': 'light.new'}, rows))
        self.assertTrue(selected_powercalc_reference(capture, {'sensor.group': 'sensor.new'}, rows))
        self.assertFalse(selected_powercalc_reference(capture, {'light.unrelated': 'light.new'}, rows))
