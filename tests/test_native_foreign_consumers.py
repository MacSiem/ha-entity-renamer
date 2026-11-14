"""Core 11 native coexistence: configuration only, with fail-closed changes."""
from datetime import timedelta
import importlib
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest

import test_native_helper_quiescence as fixture


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'requires actual Home Assistant')
class ForeignConsumerTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.NativeHelperQuiescenceTests.asyncSetUp
    _before_start = fixture.NativeHelperQuiescenceTests._before_start

    async def test_absent_foreign_domains_keep_existing_admission(self):
        module = importlib.import_module(fixture.fixtures.PACKAGE + '.native_template_yaml_server')
        self.assertFalse({'mqtt', 'rest', 'command_line'} & set(self.f.hass.config.components))
        server = SimpleNamespace(hass=self.f.hass)
        await module._admit_foreign(server, {})
        self.assertFalse(hasattr(server, 'foreign'))

    async def test_rest_runtime_scope_and_source_drift(self):
        from homeassistant.const import __version__
        if __version__ != '2026.11.1':
            self.skipTest('Core 11.1 only')
        from homeassistant.helpers.entity_platform import EntityPlatform
        from homeassistant.helpers.trigger_template_entity import ValueTemplate
        from homeassistant.components.rest.sensor import RestSensor
        from homeassistant.components.rest.data import RestData
        from homeassistant.components.rest.coordinator import RestCoordinator
        from homeassistant.helpers.template import Template
        hass = self.f.hass
        module = importlib.import_module(fixture.fixtures.PACKAGE + '.native_foreign_consumers')
        tx = importlib.import_module(fixture.fixtures.PACKAGE + '.transaction')
        planner = importlib.import_module(fixture.fixtures.PACKAGE + '.yaml_plan')
        mapping = {'sensor.old': 'sensor.new'}
        source = "{{ is_state('sensor.other', 'on') }}"
        resource = "{{ states('sensor.other') }}"
        (self.f.root / 'configuration.yaml').write_text(
            'rest:\n  - resource_template: "' + resource + '"\n    sensor:\n      - value_template: "' + source + '"\n')
        entry = self.f.entity.async_get_or_create('sensor', 'rest', 'fixture-unique')
        own = self.f.entity.async_get_or_create('sensor', 'fixture', 'selected-source',
                                                suggested_object_id='old')
        self.assertEqual(own.entity_id, 'sensor.old')
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
        entity._coordinator = None
        entity.rest = RestData(hass, 'GET', 'http://example.invalid', 'utf-8', None,
                               None, None, None, True, 'python_default', 10)
        entity.platform, entity.registry_entry, entity._attr_unique_id = platform, entry, entry.unique_id
        entity._unique_id = entry.unique_id
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
                self.f.entity.async_update_entity(own.entity_id, name='Selected renamed')
                hass.bus.async_fire('device_registry_updated', {'action': 'update', 'device_id': 'selected-device'})
                await hass.async_block_till_done()
                fence.check(require_sources=True)
                yaml_path = self.f.root / 'configuration.yaml'
                original = yaml_path.read_text()
                yaml_path.write_text(original + '\nautomation: []\n')
                fence.check(require_sources=True)  # unrelated same-file change
                yaml_path.write_text(original.replace("sensor.other", "sensor.changed"))
                with self.assertRaises(tx.Refused):
                    fence.check()
                yaml_path.write_text(original)
                with self.assertRaises(tx.Refused):  # observed drift stays latched
                    fence.check()
            finally:
                fence.close()
            fence = module.NativeForeignConsumers(hass, mapping, {})
            try:
                fence.mapping['sensor.old'] = 'sensor.changed'
                with self.assertRaises(tx.Refused):
                    fence.check()
            finally:
                fence.close()
            for changed in ("{{ states('sensor.old') }}", "{{ states(variable) }}", "{{ 1 }}"):
                fence = module.NativeForeignConsumers(hass, mapping, {})
                try:
                    entity._value_template = ValueTemplate(changed, hass)
                    with self.assertRaises(tx.Refused):
                        fence.check()
                finally:
                    entity._value_template = ValueTemplate(source, hass)
                    fence.close()
            entity._value_template = ValueTemplate(source, hass)
            entity._coordinator = RestCoordinator(hass, entity.rest, None,
                Template(resource, hass), None, timedelta(seconds=60))
            fence = module.NativeForeignConsumers(hass, mapping, {})
            try:
                fence.bind_sources(plan, self.f.root)
                fence.check(require_sources=True)
                # URL is the last rendered telemetry, not the resource template.
                entity.rest._resource = 'http://changed.invalid'
                fence.check(require_sources=True)
            finally:
                fence.close()
            entity._coordinator = None
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

    async def test_semantic_cache_is_selection_bound_and_includes_refuse(self):
        from unittest.mock import patch
        module = importlib.import_module(fixture.fixtures.PACKAGE + '.native_foreign_consumers')
        tx = importlib.import_module(fixture.fixtures.PACKAGE + '.transaction')
        text = "{{ states('sensor.other') }}"
        mapping = {'sensor.old': 'sensor.new'}
        cache = set()
        with patch.object(module, '_template', wraps=module._template) as validate:
            module._freeze(text, mapping, set(), template_cache=cache)
            module._freeze(text, mapping, set(), template_cache=cache)
            self.assertEqual(validate.call_count, 1)
        with self.assertRaises(tx.Refused):
            module._freeze("{{ states('sensor.old') }}", mapping, set(), template_cache=cache)
        for raw in (b'rest: !include sensors.yaml\n',
                    b'command_line:\n  - sensor: !include sensors.yaml\n'):
            with self.assertRaises(tx.Refused):
                module.NativeForeignConsumers._yaml_shape(raw)

    async def test_dynamic_and_unknown_template_calls_refuse(self):
        module = importlib.import_module(fixture.fixtures.PACKAGE + '.native_foreign_consumers')
        tx = importlib.import_module(fixture.fixtures.PACKAGE + '.transaction')
        mapping = {'sensor.old': 'sensor.new'}
        module._template("{{ is_state('sensor.other', 'on') }}", mapping)
        for value in ("{{ states('sensor.old') }}", "{{ states(variable) }}",
                      "{{ custom_lookup('sensor.other') }}", "{{ states('sensor.new') }}"):
            with self.subTest(value=value), self.assertRaises(tx.Refused):
                module._template(value, mapping)

    async def test_native_mqtt_yaml_schema_scalar_freeze(self):
        from homeassistant.const import __version__
        if __version__ != '2026.11.1':
            self.skipTest('Core 11.1 only')
        from homeassistant.components.mqtt.sensor import PLATFORM_SCHEMA_MODERN
        from homeassistant.util.yaml import load_yaml
        from homeassistant.util.yaml.objects import NodeStrClass
        module = importlib.import_module(fixture.fixtures.PACKAGE + '.native_foreign_consumers')
        tx = importlib.import_module(fixture.fixtures.PACKAGE + '.transaction')
        path = self.f.root / 'mqtt-fixture.yaml'
        path.write_text('state_topic: fixture/topic\nname: Fixture sensor\n')
        raw = load_yaml(str(path))
        config = PLATFORM_SCHEMA_MODERN(raw)
        self.assertTrue(any(type(k) is NodeStrClass for k in config))
        self.assertTrue(any(type(v) is NodeStrClass for v in config.values()))
        mapping = {'sensor.old': 'sensor.new'}
        frozen = module._freeze(config, mapping, set())
        plain = {str(k): str(v) if type(v) is NodeStrClass else v for k, v in config.items()}
        self.assertEqual(frozen, module._freeze(plain, mapping, set()))
        self.assertTrue(all(type(k) is str for k, _ in frozen))
        for bad in ({object(): 'value'}, {'key': object()},
                    {NodeStrClass('sensor.old'): 'value'},
                    {'key': NodeStrClass("{{ states(variable) }}")}):
            with self.subTest(kind=type(next(iter(bad))).__name__), self.assertRaises(tx.Refused):
                module._freeze(bad, mapping, set())

    async def test_native_mqtt_discovery_latches_and_device_scope(self):
        from homeassistant.const import __version__
        if __version__ != '2026.11.1':
            self.skipTest('Core 11.1 only')
        from homeassistant.config_entries import ConfigEntry, ConfigEntryState
        from homeassistant.helpers.entity_platform import EntityPlatform
        from homeassistant.helpers.dispatcher import async_dispatcher_send
        from homeassistant.components.mqtt.models import DATA_MQTT, MqttData
        from homeassistant.components.mqtt.sensor import MqttSensor
        from homeassistant.components.mqtt.discovery import MQTT_DISCOVERY_NEW
        hass = self.f.hass
        module = importlib.import_module(fixture.fixtures.PACKAGE + '.native_foreign_consumers')
        tx = importlib.import_module(fixture.fixtures.PACKAGE + '.transaction')
        entry = ConfigEntry(domain='mqtt', title='fixture', data={}, options={},
            source='user', version=1, minor_version=1, unique_id='test-mqtt',
            discovery_keys={}, subentries_data=[], state=ConfigEntryState.LOADED)
        hass.config_entries._entries[entry.entry_id] = entry
        mqtt = MqttData(client=object(), config=[])
        hass.data[DATA_MQTT] = mqtt
        hass.config.components.add('mqtt')
        registry = self.f.entity.async_get_or_create('sensor', 'mqtt', 'fixture-mqtt',
                                                      config_entry=entry)
        await hass.async_block_till_done()
        platform = EntityPlatform(hass=hass, logger=logging.getLogger(__name__),
            domain='sensor', platform_name='mqtt', platform=None,
            scan_interval=timedelta(seconds=30), entity_namespace=None)
        platform.config_entry = entry
        platform.async_prepare()
        platform._setup_complete = True
        entity = object.__new__(MqttSensor)
        entity.hass, entity.entity_id = hass, registry.entity_id
        entity._config = {'value_template': "{{ value_json.temperature }}"}
        entity._discovery_data = None
        entity.platform, entity.registry_entry, entity._attr_unique_id = platform, registry, registry.unique_id
        platform.entities[registry.entity_id] = entity
        unregistered = object.__new__(MqttSensor)
        unregistered.hass, unregistered.platform = hass, platform
        unregistered.entity_id, unregistered._attr_unique_id = 'sensor.foreign_live', None
        unregistered._config, unregistered._discovery_data = {'state_topic': 'fixture/topic'}, None
        platform.entities[unregistered.entity_id] = unregistered
        try:
            for mapping in ({'sensor.foreign_live': 'sensor.next'},
                            {'sensor.old': 'sensor.foreign_live'}):
                with self.assertRaises(tx.Refused):
                    module.NativeForeignConsumers(hass, mapping, {})
            fence = module.NativeForeignConsumers(hass, {'sensor.old': 'sensor.new'}, {})
            try:
                fence.check()
                unregistered._config = {'state_topic': 'fixture/changed'}
                with self.assertRaises(tx.Refused):
                    fence.check()
            finally:
                unregistered._config = {'state_topic': 'fixture/topic'}
                fence.close()
            fence = module.NativeForeignConsumers(hass, {'sensor.old': 'sensor.new'}, {'unrelated-device': 'name'})
            try:
                fence.check()
                async_dispatcher_send(hass, MQTT_DISCOVERY_NEW.format('sensor', 'mqtt'), {})
                await hass.async_block_till_done()
                with self.assertRaises(tx.Refused):
                    fence.check()
            finally:
                fence.close()
            entity._config = {'value_template': "{{ states('sensor.old') }}"}
            with self.assertRaises(tx.Refused):
                module.NativeForeignConsumers(hass, {'sensor.old': 'sensor.new'}, {})
        finally:
            platform.entities.clear()
            await platform.async_destroy()
            hass.config.components.remove('mqtt')
            del hass.data[DATA_MQTT]
            del hass.config_entries._entries[entry.entry_id]

    async def test_recorder_script_requires_literal_unmodified_entity_sources(self):
        module = importlib.import_module(fixture.fixtures.PACKAGE + '.native_foreign_consumers')
        tx = importlib.import_module(fixture.fixtures.PACKAGE + '.transaction')
        mapping = {'sensor.old': 'sensor.new'}
        source = '''import sqlite3
ROOMS = {'x': {'item': 'sensor.other'}}
def read(con, entity_id, start):
    return con.execute('SELECT * FROM states_meta WHERE entity_id = ? AND time > ?', (entity_id, start))
def report(con, room, start):
    cfg = ROOMS[room]
    return read(con, cfg['item'], start)
'''
        module._python_source(source.encode(), mapping)
        for changed in (
            source.replace('sensor.other', 'sensor.old'),
            source.replace('sensor.other', 'sensor.new'),
            source.replace('    return con.execute', "    entity_id = 'sensor.' + room\n    return con.execute"),
            source.replace('    cfg = ROOMS[room]', "    alias = ROOMS.get(room)\n    alias['item'] = 'sensor.' + room\n    cfg = ROOMS[room]"),
            source.replace('    cfg = ROOMS[room]', "    ROOMS[room]['item'] = 'sensor.' + room\n    cfg = ROOMS[room]"),
            source.replace('    cfg = ROOMS[room]', "    ROOMS[room].update({'item': 'sensor.' + room})\n    cfg = ROOMS[room]"),
            source.replace('    cfg = ROOMS[room]', "    __builtins__['ex' + 'ec'](room)\n    cfg = ROOMS[room]"),
            source.replace('    cfg = ROOMS[room]', "    query = con.execute\n    cfg = ROOMS[room]"),
            source.replace('    cfg = ROOMS[room]', "    invoke_unknown(room)\n    cfg = ROOMS[room]"),
        ):
            with self.assertRaises(tx.Refused):
                module._python_source(changed.encode(), mapping)

    async def test_command_source_is_bound_and_drift_refused(self):
        from homeassistant.const import __version__
        if __version__ != '2026.11.1':
            self.skipTest('Core 11.1 only')
        from homeassistant.helpers.entity_platform import EntityPlatform
        from homeassistant.components.command_line.sensor import CommandSensor, CommandSensorData
        hass = self.f.hass
        module = importlib.import_module(fixture.fixtures.PACKAGE + '.native_foreign_consumers')
        tx = importlib.import_module(fixture.fixtures.PACKAGE + '.transaction')
        planner = importlib.import_module(fixture.fixtures.PACKAGE + '.yaml_plan')
        mapping = {'sensor.old': 'sensor.new'}
        command = 'python3 /config/fixture.py'
        (self.f.root / 'configuration.yaml').write_text(
            'command_line:\n  - sensor:\n      command: ' + command + '\n')
        script = self.f.root / 'fixture.py'
        script.write_text("import subprocess\nsubprocess.run(['ffmpeg', '-version'], timeout=1)\n")
        entry = self.f.entity.async_get_or_create('sensor', 'command_line', 'fixture-command')
        ghost = self.f.entity.async_get_or_create('sensor', 'command_line', 'fixture-inactive')
        await hass.async_block_till_done()
        platform = EntityPlatform(hass=hass, logger=logging.getLogger(__name__),
            domain='sensor', platform_name='command_line', platform=None,
            scan_interval=timedelta(seconds=30), entity_namespace=None)
        platform.async_prepare()
        platform._setup_complete = True
        entity = object.__new__(CommandSensor)
        entity.hass, entity.entity_id = hass, entry.entity_id
        entity._config, entity._value_template = {}, None
        entity.data = CommandSensorData(hass, command, 10, 'sensor', 'fixture')
        entity.platform, entity.registry_entry, entity._attr_unique_id = platform, entry, entry.unique_id
        entity._unique_id = entry.unique_id
        platform.entities[entry.entity_id] = entity
        hass.config.components.add('command_line')
        try:
            fence = module.NativeForeignConsumers(hass, mapping, {})
            try:
                plan = planner.plan_yaml(self.f.root, mapping)
                fence.bind_sources(plan, self.f.root)
                fence.check(require_sources=True)
                self.f.entity.async_update_entity(ghost.entity_id, name='Changed inactive entry')
                await hass.async_block_till_done()
                with self.assertRaises(tx.Refused):
                    fence.check(require_sources=True)
            finally:
                fence.close()
            fence = module.NativeForeignConsumers(hass, mapping, {})
            try:
                fence.bind_sources(plan, self.f.root)
                script.write_text("import subprocess\nsubprocess.run(['ffmpeg', 'sensor.old'])\n")
                with self.assertRaises(tx.Refused):
                    fence.check()
            finally:
                fence.close()
        finally:
            platform.entities.clear()
            hass.config.components.remove('command_line')
            await platform.async_destroy()
