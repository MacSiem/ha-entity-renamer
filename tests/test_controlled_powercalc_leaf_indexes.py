"""Exercise native leaf-index ownership fences with a small isolated population."""
import ast
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


class Refused(Exception):
    pass


def need(condition, reason):
    if not condition:
        raise Refused(reason)


# Compile the actual two handoff methods, without bootstrapping Home Assistant.
source = Path(__file__).resolve().parents[1] / 'custom_components/ha_entity_renamer/controlled_powercalc_handoff.py'
tree = ast.parse(source.read_text())
owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ControlledPowercalcHandoff')
methods = [n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name in
           ('_capture_leaf_domain_indexes', '_retire_leaf_indexes')]
module = ast.Module(body=[ast.ClassDef(name='Handoff', bases=[], keywords=[], body=methods, decorator_list=[])], type_ignores=[])
namespace = {'_need': need}
exec(compile(ast.fix_missing_locations(module), str(source), 'exec'), namespace)
Handoff = namespace['Handoff']


class VirtualPowerSensor:
    def __init__(self, name, row, state):
        self.entity_id, self.registry_entry, self._platform_state = name, row, state


class VirtualEnergySensor(VirtualPowerSensor):
    pass


class RealPowerSensor:
    def __init__(self, entity_id): self.entity_id = entity_id
class RealEnergySensor:
    def __init__(self, entity_id): self.entity_id = entity_id


def fake_module(name, **values):
    module = ModuleType(name)
    vars(module).update(values)
    return module


class LeafIndexesTests(unittest.TestCase):
    def setUp(self):
        self.removed, self.active = object(), object()
        self.row_a = SimpleNamespace(id='a', entity_id='sensor.a', unique_id='a', config_entry_id='entry-a')
        self.row_b = SimpleNamespace(id='b', entity_id='sensor.b', unique_id='b', config_entry_id='entry-b')
        self.a = VirtualPowerSensor('sensor.a', self.row_a, self.active)
        self.b = VirtualPowerSensor('sensor.b', self.row_b, self.active)
        self.foreign = object()
        self.bucket = [self.foreign, self.a, self.b]
        self.domain = {'light': self.bucket}
        self.index = {'sensor.a': self.a, 'sensor.b': self.b}
        self.h = Handoff()
        self.h.root = SimpleNamespace(mapping={'sensor.a': 'sensor.new_a'})
        self.h.direction = 'forward'
        self.h.original_entities = {'sensor.a': self.a, 'sensor.b': self.b}
        self.h.population = SimpleNamespace(descriptors=[('sensor.a', 'a', 'profile_fixed'), ('sensor.b', 'b', 'profile_fixed')])
        self.h.hass = SimpleNamespace(data={'powercalc': {'domain_entities': self.domain, 'entities': self.index}})
        self.registry = {}
        self.platform = SimpleNamespace(entities={})
        modules = {
            'custom_components.powercalc.sensors.energy': fake_module('energy', RealEnergySensor=RealEnergySensor, VirtualEnergySensor=VirtualEnergySensor),
            'custom_components.powercalc.sensors.power': fake_module('power', RealPowerSensor=RealPowerSensor, VirtualPowerSensor=VirtualPowerSensor),
            'homeassistant.helpers': fake_module('helpers', entity_registry=SimpleNamespace(async_get=lambda hass: SimpleNamespace(async_get=self.registry.get))),
            'homeassistant.helpers.entity': fake_module('entity', EntityPlatformState=SimpleNamespace(REMOVED=self.removed)),
            'homeassistant.helpers.entity_platform': fake_module('platform', async_get_platforms=lambda hass, domain: [self.platform]),
        }
        self.enterContext(patch.dict(sys.modules, modules))
        self.vendor = SimpleNamespace(classes={'VirtualPowerSensor': VirtualPowerSensor})

    def reload(self):
        self.h._capture_leaf_domain_indexes('forward')
        self.a._platform_state = self.b._platform_state = self.removed
        row_new = SimpleNamespace(id='a', entity_id='sensor.new_a', unique_id='a', config_entry_id='entry-a')
        new_a = VirtualPowerSensor('sensor.new_a', row_new, self.active)
        new_b = VirtualPowerSensor('sensor.b', self.row_b, self.active)
        self.registry.update({'sensor.new_a': row_new, 'sensor.b': self.row_b})
        self.platform.entities.update({'sensor.new_a': new_a, 'sensor.b': new_b})
        self.index.update({'sensor.new_a': new_a, 'sensor.b': new_b})
        self.bucket.extend([new_a, new_b])
        return new_a, new_b

    def test_mapped_and_unmapped_predecessors_retire_preserving_foreign(self):
        new_a, new_b = self.reload()
        self.h._retire_leaf_indexes(self.vendor)
        self.assertEqual(self.bucket, [self.foreign, new_a, new_b])
        self.assertEqual(self.index, {'sensor.new_a': new_a, 'sensor.b': new_b})

    def test_live_predecessor_or_changed_uuid_refuses_without_any_mutation(self):
        for fault in ('live', 'uuid'):
            with self.subTest(fault=fault):
                self.setUp()
                self.reload()
                if fault == 'live': self.b._platform_state = self.active
                else: self.registry['sensor.b'] = SimpleNamespace(id='foreign', entity_id='sensor.b', unique_id='b', config_entry_id='entry-b')
                before_bucket, before_index = list(self.bucket), dict(self.index)
                with self.assertRaises(Refused): self.h._retire_leaf_indexes(self.vendor)
                self.assertEqual(self.bucket, before_bucket)
                self.assertEqual(self.index, before_index)

    def test_unmapped_wrapper_is_preserved(self):
        wrapper = RealPowerSensor("sensor.b")
        self.domain["sensor"] = [wrapper]
        self.reload()
        self.h._retire_leaf_indexes(self.vendor)
        self.assertEqual(self.domain["sensor"], [wrapper])

    def test_foreign_same_id_refuses_without_removal(self):
        impostor = VirtualPowerSensor('sensor.b', self.row_b, self.active)
        self.bucket.append(impostor)
        before = list(self.bucket)
        with self.assertRaises(Refused): self.h._capture_leaf_domain_indexes('forward')
        self.assertEqual(self.bucket, before)
