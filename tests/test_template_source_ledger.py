"""Physical Template ORIGINAL inventory, without HA or native execution."""
from dataclasses import replace
import unittest
import test_yaml_source_ledger as fixture
from custom_components.ha_entity_renamer import yaml_source_ledger as ledger_module
from custom_components.ha_entity_renamer.yaml_source_ledger import SourceLedgerError


def extract_template_source_ledger(*args, **kwargs):
    function = getattr(ledger_module, "extract_template_source_ledger", None)
    if function is None:
        raise AssertionError("Template original source inventory is not implemented")
    return function(*args, **kwargs)


class TemplateSourceLedgerTests(unittest.TestCase):
    def source(self, text, **files):
        plan, bundle = fixture.SourceLedgerTests().fixture(
            {'configuration.yaml': text, **files})
        return extract_template_source_ledger(plan, bundle)

    def test_all_platform_rows_and_block_origin_are_retained(self):
        ledger = self.source('template: !include templates.yaml\n', **{
            'templates.yaml': '- unique_id: section\n  sensor:\n  - unique_id: value\n    state: "{{ states(\'sensor.original\') }}"\n  binary_sensor:\n  - unique_id: flag\n    state: "{{ is_state(\'sensor.original\', \'on\') }}"\n'})
        self.assertFalse(ledger.findings)
        self.assertEqual([(x.domain, x.native_key) for x in ledger.definitions],
            [('template.state', 'id:section'), ('template.sensor', 'section-value'),
             ('template.binary_sensor', 'section-flag')])
        self.assertTrue(all(x.origin.path == 'templates.yaml' for x in ledger.definitions))
        self.assertTrue(all(x.origin.includes for x in ledger.definitions))

    def test_trigger_and_missing_id_stay_visible_with_unknown(self):
        ledger = self.source('template:\n- triggers: [{trigger: state, entity_id: sensor.original}]\n  sensor: [{state: "{{ trigger.to_state.state }}"}]\n')
        self.assertEqual(ledger.definitions[0].domain, 'template.trigger')
        self.assertEqual(ledger.definitions[1].domain, 'template.sensor')
        self.assertIsNone(ledger.definitions[1].native_key)
        self.assertEqual(ledger.definitions[1].disposition, 'unsupported')
        self.assertIn('source_template_stable_id_missing', {f.code for f in ledger.findings})

    def test_duplicates_across_packages_and_platform_separation(self):
        ledger = self.source('template:\n- sensor: [{unique_id: value, state: "1"}]\nhomeassistant:\n  packages:\n    other:\n      template:\n      - sensor: [{unique_id: value, state: "2"}]\n        binary_sensor: [{unique_id: value, state: "true"}]\n')
        sensors = [r for r in ledger.definitions if r.domain == 'template.sensor']
        self.assertEqual(len(sensors), 2)
        self.assertEqual({r.disposition for r in sensors}, {'duplicate'})
        self.assertEqual(next(r for r in ledger.definitions if r.domain == 'template.binary_sensor').disposition, 'candidate')

    def test_missing_include_edge_and_foreign_bytes_refuse(self):
        plan, bundle = fixture.SourceLedgerTests().fixture({
            'configuration.yaml': 'template: !include template.yaml\n',
            'template.yaml': '- sensor: [{unique_id: value, state: "1"}]\n'})
        with self.assertRaises(SourceLedgerError):
            extract_template_source_ledger(replace(plan, edges=()), bundle)
        with self.assertRaises(SourceLedgerError):
            extract_template_source_ledger(plan, {**bundle, 'template.yaml': b'[]'})

    def test_legacy_and_blueprint_are_preserved_as_unknown(self):
        ledger = self.source('sensor:\n- platform: template\n  sensors: {old: {value_template: "1"}}\ntemplate:\n- use_blueprint: {path: foreign.yaml}\n')
        self.assertEqual({r.domain for r in ledger.definitions}, {'template.legacy', 'template.state'})
        codes = {r.code for r in ledger.findings}
        self.assertIn('source_template_legacy_unverified', codes)
        self.assertIn('source_blueprint_unsupported', codes)

    def test_labeled_sections_and_directory_include_preserve_all_rows(self):
        ledger = self.source('template first: !include_dir_merge_list templates\ntemplate second:\n- update: [{unique_id: newer, installed_version: "1", latest_version: "2"}]\n', **{
            'templates/a.yaml': '- sensor: [{unique_id: one, state: "1"}]\n',
            'templates/b.yaml': '- sensor: [{unique_id: two, state: "2"}]\n'})
        self.assertEqual([r.native_key for r in ledger.definitions if r.domain == 'template.sensor'], ['one', 'two'])
        self.assertIn('template.update', {r.domain for r in ledger.definitions})
        self.assertFalse(ledger.findings)

    def test_unprefixed_block_identity_survives_package_order(self):
        # Native package merging need not preserve physical encounter order.
        ledger = self.source('homeassistant:\n  packages:\n    first:\n      template:\n      - sensor: [{unique_id: package, state: "1"}]\ntemplate:\n- sensor: [{unique_id: root, state: "2"}]\n')
        blocks = [r for r in ledger.definitions if r.domain == 'template.state']
        self.assertEqual({r.native_key for r in blocks}, {
            'entities:[["template.sensor","package"]]',
            'entities:[["template.sensor","root"]]'})
        self.assertFalse(ledger.findings)

    def test_old_automation_ledger_is_not_given_template_findings(self):
        from custom_components.ha_entity_renamer.yaml_source_ledger import extract_source_ledger
        plan, bundle = fixture.SourceLedgerTests().fixture({
            'configuration.yaml': 'script: {one: {sequence: []}}\ntemplate:\n- sensor: [{state: "1"}]\n'})
        legacy = extract_source_ledger(plan, bundle)
        self.assertEqual(legacy.schema, 1)
        self.assertFalse(legacy.findings)
        self.assertEqual(legacy.keys_for_native_projection('script'), ('one',))
        self.assertTrue(extract_template_source_ledger(plan, bundle).findings)

    def test_template_schema_cannot_be_reused_as_automation_inventory(self):
        ledger = self.source('template:\n- sensor: [{unique_id: one, state: "1"}]\n')
        self.assertEqual(ledger.schema, 2)
        with self.assertRaises(SourceLedgerError):
            ledger.keys_for_native_projection('script')
