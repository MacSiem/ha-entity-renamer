"""Server-owned numeric-entry forward on the existing actual native fixture.

Owner runs RED with tests.patch alone, then GREEN with product.patch via FIFO.
No test-built Plan, ORIGINAL, receipt, prepared reference or adapter is passed.
"""
import importlib
import json
import sys
import unittest
from types import MappingProxyType

import test_native_template_configentry_activation as activation

fixtures = activation.fixtures


@unittest.skipUnless(fixtures.HAS_HA, 'actual pinned HA required')
class NativeTemplateForwardTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = activation.NativeTemplateConfigEntryActivationTests.asyncSetUp
    _before_start = activation.NativeTemplateConfigEntryActivationTests._before_start
    _flush_documents = activation.NativeTemplateConfigEntryActivationTests._flush_documents
    _same_native_documents = activation.NativeTemplateConfigEntryActivationTests._same_native_documents
    emit = activation.NativeTemplateConfigEntryActivationTests.emit
    with_yaml = False

    async def _fixture(self):
        entry_unique_id = None
        from homeassistant.components.sensor import SensorEntity
        from homeassistant.components.template.config_flow import TemplateConfigFlowHandler
        from homeassistant.config_entries import ConfigEntry, ConfigEntryState
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.setup import async_setup_component

        def module(name):
            return importlib.import_module(fixtures.PACKAGE + '.' + name)

        hass, root = self.f.hass, self.f.root
        self.assertTrue(await async_setup_component(hass, 'sensor', {'sensor': []}))

        class Producer(SensorEntity):
            _attr_unique_id = 'template-entry-activation-producer'
            _attr_name = 'Template entry activation producer'
            _attr_should_poll = False
            _attr_native_value = 1

        prepare_cold_source = getattr(self, '_prepare_cold_source', None)
        if prepare_cold_source is None:
            self.producer = Producer()
            await hass.data[DATA_INSTANCES]['sensor'].async_add_entities([self.producer])
        else:
            # TEST ONLY: source must reconstruct from its native persisted entry.
            self.producer = await prepare_cold_source(hass)
        self.source_id = self.producer.entity_id
        target = 'sensor.template_entry_activation_next'
        mapping = {self.source_id: target}
        self.template_variant, self.consumer_domain = 'sensor', 'sensor'
        expression = "{{ states('" + self.source_id + "') | int(0) * 2 }}"
        entry = ConfigEntry(domain='template', title='Entry activation consumer', data={},
            options={'template_type': 'sensor', 'name': 'Entry activation consumer',
                     'state': expression},
            version=TemplateConfigFlowHandler.VERSION,
            minor_version=TemplateConfigFlowHandler.MINOR_VERSION,
            source='user', discovery_keys=MappingProxyType({}), subentries_data=[],
            unique_id=entry_unique_id, pref_disable_polling=True)
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
        self.assertIs(entry.state, ConfigEntryState.LOADED)
        self.consumer_key = entry.entry_id
        self.consumer = next(entity for platform in async_get_platforms(hass, 'template')
            if platform.domain == 'sensor' and platform.config_entry is entry
            for entity in platform.entities.values() if entity.unique_id == entry.entry_id)
        row = self.f.entity.async_get(self.consumer.entity_id)
        row = self.f.entity.async_update_entity(row.entity_id,
            hidden_by=er.RegistryEntryHider.USER, name='Retained entry preference')
        self.consumer_uuid = row.id
        consumer_identity = (row.entity_id, row.id, row.unique_id, row.config_entry_id,
                             row.hidden_by, row.name)
        source_uuid = self.f.entity.async_get(self.source_id).id
        original = module('helper_native').read_entry(hass, entry.entry_id)
        self.assertTrue(original['identity']['pref_disable_polling'])
        for value in (5, 7):
            await self.emit(value)  # Actual producer writes, genuine consumer 10/14.
        before_documents = await self._flush_documents(initialize=True)

        return module, entry, mapping, consumer_identity, original, before_documents

    async def _runtime(self, module):
        # Execute the installed backend in the fixture's ordinary package,
        # exactly as the existing fresh-reverse scenario does.
        import importlib.util
        from pathlib import Path
        backend = importlib.import_module(module('const').__package__)
        if not hasattr(backend, 'Runtime'):
            source = Path(module('const').__file__).with_name('__init__.py')
            spec = importlib.util.spec_from_file_location(backend.__name__, source,
                        submodule_search_locations=list(backend.__path__))
            backend.__spec__, backend.__loader__ = spec, spec.loader
            backend.__package__, backend.__file__ = backend.__name__, str(source)
            spec.loader.exec_module(backend)
        preparations = module('prepared').native_prepared_store(self.f.hass)
        journal = module('journal').native_journal(self.f.hass)
        await preparations.initialize()
        await journal.initialize()
        runtime = backend.Runtime('server-forward-fixture', journal, preparations)
        self.f.hass.data[backend.DATA_RUNTIME] = runtime
        return runtime

    async def test_server_forward_then_fresh_strict_reverse_same_original(self):
        module, entry, mapping, identity, original, _ = await self._fixture()
        runtime = await self._runtime(module)
        factory = module('native_template_forward').NativeTemplateForward
        async def refresh():
            return None
        async with runtime.operation_lock:
            session = await factory.prepare(self.f.hass, mapping, guard=lambda: None, refresh=refresh)
            try:
                self.assertEqual(session.lease.entry_ids, (entry.entry_id,))
                baseline, composition = session.preparations.resolve_composition(session.plan.native_inputs)
                operation, fingerprint = session.operation_id, session.fingerprint
                digest, original_json = baseline.digest, baseline.receipt_json
                record = await self._apply_with_durable_unload_observation(session)
                self.assertEqual(record.status, 'applied')
                self.assertEqual(record.plan.native_inputs[2], digest)
                for value in (5, 7):
                    await self.emit(value)
                self.assertEqual(self.producer.entity_id, next(iter(mapping.values())))
                self.assertTrue(await session.history.verify(composition.source_history))
                self.assertTrue(await session.history.verify(composition.consumer_history))
                self.assertEqual(module('helper_native').read_entry(self.f.hass, entry.entry_id)['identity'],
                                 original['identity'])
                current = self.f.entity.async_get(identity[0])
                self.assertEqual((current.entity_id, current.id, current.unique_id,
                    current.config_entry_id, current.hidden_by, current.name), identity)
                with self.assertRaises(module('transaction').Refused):
                    await session.apply()
            finally:
                session.close()
        del baseline, composition, session, record
        from native_template_composition_scenario import fresh_reverse_scenario
        await fresh_reverse_scenario(self, module, operation, fingerprint, digest,
                                    original_json, identity, original, mapping, None)

    async def test_forward_is_durable_before_actual_native_unload(self):
        self.require_durable_before_unload = True
        await self.test_server_forward_then_fresh_strict_reverse_same_original()

    async def _apply_with_durable_unload_observation(self, session):
        if not getattr(self, 'require_durable_before_unload', False):
            return await session.apply()
        module = importlib.import_module(fixtures.PACKAGE + '.native_helper_activation_lease')
        from homeassistant.config_entries import ConfigEntryState
        code = module.NativeHelperActivationLease.async_unload.__code__
        seen = []
        tool = None
        def observe(current_code, offset):
            frame = sys._getframe()
            while frame is not None and frame.f_code is not current_code:
                frame = frame.f_back
            if frame is None or frame.f_locals.get('self') is not session.lease:
                return
            self.assertEqual(frame.f_locals.get('entry_id'), session.entry_id)
            self.assertIs(session.entry.state, ConfigEntryState.LOADED)
            self.assertTrue(session.runtime.operation_lock.locked())
            path = self.f.root / '.storage' / 'ha_entity_renamer.migrations'
            self.assertTrue(path.is_file(), 'journal must be durable BEFORE actual native unload')
            # Independent physical read of the actual supported Store envelope.
            # No fabricated Record, expected receipt or writer seam is supplied.
            journal = importlib.import_module(fixtures.PACKAGE + '.journal')
            records = journal.decode(json.loads(path.read_bytes())['data'])
            record = records.get(session.operation_id)
            self.assertIsNotNone(record, 'native unload has no durable operation')
            self.assertEqual(record.plan.fingerprint, session.fingerprint)
            self.assertEqual(record.plan.native_inputs, session.plan.native_inputs)
            self.assertEqual((record.status, record.direction, record.phases),
                             ('prepared', 'forward', ('pending', 'pending')))
            seen.append(record.operation_id)
        for ident in range(6):
            try:
                sys.monitoring.use_tool_id(ident, 'server forward durable before native unload')
            except ValueError:
                continue
            tool = ident
            break
        self.assertIsNotNone(tool)
        try:
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, observe)
            sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START)
            result = await session.apply()
            self.assertEqual(seen, [session.operation_id], 'actual native unload was not observed once')
            return result
        finally:
            sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.free_tool_id(tool)

    async def test_malformed_mapping_refuses_without_native_effects(self):
        module, entry, mapping, _, original, before = await self._fixture()
        runtime = await self._runtime(module)
        factory = module('native_template_forward').NativeTemplateForward
        async def refresh():
            return None
        async with runtime.operation_lock:
            for malformed in ({}, {'sensor.a': 'switch.b'}, {next(iter(mapping)): 'bad'},
                              {'sensor.a': 'sensor.b', 'sensor.c': 'sensor.d'}):
                with self.assertRaises((ValueError, module('transaction').Refused)):
                    await factory.prepare(self.f.hass, malformed, guard=lambda: None, refresh=refresh)
        self._same_native_documents(before, await self._flush_documents())
        self.assertEqual(module('helper_native').read_entry(self.f.hass, entry.entry_id), original)
        self.assertEqual(entry.state.value, 'loaded')

    async def test_revoked_authorization_before_apply_has_zero_native_writes(self):
        await self._negative('auth')

    async def test_foreign_source_scope_before_apply_has_zero_native_writes(self):
        await self._negative('foreign')

    async def _negative(self, fault):
        module, entry, mapping, _, original, _ = await self._fixture()
        runtime = await self._runtime(module)
        factory = module('native_template_forward').NativeTemplateForward
        revoked = False
        def guard():
            if revoked:
                raise PermissionError('fixture_admin_revoked')
        async def refresh():
            guard()
        async with runtime.operation_lock:
            session = await factory.prepare(self.f.hass, mapping, guard=guard, refresh=refresh)
            try:
                if fault == 'foreign':
                    self.f.entity.async_update_entity(self.producer.entity_id, name='foreign source preference')
                before = await self._flush_documents()
                if fault == 'auth':
                    revoked = True
                with self.assertRaises((ValueError, PermissionError, module('transaction').Refused)):
                    await session.apply()
                self._same_native_documents(before, await self._flush_documents())
                self.assertEqual(module('helper_native').read_entry(self.f.hass, entry.entry_id), original)
                self.assertEqual(entry.state.value, 'loaded')
                self.assertEqual(self.producer.entity_id, next(iter(mapping)))
            finally:
                session.close()
