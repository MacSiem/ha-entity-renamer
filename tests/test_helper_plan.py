"""Pure synthetic fixtures. Owner runs via approved unit-test runner."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
import unittest

from custom_components.ha_entity_renamer.helper_plan import plan_helper, rebase_after_listeners
from custom_components.ha_entity_renamer.helper_native import apply_entry, read_entry

MAP = {'sensor.before': 'sensor.after'}


def snapshot(domain, data=None, options=None):
    return {'domain': domain, 'identity': {'entry_id': 'synthetic', 'unique_id': 'fixed',
            'title': 'Display', 'version': 1, 'minor_version': 1, 'source': 'user'},
            'data': data or {}, 'options': options or {}, 'subentries': {}}


class PlannerTests(unittest.TestCase):
    def test_native_schemas_and_identity(self):
        for domain, field, value in [('group', 'entities', ['sensor.before']),
            ('min_max', 'entity_ids', ['sensor.before']), ('integration', 'source', 'sensor.before'),
            ('utility_meter', 'source', 'sensor.before'), ('derivative', 'source', 'sensor.before'),
            ('filter', 'entity_id', 'sensor.before'), ('statistics', 'entity_id', 'sensor.before'),
            ('threshold', 'entity_id', 'sensor.before')]:
            with self.subTest(domain=domain):
                before = snapshot(domain, options={field: value, 'name': 'sensor.before'})
                plan = plan_helper(before, MAP)
                self.assertFalse(plan.blocked, plan.markers)
                self.assertEqual(plan.after['identity'], before['identity'])
                self.assertEqual(plan.after['options']['name'], 'sensor.before')
                self.assertNotEqual(plan.after['options'][field], value)
                self.assertEqual(before['options'][field], value)

    def test_registry_uuid_preserved(self):
        before = snapshot('integration', options={'source': 'uuid-reference'})
        self.assertEqual(plan_helper(before, MAP).after, before)

    def test_native_listener_forms_and_foreign_edits(self):
        plan = plan_helper(snapshot('integration', options={'source': 'sensor.before'}), MAP)
        self.assertEqual(rebase_after_listeners(plan, plan.before), plan.after)
        self.assertEqual(rebase_after_listeners(plan, plan.after), plan.after)
        changed = deepcopy(plan.after)
        changed['identity']['title'] = 'foreign'
        with self.assertRaisesRegex(ValueError, 'conflict'):
            rebase_after_listeners(plan, changed)

    def test_powercalc_listener_does_not_hide_other_edits(self):
        plan = plan_helper(snapshot('powercalc', {'entity_id': 'sensor.before',
            'energy_price_sensor': 'sensor.before', 'group_power_entities': ['sensor.before'],
            'cost': {'energy_sensor_id': 'sensor.before'}}), MAP)
        self.assertFalse(plan.blocked)
        native = deepcopy(plan.before)
        native['data']['entity_id'] = 'sensor.after'
        self.assertEqual(rebase_after_listeners(plan, native), plan.after)
        native['data']['energy_price_sensor'] = 'sensor.after'
        with self.assertRaisesRegex(ValueError, 'conflict'):
            rebase_after_listeners(plan, native)

    def test_template_and_dynamic_blocker(self):
        plan = plan_helper(snapshot('template', options={
            'state': "{{ states('sensor.before') }}",
            'additional_options': {'availability': "{{ is_state('sensor.before', 'on') }}"}}), MAP)
        self.assertFalse(plan.blocked, plan.markers)
        self.assertIn('sensor.after', plan.after['options']['state'])
        self.assertTrue(plan_helper(snapshot('template', options={
            'state': "{{ states('sensor.' ~ suffix) }}"}), MAP).blocked)

    def test_unknown_domain_nested_action_and_subentry_block(self):
        for before in [snapshot('unknown', {'source': 'sensor.before'}),
            snapshot('unknown', {'expression': '{{ states(variable) }}'}),
            snapshot('template', options={'turn_on': [{'target': {'entity_id': 'sensor.before'}}]}),
            snapshot('powercalc', {'fixed': {'power': "{{ states('sensor.before') }}"}})]:
            self.assertTrue(plan_helper(before, MAP).blocked)
        before = snapshot('group')
        before['subentries'] = {'s': {'data': {'entities': ['sensor.before']}}}
        self.assertTrue(plan_helper(before, MAP).blocked)

    def test_unknown_domain_unaffected_is_not_claimed_complete(self):
        plan = plan_helper(snapshot('unknown', {'enabled': True}), MAP)
        self.assertFalse(plan.blocked)
        self.assertEqual(plan.coverage, 'installed_schema_fields_only')

    def test_dynamic_scalar_blocks_even_without_literal_source(self):
        self.assertTrue(plan_helper(snapshot('integration', options={'source': '{{ states(variable) }}'}), MAP).blocked)

    def test_wrong_reference_type_and_data_layout_block(self):
        self.assertTrue(plan_helper(snapshot('group', options={'entities': {'bad': 'sensor.before'}}), MAP).blocked)
        self.assertTrue(plan_helper(snapshot('integration', {'source': 'sensor.before'}), MAP).blocked)


class NativeTests(unittest.IsolatedAsyncioTestCase):
    def setup_bridge(self, before):
        entry = SimpleNamespace(domain=before['domain'], **before['identity'],
            data=deepcopy(before['data']), options=deepcopy(before['options']), subentries={})
        self.calls = []
        def update(e, **kwargs):
            self.calls.append('write')
            e.data, e.options = deepcopy(kwargs['data']), deepcopy(kwargs['options'])
            return True
        async def reload(_):
            self.calls.append('reload')
            return True
        manager = SimpleNamespace(async_get_entry=lambda _: entry,
                                  async_update_entry=update, async_reload=reload)
        hass = SimpleNamespace(config_entries=manager)
        async def settle(): self.calls.append('settle')
        async def intent(before, after): self.calls.append('intent')
        async def runtime(after): self.calls.append('runtime')
        async def validate(after): pass
        return hass, entry, dict(validate_snapshot=validate, guard=lambda: None, settle=settle,
                                record_intent=intent, verify_runtime=runtime)

    async def test_reload_and_readback(self):
        before = snapshot('group', options={'entities': ['sensor.before']})
        hass, _, callbacks = self.setup_bridge(before)
        result = await apply_entry(hass, plan_helper(before, MAP), **callbacks)
        self.assertEqual(self.calls, ['settle', 'intent', 'write', 'reload', 'settle', 'runtime'])
        self.assertEqual(result['persistence'], 'unknown')

    async def test_listener_only_no_duplicate_reload(self):
        before = snapshot('powercalc', {'entity_id': 'sensor.before'})
        plan = plan_helper(before, MAP)
        hass, entry, callbacks = self.setup_bridge(before)
        entry.data = deepcopy(plan.after['data'])
        result = await apply_entry(hass, plan, **callbacks)
        self.assertFalse(result['adapter_write'])
        self.assertNotIn('write', self.calls)
        self.assertNotIn('reload', self.calls)

    async def test_edit_during_journal_await_rejected(self):
        before = snapshot('group', options={'entities': ['sensor.before']})
        hass, entry, callbacks = self.setup_bridge(before)
        async def edit(_, after): entry.title = 'foreign'
        callbacks['record_intent'] = edit
        with self.assertRaisesRegex(ValueError, 'conflict'):
            await apply_entry(hass, plan_helper(before, MAP), **callbacks)
        self.assertNotIn('write', self.calls)

    async def test_failed_reload_does_not_report_success(self):
        before = snapshot('group', options={'entities': ['sensor.before']})
        hass, entry, callbacks = self.setup_bridge(before)
        async def fail(_): return False
        hass.config_entries.async_reload = fail
        with self.assertRaisesRegex(ValueError, 'reload_failed'):
            await apply_entry(hass, plan_helper(before, MAP), **callbacks)


if __name__ == '__main__':
    unittest.main()
