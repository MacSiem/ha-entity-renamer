"""Actual server-owned Template/automation/script forward and fresh reverse.

The fixture supplies native data, never a Plan or a positive verifier. Owner
runs this acceptance target through FIFO on the supported actual Core versions.
"""
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import unittest

import test_native_template_yaml_server as server


@contextmanager
def native_diagnostics(module):
    """Observe sanitized product failures/positions without replacing a seam."""
    failures, positions = [], []
    tool = next(i for i in range(6) if sys.monitoring.get_tool(i) is None)
    sys.monitoring.use_tool_id(tool, 'mixed native YAML acceptance diagnosis')
    def raised(code, offset, error):
        if ('/custom_components/ha_entity_renamer/' not in code.co_filename
                or isinstance(error, (StopIteration, StopAsyncIteration, GeneratorExit))
                or str(error) == 'template_composition_rejected'):
            return
        row = (Path(code.co_filename).name, code.co_name, type(error).__name__, str(error)[:160])
        if row not in failures:
            failures.append(row)
            del failures[:-30]
    def returned(code, offset, value):
        row = {'owner': code.co_qualname}
        if type(value).__name__ == 'RegistryEvidence':
            row.update(position=value.position, reason=value.reason)
        else:
            row['position'] = getattr(value, 'value', type(value).__name__)
        positions.append(row)
        del positions[:-20]
    codes = [module('native_registry').NativeRegistryBridge.inspect.__code__,
             module('native_registry_transaction').ReceiptedRegistryTransactionAdapter.inspect.__code__,
             module('native_yaml_transaction').ReceiptedYAMLTransactionAdapter.inspect.__code__]
    try:
        sys.monitoring.register_callback(tool, sys.monitoring.events.RAISE, raised)
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, returned)
        sys.monitoring.set_events(tool, sys.monitoring.events.RAISE)
        for code in codes:
            sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_RETURN)
        yield
    finally:
        sys.monitoring.set_events(tool, 0)
        for code in codes:
            sys.monitoring.set_local_events(tool, code, 0)
        sys.monitoring.register_callback(tool, sys.monitoring.events.RAISE, None)
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, None)
        sys.monitoring.free_tool_id(tool)
        print('MIXED_NATIVE_FAILURES', json.dumps(failures))
        print('MIXED_NATIVE_POSITIONS', json.dumps(positions))


class CompositeGuardThreadTests(unittest.IsolatedAsyncioTestCase):
    async def test_physical_worker_does_not_read_dependent_ha_runtime(self):
        import importlib
        import threading
        runtime = importlib.import_module(server.activation.fixture.fixtures.PACKAGE + '.mixed_yaml_runtime')
        consumers = object.__new__(runtime.MixedYAMLConsumers)
        consumers.thread = threading.get_ident()
        class LoopOnly:
            def check(self):
                raise AssertionError('physical worker read the HA runtime')
        consumers.phases = LoopOnly()
        await asyncio.to_thread(consumers.check)


