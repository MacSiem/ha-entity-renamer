"""Proposed regressions. NOT executed by the artifact worker."""
import asyncio
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from custom_components.ha_entity_renamer.helper_observers import (
    NativeHelperObservers, ObservationUnavailable, finite_number,
    min_max_expected, threshold_expected,
)


class SampleSemanticsTests(unittest.TestCase):
    def test_threshold_deterministic_branches_and_history_refusal(self):
        upper = {'lower': None, 'upper': 42.5, 'hysteresis': 1.0}
        self.assertEqual(threshold_expected(upper, 44), (True, 'above'))
        self.assertEqual(threshold_expected(upper, 40), (False, 'below'))
        for value in (41.5, 42.5, 43.5):
            with self.assertRaisesRegex(ObservationUnavailable, 'history_required'):
                threshold_expected(upper, value)
        lower = {'lower': 20, 'upper': None, 'hysteresis': 1}
        self.assertEqual(threshold_expected(lower, 18), (True, 'below'))
        self.assertEqual(threshold_expected(lower, 22), (False, 'above'))
        window = {'lower': 20, 'upper': 30, 'hysteresis': 1}
        self.assertEqual(threshold_expected(window, 25), (True, 'in_range'))
        self.assertEqual(threshold_expected(window, 18), (False, 'below'))
        self.assertEqual(threshold_expected(window, 32), (False, 'above'))

    def test_all_six_stateless_min_max_types(self):
        values = [3.0, 6.0, 10.0]
        expected = {'min': 3, 'max': 10, 'mean': 6.33, 'median': 6,
                    'range': 7, 'sum': 19}
        for kind, value in expected.items():
            self.assertEqual(min_max_expected(kind, values, 2), value)
        with self.assertRaisesRegex(ObservationUnavailable, 'history_required'):
            min_max_expected('last', values, 2)

    def test_nonfinite_and_bool_do_not_prove_numeric_state(self):
        for value in (True, float('nan'), float('inf'), '42'):
            with self.assertRaises(ObservationUnavailable):
                finite_number(value)

    def test_arbitrary_group_variant_refused(self):
        with self.assertRaisesRegex(ObservationUnavailable, 'unsupported'):
            NativeHelperObservers._references({
                'domain': 'group', 'options': {'group_type': 'light', 'entities': []}})

    def test_reused_target_entity_id_does_not_prove_registry_identity(self):
        observer = object.__new__(NativeHelperObservers)
        observer.source_rows = {'entry': [('sensor.old', ('original-uuid', 'unique',
                                                         'test', 'producer', None))]}
        observer.mapping = {'sensor.old': 'sensor.new'}
        row = SimpleNamespace(id='foreign-uuid', unique_id='unique', platform='test',
                              config_entry_id='producer', config_subentry_id=None,
                              disabled_by=None)
        state = SimpleNamespace(entity_id='sensor.new', state='50')
        registry = SimpleNamespace(async_get=lambda _: row)
        er = SimpleNamespace(async_get=lambda _: registry,
                             async_validate_entity_id=lambda _, ref: ref)
        observer.hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: state))
        snapshot = {'domain': 'threshold', 'identity': {'entry_id': 'entry'},
                    'options': {'entity_id': 'sensor.new'}}
        # The source exists and has a valid value; its stable UUID is wrong.
        with patch.dict(sys.modules, {'homeassistant.helpers': SimpleNamespace(entity_registry=er)}):
            with self.assertRaisesRegex(ObservationUnavailable, 'registry_identity_changed'):
                observer._source_states(snapshot)
            row.id = 'original-uuid'
            self.assertEqual(observer._source_states(snapshot), [state])


@unittest.skipUnless(hasattr(asyncio, "timeout"), "Native HA runtime requires Python 3.11+ timeout")
class SettleTests(unittest.IsolatedAsyncioTestCase):
    def observer(self, drain, *, state='loaded', timeout=0.02):
        entry = SimpleNamespace(entry_id='entry', state=SimpleNamespace(value=state))
        hass = SimpleNamespace(async_block_till_done=drain,
            config_entries=SimpleNamespace(async_entries=lambda: [entry],
                                           async_get_entry=lambda _: entry))
        return NativeHelperObservers(hass, {'entry': {}}, {'entry': {}}, {}, {}, {}, {},
                                     settle_timeout=timeout)

    async def test_drain_does_not_create_child_task_that_waits_on_parent(self):
        current = asyncio.current_task()
        async def drain():
            self.assertIs(asyncio.current_task(), current)
            await asyncio.sleep(0)
        self.assertTrue(await self.observer(drain).settle())

    async def test_timeout_is_refusal(self):
        async def drain():
            await asyncio.Event().wait()
        with self.assertRaisesRegex(ObservationUnavailable, 'settle_timeout'):
            await self.observer(drain).settle()

    async def test_setup_retry_is_not_loaded_success(self):
        async def drain():
            pass
        with self.assertRaisesRegex(ObservationUnavailable, 'not_settled'):
            await self.observer(drain, state='setup_retry').settle()
