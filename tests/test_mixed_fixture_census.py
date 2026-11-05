"""Bounded synthetic preflight for the concrete native caller census."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from mixed_fixture_census import _observed_consumers, _plan_graph, _walk, audit, census_coverage, consumer_evidence, fresh_inverse_after_apply, refs


def sample():
    source = 'sensor.producer'
    return {'configuration': {'automation': [], 'script': {}, 'template': []},
        'source': source, 'graph': ['configuration.yaml', 'template.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml'],
        'source_specs': {'automation': {'observer': [source]}, 'script': {'caller': [source]},
                         'template': {'sensor:scope-view': [source]}},
        'bindings': {'automation': {'observer': {'entity_id': 'automation.observer', 'refs': [source], 'blueprint': False}},
                     'script': {'caller': {'entity_id': 'script.caller', 'refs': [source], 'blueprint': False}},
                     'template': {'sensor:scope-view': {'entity_id': 'sensor.view', 'refs': [], 'blueprint': False}}},
        'entries': [], 'loaded_components': ['automation', 'script', 'template', 'sensor'],
        'network': {'interfaces': ['lo'], 'interfaces_up': ['lo'], 'processes': ['python', 'sleep'],
                    'foreign_processes': [], 'nonloopback_sockets': 0, 'loopback_listeners': 1,
                    'loopback_connections': 0, 'unix_sockets': 0, 'unreadable': False},
        'dashboards': [], 'observed_consumers': ['automation:observer', 'script:caller', 'template:sensor:scope-view'],
        'samples': [], 'outside_consumers': [], 'inactive_blueprints': [], 'collection_errors': []}


class MixedFixtureCensusTests(unittest.TestCase):
    def test_empty_external_scope_and_bindings_are_derived_from_facts(self):
        observed = sample()
        observed['configuration'] = {'automation': [{'entity_id': 'sensor.producer'}],
                                     'script': {'caller': {'entity_id': 'sensor.producer'}},
                                     'template': [{'state': "{{ states('sensor.producer') }}"}]}
        json.dumps(observed, allow_nan=False)
        result = audit(observed)
        self.assertEqual([], result['unverified'])
        self.assertTrue(result['complete'])
        self.assertEqual([], result['external']['outside_consumers'])

    def test_unobserved_active_map_and_unknown_external_are_not_empty(self):
        observed = sample()
        observed['dashboards'] = [{'route': 'map', 'mode': 'storage', 'path': '.storage/lovelace',
                                   'status': 'COMPLETE', 'cards': [{'type': 'map', 'refs': ['sensor.producer']}]}]
        observed['loaded_components'].append('mqtt')
        observed['network']['nonloopback_sockets'] = 1
        result = audit(observed)
        self.assertFalse(result['complete'])
        self.assertIn(('dashboard:map', 'active_card_render_not_observed:map'), result['unverified'])
        self.assertIn(('integration:mqtt', 'unclassified_loaded_integration'), result['unverified'])
        self.assertIn(('external', 'network_endpoints_present'), result['unverified'])

    def test_unclassified_live_entity_prevents_empty_external_inventory(self):
        observed = sample()
        observed['runtime_entities'] = ['sensor.producer', 'sensor.view', 'automation.observer',
                                        'script.caller', 'sensor.unknown_consumer']
        result = audit(observed)
        self.assertIn(('runtime:sensor.unknown_consumer', 'unclassified_loaded_entity'), result['unverified'])
        self.assertFalse(result['external']['empty_in_bounded_fixture'])
        families, external = census_coverage(result, {'automation': 'UNKNOWN'})
        self.assertEqual(families['automation'], 'UNKNOWN')
        self.assertEqual(external['independent_census'], 'UNKNOWN:fixture_not_complete')

    def test_consumer_checks_use_both_census_samples_and_inactive_blueprint_proof(self):
        observed = sample()
        observed['configuration'] = {'automation': [{'entity_id': 'sensor.producer'}],
                                     'script': {'caller': {'entity_id': 'sensor.producer'}},
                                     'template': [{'state': "{{ states('sensor.producer') }}"}]}
        observed['samples'] = [{'automation': ['5'], 'script': ['5'], 'template': {'sensor:scope-view': '10'}},
                               {'automation': ['13'], 'script': ['13'], 'template': {'sensor:scope-view': '26'}}]
        observed['inactive_blueprints'] = ['blueprints/template/homeassistant/unused.yaml']
        first = audit(observed)
        second = copy.deepcopy(first)
        second['samples'][1]['script'] = ['wrong']
        inventory, checks, bindings = consumer_evidence(first, second)
        script = next(check for check in checks if check.name == 'script:caller')
        self.assertNotEqual(script.original, script.observed)
        self.assertEqual(['inactive:blueprints/template/homeassistant/unused.yaml'],
                         bindings['blueprints/template/homeassistant/unused.yaml'])
        self.assertEqual(inventory['script:caller'], 'SUPPORTED')
        second['complete'] = False
        with self.assertRaises(ValueError):
            consumer_evidence(first, second)

    def test_binary_binding_requires_on_then_off_in_every_phase(self):
        observed = sample()
        observed['configuration'] = {'automation': [{'entity_id': 'sensor.producer'}],
                                     'script': {'mixed_source': {'entity_id': 'sensor.producer'}},
                                     'template': [{'state': "{{ states('sensor.producer') }}"}]}
        observed['source_specs']['script'] = {'mixed_source': ['sensor.producer']}
        observed['bindings']['script'] = {'mixed_source': {'entity_id': 'script.mixed_source',
            'refs': ['sensor.producer'], 'blueprint': False}}
        observed['source_specs']['template']['binary_sensor:scope-value'] = ['sensor.producer']
        observed['bindings']['template']['binary_sensor:scope-value'] = {
            'entity_id': 'binary_sensor.view', 'refs': [], 'blueprint': False}
        observed['samples'] = [
            {'source': 5, 'automation': ['5'], 'script': ['5'],
             'template': {'sensor:scope-view': '10', 'binary_sensor:scope-value': 'on'}},
            {'source': 13, 'automation': ['13'], 'script': ['13'],
             'template': {'sensor:scope-view': '26', 'binary_sensor:scope-value': 'off'}}]
        observed['observed_consumers'] = _observed_consumers(observed['bindings'], observed['samples'])
        good = audit(observed)
        self.assertTrue(good['complete'], good['unverified'])
        self.assertIn('template:binary_sensor:scope-value', good['observed_consumers'])
        for phase in ('before', 'after', 'restored'):
            for failure, states in (('always_off', ('off', 'off')), ('stale_on', ('on', 'on'))):
                with self.subTest(phase=phase, failure=failure):
                    bad = copy.deepcopy(observed)
                    for item, state in zip(bad['samples'], states, strict=True):
                        item['template']['binary_sensor:scope-value'] = state
                    bad['observed_consumers'] = _observed_consumers(bad['bindings'], bad['samples'])
                    result = audit(bad)
                    self.assertFalse(result['complete'])
                    self.assertIn(('template:binary_sensor:scope-value', 'behavior_not_observed'),
                                  result['unverified'])
                    with self.assertRaises(ValueError):
                        consumer_evidence(good, result)
        for bad_samples in ([], observed['samples'][:1], list(reversed(observed['samples']))):
            self.assertEqual([], _observed_consumers(observed['bindings'], bad_samples))

    def test_template_entity_identity_must_match_across_phases(self):
        observed = sample()
        observed['configuration'] = {'automation': [{'entity_id': 'sensor.producer'}],
                                     'script': {'caller': {'entity_id': 'sensor.producer'}},
                                     'template': [{'state': "{{ states('sensor.producer') }}"}]}
        observed['samples'] = [{'automation': ['5'], 'script': ['5'], 'template': {'sensor:scope-view': '10'}},
                               {'automation': ['13'], 'script': ['13'], 'template': {'sensor:scope-view': '26'}}]
        original = audit(observed)
        changed = copy.deepcopy(original)
        changed['bindings']['template']['sensor:scope-view']['entity_id'] = 'sensor.other'
        with self.assertRaisesRegex(ValueError, 'template consumer entity identity changed'):
            consumer_evidence(original, changed)

    def test_outside_consumer_and_missing_loaded_binding_are_reported(self):
        observed = sample()
        observed['outside_consumers'] = ['unselected.yaml']
        observed['bindings']['script']['caller']['refs'] = ['sensor.foreign']
        result = audit(observed)
        self.assertIn(('outside:unselected.yaml', 'reference_outside_active_graph'), result['unverified'])
        self.assertIn(('script:caller', 'source_runtime_references_mismatch'), result['unverified'])
        self.assertFalse(result['complete'])

    def test_disabled_or_unloaded_entry_with_reference_is_not_assumed_irrelevant(self):
        observed = sample()
        observed['entries'] = [{'id': 'entry', 'domain': 'foreign', 'state': 'not_loaded',
                                'live_entity_ids': [], 'refs': ['sensor.producer'], 'dynamic': False,
                                'registry_count': 1, 'enabled_count': 0}]
        self.assertIn(('config_entry:entry', 'configured_reference_requires_integration_adapter'),
                      audit(observed)['unverified'])

    def test_active_blueprint_or_unobserved_behavior_is_not_certified(self):
        observed = sample()
        observed['bindings']['automation']['observer']['blueprint'] = True
        observed['observed_consumers'].remove('script:caller')
        result = audit(observed)
        self.assertIn(('automation:observer', 'active_blueprint_unverified'), result['unverified'])
        self.assertIn(('script:caller', 'behavior_not_observed'), result['unverified'])

    def test_bounded_recursion_and_safe_serialization(self):
        nested = value = {}
        for _ in range(52):
            nested['more'] = {}
            nested = nested['more']
        errors = []
        detached = _walk(value, errors)
        json.dumps(detached, allow_nan=False)
        self.assertTrue(any(reason == 'source_complexity_limit' for _, reason in errors))
        self.assertEqual(['sensor.source'], refs({'value': "{{ states('sensor.source') }}"}))

    def test_native_yaml_string_subclasses_are_normalized(self):
        class MarkedString(str):
            pass
        class MarkedList(list):
            pass
        errors = []
        value = _walk({MarkedString('entity_id'): MarkedList([MarkedString('sensor.source')])}, errors)
        self.assertEqual({'entity_id': ['sensor.source']}, value)
        self.assertEqual([], errors)
        json.dumps(value, allow_nan=False)

    def test_graph_entrypoint_type_and_argument_are_bound(self):
        calls = []
        def planner(root, mapping, *, yaml_dashboards):
            calls.append((root, mapping, yaml_dashboards))
            return 'result'
        self.assertEqual('result', _plan_graph(planner, '/owned', {'sensor.a': 'sensor.b'}, ['board.yaml']))
        self.assertEqual([('/owned', {'sensor.a': 'sensor.b'}, ('board.yaml',))], calls)
        with self.assertRaises(ValueError):
            _plan_graph(planner, '/owned', {}, [None])


class FreshInverseTest(unittest.IsolatedAsyncioTestCase):
    async def test_exception_or_unknown_never_replays_or_blindly_inverts(self):
        for applied in (None, {'state': 'UNKNOWN'}):
            runtime = SimpleNamespace(recovery_inspect=AsyncMock(return_value={'state': 'UNKNOWN', 'eligible': False}),
                                      native_yaml_reverse=AsyncMock(), native_yaml_apply=AsyncMock())
            inspected, inverse = await fresh_inverse_after_apply(runtime, object(), 'op', 'fingerprint', applied, None)
            self.assertEqual(inspected['state'], 'UNKNOWN')
            self.assertIsNone(inverse)
            self.assertEqual(runtime.recovery_inspect.await_count, 1)
            self.assertEqual(runtime.native_yaml_reverse.await_count, 0)
            self.assertEqual(runtime.native_yaml_apply.await_count, 0)

    async def test_fresh_proven_applied_after_exception_permits_one_inverse(self):
        runtime = SimpleNamespace(recovery_inspect=AsyncMock(return_value={'state': 'applied', 'eligible': True}),
                                  native_yaml_reverse=AsyncMock(return_value={'state': 'rolled_back'}),
                                  native_yaml_apply=AsyncMock())
        inspected, inverse = await fresh_inverse_after_apply(runtime, object(), 'op', 'fingerprint', None, None)
        self.assertEqual(inspected['state'], 'applied')
        self.assertEqual(inverse['state'], 'rolled_back')
        self.assertEqual(runtime.recovery_inspect.await_count, 1)
        self.assertEqual(runtime.native_yaml_reverse.await_count, 1)
        self.assertEqual(runtime.native_yaml_apply.await_count, 0)

    async def test_reported_applied_but_ineligible_does_not_inverse(self):
        runtime = SimpleNamespace(recovery_inspect=AsyncMock(return_value={'state': 'UNKNOWN', 'eligible': False}),
                                  native_yaml_reverse=AsyncMock(), native_yaml_apply=AsyncMock())
        with self.assertRaises(RuntimeError):
            await fresh_inverse_after_apply(runtime, object(), 'op', 'fingerprint', {'state': 'applied'}, None)
        self.assertEqual(runtime.recovery_inspect.await_count, 1)
        self.assertEqual(runtime.native_yaml_reverse.await_count, 0)
        self.assertEqual(runtime.native_yaml_apply.await_count, 0)


if __name__ == '__main__':
    unittest.main()
