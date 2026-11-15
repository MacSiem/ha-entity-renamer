"""One profile binds all effects; malformed order cannot reopen."""
from types import SimpleNamespace
import unittest

from custom_components.ha_entity_renamer.native_template_state_composition import ControlledReloadEntryComposition
from custom_components.ha_entity_renamer.native_template_yaml_server import _entry_reload_plan


def plan(adapters, scopes):
    return SimpleNamespace(required_scopes=scopes, covered_scopes=scopes, verification_adapters=scopes,
                           steps=tuple(SimpleNamespace(adapter=a, caused_by=()) for a in adapters))


class ControlledPlanTests(unittest.TestCase):
    def test_one_helper_same_plan_and_strict_order(self):
        composition = object.__new__(ControlledReloadEntryComposition)
        self.assertTrue(_entry_reload_plan(plan(('registry', 'helpers', 'yaml'),
                                              ('registry', 'helpers', 'yaml')), composition))
        self.assertTrue(_entry_reload_plan(plan(('registry', 'helpers', 'dashboard', 'yaml'),
                                              ('registry', 'helpers', 'dashboard', 'yaml')), composition))
        for adapters, scopes in ((('registry', 'yaml'), ('registry', 'helpers', 'yaml')),
                                 (('registry', 'yaml', 'helpers'), ('registry', 'helpers', 'yaml')),
                                 (('registry', 'helpers', 'helpers', 'yaml'), ('registry', 'helpers', 'yaml')),
                                 (('helpers', 'yaml'), ('registry', 'helpers', 'yaml'))):
            self.assertFalse(_entry_reload_plan(plan(adapters, scopes), composition))

    def test_script_and_numeric_entry_share_the_same_plan(self):
        from custom_components.ha_entity_renamer.controlled_script_composition import ControlledScriptComposition
        script = object.__new__(ControlledScriptComposition)
        self.assertTrue(_entry_reload_plan(plan(('registry', 'helpers', 'yaml'),
                                                ('registry', 'helpers', 'yaml')), script))
        self.assertFalse(_entry_reload_plan(plan(('registry', 'yaml', 'helpers'),
                                                 ('registry', 'helpers', 'yaml')), script))

    def test_recovery_routes_only_retained_controlled_entry_profile(self):
        from custom_components.ha_entity_renamer.recovery_controller import _controlled_entry_plan
        composition = object.__new__(ControlledReloadEntryComposition)
        operation = plan(('registry', 'helpers', 'yaml'), ('registry', 'helpers', 'yaml'))
        operation.native_inputs = ('original',)
        record = SimpleNamespace(plan=operation)
        store = SimpleNamespace(resolve_composition=lambda _: (object(), composition))
        self.assertTrue(_controlled_entry_plan(store, record))
        store.resolve_composition = lambda _: (object(), object())
        self.assertFalse(_controlled_entry_plan(store, record))
        def stale(_):
            raise ValueError('stale')
        store.resolve_composition = stale
        self.assertFalse(_controlled_entry_plan(store, record))

    def test_frozen_review_keeps_entry_and_yaml_original_direction(self):
        import json
        from unittest.mock import patch
        from custom_components.ha_entity_renamer.native_registry import PreparedRegistryChange
        from custom_components.ha_entity_renamer.recovery_controller import _yaml_frozen_review
        before = {'id': 'src', 'platform': 'mqtt', 'unique_id': 'u', 'device_id': None,
                  'entity_id': 'sensor.old', 'name': None}
        after = dict(before, entity_id='sensor.new')
        change = PreparedRegistryChange('entity', 'src', json.dumps(before), json.dumps(after))
        composition = object.__new__(ControlledReloadEntryComposition)
        operation = plan(('registry', 'helpers', 'yaml'), ('registry', 'helpers', 'yaml'))
        record = SimpleNamespace(plan=operation, phases=('verified', 'verified', 'verified'))
        raw = json.dumps({'data': {'entities': [{'config_entry_id': 'entry', 'entity_id': 'sensor.consumer'}]}}).encode()
        session = SimpleNamespace(record=record, registry_changes=(change,), composition=composition,
            receipt=SimpleNamespace(changed_domains=('automation', 'script', 'template'), proposed_files=('template.yaml',)),
            evidence=SimpleNamespace(template_sources=SimpleNamespace(ledger=SimpleNamespace(
                definitions=(SimpleNamespace(domain='template.sensor'),)))),
            entry_change=SimpleNamespace(mapping={'sensor.old': 'sensor.new'}, before={'identity': {'entry_id': 'entry'}}),
            chain=SimpleNamespace(baseline=SimpleNamespace(files=(SimpleNamespace(key='core.entity_registry', raw=raw),))))
        with patch('custom_components.ha_entity_renamer.dependency_review.dependency_files', return_value=[]):
            review = _yaml_frozen_review(session)
        self.assertEqual(review['dependencies']['config_entries'], [{'entity_id': 'sensor.consumer',
            'before': 'sensor.old', 'after': 'sensor.new'}])
        self.assertEqual(set(review['dependencies']['domains']), {'automation', 'script', 'template'})

    def test_frozen_powercalc_review_keeps_both_entry_families(self):
        import json
        from unittest.mock import patch
        from custom_components.ha_entity_renamer.native_registry import PreparedRegistryChange
        from custom_components.ha_entity_renamer.controlled_powercalc_composition import ControlledPowercalcComposition
        from custom_components.ha_entity_renamer.recovery_controller import _yaml_frozen_review
        before = {'id': 'src', 'platform': 'mqtt', 'unique_id': 'u', 'device_id': None,
                  'entity_id': 'sensor.old', 'name': None}
        change = PreparedRegistryChange('entity', 'src', json.dumps(before), json.dumps(dict(before, entity_id='sensor.new')))
        composition = object.__new__(ControlledPowercalcComposition)
        object.__setattr__(composition, 'prepared_digests', (('helper', 'digest'),))
        operation = plan(('registry', 'helpers', 'yaml'), ('registry', 'helpers', 'yaml'))
        operation.steps[1].prepared_ref = 'helper'
        raw = json.dumps({'data': {'entities': [{'config_entry_id': 'entry', 'entity_id': 'sensor.consumer'}]}}).encode()
        session = SimpleNamespace(record=SimpleNamespace(plan=operation, phases=('verified',)*3),
            plan=operation, registry_changes=(change,), composition=composition,
            receipt=SimpleNamespace(changed_domains=('automation', 'template'), proposed_files=('template.yaml',)),
            evidence=SimpleNamespace(template_sources=SimpleNamespace(ledger=SimpleNamespace(definitions=()))),
            entry_change=SimpleNamespace(mapping={'sensor.old': 'sensor.new'}, before={'identity': {'entry_id': 'entry'}}),
            preparations=SimpleNamespace(resolve=lambda *_: {}),
            chain=SimpleNamespace(baseline=SimpleNamespace(files=(SimpleNamespace(key='core.entity_registry', raw=raw),))))
        group = {'kind': 'powercalc', 'entity_id': 'sensor.group', 'before': 'sensor.group', 'after': 'sensor.new_group'}
        with patch('custom_components.ha_entity_renamer.dependency_review.dependency_files', return_value=[]), \
             patch('custom_components.ha_entity_renamer.dependency_review.dependency_powercalc_groups', return_value=[group]), \
             patch('custom_components.ha_entity_renamer.prepared.decode_change', return_value=object()):
            entries = _yaml_frozen_review(session)['dependencies']['config_entries']
        self.assertEqual(entries, [{'entity_id': 'sensor.consumer', 'before': 'sensor.old', 'after': 'sensor.new'}, group])
