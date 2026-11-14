"""Native prepare must refuse unknown projections without blocking on discarded diagnostics."""
import asyncio
import importlib
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_native_helper_quiescence as fixture


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'native HA dependencies required')
class NativePrepareRuntimeDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.NativeHelperQuiescenceTests.asyncSetUp
    _before_start = fixture.NativeHelperQuiescenceTests._before_start

    async def asyncSetUp_runtime(self):
        from homeassistant.setup import async_setup_component
        hass = self.f.hass
        self.assertTrue(await async_setup_component(hass, 'script', {'script': {
            'diagnostic_probe': {'sequence': [{'variables': {'sample': 'unchanged'}}]}}}))
        await hass.async_block_till_done()
        package = fixture.fixtures.PACKAGE
        return (importlib.import_module(package + '.discovery'),
                importlib.import_module(package + '.native_template_forward'),
                importlib.import_module(package + '.yaml_runtime_observer'))

    async def test_complete_projection_is_identical_when_diagnostics_disabled(self):
        discovery, _, _ = await self.asyncSetUp_runtime()
        normal = discovery._runtime_family(self.f.hass, 'script')
        fast = discovery._runtime_family(self.f.hass, 'script', include_row_diagnostics=False)
        self.assertEqual(normal, fast)
        self.assertEqual(fast[0].status, 'COMPLETE')
        self.assertIsNotNone(fast[1])

    async def test_prepare_refuses_unknown_without_running_discarded_row_diagnostics(self):
        discovery, forward, observer = await self.asyncSetUp_runtime()
        target = object.__new__(forward._UnchangedConsumers)
        target.hass, target.loaded = self.f.hass, frozenset({'script'})
        evidence = SimpleNamespace(ledger=SimpleNamespace(keys_for_native_projection=lambda _: ()))
        calls = []
        def slow_diagnostics(*args, **kwargs):
            calls.append(True)
            sleep(2)
            return ()
        # A heartbeat due before this prepare phase detects a synchronous stall.
        start = monotonic()
        tick = asyncio.create_task(asyncio.sleep(0.01))
        with patch.object(observer.NativeYAMLRuntimeReadback, '_projection', side_effect=ValueError('unverified')):
            with patch.object(discovery, '_runtime_row_diagnostics', side_effect=slow_diagnostics):
                with self.assertRaisesRegex(forward.Refused, 'script_runtime_unqualified'):
                    await target.bind_sources(object(), evidence, guard=lambda: None)
                await tick
        self.assertEqual(calls, [])
        self.assertLess(monotonic() - start, 1.0)

    async def test_default_unknown_still_collects_independent_row_diagnostics(self):
        discovery, _, observer = await self.asyncSetUp_runtime()
        rows = (('script.diagnostic_probe', 'UNKNOWN', 'runtime_row_changed'),)
        with patch.object(observer.NativeYAMLRuntimeReadback, '_projection', side_effect=ValueError('unverified')):
            with patch.object(discovery, '_runtime_row_diagnostics', return_value=rows) as diagnostic:
                family, token = discovery._runtime_family(self.f.hass, 'script')
        diagnostic.assert_called_once()
        self.assertEqual(family.status, 'UNKNOWN')
        self.assertEqual(family.reason, 'runtime_rows_partially_unverified')
        self.assertIsNone(token)
