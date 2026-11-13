"""HomeKit terminal readback fences both affected and foreign bridges."""
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
import unittest
from custom_components.ha_entity_renamer.native_homekit_yaml_binding import HomeKitYAMLBinding
from custom_components.ha_entity_renamer.transaction import Refused
from test_helper_plan import snapshot

class BindingTests(unittest.TestCase):
    def test_foreign_bridge_identity_and_filter_are_rechecked(self):
        old, new = 'sensor.before', 'sensor.after'
        first = snapshot('homekit', options={'filter':{'exclude_entities':[old]}})
        first['identity'].update(entry_id='first', source='import')
        second = snapshot('homekit', options={'filter':{'exclude_entities':[]}})
        second['identity'].update(entry_id='second', source='import')
        after = snapshot('homekit', options={'filter':{'exclude_entities':[new]}})
        after['identity'].update(entry_id='first', source='import')
        def native_filter(filt):
            return lambda entity: entity not in filt.get('exclude_entities', [])
        class Schema:
            def __init__(self, filt): self.filt=filt
            def get_filter(self): return native_filter(self.filt)
        def entry(row, identity):
            filt=row['options']['filter']
            return SimpleNamespace(state=SimpleNamespace(value='loaded'), identity=identity,
                snapshot=row, runtime_data=SimpleNamespace(homekit=SimpleNamespace(_filter=Schema(filt))))
        entries={'first':entry(after,'one'), 'second':entry(second,'two')}
        manager=SimpleNamespace(async_entries=lambda domain: [SimpleNamespace(entry_id=k) for k in sorted(entries)],
                                async_get_entry=lambda key: entries[key])
        binding=object.__new__(HomeKitYAMLBinding)
        binding.hass=SimpleNamespace(config_entries=manager)
        binding.mapping={old:new}
        binding.phase=SimpleNamespace(capture=SimpleNamespace(entries=(first,second),
            expected=(after,second), bridge_identity=('one','two')))
        homekit=ModuleType('homeassistant.components.homekit'); homekit.FILTER_SCHEMA=Schema
        modules={'homeassistant':ModuleType('homeassistant'), 'homeassistant.components':ModuleType('homeassistant.components'),
                 'homeassistant.components.homekit':homekit}
        with patch.dict('sys.modules', modules), \
             patch('custom_components.ha_entity_renamer.helper_native.read_entry', lambda hass, key:entries[key].snapshot), \
             patch('custom_components.ha_entity_renamer.native_homekit_filter._bridge_identity', lambda value:value.identity):
            self.assertTrue(binding.verify('after'))
            self.assertTrue(binding.verify_foreign())
            entries['second'].identity='changed'
            with self.assertRaises(Refused): binding.verify('after')
            with self.assertRaises(Refused): binding.verify_foreign()
            entries['second'].identity='two'
            entries['second'].runtime_data.homekit._filter=Schema({'exclude_entities':[new]})
            with self.assertRaises(Refused): binding.verify('after')

class HomeKitProjectionTests(unittest.TestCase):
    def test_filter_projection_is_order_independent_but_keeps_values(self):
        from custom_components.ha_entity_renamer.yaml_validation_worker import _canonical_homekit_filter
        first = {'include_entities': ['sensor.one'], 'exclude_entities': []}
        reordered = {'exclude_entities': [], 'include_entities': ['sensor.one']}
        changed = {'exclude_entities': [], 'include_entities': ['sensor.two']}
        self.assertEqual(list(_canonical_homekit_filter(first).items()),
                         list(_canonical_homekit_filter(reordered).items()))
        self.assertNotEqual(_canonical_homekit_filter(first), _canonical_homekit_filter(changed))
        with self.assertRaises(ValueError):
            _canonical_homekit_filter({1: []})
