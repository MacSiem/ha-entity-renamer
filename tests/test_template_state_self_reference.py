"""Self references are admitted only for unshadowed state entity templates."""
import importlib
import unittest

import test_native_input_baseline as fixture

subject = importlib.import_module(fixture.PACKAGE + '.template_state_program')


def tree(expression, *, block_variables=None, entity_variables=None, field='state'):
    context = [] if block_variables is None else [['variables', {'$map': block_variables}]]
    fields = [[field, {'$template': expression}]]
    if entity_variables is not None:
        fields.append(['variables', {'$map': entity_variables}])
    return ['template_state_entity_v3', 'sensor', {'$map': context}, {'$map': fields}]


class NativeSelfReferenceTests(unittest.TestCase):
    def project(self, value):
        return subject._project(value, {'sensor.old': 'sensor.new'}, frozenset({'sensor.old'}))[0]

    def test_self_ha_calls_and_literal_rewrite(self):
        for expression in ("{{ states(this.entity_id) }}", "{{ state_attr(this.entity_id, 'x') }}",
                           "{{ is_state(this.entity_id, 'on') }}", "{{ states(this['entity_id']) }}"):
            with self.subTest(expression=expression):
                semantic = tree(expression)
                self.assertEqual(self.project(semantic), semantic)
        value = tree("{{ states(this.entity_id) ~ states('sensor.old') }}")
        self.assertIn('sensor.new', str(self.project(value)))

    def test_unknown_and_shadowed_self_remain_refused(self):
        cases = [
            tree('{{ states(other.entity_id) }}'),
            tree('{{ states(this.other) }}'),
            tree('{{ states(this[dynamic_key]) }}'),
            tree('{% set alias = this %}{{ states(alias.entity_id) }}'),
            tree('{% macro m(this) %}{{ states(this.entity_id) }}{% endmacro %}{{ m(other) }}'),
            tree('{% set this = other %}{{ states(this.entity_id) }}'),
            tree('{{ states(this.entity_id) }}', entity_variables=[['this', 'sensor.old']]),
            tree('{{ states(this.entity_id) }}', block_variables=[['this', 'sensor.old']]),
            tree('{{ states(this.entity_id) }}', field='turn_on'),
            ['template_state_entity_v3', 'sensor', {'$map': [['name', {'$template': '{{ states(this.entity_id) }}'}]]}, {'$map': []}],
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'template_state_reference_scope_unverified'):
                self.project(value)


if __name__ == '__main__':
    unittest.main()
