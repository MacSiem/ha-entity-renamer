"""Synthetic unit cases; owner runs through the host test wrapper."""
import unittest
import importlib.util
from pathlib import Path
import sys
spec = importlib.util.spec_from_file_location('reference_planner', Path(__file__).parents[1] / 'custom_components/ha_entity_renamer/reference_planner.py')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

from reference_planner import (
    Consumer, Context, Limits, PlanError, Scope, Syntax, plan_references,
)


def refs(*paths, consumer=Consumer.AUTOMATION):
    return Context(consumer, tuple(Scope(path, Syntax.ENTITY_TREE) for path in paths))


def jinja(path=()):
    return Context(Consumer.TEMPLATE, (Scope(path, Syntax.JINJA),))


class ReferencePlannerTest(unittest.TestCase):
    def test_local_binding_preserves_global_static_reference_migration(self):
        for source in ("{% set reading = states('sensor.a') %}{{ reading }}",
                       "{% for item in items %}{{ states('sensor.a') }}{% endfor %}",
                       "{% set text %}{{ states.sensor.a.state }}{% endset %}{{ text }}",
                       "{% for key, value in items %}{{ states('sensor.a') }}{% endfor %}"):
            plan = plan_references(source, {'sensor.a': 'sensor.b'}, jinja())
            self.assertFalse(plan.markers, plan.markers)
            self.assertEqual(plan.value, source.replace('sensor.a', 'sensor.b'))

    def test_binding_shadowing_and_namespace_assignment_are_blocked(self):
        for source in ("{% set foo, states = pair %}{{ states('sensor.a') }}",
                       "{% set ns.states = lookup %}{{ states('sensor.a') }}",
                       "{% for foo, expand in pairs %}{{ expand('sensor.a') }}{% endfor %}"):
            plan = plan_references(source, {'sensor.a': 'sensor.b'}, jinja())
            self.assertEqual(plan.value, source)
            self.assertIn('unsupported_template_scope', [m.code for m in plan.markers])

    def test_static_bracket_reference_preserves_attribute_and_format(self):
        source = "{{ states [ 'sensor.a' ].state }}"
        plan = plan_references(source, {'sensor.a': 'sensor.b'}, jinja())
        self.assertEqual(plan.value, "{{ states [ 'sensor.b' ].state }}")
        self.assertFalse(plan.markers)

    def test_expand_all_static_arguments_and_list_without_cascading(self):
        for source in ("{{ expand('sensor.a', 'sensor.b') }}",
                       "{{ expand(['sensor.a', 'sensor.b',]) | list }}"):
            plan = plan_references(source, {'sensor.a': 'sensor.b', 'sensor.b': 'sensor.c'}, jinja())
            self.assertEqual(plan.value, source.replace('sensor.b', 'sensor.c').replace('sensor.a', 'sensor.b'))
            self.assertFalse(plan.markers)

    def test_expand_dynamic_or_malformed_arguments_remain_blocked(self):
        for source in ("{{ expand('sensor.a', others) }}", "{{ expand(['sensor.a'] + others) }}",
                       "{{ expand('sensor.a' ~ suffix) }}", "{{ expand('sensor.a' 'sensor.b') }}"):
            plan = plan_references(source, {'sensor.a': 'sensor.b'}, jinja())
            self.assertEqual(plan.value, source)
            self.assertIn('dynamic_reference', [m.code for m in plan.markers])

    def test_bracket_dynamic_and_quoted_code_not_rewritten(self):
        for source in ("{{ states['sensor.' ~ suffix] }}", '''{{ "states['sensor.a']" }}'''):
            plan = plan_references(source, {'sensor.a': 'sensor.b'}, jinja())
            self.assertEqual(plan.value, source)
            self.assertTrue(plan.markers)

    def test_arithmetic_on_static_reading_is_not_a_dynamic_entity_reference(self):
        source = "{{ states('sensor.a') | float + 2 }}"
        plan = plan_references(source, {'sensor.a': 'sensor.b'}, jinja())
        self.assertEqual(plan.value, source.replace('sensor.a', 'sensor.b'))
        self.assertFalse(plan.markers)

    def test_shadowed_states_function_is_not_blindly_rewritten(self):
        source = "{% set states = custom_lookup %}{{ states('sensor.a') }}"
        plan = plan_references(source, {'sensor.a': 'sensor.b'}, jinja())
        self.assertEqual(plan.value, source)
        self.assertTrue(plan.markers)

    def test_nested_simultaneous_swaps_keys_order_types_no_mutation(self):
        source = {"sensor.a": ["sensor.b", {"ref": "sensor.a"}], "meta": [1, True, None, 2.5]}
        plan = plan_references(source, {"sensor.a": "sensor.b", "sensor.b": "sensor.a"}, refs(()))
        self.assertEqual(list(plan.value), ["sensor.b", "meta"])
        self.assertEqual(plan.value["sensor.b"], ["sensor.a", {"ref": "sensor.b"}])
        self.assertEqual(source["sensor.a"][0], "sensor.b")
        self.assertIsNot(plan.value["meta"], source["meta"])
        self.assertIs(type(plan.value["meta"][1]), bool)
        self.assertEqual([c.location for c in plan.changes], ["key", "value", "value"])
        self.assertEqual(plan.changes[1].path, ("sensor.a", 0))

    def test_chain_never_cascades(self):
        plan = plan_references(["sensor.a", "sensor.b"], {"sensor.a": "sensor.b", "sensor.b": "sensor.c"}, refs(()))
        self.assertEqual(plan.value, ["sensor.b", "sensor.c"])

    def test_existing_target_key_collision_is_atomic_failure(self):
        source = {"sensor.a": 1, "sensor.b": 2}
        with self.assertRaisesRegex(PlanError, "collision"):
            plan_references(source, {"sensor.a": "sensor.b"}, refs(()))
        self.assertEqual(source, {"sensor.a": 1, "sensor.b": 2})

    def test_template_literals_state_objects_and_token_boundaries(self):
        source = "{{ states('sensor.a') }} {{ states.sensor.a.state }} {{ states('sensor.ab') }}"
        plan = plan_references(source, {"sensor.a": "sensor.b"}, jinja())
        self.assertEqual(plan.value, "{{ states('sensor.b') }} {{ states.sensor.b.state }} {{ states('sensor.ab') }}")
        self.assertEqual(plan.markers, ())

    def test_template_swap_replacements_use_original_spans(self):
        source = "{{ is_state('sensor.a', 'on') and state_attr('sensor.b', 'unit') }}"
        plan = plan_references(source, {"sensor.a": "sensor.b", "sensor.b": "sensor.a"}, jinja())
        self.assertEqual(plan.value, "{{ is_state('sensor.b', 'on') and state_attr('sensor.a', 'unit') }}")

    def test_no_global_arbitrary_text_replacement(self):
        source = {"target": {"entity_id": "sensor.a"}, "description": "sensor.a", "url": "https://example.invalid/sensor.a"}
        plan = plan_references(source, {"sensor.a": "sensor.b"}, refs(("target", "entity_id")))
        self.assertEqual(plan.value["target"]["entity_id"], "sensor.b")
        self.assertEqual(plan.value["description"], "sensor.a")
        self.assertEqual(plan.value["url"], source["url"])
        self.assertEqual(plan.coverage, "explicit_scopes_only")

    def test_url_and_script_in_templates_are_not_rewritten(self):
        for source in ("https://example.invalid/{{ states('sensor.a') }}", "<script>{{ states('sensor.a') }}</script>"):
            plan = plan_references(source, {"sensor.a": "sensor.b"}, jinja())
            self.assertEqual(plan.value, source)
            self.assertEqual(plan.markers[0].code, "external_content")

    def test_dynamic_and_unknown_references_are_marked(self):
        source = "{{ states('sensor.' ~ name) }} {{ custom('sensor.a') }}"
        plan = plan_references(source, {"sensor.a": "sensor.b"}, jinja())
        self.assertEqual(plan.value, source)
        self.assertEqual({m.code for m in plan.markers}, {"dynamic_reference", "unsupported_reference"})

    def test_known_reference_can_change_while_dynamic_requires_review(self):
        plan = plan_references("{{ states('sensor.a') + states(entity) }}", {"sensor.a": "sensor.b"}, jinja())
        self.assertIn("states('sensor.b')", plan.value)
        self.assertIn("dynamic_reference", [m.code for m in plan.markers])

    def test_strings_containing_code_raw_and_comments_are_not_code(self):
        for source in ('''{{ "states.sensor.a" }}''', "{% raw %}{{ states('sensor.a') }}{% endraw %}", "{# {{ states('sensor.a') }} #}"):
            plan = plan_references(source, {"sensor.a": "sensor.b"}, jinja())
            self.assertEqual(plan.value, source)
            self.assertTrue(plan.markers)

    def test_plain_template_text_and_exact_tree_embedded_tokens(self):
        for context in (jinja(), refs(())):
            plan = plan_references("sensor.a is a label", {"sensor.a": "sensor.b"}, context)
            self.assertEqual(plan.value, "sensor.a is a label")
            self.assertEqual(plan.markers[0].code, "unsupported_reference")
        plan = plan_references("sensor.ab", {"sensor.a": "sensor.b"}, refs(()))
        self.assertEqual(plan.markers, ())

    def test_quoted_template_delimiters_and_unterminated_blocks(self):
        source = '''{{ '}}' ~ states('sensor.a') }}'''
        plan = plan_references(source, {"sensor.a": "sensor.b"}, jinja())
        self.assertIn("states('sensor.b')", plan.value)
        malformed = "{{" * 1000
        plan = plan_references(malformed, {"sensor.a": "sensor.b"}, jinja())
        self.assertEqual(plan.value, malformed)
        self.assertEqual(plan.markers[0].code, "unsupported_template")

    def test_quoted_call_text_is_unchanged(self):
        source = '''{{ "states('sensor.a')" }}'''
        plan = plan_references(source, {"sensor.a": "sensor.b"}, jinja())
        self.assertEqual(plan.value, source)
        self.assertIn("unsupported_reference", [m.code for m in plan.markers])

    def test_loop_bindings_do_not_rewrite_local_states(self):
        source = "{% for states in items %}{{ states.sensor.a }}{% endfor %}"
        plan = plan_references(source, {"sensor.a": "sensor.b"}, jinja())
        self.assertEqual(plan.value, source)
        self.assertIn("unsupported_template_scope", [m.code for m in plan.markers])

    def test_spaced_attribute_access_is_migrated(self):
        for source in ("{{ states . sensor . a.state }}", "{{ states.sensor . a }}",
                       "{{ states .sensor.a }}"):
            plan = plan_references(source, {"sensor.a": "sensor.b"}, jinja())
            self.assertFalse(plan.markers)
            self.assertEqual(len(plan.changes), 1)
            self.assertNotIn(". a", plan.value)
            self.assertIn("sensor.b", plan.value)

    def test_each_consumer_requires_explicit_supported_scopes(self):
        for consumer in Consumer:
            plan = plan_references({"entity": "sensor.a"}, {"sensor.a": "sensor.b"}, refs(("entity",), consumer=consumer))
            self.assertEqual(plan.value["entity"], "sensor.b")
        with self.assertRaises(PlanError):
            plan_references("{{ states('sensor.a') }}", {}, Context(Consumer.ENERGY, (Scope((), Syntax.JINJA),)))

    def test_invalid_mapping_domain_duplicate_target_and_injection(self):
        for mapping in (
            {"sensor.a": "light.a"},
            {"sensor.a": "sensor.c", "sensor.b": "sensor.c"},
            {"sensor.a": "sensor.x; __import__('os')"},
            {"sensor.a": "sensor.ümlaut"},
            {"../sensor.a": "sensor.b"},
            {1: "sensor.b"},
        ):
            with self.subTest(mapping=mapping), self.assertRaises(PlanError):
                plan_references({}, mapping, refs(()))

    def test_unselected_tree_is_still_copied_and_validated(self):
        source = {"entity": ["sensor.a"]}
        plan = plan_references(source, {"sensor.a": "sensor.b"}, refs())
        self.assertEqual(plan.value, source)
        self.assertIsNot(plan.value["entity"], source["entity"])
        self.assertEqual(plan.markers[0].code, "no_supported_scopes")

    def test_missing_overlapping_or_untyped_scope_fails(self):
        for context in (
            refs(("missing",)), refs((), ("entity",)),
            Context("automation", ()),
            Context(Consumer.AUTOMATION, (Scope((), "entity_tree"),)),
        ):
            with self.subTest(context=context), self.assertRaises(PlanError):
                plan_references({"entity": "sensor.a"}, {}, context)

    def test_cycles_depth_nodes_strings_and_custom_objects_fail(self):
        cycle = []; cycle.append(cycle)
        deep = "sensor.a"
        for _ in range(8):
            deep = [deep]
        cases = [
            (cycle, Limits()), (deep, Limits(max_depth=4)),
            ([1, 2, 3], Limits(max_nodes=2)),
            ("too long", Limits(max_string_length=3)),
            ({"long_key": 1}, Limits(max_string_length=3)),
            (object(), Limits()), ({False: 1}, Limits()),
        ]
        for value, limits in cases:
            with self.subTest(value_type=type(value)), self.assertRaises(PlanError):
                plan_references(value, {}, refs(()), limits=limits)

    def test_deterministic_and_idempotent_for_acyclic_mapping(self):
        source = {"refs": ["sensor.a"], "id": 1}
        mapping = {"sensor.a": "sensor.b"}
        first = plan_references(source, mapping, refs(("refs",)))
        self.assertEqual(first, plan_references(source, mapping, refs(("refs",))))
        self.assertEqual(plan_references(first.value, mapping, refs(("refs",))).changes, ())


if __name__ == "__main__":
    unittest.main()
