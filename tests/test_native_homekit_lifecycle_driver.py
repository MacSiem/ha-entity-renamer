"""HomeKit lifecycle accepts only its exact prepared, YAML-last plan."""
from types import SimpleNamespace
import unittest
from custom_components.ha_entity_renamer.helper_plan import plan_helper
from custom_components.ha_entity_renamer.helper_transaction import prepare_helper
from custom_components.ha_entity_renamer.native_homekit_lifecycle_driver import HomeKitLifecycleDriver
from custom_components.ha_entity_renamer.native_homekit_yaml_binding import HomeKitYAMLBinding
from custom_components.ha_entity_renamer.prepared import encode_change
from custom_components.ha_entity_renamer.transaction import Plan, Refused, Step
from test_helper_plan import snapshot

class HomeKitLifecyclePlanTests(unittest.TestCase):
    def root(self):
        mapping = {'sensor.before':'sensor.after'}
        before = snapshot('homekit', options={'filter': {'exclude_entities':['sensor.before']}})
        before['identity']['source'] = 'import'
        after = plan_helper(before, mapping).after
        change = prepare_helper(before, mapping)
        binding = object.__new__(HomeKitYAMLBinding)
        binding.hass, binding.receipt, binding.mapping = object(), object(), mapping
        binding.observer = SimpleNamespace(before={'synthetic':before}, after={'synthetic':after})
        registry = Step('r', 'registry', 'entity', 'old', 'new', 'r')
        helper = Step('h', 'helpers', change.identity, change.before_revision, change.after_revision, 'h')
        yaml = Step('y', 'yaml', 'files', 'old', 'new', 'y')
        plan = Plan('plan', ('registry','helpers','yaml'), ('registry','helpers','yaml'),
                    (registry,helper,yaml), 'proof', ('registry','helpers','yaml'))
        payload = encode_change(change)
        store = SimpleNamespace(resolve=lambda ref, digest: payload)
        root = SimpleNamespace(homekit_yaml_binding=binding, hass=binding.hass, receipt=binding.receipt,
            mapping=mapping, plan=plan, preparations=store, digests={'h':'synthetic'})
        return root, (registry,helper,yaml)

    def test_exact_prepared_plan_is_required(self):
        root, steps = self.root()
        self.assertIs(HomeKitLifecycleDriver(root).binding, root.homekit_yaml_binding)
        root.plan = Plan('plan', ('registry','helpers','yaml'), ('registry','helpers','yaml'),
                         (steps[1],steps[0],steps[2]), 'proof', ('registry','helpers','yaml'))
        with self.assertRaises(Refused): HomeKitLifecycleDriver(root)

    def test_original_receipt_and_candidate_are_not_replaceable(self):
        root, _ = self.root()
        root.receipt = object()
        with self.assertRaises(Refused): HomeKitLifecycleDriver(root)
        root.receipt = root.homekit_yaml_binding.receipt
        root.homekit_yaml_binding.observer.after['synthetic'] = root.homekit_yaml_binding.observer.before['synthetic']
        with self.assertRaises(Refused): HomeKitLifecycleDriver(root)

class HomeKitLifecycleOrderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_durable_record_before_quiesce_and_setup_after_receipts(self):
        from unittest.mock import patch
        from custom_components.ha_entity_renamer.native_helper_activation_lease import NativeHelperActivationLease
        from custom_components.ha_entity_renamer.native_helper_lifecycle import NativeHelperLifecycle
        from custom_components.ha_entity_renamer.native_registry_receipts import RegistryReceiptStore
        root, _ = HomeKitLifecyclePlanTests().root()
        binding = root.homekit_yaml_binding
        events = []
        async def done(*args): return True
        async def create(record):
            self.assertEqual(record.phases, ('pending',)*3)
            events.append('journal')
        class Lease:
            def check(self): pass
            def release(self): events.append('release')
        lease = Lease()
        async def acquire(*args, **kwargs): return lease
        case = self
        class Life:
            async def quiesce(self): events.append('unload')
            def require_quiescent(self): pass
            async def restore(self, authorization):
                case.assertEqual(authorization.status, 'applied')
                events.append('setup')
            def inspect(self): return {'activation_receipts_complete':True, 'pending':[]}
            def close_source_gaps(self): pass
        async def start(*args, **kwargs):
            self.assertEqual(events, ['journal'])
            return Life()
        class Coordinator:
            def __init__(self): self.guard = lambda:None
            async def reconcile(self, operation):
                return SimpleNamespace(status='ready', direction='forward', phases=('pending',)*3)
            async def resume(self, operation):
                self.guard(); events.append('receipts')
                return SimpleNamespace(status='applied')
        async def initialize(): pass
        original_store = object.__new__(RegistryReceiptStore)
        original_store.preparations, original_store.rows = root.preparations, {}
        fresh = object.__new__(RegistryReceiptStore)
        fresh.preparations, fresh.rows, fresh.initialize = root.preparations, {}, initialize
        binding.phase = SimpleNamespace(check=lambda:None)
        binding.verify = lambda position: position == 'after'
        binding.observer.bind_activation_lease = lambda lease:None
        binding.observer.settle = done
        binding.observer.verify_inactive = done
        binding.observer.verify_lifecycle_runtime = done
        binding.observer.verify_inventory = done
        root.hass = binding.hass = SimpleNamespace(config_entries=SimpleNamespace(async_get_entry=lambda key: object()))
        root._scope = lambda:None
        root.homekit_lifecycle_store = original_store
        root.operation_id, root.refresh = 'operation', done
        root.journal = SimpleNamespace(create=create)
        root._coordinator = Coordinator
        root.chain = SimpleNamespace(reconstruct_inputs=lambda:SimpleNamespace(head='a'*64))
        async def terminal(*args, **kwargs): events.append('terminal')
        root._terminal = terminal
        with patch.object(NativeHelperActivationLease, 'acquire', acquire), \
             patch.object(NativeHelperLifecycle, 'start', start), \
             patch('custom_components.ha_entity_renamer.native_helper_lifecycle.native_helper_lifecycle_store',
                   lambda *args: fresh):
            driver = HomeKitLifecycleDriver(root)
            result = await driver.apply()
            with self.assertRaises(Refused):
                await driver.apply()
        self.assertEqual(result.status, 'applied')
        self.assertEqual(events, ['journal','unload','receipts','setup','terminal','release'])
        self.assertIsNone(root.homekit_lease)