@unittest.skipUnless(server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeTemplateMixedYAMLServerTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = server.NativeTemplateYAMLServerTests.asyncSetUp
    _before_start = server.NativeTemplateYAMLServerTests._before_start
    _flush_documents = server.NativeTemplateYAMLServerTests._flush_documents
    prepare = server.NativeTemplateYAMLServerTests.prepare
    emit = server.NativeTemplateYAMLServerTests.emit
    _runtime = server.NativeTemplateYAMLServerTests._runtime
    module = server.NativeTemplateYAMLServerTests.module
    stimulus = server.NativeTemplateYAMLServerTests.stimulus

    async def data(self):
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.setup import async_setup_component
        # This helper sets up native data only (validate=False). It constructs
        # no transaction Plan/receipt/chain and no positive verifier.
        runtime, mapping, uuids, _ = await server.NativeTemplateYAMLServerTests.data(
            self, variant='mixed', source_consumers=False)
        hass, root = self.f.hass, self.f.root
        old = next(iter(mapping))
        (root / 'mixed_automations.yaml').write_text(
            '- id: mixed_source\n'
            '  alias: Mixed source observer\n'
            '  initial_state: true\n'
            '  triggers:\n'
            '  - trigger: state\n'
            '    entity_id: ' + old + '\n'
            '  actions:\n'
            '  - event: mixed_yaml_automation_result\n'
            '    event_data:\n'
            '      value: "{{ states(\'' + old + '\') }}"\n')
        (root / 'mixed_scripts.yaml').write_text(
            'mixed_source:\n'
            '  sequence:\n'
            '  - event: mixed_yaml_script_result\n'
            '    event_data:\n'
            '      value: "{{ states(\'' + old + '\') }}"\n')
        with (root / 'configuration.yaml').open('a') as handle:
            handle.write('\nautomation: !include mixed_automations.yaml\n'
                         'script: !include mixed_scripts.yaml\n')
        config = await async_hass_config_yaml(hass)
        for domain in ('automation', 'script'):
            self.assertTrue(await async_setup_component(hass, domain, config))
        await hass.async_block_till_done()
        await self._flush_documents()
        # Keep registry UUID and all consumer IDs; native reload is expected to
        # replace objects/listener tokens while preserving this logical graph.
        dependents = tuple(sorted((row.platform, row.unique_id, row.entity_id, row.id)
            for row in self.f.entity.entities.values()
            if row.platform in ('automation', 'script')))
        self.assertEqual({(domain, key) for domain, key, _, _ in dependents},
                         {('automation', 'mixed_source'), ('script', 'mixed_source')})
        paths = ('configuration.yaml', 'template.yaml',
                 'mixed_automations.yaml', 'mixed_scripts.yaml')
        original = {path: (root / path).read_bytes() for path in paths}
        return runtime, mapping, uuids, dependents, original

    async def _native_staging_case(self, enabled):
        runtime, mapping, _, dependents, original = await self.data()
        hass, root = self.f.hass, self.f.root
        component = hass.data['automation']
        entity, = component.entities
        if not enabled:
            await entity.async_turn_off()
        events = []
        remove = hass.bus.async_listen('mixed_yaml_automation_result',
                                      lambda event: events.append(str(event.data['value'])))
        self.addCleanup(remove)
        native = self.module('native_automation_transition')
        transition = native.NativeAutomationTransition(hass)
        self.assertEqual(transition.enabled, (('mixed_source', enabled),))
        before_ids = {(key, row.id) for key, row in self.f.entity.entities.items()}
        await transition.quiesce()
        transition.assert_disabled()
        # A native reload would obey YAML initial_state:true. The staged native
        # constructor must stay disabled even after a changed configuration.
        path = root / 'mixed_automations.yaml'
        proposed = original['mixed_automations.yaml'].replace(
            b"states('", b"1 + states('").replace(b"') }}", b"')|int }}")
        self.assertNotEqual(proposed, original['mixed_automations.yaml'])
        path.write_bytes(proposed)
        await transition.stage()
        await hass.async_block_till_done()
        transition.assert_disabled()
        staged, = component.entities
        self.assertIsNot(staged, entity)
        self.assertEqual(staged.raw_config['initial_state'], True)
        self.assertEqual(staged._initial_state, False)
        self.assertEqual(events, [])
        self.assertEqual({(key, row.id) for key, row in self.f.entity.entities.items()}, before_ids)
        await transition.restore()
        await hass.async_block_till_done()
        self.assertIs(staged._is_enabled, enabled)
        self.assertEqual(events, [])
        await self.stimulus(11)
        await hass.async_block_till_done()
        self.assertEqual(events, ['12'] if enabled else [])
        events.clear()
        # Fresh capability, same native source and original physical bytes.
        reverse = native.NativeAutomationTransition(hass)
        await reverse.quiesce()
        path.write_bytes(original['mixed_automations.yaml'])
        await reverse.stage()
        reverse.assert_disabled()
        await reverse.restore()
        await hass.async_block_till_done()
        restored, = component.entities
        self.assertIs(restored._is_enabled, enabled)
        self.assertEqual(restored.raw_config, entity.raw_config)
        self.assertEqual(events, [])
        await self.stimulus(13)
        await hass.async_block_till_done()
        self.assertEqual(events, ['13'] if enabled else [])
        self.assertEqual({(key, row.id) for key, row in self.f.entity.entities.items()}, before_ids)

    async def test_native_staging_preserves_enabled_with_yaml_initial_true(self):
        await self._native_staging_case(True)

    async def test_native_staging_preserves_disabled_with_yaml_initial_true(self):
        await self._native_staging_case(False)

    async def test_real_source_change_in_detached_interval_never_succeeds(self):
        from attr import asdict
        runtime, mapping, _, _, original = await self.data()
        hass, root = self.f.hass, self.f.root
        entity, = hass.data['automation'].entities
        before = {key: asdict(row) for key, row in self.f.entity.entities.items()}
        changed = False
        def guard():
            nonlocal changed
            if not changed and entity._is_enabled is False:
                changed = True
                # Real source event, no replacement observer/baseline or fake ACK.
                self.producer._attr_native_value = 17
                self.producer.async_write_ha_state()
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        with self.assertRaises(self.module('transaction').Refused):
            await runtime.native_forward(hass, mapping, guard=guard, refresh=refresh)
        self.assertTrue(changed)
        self.assertEqual({key: asdict(row) for key, row in self.f.entity.entities.items()}, before)
        for path, raw in original.items():
            self.assertEqual((root / path).read_bytes(), raw)
        records = await runtime.journal.active()
        self.assertEqual(len(records), 1)
        self.assertNotEqual(records[0].status, 'applied')
        store = self.module('native_registry_receipts').native_registry_receipt_store(hass, runtime.preparations)
        await store.initialize()
        key = self.module('native_automation_lifecycle')._key(
            records[0].operation_id, records[0].plan.fingerprint, 'forward', 'completed')
        self.assertIsNone(store.read(key))
        self.assertFalse(runtime.operation_lock.locked())

    async def test_running_automation_action_refuses_before_effects(self):
        from attr import asdict
        from homeassistant.core import Context
        runtime, mapping, _, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        path = root / 'mixed_automations.yaml'
        path.write_text(path.read_text() + '  - delay: 60\n')
        await hass.services.async_call('automation', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        await self._flush_documents()
        entity, = hass.data['automation'].entities
        self.producer._attr_native_value = 5
        self.producer.async_write_ha_state()
        for _ in range(100):
            if entity.action_script.is_running:
                break
            await asyncio.sleep(0.001)
        self.assertTrue(entity.action_script.is_running)
        before = {key: asdict(row) for key, row in self.f.entity.entities.items()}
        files = {name: (root / name).read_bytes() for name in (
            'configuration.yaml', 'template.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml')}
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        try:
            with self.assertRaises((ValueError, self.module('transaction').Refused)):
                await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
            self.assertEqual({key: asdict(row) for key, row in self.f.entity.entities.items()}, before)
            for name, raw in files.items():
                self.assertEqual((root / name).read_bytes(), raw)
            self.assertTrue(entity.action_script.is_running, 'admission must not cancel user actions')
            self.assertFalse(runtime.operation_lock.locked())
        finally:
            await entity.async_turn_off()

    async def test_dependent_source_identity_refuses_before_effects(self):
        from attr import asdict
        runtime, mapping, _, dependents, original = await self.data()
        hass, root = self.f.hass, self.f.root
        script_id = next(row[2] for row in dependents if row[0] == 'script')
        mapping[script_id] = 'script.mixed_source_renamed'
        before = {key: asdict(row) for key, row in self.f.entity.entities.items()}
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        with self.assertRaisesRegex(self.module('transaction').Refused,
                                    'dependent_source_identity_unsupported'):
            await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
        self.assertEqual({key: asdict(row) for key, row in self.f.entity.entities.items()}, before)
        for path, raw in original.items():
            self.assertEqual((root / path).read_bytes(), raw)
        self.assertFalse(await runtime.journal.active())
        self.assertFalse(runtime.operation_lock.locked())

    async def test_one_mapping_actual_dependents_forward_and_fresh_reverse(self):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_platform import async_get_platforms
        runtime, mapping, uuids, dependents, original = await self.data()
        hass, root = self.f.hass, self.f.root
        events = {'automation': [], 'script': []}
        for domain in events:
            def observed(event, domain=domain):
                events[domain].append(str(event.data['value']))
            remove = hass.bus.async_listen('mixed_yaml_' + domain + '_result', observed)
            self.addCleanup(remove)

        def assert_identities():
            self.assertEqual(tuple(sorted((row.platform, row.unique_id, row.entity_id, row.id)
                for row in self.f.entity.entities.values()
                if row.platform in ('automation', 'script'))), dependents)
            self.assertEqual({(p.domain, e.unique_id):
                (e.entity_id, self.f.entity.async_get(e.entity_id).id)
                for p in async_get_platforms(hass, 'template') for e in p.entities.values()},
                self.identities)

        async def stimulus(value):
            events['automation'].clear()
            events['script'].clear()
            await self.stimulus(value)
            await hass.services.async_call('script', 'mixed_source', {}, blocking=True,
                                           context=Context())
            await hass.async_block_till_done()
            self.assertEqual(events['automation'], [str(value)])
            self.assertEqual(events['script'], [str(value)])
            assert_identities()

        for value in (5, 7):
            await stimulus(value)
        history = self.module('history_guard').NativeHistoryGuard(
            self.module('recorder_guard').NativeRecorderGuard(hass))
        before_sources = await history.capture(mapping)
        consumer_ids = {entity_id: entity_id for entity_id, _ in self.identities.values()}
        consumer_ids.update({entity_id: entity_id for _, _, entity_id, _ in dependents})
        before_consumers = await history.capture(consumer_ids)
        self.assertTrue(before_consumers['state_ids'], 'real consumer Recorder rows required')

        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())

        events['automation'].clear()
        events['script'].clear()
        # The ONLY caller input is the ordinary source mapping. In particular,
        # do not supply a fixture Plan, receipt, callback or alternate baseline.
        with native_diagnostics(self.module):
            result = await asyncio.wait_for(hass.async_create_task(
                runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh),
                'actual mixed YAML forward', eager_start=False), 180)
        self.assertEqual(result['state'], 'applied')
        self.assertEqual(events['automation'], [], 'ID rename must not fire actions without a real source-value change')
        self.assertEqual(events['script'], [], 'migration must not invoke the user script')
        record = await runtime.journal.load(result['operation_id'])
        self.assertEqual(record.phases, ('verified',) * len(record.plan.steps))
        self.assertEqual(tuple(row.adapter for row in record.plan.steps),
                         ('registry',) * len(mapping) + ('yaml',))
        baseline, composition = runtime.preparations.resolve_composition(record.plan.native_inputs)
        reference, original_json = record.plan.native_inputs, baseline.receipt_json
        self.assertEqual(baseline.receipt.changed_domains, ('automation', 'script', 'template'))
        self.assertEqual({row[0] for row in baseline.receipt.runtime_expected},
                         {'automation', 'script', 'template'})
        self.assertEqual({row[0] for row in baseline.receipt.proposed_files},
                         {'template.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml'})
        self.assertEqual(set(dict(composition.prepared_digests)),
                         {row.prepared_ref for row in record.plan.steps})
        # Proposed typed mixed profile's consumer_history covers the complete
        # Template AND automation/script population, not a Template-only prefix.
        self.assertTrue(set(before_consumers['state_ids']) <=
                        set(composition.consumer_history['state_ids']))
        self.assertTrue(set(before_consumers['statistic_ids']) <=
                        set(composition.consumer_history['statistic_ids']))
        self.assertTrue(await history.verify(composition.source_history))
        self.assertTrue(await history.verify(composition.consumer_history))
        for old, new in mapping.items():
            self.assertIsNone(self.f.entity.async_get(old))
            self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
            for path in ('template.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml'):
                self.assertNotIn(old.encode(), (root / path).read_bytes())
                self.assertIn(new.encode(), (root / path).read_bytes())
        for value in (11, 13):
            await stimulus(value)
        events['automation'].clear()
        events['script'].clear()
        prior_stores = runtime.preparations, runtime.journal
        # Fresh native factory receives only opid/fingerprint; none of the live
        # forward session's objects or effects are handed into reverse.
        reverse = await asyncio.wait_for(hass.async_create_task(
            runtime.native_yaml_reverse(hass, result['operation_id'], result['fingerprint'],
                guard=lambda: None, refresh=refresh),
            'actual fresh mixed YAML reverse', eager_start=False), 180)
        self.assertEqual(reverse['state'], 'rolled_back')
        self.assertEqual(events['automation'], [], 'reverse ID rename must not fire actions without a real source-value change')
        self.assertEqual(events['script'], [], 'reverse must not invoke the user script')
        self.assertIsNot(runtime.preparations, prior_stores[0])
        self.assertIsNot(runtime.journal, prior_stores[1])
        final = await runtime.journal.load(result['operation_id'])
        self.assertEqual(final.plan, record.plan)
        self.assertEqual(final.plan.native_inputs, reference)
        self.assertEqual(final.phases, ('undone',) * len(final.plan.steps))
        reopened, restored = runtime.preparations.resolve_composition(reference)
        self.assertEqual(reopened.receipt_json, original_json)
        self.assertEqual(restored, composition)
        for path, raw in original.items():
            self.assertEqual((root / path).read_bytes(), raw)
        for old, new in mapping.items():
            self.assertIsNone(self.f.entity.async_get(new))
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        for value in (17, 19):
            await stimulus(value)
        for snapshot in (before_sources, before_consumers,
                         composition.source_history, composition.consumer_history):
            self.assertTrue(await history.verify(snapshot))
        self.assertFalse(runtime.operation_lock.locked())
        for status in (result, reverse):
            self.assertIs(status['migration_ready'], False)
            self.assertIs(status['family_complete'], False)
