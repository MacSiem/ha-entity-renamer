"""Owner-only native checker/profile regression. Worker authored; NOT RUN.

Run on both pinned native Core versions through the owner's FIFO harness.
These cases prove source semantics only, never selective runtime activation.
"""
import copy
import hashlib
import importlib
import json
import unittest
import test_native_helper_quiescence as fixture


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'requires actual pinned HA')
class NativeTemplateStateSourceProfileTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.NativeHelperQuiescenceTests.asyncSetUp
    _before_start = fixture.NativeHelperQuiescenceTests._before_start
    _flush_documents = fixture.NativeHelperQuiescenceTests._flush_documents

    def module(self, name):
        return importlib.import_module(fixture.fixtures.PACKAGE + '.' + name)

    async def checked(self, text):
        from homeassistant.config import (async_hass_config_yaml,
            async_process_component_and_handle_errors)
        from homeassistant.loader import async_get_integration
        root = self.f.root
        (root / 'configuration.yaml').write_text('template: !include template.yaml\n')
        (root / 'template.yaml').write_text(text)
        config = await async_hass_config_yaml(self.f.hass)
        result = await async_process_component_and_handle_errors(self.f.hass, config,
            await async_get_integration(self.f.hass, 'template'))
        self.assertIsNotNone(result, 'fixture must be accepted by actual native schema')
        return result

    async def test_full_native_config_statements_namespace_metadata_and_delays(self):
        text = '''- unique_id: section
  sensor:
  - unique_id: total
    name: "Total"
    unit_of_measurement: W
    device_class: power
    state_class: measurement
    availability: "{{ not is_state('sensor.original', 'unavailable') }}"
    state: >-
      {% set ns = namespace(total=0) %}
      {% for n in [1, 2] %}
      {% if n > 0 %}{% set ns.total = ns.total + n %}{% endif %}
      {% endfor %}
      {{ ns.total + (states('sensor.original') | float(0)) }}
    attributes:
      source_attribute: "{{ state_attr('sensor.original', 'value') }}"
      tick: "{{ now().minute }}"
      identity: "{{ this.entity_id }}"
      memory: "{{ this.attributes.get('count', 0) + 1 }}"
  binary_sensor:
  - unique_id: delayed
    name: "Delayed"
    state: "{{ states('sensor.original') | float(0) > 0 }}"
    delay_on: "00:00:05"
    delay_off: "00:00:03"
'''
        checked = await self.checked(text)
        worker = self.module('yaml_validation_worker')
        profile = worker.template_state_source_projection(checked, str(self.f.root))
        self.assertEqual(profile['profile'], 'template_state_source_v3')
        self.assertEqual({r[0] for r in profile['states']}, {'template.sensor', 'template.binary_sensor'})
        rows = {r[0]: r for r in profile['states']}
        feature = rows['template.sensor'][3]
        self.assertTrue(feature['availability'])
        self.assertTrue(any(s['local_bindings'] for s in feature['slots']))
        self.assertTrue(any(s['this_identity'] for s in feature['slots']))
        self.assertTrue(any(s['this_memory'] for s in feature['slots']))
        self.assertTrue(any('now' in s['time'] for s in feature['slots']))
        self.assertTrue(rows['template.binary_sensor'][3]['pending_delay_proof_required'])
        # No render oracle or old AST whitelist: source declared, runtime unknown.
        with self.assertRaises(ValueError):
            worker.qualified_template_state_projection(checked, str(self.f.root))
        entity = checked['template'][0]['sensor'][0]
        semantic = worker.full_template_state_semantics(checked['template'][0], 'sensor', entity)
        digest = worker._fingerprint(semantic)
        self.assertEqual(digest, rows['template.sensor'][2])
        for key, replacement in (('unit_of_measurement', 'kW'), ('state_class', 'total')):
            changed = dict(entity, **{key: replacement})
            self.assertNotEqual(worker._fingerprint(worker.full_template_state_semantics(
                checked['template'][0], 'sensor', changed)), digest)
        sibling_edit = dict(checked['template'][0])
        sibling_edit['binary_sensor'] = [dict(sibling_edit['binary_sensor'][0],
            name=entity['name'])]
        self.assertEqual(worker._fingerprint(worker.full_template_state_semantics(
            sibling_edit, 'sensor', entity)), digest)
        context_edit = dict(checked['template'][0], unique_id='another-section')
        self.assertNotEqual(worker._fingerprint(worker.full_template_state_semantics(
            context_edit, 'sensor', entity)), digest)
        self.assertIn('$template', json.dumps(worker._normal(semantic)))
        # Exact body is native Template.template, including its own normalization.
        normalized = json.dumps(worker._normal(semantic))
        self.assertIn(json.dumps(entity['state'].template)[1:-1], normalized)

    async def test_legacy_v1_v2_semantics_bytes_are_unchanged(self):
        worker = self.module('yaml_validation_worker')
        from homeassistant.helpers.template import Template
        base = {'state': Template("{{ states('sensor.original') | float(0) }}", self.f.hass),
            'name': Template('Total', self.f.hass), 'unit_of_measurement': 'W',
            'device_class': 'power', 'state_class': 'measurement'}
        expected = ['state_sensor_v1', base['state'].template, 'Total', 'W', 'power', 'measurement']
        self.assertEqual(worker.simple_template_sensor_semantics(base), expected)
        before = worker._fingerprint(expected)
        worker.full_template_state_semantics({'sensor': [base]}, 'sensor', base)
        self.assertEqual(before, worker._fingerprint(worker.simple_template_sensor_semantics(base)))
        metadata = dict(base, icon=Template("{{ states('sensor.original') }}", self.f.hass))
        expected[0] = 'state_sensor_metadata_v2'
        expected.extend([[None, None], [None, metadata['icon'].template, None, []]])
        self.assertEqual(worker.simple_template_sensor_semantics(metadata), expected)
        binary = {'state': Template("{{ is_state('sensor.original', 'on') }}", self.f.hass),
            'name': Template('Flag', self.f.hass)}
        self.assertEqual(worker.simple_template_binary_semantics(binary),
            ['state_binary_v1', binary['state'].template, 'Flag', None])

    async def test_uidless_full_profile_native_bijection_uses_full_config(self):
        worker = self.module('yaml_validation_worker')
        checked = await self.checked('''- sensor:
  - name: "Counter"
    state: "{% set x = 2 %}{{ state_attr('sensor.original', 'value') | float(0) + x }}"
    attributes:
      tick: "{{ now().minute }}"
''')
        profile = worker.template_state_source_projection(checked, str(self.f.root))
        self.assertEqual(len(profile['states']), 1)
        self.assertTrue(profile['states'][0][1])
        # Full config pairing must not depend on the old simple expression subset.
        self.assertTrue(any('state_attr' in slot['calls']
            for slot in profile['states'][0][3]['slots']))

    async def test_checker_opt_in_and_strict_source_seal_no_runtime_upgrade(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        worker = self.module('yaml_validation_worker')
        planner = self.module('yaml_plan')
        ledger_module = self.module('yaml_source_ledger')
        seals = self.module('template_source_evidence')
        await self.checked('''- unique_id: block
  sensor:
  - unique_id: value
    name: "Value"
    state: "{% set x = 1 %}{{ states('sensor.original') | float(0) + x }}"
    attributes:
      tick: "{{ now().minute }}"
''')
        root = self.f.root
        mapping = {'sensor.original': 'sensor.changed'}
        plan = planner.plan_yaml(root, mapping)
        self.assertTrue(plan.ready, plan.issues)
        original = {p.path: (root / p.path).read_bytes() for p in plan.files}
        ledger = ledger_module.extract_template_source_ledger(plan, original)
        with tempfile.TemporaryDirectory(dir=root.parent) as temporary:
            proposed = Path(temporary)
            for path, raw in original.items():
                (proposed / path).parent.mkdir(parents=True, exist_ok=True)
                (proposed / path).write_bytes(raw)
            for patch in plan.patches:
                (proposed / patch.path).write_bytes(patch.proposed_bytes)
            legacy = await worker.check(str(root), str(proposed), with_sources=True)
            result = await worker.check(str(root), str(proposed), with_sources=True,
                template_source_profile='template_state_source_v3', template_reference_mapping=mapping)
            self.assertEqual(result['status'], 'valid')
            self.assertEqual(result['runtime_expected'], legacy['runtime_expected'])
            self.assertEqual(result['template_sources'], legacy['template_sources'])
            self.assertIn('template', result['unsupported_domains'])
            native = json.loads(json.dumps(result['template_state_source_profile']))
            receipt = SimpleNamespace(version=result['version'],
                source_files=tuple((p.path, p.sha256, p.device, p.inode) for p in plan.files),
                proposed_files=tuple(sorted((p.path, hashlib.sha256(p.proposed_bytes).hexdigest())
                                           for p in plan.patches)))
            source = seals.seal_template_state_sources(receipt, ledger, native, plan=plan, mapping=mapping)
            self.assertTrue(source.source_matched)
            self.assertFalse(source.runtime_verified)
            self.assertFalse(source.migration_ready)
            with self.assertRaises(ValueError):
                seals.seal_template_sources(receipt, ledger, native)
            for mutate in ('extra', 'feature', 'proof'):
                bad = copy.deepcopy(native)
                if mutate == 'extra':
                    bad['runtime_verified'] = True
                elif mutate == 'feature':
                    bad['states'][0][3]['slots'][0]['this_memory'] = 'yes'
                else:
                    bad['reference_proof']['files'][0][2] = '0' * 64
                with self.assertRaises(ValueError):
                    seals.seal_template_state_sources(receipt, ledger, bad, plan=plan, mapping=mapping)
            # Exact ORIGINAL/proposal mismatches refuse before schema approval.
            (proposed / 'template.yaml').write_text('# tampered\n')
            with self.assertRaises(ValueError):
                await worker.check(str(root), str(proposed), with_sources=True,
                    template_source_profile='template_state_source_v3', template_reference_mapping=mapping)

    async def test_dynamic_and_shadowed_source_still_block(self):
        import tempfile
        worker = self.module('yaml_validation_worker')
        for expression in ("{{ states('sensor.' ~ suffix) }}",
                           "{% set states = arbitrary %}{{ states('sensor.original') }}"):
            await self.checked('- unique_id: block\n  sensor:\n  - unique_id: value\n'
                               '    name: "Value"\n    state: "' + expression + '"\n')
            with tempfile.TemporaryDirectory(dir=self.f.root.parent) as proposed:
                with self.assertRaises(ValueError):
                    worker._template_reference_proof(str(self.f.root), proposed,
                        {'sensor.original': 'sensor.changed'})


    async def test_trigger_inventory_does_not_acquire_state_qualification(self):
        worker = self.module('yaml_validation_worker')
        checked = await self.checked('''- unique_id: events
  triggers:
  - trigger: event
    event_type: source_profile_probe
  sensor:
  - unique_id: event_value
    name: "Event value"
    state: "{{ trigger.event.event_type }}"
''')
        profile = worker.template_state_source_projection(checked, str(self.f.root))
        self.assertEqual(profile['states'], [])
        self.assertTrue(profile['rows'])
        self.assertTrue(all(r[2] == 'trigger' for r in profile['families']))

    async def test_parent_and_entity_variables_and_native_defaults_are_sealed(self):
        worker = self.module('yaml_validation_worker')
        checked = await self.checked('''- unique_id: variables
  variables:
    source: sensor.original
    offset: "{{ 2 + 3 }}"
  sensor:
  - unique_id: output
    name: "{{ 'Variable sensor' }}"
    state: "{{ states(source) | float(0) + offset }}"
    attributes:
      identity: "{{ this.entity_id }}"
''')
        projection = worker.template_state_source_projection(checked, str(self.f.root))
        row = projection['states'][0]
        self.assertTrue(row[3]['variables'])
        self.assertTrue(any(slot['path'][:2] == ['block', 'variables'] for slot in row[3]['slots']))
        before = worker._fingerprint(worker._template_source_projection(checked, str(self.f.root)))
        worker.template_state_source_projection(checked, str(self.f.root))
        self.assertEqual(before, worker._fingerprint(worker._template_source_projection(checked, str(self.f.root))))
        block = checked['template'][0]
        entity = block['sensor'][0]
        without_context = dict(block)
        without_context.pop('variables')
        self.assertNotEqual(row[2], worker._fingerprint(worker.full_template_state_semantics(
            without_context, 'sensor', entity)))
        # Zero/false native defaults remain typed inputs, not truthy omissions.
        normal = worker._normal(worker.full_template_state_semantics(block, 'sensor', entity))
        self.assertEqual(row[2], worker._fingerprint(worker.full_template_state_semantics(block, 'sensor', entity)))
        self.assertIsInstance(normal, list)
