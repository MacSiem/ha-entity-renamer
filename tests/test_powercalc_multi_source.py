"""A multi-entry admission must prove each producer's own native pair."""
from dataclasses import replace
import unittest

from custom_components.ha_entity_renamer.powercalc_multi_source import qualify_multi_entry_sources
from custom_components.ha_entity_renamer.powercalc_source_binding import PowercalcBoundRow
from custom_components.ha_entity_renamer.transaction import Refused


class MultiSourceTests(unittest.TestCase):
    def setUp(self):
        self.sources = ('light.a', 'light.b')
        self.registry, rows = [], []
        for name in ('a', 'b'):
            source, power, energy, entry = 'light.' + name, 'sensor.' + name, 'sensor.' + name + '_energy', 'entry-' + name
            self.registry.extend(({'entity_id': source, 'id': 'src-' + name},
                                  {'entity_id': power, 'id': 'power-' + name, 'config_entry_id': entry},
                                  {'entity_id': energy, 'id': 'energy-' + name, 'config_entry_id': entry}))
            rows.extend((PowercalcBoundRow(power, 'power-' + name, True, 'bound', source, 'src-' + name,
                                          'entry:' + entry, edges=((source, power, 1),), relation_kind='profile_fixed'),
                         PowercalcBoundRow(energy, 'energy-' + name, True, 'bound', origin='profile:entry:' + entry,
                                           edges=((power, energy, 1),), relation_kind='energy_integral')))
        self.rows = tuple(rows)

    def test_each_entry_requires_its_own_verified_pair_and_registry_identity(self):
        qualify_multi_entry_sources(self.sources, self.rows, self.registry)
        for changed in (
                self.rows[:3],
                self.rows[:2] + (replace(self.rows[2], source_registry_id='src-a'), self.rows[3]),
                self.rows[:3] + (replace(self.rows[3], edges=(('sensor.a', 'sensor.b_energy', 1),)),),
                self.rows[:2] + (replace(self.rows[2], source_verified=False), self.rows[3])):
            with self.subTest(changed=changed), self.assertRaises(Refused):
                qualify_multi_entry_sources(self.sources, changed, self.registry)
        registry = [dict(row) for row in self.registry]
        registry[-1]['config_entry_id'] = 'entry-a'
        with self.assertRaises(Refused):
            qualify_multi_entry_sources(self.sources, self.rows, registry)
