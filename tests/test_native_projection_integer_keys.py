"""Typed native mapping keys remain distinct through projection and drift checks."""
import importlib
import json
import unittest

import test_native_input_baseline as fixture


@unittest.skipUnless(fixture.HAS_HA, 'actual HA required')
class NativeProjectionIntegerKeysTests(unittest.TestCase):
    def setUp(self):
        self.worker = importlib.import_module(fixture.PACKAGE + '.yaml_validation_worker')

    def test_typed_keys_order_and_unknown_refusal(self):
        normal = self.worker._normal
        value = {1: 'integer', '1': 'string', 2: {'value': 'nested'}}
        self.assertEqual(normal(value), {'$map': [[{'$int': 1}, 'integer'],
            ['1', 'string'], [{'$int': 2}, {'$map': [['value', 'nested']]}]]})
        self.assertEqual(json.loads(json.dumps(normal(value))), normal(value))
        self.assertNotEqual(self.worker._fingerprint({1: 'x'}), self.worker._fingerprint({'1': 'x'}))
        for key in (True, 1.5, None, object()):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'runtime_projection_key'):
                normal({key: 'x'})
        with self.assertRaisesRegex(ValueError, 'runtime_projection_type'):
            normal({'unknown': object()})

    def test_projector_preserves_integer_key_and_rejects_key_drift(self):
        transport = importlib.import_module(fixture.PACKAGE + '.native_script_config_transport')
        tx = object.__new__(transport.NativeScriptConfigTransport)
        tx._projected, tx.operations = {}, []
        live = {1: {'target': 'sensor.original'}, '1': 'ordinary'}
        before = self.worker._normal(live)
        after = self.worker._normal({1: {'target': 'sensor.changed'}, '1': 'ordinary'})
        self.assertIs(tx._project(live, before, after), live)
        self.assertTrue(any(operation[0] == 'dict' for operation in tx.operations))
        changed_key = self.worker._normal({2: {'target': 'sensor.changed'}, '1': 'ordinary'})
        tx._projected, tx.operations = {}, []
        with self.assertRaises(transport.Refused):
            tx._project(live, before, changed_key)

    def test_template_paths_match_real_native_integer_keys(self):
        from types import SimpleNamespace
        from homeassistant.helpers.template import Template
        from homeassistant.helpers.script_variables import ScriptVariables
        from homeassistant.helpers import template
        binding = importlib.import_module(fixture.PACKAGE + '.native_template_state_binding')
        native = SimpleNamespace(template=template, ScriptVariables=ScriptVariables)
        value = {1: Template('{{ 1 }}', None), '1': Template('{{ 2 }}', None)}
        expected = dict(binding._templates(self.worker._normal(value)))
        actual = dict(binding._objects(value, native))
        self.assertEqual(set(expected), set(actual))
        self.assertEqual(expected, {path: obj.template for path, obj in actual.items()})

    def test_idless_private_key_refuses_namespace_and_distinguishes_raw(self):
        key = self.worker._automation_key
        value = key(None, {'alias': 'first'})
        self.assertTrue(value.startswith('\0automation:'))
        self.assertNotEqual(value, key(None, {'alias': 'second'}))
        with self.assertRaises(ValueError):
            key(value, {'alias': 'first'})
        with self.assertRaises(ValueError):
            key(None, None)
