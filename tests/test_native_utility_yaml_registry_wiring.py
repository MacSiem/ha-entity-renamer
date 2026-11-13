"""The coordinator may not bypass existing Utility native-effect sidecars."""
from types import SimpleNamespace
import unittest

from custom_components.ha_entity_renamer.native_utility_yaml_wiring import registry_adapter as _utility_registry_adapter
from custom_components.ha_entity_renamer.native_utility_yaml_binding import UtilityYAMLBinding
from custom_components.ha_entity_renamer.native_utility_yaml_handoff import UtilityYAMLHandoff
from custom_components.ha_entity_renamer.native_utility_yaml_receipt import UtilityYAMLRegistryAdapter, _id
from custom_components.ha_entity_renamer.transaction import Plan, Refused, Step

class RegistryWiringTests(unittest.TestCase):
    def make_root(self):
        step = Step('source', 'registry', 'uuid', 'before', 'after', 'prepared')
        plan = Plan('plan', ('registry',), ('registry',), (step,), 'covered', ('registry',))
        return SimpleNamespace(preparations=SimpleNamespace(plans={}), operation_id='operation',
                               plan=plan, hass=object(), mapping={'sensor.old':'sensor.new'},
                               _scope=lambda: None)

    def test_plain_registry_unchanged_without_utility(self):
        root, registry = self.make_root(), object()
        self.assertIs(_utility_registry_adapter(root, registry, lambda ref: None, False), registry)

    def test_cold_unresolved_sidecar_cannot_be_bypassed(self):
        root = self.make_root()
        key = _id(root.operation_id, root.plan.fingerprint, 'source', 'forward', 'intent')
        root.preparations.plans[key] = {}
        with self.assertRaises(Refused):
            _utility_registry_adapter(root, object(), lambda ref: None, False)

    def test_only_exact_live_binding_is_accepted(self):
        root = self.make_root()
        binding = object.__new__(UtilityYAMLBinding)
        binding.hass, binding.mapping = root.hass, dict(root.mapping)
        binding.handoff = object.__new__(UtilityYAMLHandoff)
        root.utility_yaml_binding = binding
        wrapped = _utility_registry_adapter(root, object(), lambda ref: None, False)
        self.assertIs(type(wrapped), UtilityYAMLRegistryAdapter)
        with self.assertRaises(Refused):
            _utility_registry_adapter(root, object(), lambda ref: None, True)
        binding.mapping = {'sensor.foreign':'sensor.new'}
        with self.assertRaises(Refused):
            _utility_registry_adapter(root, object(), lambda ref: None, False)
