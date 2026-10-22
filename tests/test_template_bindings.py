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

    def test_original_newlines_unicode_and_plain_labels_are_preserved(self):
        for newline in ('\n', '\r\n', '\r'):
            source = ("żółć sensor.a" + newline + "{% set e='sensor.a' %}" + newline
                      + "{{ states(e) }}" + newline + "{{ states.sensor.a.state }}")
            expected = source.replace("e='sensor.a'", "e='sensor.b'").replace('states.sensor.a', 'states.sensor.b')
            self.assert_migrates(source, expected)

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
        for use in ['{{ e }}', '{{ loop.previtem }}', '{{ loop.nextitem }}']:
            self.assert_blocked("{% for e in ['sensor.a'] %}{{ states(e) }}" + use + '{% endfor %}')

    def test_native_loopcontrols_and_safe_counter_attributes(self):
        source = ("{% for e in ['sensor.a','sensor.c'] %}"
                  "{% if loop.first %}{% continue %}{% endif %}"
                  "{{ states(e) }}{% break %}{% endfor %}")
        self.assert_migrates(source, source.replace("'sensor.a'", "'sensor.b'"))
        for attribute in ('index', 'index0', 'revindex', 'revindex0',
                          'first', 'last', 'length', 'depth', 'depth0'):
            source = ("{% for e in ['sensor.a'] %}{{ loop." + attribute
                      + " }}{{ states(e) }}{% endfor %}")
            self.assert_migrates(source, source.replace("'sensor.a'", "'sensor.b'"))

    def test_do_statement_consumes_only_proven_ha_calls(self):
        source = "{% set e='sensor.a' %}{% do states(e) %}{{ states(e) }}"
        self.assert_migrates(source, source.replace("'sensor.a'", "'sensor.b'"))
        self.assert_blocked("{% set e='sensor.a' %}{% do custom(e) %}{{ states(e) }}",
                            'unsupported_reference')

    def test_with_uses_outer_rhs_and_never_leaks_local_bindings(self):
        source = ("{% set e='sensor.c' %}{% with e='sensor.a', alias=e %}"
                  "{{ states(e) }}{{ states(alias) }}{% endwith %}{{ states(e) }}")
        self.assert_migrates(source, source.replace("'sensor.a'", "'sensor.b'"))
        source = "{% with e='sensor.a', alias=e %}{{ states(alias) }}{% endwith %}"
        self.assert_blocked(source, 'dynamic_reference')
        self.assert_blocked("{% with e='sensor.a' %}{{ states(e) }}{{ e }}{% endwith %}",
                            'unsupported_reference')

    def test_loop_name_bound_to_entity_values_is_not_native_loop_metadata(self):
        source = "{% set loop=['sensor.a'] %}{{ expand(loop) }}{{ loop.index }}"
        self.assert_blocked(source, 'unsupported_reference')

    def test_branch_merge_is_unknown_and_dynamic_rebind_remains_blocked(self):
        for source in ["{% set e='sensor.a' %}{% if flag %}{% set e=other %}{% endif %}{{ states(e) }}",
                       "{% set e='sensor.a' %}{% set e=other %}{{ states(e) }}"]:
            self.assert_blocked(source, 'dynamic_reference')

    def test_exhaustive_branches_join_known_entity_origins(self):
        for source in [
            "{% if flag %}{% set e='sensor.a' %}{% else %}{% set e='sensor.c' %}{% endif %}{{ states(e) }}",
            "{% set e=other %}{% if flag %}{% set e='sensor.a' %}{% else %}{% set e='sensor.c' %}{% endif %}{{ states(e) }}",
            "{% if flag %}{% set e='sensor.a' %}{% elif other %}{% set e='sensor.c' %}{% else %}{% set e='sensor.a' %}{% endif %}{{ states(e) }}",
            "{% set e='sensor.a' %}{% if flag %}{% set e='sensor.c' %}{% endif %}{{ states(e) }}",
            "{% if flag %}{% set e=['sensor.a'] %}{% else %}{% set e=['sensor.c'] %}{% endif %}{{ expand(e) | list }}",
        ]:
            self.assert_migrates(source, source.replace("'sensor.a'", "'sensor.b'"))

    def test_branch_shape_or_missing_origin_cannot_authorize_migration(self):
        for source in [
            "{% if flag %}{% set e='sensor.a' %}{% endif %}{{ states(e) }}",
            "{% if flag %}{% set e='sensor.a' %}{% else %}{% set e=['sensor.a'] %}{% endif %}{{ expand(e) }}",
            "{% if flag %}{% set e='sensor.a' %}{% else %}{% set e=other %}{% endif %}{{ states(e) }}",
        ]:
            self.assert_blocked(source, 'dynamic_reference')

    def test_exhaustive_branch_alias_escape_blocks_all_possible_origins(self):
        source = ("{% if flag %}{% set e='sensor.a' %}{% else %}{% set e='sensor.c' %}{% endif %}"
                  "{{ states(e) }}{{ e }}")
        self.assert_blocked(source, 'unsupported_reference')

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

    def test_numeric_namespace_accumulators_consume_references_without_storing_ids(self):
        source = ("{% set ns=namespace(total=0) %}{% for e in ['sensor.a'] %}"
                  "{% set ns.total=ns.total+(states(e)|int) %}{% endfor %}{{ ns.total }}")
        self.assert_migrates(source, source.replace("'sensor.a'", "'sensor.b'"))
        for source in ["{% set e='sensor.a' %}{% set ns=namespace(ref=e) %}{{ states(e) }}",
                       "{% set e='sensor.a' %}{% set ns.ref=e %}{{ states(e) }}",
                       "{% set ns.refs=['sensor.a'] %}{{ expand(ns.refs) }}"]:
            self.assert_blocked(source)

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

    def test_comments_raw_malformed_and_budget_have_explicit_blockers(self):
        for source in ["{# states('sensor.a') #}", "{% raw %}{{ states('sensor.a') }}{% endraw %}",
                       "{{ states('sensor.a') ",
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



@requires_jinja
class ExternalYAMLBindingTests(unittest.TestCase):
    def test_static_map_keys_and_items_preserve_key_provenance(self):
        for source in ('{% for e in refs.keys() %}{{ states(e) }}{% endfor %}',
                       '{% for e, meta in refs.items() %}{{ states(e) }} {{ meta.label }}{% endfor %}',
                       '{{ expand(refs.keys()) }}'):
            result = self.analyze(source, shape='keys')
            self.assertFalse(result[1], source)
            self.assertEqual(result[2], {self.origin})
            self.assertFalse(result[3])

    def test_map_items_key_escape_and_unknown_metadata_sinks_block(self):
        for source in ('{% for e, meta in refs.items() %}{{ states(e) }} {{ e }}{% endfor %}',
                       '{% for e, meta in refs.items() %}{{ states(e) }} {{ loop.previtem }}{% endfor %}',
                       '{% for e, meta in refs.items() %}{{ states(meta.entity) }}{% endfor %}',
                       '{% for e, e in refs.items() %}{{ states(e) }}{% endfor %}',
                       '{{ refs.items() }}'):
            result = self.analyze(source, shape='keys')
            self.assertIn(self.origin, result[3], source)

    def test_items_method_on_non_map_or_with_arguments_is_unknown(self):
        for shape in ('scalar', 'list'):
            result = self.analyze('{% for e, meta in refs.items() %}{{ states(e) }}{% endfor %}', shape=shape)
            self.assertIn(self.origin, result[3])
        result = self.analyze('{% for e in refs.keys(1) %}{{ states(e) }}{% endfor %}', shape='keys')
        self.assertIn(self.origin, result[3])

    def analyze(self, source, *, shape='scalar', field=False):
        from reference_planner import analyze_template_bindings
        self.origin = (-1, 0, 'sensor.a')
        return analyze_template_bindings(source, MAPPING,
                                        {'refs': (shape, (self.origin,))},
                                        output_reference=field)

    def test_external_scalar_has_no_editable_template_span(self):
        source = "{{ states(refs) }}"
        value, markers, used, unsafe = self.analyze(source)
        self.assertEqual(value, source)
        self.assertFalse(markers)
        self.assertEqual(used, {self.origin})
        self.assertFalse(unsafe)

    def test_external_list_and_key_iteration(self):
        for shape in ('list', 'keys'):
            result = self.analyze('{% for e in refs %}{{ states(e) }}{% endfor %}', shape=shape)
            self.assertFalse(result[1])
            self.assertEqual(result[2], {self.origin})
            self.assertFalse(result[3])

    def test_direct_field_output_requires_one_complete_expression(self):
        safe = self.analyze('{{ refs }}', shape='list', field=True)
        self.assertEqual(safe[2], {self.origin})
        self.assertFalse(safe[3])
        for source in ('{{ refs }} suffix', '{% for e in refs %}{{ e }}{% endfor %}'):
            result = self.analyze(source, shape='list', field=True)
            self.assertIn(self.origin, result[3])

    def test_with_and_numeric_namespace_preserve_external_entity_origins(self):
        for source in ["{% with x=refs %}{{ states(x) }}{% endwith %}",
                       "{% set ns=namespace(total=0) %}{% set ns.total=states(refs)|int %}{{ ns.total }}"]:
            value, markers, used, unsafe = self.analyze(source)
            self.assertEqual(value, source)
            self.assertFalse(markers)
            self.assertEqual(used, {self.origin})
            self.assertFalse(unsafe)

    def test_non_reference_escape_and_unknown_scope_retain_origins(self):
        for source in ("{{ states(refs) }} {{ refs }}",
                       "{{ refs | join(',') }}", "{{ custom(refs) }}", "{# refs #}{{ states(refs) }}"):
            result = self.analyze(source)
            self.assertIn(self.origin, result[3], source)

    def test_static_key_map_is_not_an_expand_list(self):
        result = self.analyze('{{ expand(refs) }}', shape='keys')
        self.assertTrue(result[1])
        self.assertIn(self.origin, result[3])

    def test_external_union_of_scalar_origins_is_one_pathwise_scalar(self):
        from reference_planner import analyze_template_bindings
        first, second = (-1, 0, 'sensor.a'), (-2, 0, 'sensor.ab')
        result = analyze_template_bindings('{{ states(ref) }}', MAPPING,
                                           {'ref': ('scalar', (first, second))})
        self.assertFalse(result[1])
        self.assertEqual(result[2], {first, second})
        self.assertFalse(result[3])

    def test_unknown_external_union_preserves_every_origin_as_unsafe(self):
        from reference_planner import analyze_template_bindings
        first, second = (-1, 0, 'sensor.a'), (-2, 0, 'sensor.ab')
        for source in ('{{ states(ref) }}', '{{ ref }}'):
            result = analyze_template_bindings(source, MAPPING,
                                               {'ref': ('unknown', (first, second))})
            self.assertFalse(result[2])
            self.assertEqual(result[3], {first, second})

if __name__ == '__main__':
    unittest.main()
