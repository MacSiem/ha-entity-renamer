"""Real native schema prefix compilation; no live setup/reload/actions or apply."""
import importlib
import json
import unittest

import test_native_helper_quiescence as fixture


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeDependentConfigProgramTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.NativeHelperQuiescenceTests.asyncSetUp
    _before_start = fixture.NativeHelperQuiescenceTests._before_start
    _flush_documents = fixture.NativeHelperQuiescenceTests._flush_documents

    def module(self, name):
        return importlib.import_module(fixture.fixtures.PACKAGE + '.' + name)

    async def compile(self, *, empty_automation=False, secret=False):
        root = self.f.root
        (root / 'configuration.yaml').write_text('script: !include scripts.yaml\n' +
            ('automation: []\n' if empty_automation else 'automation: !include automations.yaml\n'))
        # Literal producer, alias, nested action and ordinary metadata:
        # only source-planner-accounted references may change.
        (root / 'scripts.yaml').write_text('''probe:
  alias: Unchanged script metadata
  sequence:
    - variables:
        source: sensor.original
        alias: "{{ source }}"
    - if: "{{ is_state(alias, 'on') }}"
      then:
        - variables:
            value: "{{ states('sensor.second') }}"
      else:
        - variables:
            value: "{{ states(alias) }}"
''')
        (root / 'automations.yaml').write_text('''- id: dependent_probe
  alias: Unchanged automation metadata
  triggers:
    - trigger: state
      entity_id: sensor.original
  actions:
    - variables:
        sample: "{{ states('sensor.original') ~ states('sensor.second') }}"
''')
        if secret:
            with (root / 'scripts.yaml').open('a') as stream:
                stream.write('  description: !secret synthetic_description\n')
            (root / 'secrets.yaml').write_text('synthetic_description: local_synthetic_fixture\n')
        mapping = {'sensor.original': 'sensor.changed', 'sensor.second': 'sensor.other'}
        plan = self.module('yaml_plan').plan_yaml(root, mapping)
        self.assertTrue(plan.ready, plan.issues)
        validate = self.module('yaml_validation')
        result = await validate.async_validate_proposed_with_dependent_config_program(
            self.f.hass, plan, mapping, root.parent)
        return result, plan, mapping

    async def test_full_native_prefixes_same_original_codec_and_tamper_guards(self):
        result, plan, mapping = await self.compile()
        p = result.config_program
        module = self.module('dependent_config_program')
        masks = [frozenset(), frozenset({'sensor.original'}), frozenset(mapping)]
        images = [p.rows(mask) for mask in masks]
        self.assertEqual(len({module._digest(rows) for rows in images}), 3)
        self.assertTrue(all([domain for domain, _ in rows] == ['automation', 'script'] for rows in images))
        # Fresh inverse reads ORIGINAL image, never reverses replacement strings.
        for before, after, source in [(masks[0], masks[1], 'sensor.original'),
                (masks[1], masks[2], 'sensor.second'), (masks[2], masks[1], 'sensor.second'),
                (masks[1], masks[0], 'sensor.original')]:
            p.assert_edge(before, after, source)
        self.assertEqual(p.rows(frozenset()), images[0])
        self.assertIn('Unchanged script metadata', json.dumps(images[-1]))
        for invalid in (frozenset({'sensor.second'}), frozenset({'sensor.foreign'})):
            with self.assertRaises(ValueError):
                p.rows(invalid)
        with self.assertRaises(ValueError):
            p.assert_edge(masks[0], masks[2], 'sensor.original')
        p.assert_sources(result.receipt, result.evidence, plan=plan, mapping=mapping,
                         source_order=tuple(sorted(mapping)))
        payload = module.encode_dependent_config_program(p)
        kwargs = dict(expected_sha256=p.sha256, receipt=result.receipt, evidence=result.evidence,
                      plan=plan, mapping=mapping, source_order=tuple(sorted(mapping)))
        self.assertEqual(module.decode_dependent_config_program(payload, **kwargs), p)
        for index in (1, 2):
            altered = json.loads(payload)
            altered['descriptor']['images'][index]['domains'][0][1][0][0] = 'foreign_key'
            with self.assertRaises(ValueError):
                module.decode_dependent_config_program(module._bytes(altered), **kwargs)
        altered = json.loads(payload)
        altered['descriptor']['images'][1]['files'][0][1] = '0' * 64
        with self.assertRaises(ValueError):
            module.decode_dependent_config_program(module._bytes(altered), **kwargs)
        with self.assertRaises(ValueError):
            module.decode_dependent_config_program(payload, **(kwargs | {'source_order': tuple(reversed(p.source_order))}))
        # Legacy source checker keeps the exact receipt and source evidence.
        receipt, evidence = await self.module('yaml_validation').async_validate_proposed_with_sources(
            self.f.hass, plan, self.f.root.parent)
        self.assertEqual(receipt, result.receipt)
        self.assertEqual(evidence, result.evidence)
        self.assertFalse(p.migration_ready)
        # No native components were set up; no services or state outputs created.
        self.assertFalse(self.f.hass.states.async_entity_ids('automation'))
        self.assertFalse(self.f.hass.states.async_entity_ids('script'))

    async def test_unchanged_empty_family_is_present_in_every_native_image(self):
        result, _, mapping = await self.compile(empty_automation=True)
        for active in (frozenset(), frozenset({'sensor.original'}), frozenset(mapping)):
            self.assertEqual(dict(result.config_program.rows(active))['automation'], [])

    async def test_secret_scope_refuses_without_returning_expanded_descriptors(self):
        with self.assertRaises(self.module('yaml_validation').YAMLValidationError) as raised:
            await self.compile(secret=True)
        self.assertNotIn('local_synthetic_fixture', str(raised.exception))
        self.assertNotIn('local_synthetic_fixture', str(getattr(raised.exception, '__notes__', ())))
