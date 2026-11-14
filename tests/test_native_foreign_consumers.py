"""Core 11 native coexistence: configuration only, with fail-closed changes."""
from datetime import timedelta
import importlib
import logging
from pathlib import Path
import unittest

import test_native_helper_quiescence as fixture


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'requires actual Home Assistant')
class ForeignConsumerTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.NativeHelperQuiescenceTests.asyncSetUp
    _before_start = fixture.NativeHelperQuiescenceTests._before_start

    async def test_rest_runtime_scope_and_source_drift(self):
        from homeassistant.const import __version__
        if __version__ != '2026.11.1':
            self.skipTest('Core 11.1 only')
        from homeassistant.helpers.entity_platform import EntityPlatform
        from homeassistant.helpers.trigger_template_entity import ValueTemplate
        from homeassistant.components.rest.sensor import RestSensor
        from homeassistant.components.rest.data import RestData
        hass = self.f.hass
        module = importlib.import_module(fixture.fixtures.PACKAGE + '.native_foreign_consumers')
        tx = importlib.import_module(fixture.fixtures.PACKAGE + '.transaction')
        planner = importlib.import_module(fixture.fixtures.PACKAGE + '.yaml_plan')
        mapping = {'sensor.old': 'sensor.new'}
        source = "{{ is_state('sensor.other', 'on') }}"
        (self.f.root / 'configuration.yaml').write_text(
            'rest:\n  - sensor:\n      - value_template: "' + source + '"\n')
        entry = self.f.entity.async_get_or_create('sensor', 'rest', 'fixture-unique')
        await hass.async_block_till_done()
        platform = EntityPlatform(hass=hass, logger=logging.getLogger(__name__),
            domain='sensor', platform_name='rest', platform=None,
            scan_interval=timedelta(seconds=30), entity_namespace=None)
        platform.async_prepare()
        platform._setup_complete = True
        entity = object.__new__(RestSensor)
        entity.hass = hass
        entity.entity_id = entry.entity_id
        entity._config = {}
        entity._value_template = ValueTemplate(source, hass)
        entity._resource_template = None
        entity.rest = RestData(hass, 'GET', 'http://example.invalid', 'utf-8', None,
                               None, None, None, True, 'python_default', 10)
        platform.entities[entry.entity_id] = entity
        hass.config.components.add('rest')
        try:
            fence = module.NativeForeignConsumers(hass, mapping, {})
            try:
                plan = planner.plan_yaml(self.f.root, mapping)
                fence.bind_sources(plan, self.f.root)
                fence.check(require_sources=True)
                # Telemetry is deliberately outside the configuration image.
                entity.rest.data = 'new reading'
                fence.check(require_sources=True)
                entity._value_template = ValueTemplate("{{ states('sensor.old') }}", hass)
                with self.assertRaises(tx.Refused):
                    fence.check()
                entity._value_template = ValueTemplate("{{ states(variable) }}", hass)
                with self.assertRaises(tx.Refused):
                    fence.check()
                entity._value_template = ValueTemplate("{{ 1 }}", hass)
                with self.assertRaises(tx.Refused):
                    fence.check()
            finally:
                fence.close()
            entity._value_template = ValueTemplate("{{ 1 }}", hass)
            fence = module.NativeForeignConsumers(hass, mapping, {})
            try:
                with self.assertRaises(tx.Refused):
                    fence.bind_sources(plan, self.f.root)
            finally:
                fence.close()
        finally:
            platform.entities.clear()
            hass.config.components.remove('rest')
            await platform.async_destroy()

    async def test_dynamic_and_unknown_template_calls_refuse(self):
        module = importlib.import_module(fixture.fixtures.PACKAGE + '.native_foreign_consumers')
        tx = importlib.import_module(fixture.fixtures.PACKAGE + '.transaction')
        mapping = {'sensor.old': 'sensor.new'}
        module._template("{{ is_state('sensor.other', 'on') }}", mapping)
        for value in ("{{ states('sensor.old') }}", "{{ states(variable) }}",
                      "{{ custom_lookup('sensor.other') }}", "{{ states('sensor.new') }}"):
            with self.subTest(value=value), self.assertRaises(tx.Refused):
                module._template(value, mapping)
