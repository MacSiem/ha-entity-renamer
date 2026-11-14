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

    async def test_actual_automation_projection_refuses_without_row_rescan(self):
        from homeassistant.const import __version__
        from homeassistant.setup import async_setup_component
        if __version__ != '2026.11.1':
            self.skipTest('actual Core 11.1 automation proof')
        hass = self.f.hass
        config = {'automation': [{'id': 'diagnostic_automation',
            'alias': 'Diagnostic automation', 'initial_state': True,
            'triggers': [{'trigger': 'state', 'entity_id': ['sensor.synthetic']}],
            'actions': [{'event': 'diagnostic_probe'}]}]}
        self.assertTrue(await async_setup_component(hass, 'automation', config))
        await hass.async_block_till_done()
        package = fixture.fixtures.PACKAGE
        discovery = importlib.import_module(package + '.discovery')
        forward = importlib.import_module(package + '.native_template_forward')
        core = importlib.import_module(package + '.native_controlled_yaml_core111')
        target = object.__new__(forward._UnchangedConsumers)
        target.hass, target.loaded = hass, frozenset({'automation'})
        evidence = SimpleNamespace(ledger=SimpleNamespace(keys_for_native_projection=lambda _: ()))
        tick = asyncio.create_task(asyncio.sleep(0.01))
        start = monotonic()
        with patch.object(core, 'automation_registration_token', side_effect=ValueError('unverified')) as reject:
            with patch.object(discovery, '_runtime_row_diagnostics', side_effect=AssertionError('unneeded rescan')):
                with self.assertRaisesRegex(forward.Refused, 'automation_runtime_unqualified'):
                    await target.bind_sources(object(), evidence, guard=lambda: None)
                await tick
        reject.assert_called_once()
        self.assertLess(monotonic() - start, 2.0)

    async def test_production_blocking_detector_allows_pinned_code_proof(self):
        from homeassistant import block_async_io
        from homeassistant.const import __version__
        if __version__ != '2026.11.1':
            self.skipTest('actual Core 11.1 blocking detector')
        core = importlib.import_module(fixture.fixtures.PACKAGE + '.native_controlled_yaml_core111')
        self.assertFalse(block_async_io._BLOCKED_CALLS.calls)
        read_bytes = next(row for row in block_async_io._BLOCKING_CALLS
                          if row.object is __import__('pathlib').Path and row.function == 'read_bytes')
        self.assertFalse(read_bytes.strict)
        self.assertFalse(read_bytes.strict_core)
        try:
            # Reproduce production; unittest normally skips filesystem checks.
            with patch.object(block_async_io, '_IN_TESTS', False):
                block_async_io.enable()
            core.prove_automation_projection()
        finally:
            for row in block_async_io._BLOCKED_CALLS.calls:
                setattr(row.object, row.function, row.original_func)
            block_async_io._BLOCKED_CALLS.calls.clear()

    async def test_strict_failure_log_does_not_expose_exception_values(self):
        discovery, forward, observer = await self.asyncSetUp_runtime()
        target = object.__new__(forward._UnchangedConsumers)
        target.hass, target.loaded = self.f.hass, frozenset({'script'})
        evidence = SimpleNamespace(ledger=SimpleNamespace(keys_for_native_projection=lambda _: ()))
        with patch.object(observer.NativeYAMLRuntimeReadback, '_projection',
                          side_effect=ValueError('private-caller-value')):
            with self.assertLogs(discovery.__name__, level='WARNING') as logs:
                with self.assertRaises(forward.Refused):
                    await target.bind_sources(object(), evidence, guard=lambda: None)
        self.assertIn('type=ValueError reason=other', logs.output[0])
        self.assertNotIn('private-caller-value', logs.output[0])
