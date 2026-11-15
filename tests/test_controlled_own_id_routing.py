"""Typed own-automation selection remains tied to immutable registry rows."""
import json
import unittest

from custom_components.ha_entity_renamer.native_registry import PreparedRegistryChange
from custom_components.ha_entity_renamer.native_template_state_composition import ControlledReloadComposition
from custom_components.ha_entity_renamer.native_template_yaml_server import _registry_mapping
from custom_components.ha_entity_renamer.transaction import Refused


def change(identity, platform, old, new, **extra):
    before = {'id': identity, 'platform': platform, 'entity_id': old,
              'unique_id': identity, 'name': None, **extra}
    after = {**before, 'entity_id': new}
    return PreparedRegistryChange('entity', identity, json.dumps(before), json.dumps(after))


class ControlledOwnRoutingTests(unittest.TestCase):
    def setUp(self):
        self.composition = object.__new__(ControlledReloadComposition)
        self.automation = change('automation-uuid', 'automation', 'automation.old', 'automation.new')
        self.source = change('sensor-uuid', 'mqtt', 'sensor.old', 'sensor.new')

    def test_own_automation_and_source_keep_both_mappings(self):
        self.assertEqual(_registry_mapping((self.automation, self.source), self.composition),
                         {'automation.old': 'automation.new', 'sensor.old': 'sensor.new'})

    def test_script_and_two_own_automations_are_not_silently_admitted(self):
        script = change('script-uuid', 'script', 'script.old', 'script.new')
        other = change('other-uuid', 'automation', 'automation.other', 'automation.target')
        for changes in ((script, self.source), (self.automation, other)):
            with self.subTest(changes=changes), self.assertRaises(Refused):
                _registry_mapping(changes, self.composition)

    def test_foreign_registry_field_or_uuid_change_is_rejected(self):
        bad = change('automation-uuid', 'automation', 'automation.old', 'automation.new')
        after = {**bad.after, 'device_id': 'foreign-device'}
        altered = PreparedRegistryChange('entity', bad.identity, bad.before_json, json.dumps(after))
        with self.assertRaises(Refused):
            _registry_mapping((altered, self.source), self.composition)

    def test_own_automation_name_requires_native_runtime_witness(self):
        after = {**self.automation.after, 'name': 'Changed name'}
        unwitnessed = PreparedRegistryChange('entity', self.automation.identity,
                                             self.automation.before_json, json.dumps(after))
        with self.assertRaises(Refused):
            _registry_mapping((unwitnessed, self.source), self.composition)
        witnessed = PreparedRegistryChange('entity', self.automation.identity,
                                           self.automation.before_json, json.dumps(after),
                                           runtime_before_json='{}')
        self.assertEqual(_registry_mapping((witnessed, self.source), self.composition),
                         {'automation.old': 'automation.new', 'sensor.old': 'sensor.new'})

    def test_name_and_id_native_event_must_match_old_values(self):
        from custom_components.ha_entity_renamer.native_rename_emission_classifier import NativeRenameEmissions
        witness = object.__new__(NativeRenameEmissions)
        witness.source_id = 'automation.old'
        witness.target_id = 'automation.new'
        witness.script_unique_move = False
        witness.registry_name_move = True
        witness.source_registry_name = None
        raw = {'action': 'update', 'entity_id': witness.target_id,
               'old_entity_id': witness.source_id,
               'changes': {'entity_id': witness.source_id, 'name': None}}
        self.assertTrue(witness._registry_data(raw))
        for changes in ({'entity_id': witness.source_id},
                        {'entity_id': witness.source_id, 'name': 'foreign'},
                        {'entity_id': witness.source_id, 'name': None, 'area_id': 'foreign'}):
            with self.subTest(changes=changes):
                self.assertFalse(witness._registry_data({**raw, 'changes': changes}))


if __name__ == '__main__':
    unittest.main()
