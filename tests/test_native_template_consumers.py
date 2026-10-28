"""Actual native consumers and two ordinary producer events; owner runs FIFO.

No product apply/recovery/own-ID acceptance is claimed. The native setup and
native negative detach are real; no helper output/field/cache seeds or callbacks.
"""
import asyncio
from datetime import timedelta
import importlib
import logging
import threading
from types import MappingProxyType
import unittest
from unittest.mock import patch
import test_native_helper_quiescence as fixture


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeTemplateConsumerTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.NativeHelperQuiescenceTests.asyncSetUp
    _before_start = fixture.NativeHelperQuiescenceTests._before_start
    _flush_documents = fixture.NativeHelperQuiescenceTests._flush_documents

    async def prepare(self, kind, *, foreign_runtime=False, sibling=False, variant='sensor',
                      binary_extra=None, binary_device_class=None, validate=True):
        self.assertIn(variant, ('sensor', 'binary_sensor', 'mixed'))
        self.template_variant = variant
        self.consumer_domain = 'binary_sensor' if variant == 'binary_sensor' else 'sensor'
        self.binary_match = 7 if foreign_runtime and variant == 'binary_sensor' else 5
        self.assertIn(binary_extra, (None, 'delay_on'))
        if binary_extra is not None or binary_device_class is not None:
            self.assertEqual(variant, 'binary_sensor')
        if variant in ('binary_sensor', 'mixed'):
            self.assertEqual(kind, 'yaml')
            self.assertFalse(sibling)
        else:
            self.assertIsNone(binary_extra)
        from homeassistant.components.sensor import SensorEntity
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.config_entries import ConfigEntry
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        from homeassistant.setup import async_setup_component
        hass, root = self.f.hass, self.f.root
        self.assertTrue(await async_setup_component(hass, 'sensor', {'sensor': []}))
        class Producer(SensorEntity):
            _attr_unique_id = 'template-runtime-producer'
            _attr_name = 'Template runtime producer'
            _attr_should_poll = False
            _attr_native_value = 1
        self.producer = Producer()
        await hass.data[DATA_INSTANCES]['sensor'].async_add_entities([self.producer])
        self.source_id = self.producer.entity_id
        state = "{{ states('" + self.source_id + "') | int(0) * 2 }}"
        if kind == 'yaml':
            if variant == 'binary_sensor':
                state = "{{ is_state('" + self.source_id + "', '5') }}"
            text = ('- unique_id: scope\n  ' + self.consumer_domain + ':\n  - unique_id: value\n'
                    '    name: Runtime consumer\n    state: "' + state + '"\n')
            if binary_extra:
                text += '    delay_on: "00:00:01"\n'
            if binary_device_class is not None:
                self.assertEqual(binary_device_class, 'power')
                text += '    device_class: power\n'
            if variant == 'mixed':
                # Same native key in different domains must never collapse.
                text += ('  binary_sensor:\n  - unique_id: value\n    name: Runtime binary\n'
                         '    state: "{{ is_state(\'' + self.source_id + '\', \'5\') }}"\n')
            if sibling:
                text += '  binary_sensor:\n  - unique_id: flag\n    name: Runtime flag\n    state: "{{ states(\'' + self.source_id + '\') | int(0) > 0 }}"\n'
            (root / 'template.yaml').write_text(text)
            (root / 'configuration.yaml').write_text('template: !include template.yaml\n')
            cfg = await async_hass_config_yaml(hass)
            if foreign_runtime:
                cfg['template'][0][self.consumer_domain][0]['state'] = (
                    state.replace("'5'", "'7'") if variant == 'binary_sensor'
                    else state.replace('* 2', '* 3'))
            self.assertTrue(await async_setup_component(hass, 'template', cfg))
            self.consumer_key, self.entry_id = 'scope-value', None
        else:
            from homeassistant.components.template.config_flow import TemplateConfigFlowHandler
            # A real changed YAML source gives the existing checker an operation;
            # ConfigEntry itself remains ORIGINAL full-three snapshot evidence.
            (root / 'configuration.yaml').write_text('script:\n  source_probe:\n    sequence:\n    - variables:\n        sample: "' + state + '"\n')
            entry = ConfigEntry(domain='template', title='Runtime consumer', data={},
                options={'template_type': 'sensor', 'name': 'Runtime consumer', 'state': state},
                version=TemplateConfigFlowHandler.VERSION,
                minor_version=TemplateConfigFlowHandler.MINOR_VERSION,
                source='user', discovery_keys=MappingProxyType({}), subentries_data=[],
                unique_id='template-runtime-entry')
            await hass.config_entries.async_add(entry)
            self.entry_id = self.consumer_key = entry.entry_id
        await hass.async_block_till_done()
        from homeassistant.helpers.entity_platform import async_get_platforms
        self.consumer = next(e for p in async_get_platforms(hass, 'template') if p.domain == self.consumer_domain
            for e in p.entities.values() if e.unique_id == self.consumer_key)
        self.consumer_uuid = self.f.entity.async_get(self.consumer.entity_id).id
        self.consumer_identities = {(p.domain, e.unique_id): (e.entity_id, self.f.entity.async_get(e.entity_id).id)
            for p in async_get_platforms(hass, 'template') for e in p.entities.values()}
        await self._flush_documents(initialize=True)
        if not validate:
            # Data-only setup for the server producer: no fixture Plan/receipt.
            return
        planner = importlib.import_module(fixture.fixtures.PACKAGE + '.yaml_plan')
        validation = importlib.import_module(fixture.fixtures.PACKAGE + '.yaml_validation')
        self.plan = planner.plan_yaml(root, {self.source_id: 'sensor.accepted_next'})
        self.assertTrue(self.plan.ready, self.plan.issues)
        self.receipt, self.evidence = await validation.async_validate_proposed_with_sources(
            hass, self.plan, root.parent)

    async def observe(self):
        try:
            module = importlib.import_module(fixture.fixtures.PACKAGE + '.native_template_consumers')
        except ModuleNotFoundError as error:
            if error.name != fixture.fixtures.PACKAGE + '.native_template_consumers':
                raise
            self.fail('actual native Template consumer observer is not implemented')
        return await module.async_observe_template_consumers(
            self.f.hass, self.receipt, self.evidence, guard=lambda: None)

    def consumer_row(self, observed):
        return next(r for r in observed.rows if r.kind == 'entity'
                    and r.entity_id == self.consumer.entity_id)

    async def emit(self, value, multiplier=2):
        self.producer._attr_native_value = value
        self.producer.async_write_ha_state()
        await self.f.hass.async_block_till_done()
        expected = (('on' if value == self.binary_match else 'off') if self.template_variant == 'binary_sensor'
                    else str(value * multiplier))
        self.assertEqual(self.f.hass.states.get(self.consumer.entity_id).state, expected)
        if self.template_variant == 'mixed':
            from homeassistant.helpers.entity_platform import async_get_platforms
            binary = next(e for p in async_get_platforms(self.f.hass, 'template') if p.domain == 'binary_sensor'
                          for e in p.entities.values() if e.unique_id == self.consumer_key)
            self.assertEqual(self.f.hass.states.get(binary.entity_id).state, 'on' if value == 5 else 'off')
            self.assertEqual((binary.entity_id, self.f.entity.async_get(binary.entity_id).id),
                             self.consumer_identities['binary_sensor', self.consumer_key])

    async def test_binary_same_native_key_with_foreign_body_does_not_match_original(self):
        await self.prepare('yaml', variant='binary_sensor', foreign_runtime=True)
        for value in (5, 7, 5):
            await self.emit(value)
        row = self.consumer_row(await self.observe())
        self.assertTrue(row.consumer_verified, row.reason)
        self.assertFalse(row.source_verified)
        self.assertEqual(row.reason, 'source_semantics_mismatch')

    async def test_binary_static_device_class_keeps_source_and_native_boolean_proof(self):
        await self.prepare('yaml', variant='binary_sensor', binary_device_class='power')
        self.assertTrue(self.receipt.ready_for_apply, self.receipt.unsupported_domains)
        for value in (5, 7, 5):
            await self.emit(value)
        row = self.consumer_row(await self.observe())
        self.assertTrue(row.consumer_verified, row.reason)
        self.assertTrue(row.source_verified, row.reason)
        self.assertEqual(row.registry_id, self.consumer_uuid)
        self.assertEqual(row.dependencies, (self.source_id,))
        self.assertEqual(self.consumer._attr_device_class, 'power')

    async def test_binary_delay_is_inventoried_but_refuses_activation_and_consumer_proof(self):
        await self.prepare('yaml', variant='binary_sensor', binary_extra='delay_on')
        self.assertFalse(self.receipt.ready_for_apply)
        self.assertIn('template', self.receipt.unsupported_domains)
        self.assertTrue(self.evidence.template_sources.source_matched)
        self.assertEqual({(row.domain, row.native_key) for row in self.evidence.template_sources.ledger.definitions},
                         {('template.state', 'id:scope'), ('template.binary_sensor', 'scope-value')})
        observed = await self.observe()
        row = self.consumer_row(observed)
        self.assertFalse(row.consumer_verified)
        self.assertFalse(row.source_verified)
        self.assertTrue(any(row.kind == 'source' and row.platform == 'template.binary_sensor' for row in observed.rows))
        self.assertFalse(observed.family_complete)

    async def test_binary_foreign_boolean_conversion_refuses_without_executing_it(self):
        from homeassistant.components.template import binary_sensor
        await self.prepare('yaml', variant='binary_sensor')
        invoked = []
        def foreign_conversion(value):
            invoked.append(value)
            raise AssertionError('foreign conversion must not execute')
        with patch.object(binary_sensor.template, 'result_as_boolean', foreign_conversion):
            with self.assertRaises(ValueError):
                await self.observe()
        self.assertEqual(invoked, [])

    async def test_binary_dynamic_reference_and_expression_wrappers_remain_unqualified(self):
        worker = importlib.import_module(fixture.fixtures.PACKAGE + '.yaml_validation_worker')
        self.assertEqual(worker._qualified_binary_expression("{{ is_state('sensor.original', '5') }}"),
                         ('sensor.original',))
        for text in ("{{ is_state(source, '5') }}", "{{ is_state('sensor.original', wanted) }}",
                     "{{ states('sensor.original') | int(0) > 0 }}",
                     "{{ is_state('sensor.original', '5') or true }}",
                     "prefix {{ is_state('sensor.original', '5') }}",
                     "{{ is_state('sensor.original', '5', extra=true) }}"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                worker._qualified_binary_expression(text)

    async def check_two_events(self):
        for value in (5, 7):
            await self.emit(value)
            observed = await self.observe()
            row = self.consumer_row(observed)
            self.assertTrue(row.consumer_verified, row.reason)
            self.assertTrue(row.source_verified, row.reason)
            self.assertEqual(row.dependencies, (self.source_id,))
            self.assertEqual(row.registry_id, self.consumer_uuid)
            self.assertFalse(observed.migration_ready)
            self.assertFalse(observed.family_complete)

    async def test_config_entry_source_real_consumer_and_two_producer_events(self):
        await self.prepare('entry')
        await self.check_two_events()

    async def test_yaml_source_real_consumer_and_two_producer_events(self):
        await self.prepare('yaml')
        await self.check_two_events()

    async def test_all_sibling_platforms_and_source_blocks_remain_visible_unknown(self):
        await self.prepare('yaml', sibling=True)
        observed = await self.observe()
        self.assertTrue(self.consumer_row(observed).source_verified)
        foreign = [r for r in observed.rows if r.kind == 'entity'
                   and r.platform == 'binary_sensor']
        self.assertEqual(len(foreign), 1)
        self.assertFalse(foreign[0].consumer_verified)
        self.assertTrue(any(r.kind == 'source' and r.platform == 'template.state'
                            for r in observed.rows))
        self.assertFalse(observed.family_complete)

    async def test_same_source_key_cannot_bless_foreign_native_template_body(self):
        await self.prepare('yaml', foreign_runtime=True)
        await self.emit(5, multiplier=3)
        observed = await self.observe()
        row = self.consumer_row(observed)
        self.assertTrue(row.consumer_verified, row.reason)
        self.assertFalse(row.source_verified)
        self.assertEqual(row.reason, 'source_semantics_mismatch')

    async def test_real_detach_is_detected_even_when_listeners_projection_survives(self):
        await self.prepare('entry')
        info = self.consumer._template_result_info
        old = info.listeners
        info._track_state_changes.async_remove()  # actual native cleanup, fixture only
        self.assertEqual(info.listeners, old)
        row = self.consumer_row(await self.observe())
        self.assertFalse(row.consumer_verified)
        self.assertFalse(row.source_verified)
        self.assertEqual(row.reason, 'native_registration_missing')

    async def test_extra_native_keyed_registration_cannot_hide_behind_original_detach(self):
        from homeassistant.helpers import event
        await self.prepare('entry')
        info = self.consumer._template_result_info
        remove = event.async_track_state_change_event(self.f.hass,
            'sensor.foreign', info._refresh)  # real native method, no fake callback
        try:
            row = self.consumer_row(await self.observe())
            self.assertFalse(row.consumer_verified)
            self.assertEqual(row.reason, 'native_registration_extra')
        finally:
            remove()

    async def test_late_source_edit_refuses_original_fence(self):
        await self.prepare('yaml')
        path = self.f.root / 'template.yaml'
        path.write_text(path.read_text() + '# foreign edit\n')
        with self.assertRaises(ValueError):
            await self.observe()

    async def test_physical_edit_during_final_code_source_job_refuses_original(self):
        await self.prepare('yaml')
        module = importlib.import_module(fixture.fixtures.PACKAGE + '.native_template_consumers')
        path = self.f.root / 'template.yaml'
        original = path.read_bytes()
        original_sources = module._Native.sources
        loop_thread = threading.get_ident()
        calls, edit_threads = [], []

        def sources_then_physical_edit(native):
            result = original_sources(native)  # genuine native code/source readback
            calls.append(native)
            if len(calls) == 2:
                # Runs inside the last code-source executor await. On the old
                # product this is AFTER the last ORIGINAL fence; with the fix
                # the combined job checks ORIGINAL immediately after this call.
                edit_threads.append(threading.get_ident())
                path.write_bytes(original + b'# edit during final code-source job\n')
            return result

        try:
            with patch.object(module._Native, 'sources', sources_then_physical_edit):
                task = asyncio.create_task(self.observe())
                with self.assertRaises(ValueError):
                    await task
        finally:
            path.write_bytes(original)
        self.assertEqual(len(calls), 2, 'injection must reach the final source job')
        self.assertIs(calls[0], calls[1])
        self.assertEqual(len(edit_threads), 1)
        self.assertNotEqual(edit_threads[0], loop_thread)
        self.assertIs(module._Native.sources, original_sources)
        self.assertEqual(path.read_bytes(), original)

    async def test_native_false_render_filter_cannot_keep_consumer_source_proof(self):
        await self.prepare('entry')
        info = self.consumer._template_result_info
        self.assertEqual(len(info._info), 1)
        template, render = next(iter(info._info.items()))
        native_false = importlib.import_module(type(render).__module__)._false
        original_filter, original_lifecycle = render.filter, render.filter_lifecycle
        self.assertIs(original_filter.__self__, render)
        self.assertIs(original_filter.__func__, type(render)._filter_entities)
        # Entity-only native lifecycle is ALREADY _false on both pinned Core.
        self.assertIs(original_lifecycle, native_false)
        filtered = info._track_state_changes
        remove = next(iter(filtered._listeners.values()))
        callbacks = remove.args[-1]
        memberships = tuple((key, tuple(map(id, jobs)))
                            for key, jobs in callbacks.items())
        cleanups = tuple(map(id, self.consumer._on_remove))
        try:
            render.filter = native_false  # negative fault injection ONLY
            self.assertIs(self.consumer._template_result_info, info)
            self.assertIs(info._info[template], render)
            self.assertIs(info._track_state_changes, filtered)
            self.assertIs(next(iter(filtered._listeners.values())), remove)
            self.assertEqual(tuple((key, tuple(map(id, jobs)))
                                   for key, jobs in callbacks.items()), memberships)
            self.assertEqual(tuple(map(id, self.consumer._on_remove)), cleanups)
            # Real producer event is discarded by native _event_triggers_rerender;
            # no callback invocation or consumer/cache/state seeding is involved.
            before = self.f.hass.states.get(self.consumer.entity_id).state
            self.producer._attr_native_value = 5
            self.producer.async_write_ha_state()
            await self.f.hass.async_block_till_done()
            self.assertEqual(self.f.hass.states.get(self.consumer.entity_id).state, before)
            self.assertEqual(self.f.hass.states.get(self.source_id).state, '5')
            try:
                observed = await self.observe()
            except ValueError:
                pass  # strict observer refusal is also valid
            else:
                row = self.consumer_row(observed)
                self.assertFalse(row.consumer_verified, row.reason)
                self.assertFalse(row.source_verified, row.reason)
        finally:
            render.filter = original_filter
            render.filter_lifecycle = original_lifecycle
        self.assertIs(render.filter, original_filter)
        self.assertIs(render.filter_lifecycle, original_lifecycle)
        await self.emit(7)  # genuine callback resumes and reads 14 after restore

    async def test_binary_late_native_boolean_import_alias_refuses_without_execution(self):
        import sys
        from types import SimpleNamespace
        await self.prepare('yaml', variant='binary_sensor')
        observer = importlib.import_module(fixture.fixtures.PACKAGE + '.native_template_consumers')
        # Establish real byte/method proof, then drift the actual alias which
        # the pinned converter would resolve. Do not invoke the converter.
        native = observer._Native(self.f.hass)
        invoked = []
        def foreign_conversion(value):
            invoked.append(value)
            raise AssertionError('foreign boolean alias must not execute')
        fake = SimpleNamespace(boolean=foreign_conversion)
        target, attribute = ((sys.modules['homeassistant.helpers'], 'config_validation')
                             if native.version == '2025.6.0' else (native.boolean_module, 'cv'))
        with patch.object(target, attribute, fake):
            with self.assertRaisesRegex(ValueError, 'native_binary_boolean_binding_changed'):
                observer._live_binary(native, self.consumer)
        self.assertEqual(invoked, [])
