import asyncio
from types import MappingProxyType, SimpleNamespace
import unittest

from custom_components.ha_entity_renamer.dashboard_ha_tools_fence import HAToolsFence


class HAToolsFenceTests(unittest.TestCase):
    def setUp(self):
        self.store = SimpleNamespace(_data={'devices': [{'value': 10}]}, _lock=asyncio.Lock())
        self.entry = SimpleNamespace(data=MappingProxyType({}), options=MappingProxyType({}),
                                     state=SimpleNamespace(value='loaded'))
        self.entries = [self.entry]
        self.hass = SimpleNamespace(data={'ha_vacuum_water_monitor': {'storage': self.store}},
            config_entries=SimpleNamespace(async_entries=lambda domain: tuple(self.entries)))
        self.config = {'views': [{'cards': [{'type': 'custom:ha-vacuum-water-monitor'}]}]}
        self.mapping = {'sensor.old': 'sensor.new'}

    def test_reference_membership_is_rechecked_without_pinning_telemetry(self):
        fence = HAToolsFence(self.hass, self.mapping, [self.config])
        self.store._data['devices'][0]['value'] = 11
        fence.check()
        self.store._data['devices'][0]['entity'] = 'sensor.old'
        with self.assertRaisesRegex(ValueError, 'reference_unsupported'): fence.check()
        self.store._data = {'settings': {'entity:sensor.new': True}}
        with self.assertRaisesRegex(ValueError, 'reference_unsupported'): fence.check()

    def test_missing_unloaded_or_replaced_store_refuses(self):
        fence = HAToolsFence(self.hass, self.mapping, [self.config])
        self.store._data = None
        with self.assertRaisesRegex(ValueError, 'storage_changed'): fence.check()
        self.store._data = {}
        self.hass.data['ha_vacuum_water_monitor']['storage'] = object()
        with self.assertRaisesRegex(ValueError, 'storage_changed'): fence.check()

    def test_entry_reference_refuses(self):
        self.entry.options = MappingProxyType({'destination': 'entity:sensor.old'})
        with self.assertRaisesRegex(ValueError, 'reference_unsupported'):
            HAToolsFence(self.hass, self.mapping, [self.config])
