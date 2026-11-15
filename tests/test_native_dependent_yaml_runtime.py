"""Native read-only dependent phase checks, not whole migration acceptance."""
import importlib
import unittest

import test_native_yaml_source_binding as fixture


@unittest.skipUnless(fixture.fixture.fixtures.HAS_HA, 'actual HA required')
class NativeDependentYAMLRuntimeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.NativeYAMLSourceBindingTests.asyncSetUp
    _before_start = fixture.NativeYAMLSourceBindingTests._before_start
    _flush_documents = fixture.NativeYAMLSourceBindingTests._flush_documents
    prepare = fixture.NativeYAMLSourceBindingTests.prepare

    async def data(self):
        receipt, evidence = await self.prepare()
        package = fixture.fixture.fixtures.PACKAGE
        observer = importlib.import_module(package + '.dependent_yaml_runtime')
        discovery = importlib.import_module(package + '.native_discovery_inputs')
        planner = importlib.import_module(package + '.yaml_plan')
        tx = importlib.import_module(package + '.transaction')
        observed = await discovery.async_observe_discovery_inputs(self.f.hass, guard=lambda: None)
        runtime = observer.DependentYAMLRuntime(self.f.hass, evidence, observed,
                                               receipt, position=tx.Position.BEFORE)
        plan = planner.plan_yaml(self.f.root, {'sensor.original': 'sensor.changed'})
        self.assertTrue(plan.ready)
        return observer, runtime, plan

    async def reload(self):
        from homeassistant.core import Context
        await self.f.hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
        await self.f.hass.async_block_till_done()

    async def test_actual_forward_reverse_preserves_original_keys_and_uuid(self):
        observer, runtime, plan = await self.data()
        old = {patch.path: (self.f.root / patch.path).read_bytes() for patch in plan.patches}
        uuid = self.f.entity.async_get('script.source_probe').id
        original = runtime.sample
        ticket = runtime.begin_reload('script', 'forward')
        with self.assertRaises(observer.Refused):
            runtime.check()  # In-flight ACK is not a successful observation.
        for patch in plan.patches:
            (self.f.root / patch.path).write_bytes(patch.proposed_bytes)
        await self.reload()
        runtime.complete_reload(ticket)
        runtime.check()
        self.assertEqual(runtime.sample[0], original[0])
        self.assertNotEqual(runtime.sample[2], original[2])
        self.assertEqual(self.f.entity.async_get('script.source_probe').id, uuid)
        with self.assertRaises(observer.Refused):
            runtime.begin_reload('script', 'forward')
        ticket = runtime.begin_reload('script', 'rollback')
        for name, content in old.items():
            (self.f.root / name).write_bytes(content)
        await self.reload()
        runtime.complete_reload(ticket)
        runtime.check()
        self.assertEqual(runtime.sample[0], original[0])
        self.assertEqual(self.f.entity.async_get('script.source_probe').id, uuid)
        with self.assertRaises(observer.Refused):
            runtime.complete_reload(ticket)

    async def test_native_ack_cannot_accept_foreign_body_or_resume_unconfirmed_phase(self):
        observer, runtime, _ = await self.data()
        ticket = runtime.begin_reload('script', 'forward')
        (self.f.root / 'scripts/one.yaml').write_text(
            'source_probe:\n  sequence:\n    - variables: {sample: foreign_body}\n')
        await self.reload()
        with self.assertRaises(observer.Refused):
            runtime.complete_reload(ticket)
        self.assertTrue(runtime.unresolved)
        with self.assertRaises(observer.Refused):
            runtime.check()
        with self.assertRaises(observer.Refused):
            runtime.begin_reload('script', 'forward')

    async def test_lost_ack_and_foreign_task_do_not_update_expected_phase(self):
        import asyncio
        observer, runtime, _ = await self.data()
        original = runtime.positions
        ticket = runtime.begin_reload('script', 'forward')
        async def foreign():
            with self.assertRaises(observer.Refused):
                runtime.complete_reload(ticket)
        await asyncio.create_task(foreign())
        self.assertEqual(runtime.positions, original)
        runtime.abandon_reload(ticket)
        self.assertTrue(runtime.unresolved)
        self.assertEqual(runtime.positions, original)
        with self.assertRaises(observer.Refused):
            runtime.check()
