"""Actual native consumers; owner runs tests-only RED before product GREEN.

Reuse the forward/activation fixture, native Lovelace managers/Store and actual
Energy singleton. No prepared inputs, State injection or writer interception.
"""
import importlib
import unittest

import test_native_template_forward as forward


@unittest.skipUnless(forward.fixtures.HAS_HA, 'actual pinned HA required')
class NativeTemplateForwardConsumerTests(forward.NativeTemplateForwardTests):
    empty_consumers = False

    async def _fixture(self):
        result = await super()._fixture()
        from homeassistant.const import __version__
        from homeassistant.components.lovelace import LovelaceData
        from homeassistant.components.lovelace.const import LOVELACE_DATA
        from homeassistant.components.lovelace.dashboard import LovelaceStorage
        from homeassistant.components.energy.data import async_get_manager
        from homeassistant.setup import async_setup_component
        from homeassistant.config import async_hass_config_yaml
        hass, root = self.f.hass, self.f.root
        # Concrete actual native boundary, as in test_native_discovery_consumers.
        self.dashboard = LovelaceStorage(hass, None)
        kwargs = {'resource_mode' if __version__ == '2026.10.2' else 'mode': 'storage'}
        hass.data[LOVELACE_DATA] = LovelaceData(**kwargs,
            dashboards={None: self.dashboard}, resources=None, yaml_dashboards={})
        self.energy = await async_get_manager(hass)
        if not self.empty_consumers:
            await self.dashboard.async_save({'views': [{'cards': [
                {'type': 'entity', 'entity': 'sensor.unrelated'}]}]})
            prefs = self.energy.default_preferences()
            prefs['energy_sources'] = [{'type': 'solar', 'stat_energy_from': 'sensor.unrelated'}]
            await self.energy.async_update(prefs)
            await self.energy._store.async_save(self.energy.data)
            (root / 'configuration.yaml').write_text(
                'automation:\n- id: consumer_scope_unrelated\n  alias: Unrelated consumer\n'
                '  initial_state: false\n  triggers:\n  - trigger: event\n'
                '    event_type: consumer_scope_unrelated\n  actions:\n'
                '  - variables: {retained: unrelated}\nscript:\n  unrelated:\n    sequence:\n'
                '    - variables:\n        retained: "unrelated"\n')
        else:
            # The untouched actual managers plus durable absence prove empty.
            (root / 'configuration.yaml').write_text('automation: []\nscript: {}\n')
        cfg = await async_hass_config_yaml(hass)
        for domain in ('automation', 'script'):
            self.assertTrue(await async_setup_component(hass, domain, cfg))
        await hass.async_block_till_done()
        module = result[0]
        observed = await module('native_discovery_consumers').async_observe_discovery_consumers(
            hass, guard=lambda: None)
        self.assertEqual((observed.dashboard.status, observed.energy.status), ('COMPLETE', 'COMPLETE'))
        # Qualify the actual settled native runtime before invoking the writer.
        # Keep the original projection error instead of its sanitized wrapper.
        for domain in ('automation', 'script'):
            module('yaml_runtime_observer').NativeYAMLRuntimeReadback(hass)._projection(domain)
        self.original_consumers = observed
        return (*result[:-1], await self._flush_documents())

    async def test_server_forward_then_fresh_strict_reverse_same_original(self):
        await super().test_server_forward_then_fresh_strict_reverse_same_original()
        module = importlib.import_module(
            forward.fixtures.PACKAGE + '.native_discovery_consumers')
        observed = await module.async_observe_discovery_consumers(self.f.hass, guard=lambda: None)
        self.assertEqual(observed, self.original_consumers)

    async def test_empty_native_consumers_forward_and_fresh_reverse(self):
        self.empty_consumers = True
        await self.test_server_forward_then_fresh_strict_reverse_same_original()

    async def _consumer_negative(self, family, *, late=False, marker=False):
        module, entry, mapping, _, original, _ = await self._fixture()
        runtime = await self._runtime(module)
        factory = module('native_template_forward').NativeTemplateForward
        async def refresh():
            return None
        async def change():
            if family == 'dashboard':
                config = {'views': [{'cards': [{'type': 'custom:unsupported'}] if marker else [
                    {'type': 'entity', 'entity': next(iter(mapping))}]}]}
                await self.dashboard.async_save(config)
            elif family == 'dashboard_yaml':
                from homeassistant.components.lovelace.const import LOVELACE_DATA
                from homeassistant.components.lovelace.dashboard import LovelaceYAML
                metadata = {'mode': 'yaml', 'filename': 'consumer-board.yaml', 'title': 'Read only YAML'}
                (self.f.root / 'consumer-board.yaml').write_text('views: []\n')
                data = self.f.hass.data[LOVELACE_DATA]
                data.yaml_dashboards['consumer-yaml'] = metadata
                data.dashboards['consumer-yaml'] = LovelaceYAML(self.f.hass, 'consumer-yaml', metadata)
            elif family == 'energy_unknown':
                await self.energy._store.async_save({'unsupported_schema': True})
            elif family == 'dashboard_disk':
                await self.dashboard._store.async_save({'config': {'views': []}})
            elif family == 'energy':
                prefs = self.energy.default_preferences()
                prefs['energy_sources'] = [{'type': 'solar', 'stat_energy_from': next(iter(mapping))}]
                await self.energy.async_update(prefs)
                await self.energy._store.async_save(self.energy.data)
            else:
                (self.f.root / 'configuration.yaml').write_text(
                    'automation: []\nscript:\n  unrelated:\n    sequence:\n'
                    '    - action: homeassistant.turn_on\n      target:\n'
                    '        entity_id: ' + next(iter(mapping)) + '\n')
                await self.f.hass.services.async_call('script', 'reload', blocking=True)
            await self.f.hass.async_block_till_done()
        async with runtime.operation_lock:
            session = None
            try:
                if late:
                    session = await factory.prepare(self.f.hass, mapping, guard=lambda: None, refresh=refresh)
                await change()
                before = await self._flush_documents()
                with self.assertRaises((ValueError, module('transaction').Refused)):
                    if late:
                        await session.apply()
                    else:
                        await factory.prepare(self.f.hass, mapping, guard=lambda: None, refresh=refresh)
                self._same_native_documents(before, await self._flush_documents())
                self.assertEqual(module('helper_native').read_entry(self.f.hass, entry.entry_id), original)
                self.assertEqual(entry.state.value, 'loaded')
                self.assertEqual(self.producer.entity_id, next(iter(mapping)))
            finally:
                if session is not None:
                    session.close()

    async def test_actual_dashboard_reference_refuses(self):
        await self._consumer_negative('dashboard')

    async def test_actual_energy_reference_refuses(self):
        await self._consumer_negative('energy')

    async def test_actual_script_reference_refuses(self):
        await self._consumer_negative('script')

    async def test_actual_dashboard_marker_refuses_without_literal_hit(self):
        await self._consumer_negative('dashboard', marker=True)

    async def test_unproved_native_yaml_dashboard_route_refuses(self):
        await self._consumer_negative('dashboard_yaml')

    async def test_unknown_actual_energy_schema_refuses(self):
        await self._consumer_negative('energy_unknown')

    async def test_late_dashboard_change_refuses_before_unload(self):
        await self._consumer_negative('dashboard', late=True)

    async def test_late_energy_change_refuses_before_unload(self):
        await self._consumer_negative('energy', late=True)

    async def test_late_dashboard_disk_drift_refuses_before_unload(self):
        await self._consumer_negative('dashboard_disk', late=True)

    async def test_late_script_reload_refuses_before_unload(self):
        await self._consumer_negative('script', late=True)
