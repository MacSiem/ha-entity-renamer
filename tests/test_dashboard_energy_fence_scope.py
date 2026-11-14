"""Dashboard phase must preserve the previously qualified Energy scope."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from custom_components.ha_entity_renamer.dashboard_phase import DashboardConsumerFence
from custom_components.ha_entity_renamer.discovery_final_fence import _ConsumerFence
from custom_components.ha_entity_renamer.lovelace_native import revision
from custom_components.ha_entity_renamer.transaction import Refused
from custom_components.ha_entity_renamer.energy_native import EnergySnapshot, PreparedEnergy
from custom_components.ha_entity_renamer.native_template_forward import _require_prepared_energy


class DashboardEnergyFenceScopeTests(unittest.TestCase):
    def setUp(self):
        self.hass = object()
        self.manager = SimpleNamespace(data={'foreign': 'original'})
        self.observed = SimpleNamespace(energy=SimpleNamespace(value=SimpleNamespace(
            revision=revision(self.manager.data), file_identity=('identity',))))
        self.previous = object.__new__(_ConsumerFence)
        self.previous.hass = self.hass
        self.previous.observed = self.observed
        self.previous.dashboard = None
        self.previous.energy = SimpleNamespace(manager=self.manager, _settled=Mock())
        self.previous.energy_path = '/pinned/energy'
        self.previous.energy_minor = 3
        self.phase = SimpleNamespace(check=Mock(), physical=Mock())
        self.manager_patch = patch('custom_components.ha_entity_renamer.native_discovery_consumers._energy_manager', return_value=self.manager)
        self.manager_patch.start()
        self.addCleanup(self.manager_patch.stop)

    def test_qualified_energy_uses_adapter_scope_and_dashboard_phase(self):
        # None is produced only after the existing exact PreparedEnergy admission.
        self.previous.energy = None
        fence = DashboardConsumerFence(self.hass, self.observed, self.phase, self.previous)
        self.assertIsNone(fence.energy.energy)
        fence.check()
        fence.physical()
        self.phase.check.assert_called_once()
        self.phase.physical.assert_called_once()

    def test_unchanged_energy_retains_identity_pending_and_disk_checks(self):
        fence = DashboardConsumerFence(self.hass, self.observed, self.phase, self.previous)
        self.assertIs(fence.energy.energy, self.previous.energy)
        self.assertEqual(fence.energy.energy_path, self.previous.energy_path)
        self.previous.energy._settled.side_effect = ValueError('foreign pending')
        with self.assertRaisesRegex(ValueError, 'foreign pending'):
            fence.check()
        self.previous.energy._settled.side_effect = None
        self.manager.data = {'foreign': 'changed'}
        with self.assertRaisesRegex(ValueError, 'discovery_energy_manager_changed'):
            fence.check()
        with patch('custom_components.ha_entity_renamer.lovelace_native.durable_snapshot',
                   return_value=({'foreign': 'changed'}, ('identity',))):
            with self.assertRaisesRegex(ValueError, 'discovery_energy_disk_changed'):
                fence.physical()

    def test_prepared_energy_requires_exact_images_before_write(self):
        before, after = {'meter': 'old'}, {'meter': 'new'}
        prepared = PreparedEnergy(EnergySnapshot(before, 'before', None), after, 'after')
        plan = SimpleNamespace(value=after)
        _require_prepared_energy(before, plan, prepared, False)
        _require_prepared_energy(after, SimpleNamespace(value=before), prepared, True)
        for config, candidate in (({'meter': 'foreign'}, prepared), (before, object())):
            with self.assertRaisesRegex(Refused, 'energy_preparation_mismatch'):
                _require_prepared_energy(config, plan, candidate, False)
        with self.assertRaisesRegex(Refused, 'energy_preparation_mismatch'):
            _require_prepared_energy(before, SimpleNamespace(value={'meter': 'foreign'}), prepared, False)

    def test_rejects_fence_from_another_capture(self):
        with self.assertRaisesRegex(Refused, 'consumer_fence_changed'):
            DashboardConsumerFence(self.hass, object(), self.phase, self.previous)
