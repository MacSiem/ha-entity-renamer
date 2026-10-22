"""Static Jinja dataflow regressions; no HA runtime/configuration required."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

if 'reference_planner' not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        'reference_planner', Path(__file__).parents[1] /
        'custom_components/ha_entity_renamer/reference_planner.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
from reference_planner import Consumer, Context, Scope, Syntax, plan_references

CONTEXT = Context(Consumer.TEMPLATE, (Scope((), Syntax.JINJA),))
MAPPING = {'sensor.a': 'sensor.b'}
requires_jinja = unittest.skipUnless(importlib.util.find_spec('jinja2'), 'native Jinja parser unavailable')


def plan(source, mapping=None):
    return plan_references(source, MAPPING if mapping is None else mapping, CONTEXT)


class ParserAvailabilityTest(unittest.TestCase):
    def test_missing_native_parser_is_explicit_and_import_is_still_pure(self):
        with patch.dict(sys.modules, {'jinja2': None}):
            result = plan("{% set e = 'sensor.a' %}{{ states(e) }}")
        self.assertFalse(result.changes)
        self.assertIn('unsupported_template_parser', [m.code for m in result.markers])


@requires_jinja
class TemplateBindingsTest(unittest.TestCase):
    def assert_migrates(self, source, expected):
        result = plan(source)
        self.assertEqual(result.value, expected)
        self.assertFalse(result.markers, result.markers)
        self.assertEqual(len(result.changes), 1)

    def assert_blocked(self, source, code=None):
        result = plan(source)
        self.assertEqual(result.value, source)
        self.assertTrue(result.markers)
        if code:
            self.assertIn(code, [m.code for m in result.markers])

    def test_scalar_set_aliases_and_all_scalar_sinks(self):
        for function, extra in [('states', ''), ('is_state', ", 'on'"),
                                ('state_attr', ", 'unit'"), ('is_state_attr', ", 'unit', 'C'")]:
            source = "{% set e = 'sensor.a' %}{% set alias = e %}{{ " + function + '(alias' + extra + ') }}'
            self.assert_migrates(source, source.replace("'sensor.a'", "'sensor.b'"))

    def test_literal_and_aliased_lists_for_expand(self):
        for prefix, arg in [("{% set ids=['sensor.a', 'sensor.ab'] %}", 'ids'),
                            ("{% set ids=['sensor.a', 'sensor.ab'] %}{% set alias=ids %}", 'alias'),
                            ('', "['sensor.a', 'sensor.ab']")]:
            source = prefix + '{{ expand(' + arg + ') | list }}'
            self.assert_migrates(source, source.replace("'sensor.a'", "'sensor.b'"))

    def test_literal_loop_and_outer_list_loop(self):
        for prefix, iterable in [('', "['sensor.a', 'sensor.ab']"),
                                 ("{% set ids=['sensor.a', 'sensor.ab'] %}", 'ids')]:
            source = prefix + '{% for entity in ' + iterable + ' %}{{ states(entity) }}{% endfor %}'
            self.assert_migrates(source, source.replace("'sensor.a'", "'sensor.b'"))

    def test_nested_local_shadowing_keeps_distinct_origins(self):
        source = ("{% set e='sensor.a' %}{% for e in ['sensor.ab'] %}"
                  "{{ states(e) }}{% endfor %}{{ states(e) }}")
        self.assert_migrates(source, source.replace("'sensor.a'", "'sensor.b'"))

    def test_same_line_repeated_literal_spans_and_simultaneous_swap(self):
        source = "{% set e='sensor.a' %}{{ states(e) }}{{ states('sensor.a') }}{{ states('sensor.b') }}"
        result = plan(source, {'sensor.a': 'sensor.b', 'sensor.b': 'sensor.a'})
        self.assertEqual(result.value, "{% set e='sensor.b' %}{{ states(e) }}{{ states('sensor.b') }}{{ states('sensor.a') }}")
        self.assertFalse(result.markers)

    def test_rebinding_only_consumed_origin_is_changed(self):
        source = "{% set e='sensor.ab' %}{% set e='sensor.a' %}{{ states(e) }}"
        self.assert_migrates(source, source.replace("'sensor.a'", "'sensor.b'"))

    def test_direct_calls_inside_labels_and_custom_functions_are_not_references(self):
        for source in ['{{ "states(\'sensor.a\')" }}', "{{ custom('sensor.a') }}",
                       "{{ custom(states='sensor.a') }}", "{{ 'states.sensor.a' }}"]:
            self.assert_blocked(source, 'unsupported_reference')

    def test_shared_origin_in_label_custom_argument_comparison_or_index_blocks(self):
        for use in ['{{ e }}', '{{ custom(e) }}', "{{ e == 'sensor.ab' }}",
                    "{{ states['sensor.ab'][e] }}", "{{ states.sensor.ab[e] }}"]:
            self.assert_blocked("{% set e='sensor.a' %}{{ states(e) }}" + use,
                                'unsupported_reference')

    def test_alias_escape_blocks_earlier_and_later_consumption(self):
        for body in ['{{ states(e) }}{{ alias }}', '{{ alias }}{{ states(e) }}']:
            self.assert_blocked("{% set e='sensor.a' %}{% set alias=e %}" + body,
                                'unsupported_reference')

    def test_loop_label_and_loop_previtem_escape_block(self):
        for use in ['{{ e }}', '{{ loop.previtem }}', '{{ loop.index }}']:
            self.assert_blocked("{% for e in ['sensor.a'] %}{{ states(e) }}" + use + '{% endfor %}')

    def test_branch_merge_is_unknown_and_dynamic_rebind_remains_blocked(self):
        for source in ["{% set e='sensor.a' %}{% if flag %}{% set e=other %}{% endif %}{{ states(e) }}",
                       "{% if flag %}{% set e='sensor.a' %}{% else %}{% set e='sensor.a' %}{% endif %}{{ states(e) }}",
                       "{% set e='sensor.a' %}{% set e=other %}{{ states(e) }}"]:
            self.assert_blocked(source, 'dynamic_reference')

    def test_conditional_alias_escape_retains_possible_origins(self):
        for use in ['{{ alias }}', '{{ custom(alias) }}', '{{ expand(alias) }}']:
            source = ("{% set e='sensor.a' %}{{ states(e) }}"
                      "{% if flag %}{% set alias=e %}{% endif %}" + use)
            self.assert_blocked(source, 'unsupported_reference')

    def test_direct_branch_reads_of_known_binding_are_safe(self):
        source = "{% set e='sensor.a' %}{% if flag %}{{ states(e) }}{% else %}{{ state_attr(e,'unit') }}{% endif %}"
        self.assert_migrates(source, source.replace("'sensor.a'", "'sensor.b'"))

    def test_ha_globals_shadowed_anywhere_and_namespace_assignment_block(self):
        for source in ["{% set states=custom %}{{ states('sensor.a') }}",
                       "{% for expand in items %}{{ expand('sensor.a') }}{% endfor %}",
                       "{% set ns.entity='sensor.a' %}{{ states(ns.entity) }}",
                       "{% macro f(e) %}{{ states(e) }}{% endmacro %}{{ f('sensor.a') }}"]:
            self.assert_blocked(source, 'unsupported_template_scope')

    def test_dynamic_expressions_and_failed_expand_are_atomic(self):
        for source in ["{% set e='sensor.' ~ suffix %}{{ states(e) }}",
                       "{% set ids=['sensor.a'] %}{{ expand(ids + others) }}",
                       "{{ expand('sensor.a', dynamic) }}",
                       "{{ states('sensor.a' 'sensor.ab') }}",
                       "{{ states('sensor.a', **kwargs) }}"]:
            self.assert_blocked(source, 'dynamic_reference')

    def test_escaped_literal_refuses_source_attribution(self):
        self.assert_blocked(r"{{ states('sensor.\x61') }}", 'dynamic_reference')

    def test_unknown_states_object_access_is_dynamic_without_full_source_token(self):
        for source in ["{{ states[entity] }}", "{{ states.sensor[entity] }}",
                       "{{ states['sensor'][entity] }}"]:
            self.assert_blocked(source, 'dynamic_reference')

    def test_whitespace_control_and_quoted_delimiter(self):
        source = "x  {%- set e = 'sensor.a' -%}{{ '}}' ~ states(e) }}"
        self.assert_migrates(source, source.replace("'sensor.a'", "'sensor.b'"))

    def test_comments_raw_malformed_crlf_and_budget_have_explicit_blockers(self):
        for source in ["{# states('sensor.a') #}", "{% raw %}{{ states('sensor.a') }}{% endraw %}",
                       "{{ states('sensor.a') ", "\r\n{{ states('sensor.a') }}",
                       '{{ ' + '[' * 200 + "'sensor.a'" + ']' * 200 + ' }}']:
            self.assert_blocked(source, 'unsupported_template')
        from reference_planner import Limits
        result = plan_references("{{ states('sensor.a') }}", MAPPING, CONTEXT,
                                 limits=Limits(max_nodes=3))
        self.assertFalse(result.changes)
        self.assertIn('unsupported_template', [m.code for m in result.markers])

    def test_nested_literal_loop_origins_are_deduplicated(self):
        source = ("{% for e in ['sensor.a', 'sensor.a'] %}"
                  "{% for inner in [e, e, e] %}{{ states(inner) }}"
                  "{% endfor %}{% endfor %}")
        self.assert_migrates(source, source.replace("'sensor.a'", "'sensor.b'"))

    def test_known_reference_can_change_beside_unresolved_dynamic_sink(self):
        source = "{{ states('sensor.a') }}{{ states(entity) }}"
        result = plan(source)
        self.assertEqual(result.value, source.replace("'sensor.a'", "'sensor.b'"))
        self.assertIn('dynamic_reference', [m.code for m in result.markers])

    def test_template_functions_are_never_executed(self):
        # Explode is deliberately undefined. A renderer would fail; a parser
        # merely records the call and still recognizes the independent sink.
        source = "{% set e='sensor.a' %}{{ explode() }}{{ states(e) }}"
        self.assert_migrates(source, source.replace("'sensor.a'", "'sensor.b'"))

    def test_bare_global_alias_is_explicitly_blocked(self):
        self.assert_blocked("{% set lookup=states %}{{ lookup(entity) }}", 'dynamic_reference')


if __name__ == '__main__':
    unittest.main()
