"""Actual official vendor fixture, each case in a fresh FIFO-owned process.

Reuses complete setup, registry/document flushing and genuine SwitchEntity
producers. Constants below occur only in assertion or native input config.
"""
from decimal import Decimal
import importlib
from pathlib import Path
import unittest
from unittest.mock import patch

import test_native_input_baseline as fixtures
import test_powercalc_runtime_native as existing


@unittest.skipUnless(fixtures.HAS_HA, 'requires actual pinned HA')
class PowercalcSourceBindingNativeTests(unittest.IsolatedAsyncioTestCase):
    namespace_source_bytes = b'# Preserve this ORIGINAL namespace source in both staging trees.\n'
    asyncSetUp = existing.PowercalcRuntimeNativeTests.asyncSetUp
    _setup_real_vendor = existing.PowercalcRuntimeNativeTests._setup_real_vendor
    _flush_documents = existing.PowercalcRuntimeNativeTests._flush_documents
    guard = existing.PowercalcRuntimeNativeTests.guard

    async def prepare(self):
        try:
            self.subject = importlib.import_module(fixtures.PACKAGE + '.powercalc_source_binding')
        except ModuleNotFoundError as error:
            if error.name != fixtures.PACKAGE + '.powercalc_source_binding':
                raise
            self.fail('actual Powercalc ORIGINAL source binding is not implemented')
        self.runtime = importlib.import_module(fixtures.PACKAGE + '.powercalc_runtime_observer')
        planner = importlib.import_module(fixtures.PACKAGE + '.yaml_plan')
        validator = importlib.import_module(fixtures.PACKAGE + '.yaml_validation')
        self.plan = planner.plan_yaml(self.f.root, {self.yaml_source: 'switch.original_source_target'})
        self.assertTrue(self.plan.ready, self.plan.issues)
        self.receipt, self.evidence = await validator.async_validate_proposed_with_powercalc_sources(
            self.f.hass, self.plan, self.f.root.parent)
        self.first = self.runtime.capture_powercalc_runtime(self.f.hass, guard=self.guard)
        return await self.bind()

    async def bind(self):
        return await self.subject.async_bind_powercalc_sources(self.f.hass,
            self.receipt, self.evidence, self.first, guard=self.guard)

    def assert_bound(self, result):
        rows = [row for row in result.rows if row.source_verified]
        fixed = [row for row in rows if row.reason == 'fixed_source_bound']
        grouped = [row for row in rows if row.reason == 'custom_sum_sources_bound']
        self.assertEqual({row.source for row in fixed}, {self.yaml_source, self.gui_source})
        self.assertEqual(len(grouped), 1)
        gui = next(row for row in fixed if row.source == self.gui_source)
        self.assertEqual(grouped[0].edges, ((gui.entity_id, grouped[0].entity_id, 1),))
        for row in fixed:
            self.assertEqual(row.source_registry_id, self.f.entity.async_get(row.source).id)
            self.assertEqual(row.registry_id, self.f.entity.async_get(row.entity_id).id)
        self.assertEqual(result.status, 'UNKNOWN')
        self.assertFalse(result.migration_ready)
        self.assertFalse(result.family_complete)
        self.assertIn('powercalc_continuity_unverified', result.blockers)
        return fixed, grouped[0]

    async def test_original_yaml_gui_custom_sum_and_real_producer_events(self):
        result = await self.prepare()
        fixed, grouped = self.assert_bound(result)
        original_evidence = self.evidence.sha256
        for enabled in (False, True):
            for producer in self.sources:
                producer._attr_is_on = enabled
                producer.async_write_ha_state()
            await self.f.hass.async_block_till_done()
            for row in fixed:
                expected = (7 if row.source == self.yaml_source else 10) if enabled else 0
                actual = self.f.hass.states.get(row.entity_id)
                self.assertEqual(Decimal(actual.state), Decimal(expected))
                self.assertEqual(actual.attributes['unit_of_measurement'], 'W')
            self.assertEqual(Decimal(self.f.hass.states.get(grouped.entity_id).state),
                             Decimal(10 if enabled else 0))
            # The SAME original receipt/evidence/runtime topology survives a
            # normal producer event. No replacement capture becomes a baseline.
            self.assert_bound(await self.bind())
            self.assertEqual(self.evidence.sha256, original_evidence)

    async def test_original_physical_yaml_edit_is_not_adopted(self):
        self.assert_bound(await self.prepare())
        path = self.f.root / 'configuration.yaml'
        raw = path.read_bytes()
        try:
            path.write_bytes(raw + b'\n# actual source drift\n')
            with self.assertRaises(ValueError):
                await self.bind()
        finally:
            path.write_bytes(raw)

    async def test_actual_vendor_file_drift_is_rejected(self):
        self.assert_bound(await self.prepare())
        path = self.f.root / 'custom_components/powercalc/strategy/fixed.py'
        raw = path.read_bytes()
        try:
            path.write_bytes(raw + b'\n# actual vendor source drift\n')
            with self.assertRaisesRegex(ValueError, 'vendor_source_changed'):
                await self.bind()
        finally:
            path.write_bytes(raw)

    async def test_loaded_actual_vendor_code_drift_is_rejected(self):
        self.assert_bound(await self.prepare())
        from custom_components.powercalc.strategy.fixed import FixedStrategy
        function = FixedStrategy.calculate
        code = function.__code__
        try:
            # Fault only the existing real function's code metadata. There is
            # no substitute class/callback, and no producer runs under this fault.
            function.__code__ = code.replace(co_firstlineno=code.co_firstlineno + 1)
            with self.assertRaises(ValueError):
                await self.bind()
        finally:
            function.__code__ = code

    async def test_native_config_entry_drift_does_not_refresh_original(self):
        self.assert_bound(await self.prepare())
        original = self.evidence.sha256
        self.f.hass.config_entries.async_update_entry(self.entry,
            data={**self.entry.data, 'fixed': {'power': 11}})
        await self.f.hass.async_block_till_done()
        await self._flush_documents()
        with self.assertRaises(ValueError):
            await self.bind()
        self.assertEqual(self.evidence.sha256, original)

    async def test_native_physical_source_registry_identity_drift_is_rejected(self):
        self.assert_bound(await self.prepare())
        # Actual registry writer on this test's genuine producer, preserving its
        # source ID while changing the original UUID row's physical metadata.
        self.f.entity.async_update_entity(self.gui_source, name='Changed native source metadata')
        await self.f.hass.async_block_till_done()
        await self._flush_documents()
        with self.assertRaises(ValueError):
            await self.bind()

    async def test_nonempty_original_namespace_is_preserved_in_both_staging_trees(self):
        subject = importlib.import_module(fixtures.PACKAGE + '.powercalc_source_binding')
        original = subject.stage_powercalc_bundle
        expected = (self.f.root / 'custom_components/__init__.py').read_bytes()
        self.assertEqual(expected, self.namespace_source_bytes)
        self.assertTrue(expected)
        staged = []

        def verify_actual_staging(bundle, before, after):
            result = original(bundle, before, after)  # genuine readonly check
            for root in (before, after):
                actual = (Path(root) / 'custom_components/__init__.py').read_bytes()
                self.assertEqual(actual, expected)
                staged.append(str(root))
            return result

        with patch.object(subject, 'stage_powercalc_bundle', verify_actual_staging):
            self.assert_bound(await self.prepare())
        self.assertEqual(len(staged), 2)
        self.assertEqual((self.f.root / 'custom_components/__init__.py').read_bytes(), expected)

    async def test_real_shared_bus_detach_cannot_keep_keyed_listener_source_proof(self):
        from homeassistant.helpers import event
        result = await self.prepare()
        fixed, group = self.assert_bound(result)
        tracker = self.f.hass.data[event._TRACK_STATE_CHANGE_DATA]
        callbacks = tracker.callbacks
        memberships = {key: tuple(map(id, jobs)) for key, jobs in callbacks.items()}
        before = {row.entity_id: self.f.hass.states.get(row.entity_id).state
                  for row in (*fixed, group)}
        tracker.listener()  # actual native EventBus unsubscribe, negative only
        self.assertIs(self.f.hass.data[event._TRACK_STATE_CHANGE_DATA], tracker)
        self.assertIs(tracker.callbacks, callbacks)
        self.assertEqual({key: tuple(map(id, jobs)) for key, jobs in callbacks.items()}, memberships)
        # Real producers change; the disconnected native dispatcher cannot route
        # these ordinary events, despite the surviving keyed callback dictionary.
        for producer in self.sources:
            producer._attr_is_on = False
            producer.async_write_ha_state()
        await self.f.hass.async_block_till_done()
        for producer in self.sources:
            self.assertEqual(self.f.hass.states.get(producer.entity_id).state, 'off')
        self.assertEqual({eid: self.f.hass.states.get(eid).state for eid in before}, before)
        refused = await self.bind()
        self.assertEqual([(row.entity_id, row.registry_id) for row in refused.rows],
                         [(row.entity_id, row.registry_id) for row in result.rows])
        self.assertFalse(any(row.source_verified for row in refused.rows))
        self.assertFalse(refused.family_complete)
        self.assertFalse(refused.migration_ready)
        # Do not fabricate a bus tuple to restore positive provenance. The
        # isolated native fixture owns final teardown of this detached graph.

    async def test_actual_strategy_instance_shadow_cannot_keep_fixed_source_proof(self):
        from homeassistant.helpers.entity_platform import async_get_platforms
        result = await self.prepare()
        fixed, _ = self.assert_bound(result)
        gui = next(row for row in fixed if row.source == self.gui_source)
        entity = next(entity for platform in async_get_platforms(self.f.hass, 'powercalc')
                      for entity in platform.entities.values() if entity.entity_id == gui.entity_id)
        strategy = entity._strategy_instance
        prior = dict(vars(strategy))
        native_class = type(strategy)
        original_calculate = native_class.calculate
        try:
            # A real existing native method shadows only the same instance's
            # dynamic calculate lookup. No new callback, class or function code.
            strategy.calculate = strategy.validate_config
            self.assertIs(type(strategy), native_class)
            self.assertIs(native_class.calculate, original_calculate)
            self.assertIs(entity._strategy_instance, strategy)
            refused = await self.bind()
            gui_row = next(row for row in refused.rows if row.entity_id == gui.entity_id)
            self.assertFalse(gui_row.source_verified)
        finally:
            if 'calculate' in prior:
                strategy.calculate = prior['calculate']
            else:
                del strategy.calculate
        self.assertEqual(vars(strategy), prior)
        self.assert_bound(await self.bind())

    async def test_physical_yaml_edit_during_final_validator_code_job_is_rejected(self):
        self.assert_bound(await self.prepare())
        validator = importlib.import_module(fixtures.PACKAGE + '.yaml_validation')
        path = self.f.root / 'configuration.yaml'
        original_yaml = path.read_bytes()
        original_sources = self.subject.PowercalcVendor.sources
        calls = []

        def sources_then_physical_edit(vendor):
            result = original_sources(vendor)  # genuine official code proof
            calls.append(vendor)
            if len(calls) == 2:
                # Final code-source executor job, after the checker's earlier
                # ORIGINAL fence. The fixed job now checks receipt immediately
                # after this real edit, without another executor await.
                path.write_bytes(original_yaml + b'\n# late ORIGINAL validator edit\n')
            return result

        try:
            with patch.object(self.subject.PowercalcVendor, 'sources', sources_then_physical_edit):
                with self.assertRaises(ValueError):
                    await validator.async_validate_proposed_with_powercalc_sources(
                        self.f.hass, self.plan, self.f.root.parent)
        finally:
            path.write_bytes(original_yaml)
        self.assertEqual(len(calls), 2)
        self.assertIs(calls[0], calls[1])
        self.assertIs(self.subject.PowercalcVendor.sources, original_sources)
        self.assertEqual(path.read_bytes(), original_yaml)
