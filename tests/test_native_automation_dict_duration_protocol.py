"""Owner-run native protocol qualification, not a Root/AC acceptance test.

Run this target alone in a fresh pinned Core process through FIFO. No fake hass,
State/Event, verifier, CV replacement, policy change, counter reset or hot-code
snapshot. These tests invoke native CV/rendering; the proof never invokes them.
"""
from datetime import timedelta
import importlib
from types import FunctionType, MethodType
import unittest
from unittest.mock import patch

import test_native_helper_quiescence as fixture


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeAutomationDictDurationProtocolTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.NativeHelperQuiescenceTests.asyncSetUp
    _before_start = fixture.NativeHelperQuiescenceTests._before_start

    def module(self, name):
        return importlib.import_module(fixture.fixtures.PACKAGE + '.' + name)

    async def _proof(self):
        from homeassistant.core import HomeAssistant
        self.assertIs(type(self.f.hass), HomeAssistant)
        native = await self.f.hass.async_add_executor_job(
            self.module('native_automation_state_binding')._Native, self.f.hass)
        scalar = await self.f.hass.async_add_executor_job(
            self.module('native_automation_scalar_duration').ScalarDurationNative, native)
        proof = await self.f.hass.async_add_executor_job(
            self.module('native_automation_dict_duration').DictDurationNative, scalar)
        return scalar, proof

    async def _check(self, scalar, proof):
        await self.f.hass.async_add_executor_job(scalar.check)
        await self.f.hass.async_add_executor_job(proof.check)

    async def _admit(self, proof, value):
        result = await self.f.hass.async_add_executor_job(proof.admit_result, value)
        self.assertIs(result, value)
        return result

    @staticmethod
    def _phase(proof):
        if proof.minimum:
            return 'minimum'
        schema, compiled = proof.schema, proof.schema._compiled
        if not schema._built:
            return 'lazy'
        if type(compiled) is MethodType:
            return 'bootstrap'
        if type(compiled) is FunctionType:
            if compiled.__code__.co_qualname == 'Schema._auto_counter.<locals>.validate':
                return 'AUTO'
            return 'generated'
        return 'interpreted'

    async def test_00_native_cold_auto_hot_wrapper_error_cache_and_tamper(self):
        from homeassistant.helpers import template, config_validation as cv
        scalar, proof = await self._proof()
        self.assertIs(scalar.positive, cv.positive_time_period)
        phases = {self._phase(proof)}
        if not proof.minimum:
            self.assertIn(self._phase(proof), ('lazy', 'bootstrap', 'AUTO'),
                          'run this protocol target alone in a fresh Core process')
        # >50 successful dictionary validations heat the REAL native schema.
        # The five distinct native Coerce(float) children all participate.
        data = {'days': 0, 'hours': 0, 'minutes': 1, 'seconds': '1.25', 'milliseconds': 250}
        expected = timedelta(seconds=61.5)
        for _ in range(75):
            await self._check(scalar, proof)
            accepted = await self._admit(proof, data)
            self.assertEqual(cv.positive_time_period(accepted), expected)
            self.assertEqual(data['seconds'], '1.25', 'proof/native CV must not replace the input value')
            phases.add(self._phase(proof))
        await self._check(scalar, proof)
        if not proof.minimum:
            self.assertIn('AUTO', phases)
            self.assertIn('generated', phases)
            self.assertIs(type(proof.schema._compiled), FunctionType)
            self.assertEqual(proof.schema._compiled.__code__.co_filename, '<probatio-generated>')
            self.assertEqual(proof.modules['probatio._codegen']._VALIDATOR_TYPES,
                             (proof.modules['probatio.validators.coerce'].Coerce,
                              proof.modules['probatio.validators.comparison'].Range,
                              proof.modules['probatio.validators.comparison'].In,
                              proof.modules['probatio.validators.combinators'].Any,
                              proof.modules['probatio.validators.combinators'].All,
                              proof.modules['probatio.validators.comparison'].Length,
                              proof.modules['probatio.validators.strings'].Match))
        # Renderer is used only to obtain an actual native Wrapper. No result or
        # period from elsewhere is independently re-rendered by a verifier.
        source = template.Template("{{ {'seconds': 1.25, 'milliseconds': 250} }}", self.f.hass)
        rendered = source.async_render()
        self.assertIs(type(rendered), template.RESULT_WRAPPERS[dict])
        self.assertEqual(cv.positive_time_period(await self._admit(proof, rendered)), timedelta(seconds=1.5))
        await self._check(scalar, proof)
        # Current generated exact-dict guard routes this real subclass to its
        # independently authenticated interpreted mapping. Minimum uses closures.
        if not proof.minimum:
            self.assertIs(type(proof.schema._compiled.__globals__['_interpreted']),
                          proof.modules['probatio._engine']._MappingValidator)
        cases = (
            ({'seconds': True}, timedelta(seconds=1)),
            ({'seconds': False}, timedelta(0)),
            ({'seconds': '1.125'}, timedelta(seconds=1.125)),
            ({'minutes': .5, 'seconds': 1, 'milliseconds': 250}, timedelta(seconds=31.25)),
            ({'hours': .5, 'minutes': -30, 'seconds': 0}, timedelta(0)),
            ({'seconds': 0}, timedelta(0)),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(cv.positive_time_period(await self._admit(proof, value)), expected)
                await self._check(scalar, proof)
        # Invalid native results are admitted unchanged. Original CV owns both
        # failure semantics and its legal lazily populated float failure cache.
        for value in ({}, {'weeks': 1}, {'seconds': 'invalid-float'}, {'seconds': -1},
                      {'seconds': None}, {'seconds': 'NaN'}):
            with self.subTest(invalid=value):
                admitted = await self._admit(proof, value)
                with self.assertRaises(scalar.api.Invalid):
                    cv.positive_time_period(admitted)
                await self._check(scalar, proof)
        with self.assertRaises(OverflowError):
            cv.positive_time_period(await self._admit(proof, {'seconds': 'inf'}))
        await self._check(scalar, proof)
        if not proof.minimum:
            seconds = proof.coercers[3]
            self.assertEqual(seconds._failure_cache, ('expected_type', {'expected': 'float'}, ()))
        # Scalar-only entry point remains unchanged by this additive proof.
        for value in ('00:00:02', 3, 1.25, True, timedelta(seconds=4)):
            self.assertIs(await self._admit(proof, value), value)
        await self._tamper(scalar, proof)

    async def _tamper(self, scalar, proof):
        async def rejects(owner, name, value):
            with patch.object(owner, name, value):
                with self.assertRaises(ValueError):
                    await self.f.hass.async_add_executor_job(proof.check)
            await self._check(scalar, proof)

        await rejects(proof.coercers[0], 'type', int)
        # Foreign native declaration/compiled-owner data, without replacing
        # any Core/library method body or fabricating an evaluator.
        await rejects(proof.schema, 'extra', scalar.api.ALLOW_EXTRA)
        other = scalar.api.Schema({'seconds': float})
        await rejects(proof.schema, '_compiled', other._compiled)
        with patch.dict(proof.wrapper_map, {dict: dict}):
            with self.assertRaises(ValueError):
                await self.f.hass.async_add_executor_job(proof.check)
        await self._check(scalar, proof)
        if proof.minimum:
            priority = proof.modules['voluptuous.schema_builder']._sort_item.__closure__[0].cell_contents
            old = priority[0]
            try:
                priority[0] = (9, old[1])
                with self.assertRaises(ValueError):
                    await self.f.hass.async_add_executor_job(proof.check)
            finally:
                priority[0] = old
            await self._check(scalar, proof)
            return
        await rejects(proof.coercers[0], '_failure_cache', ('expected_type', {'expected': 'int'}, ()))
        engine, cg = proof.modules['probatio._engine'], proof.modules['probatio._codegen']
        await rejects(engine._Candidate, 'check_value', vars(engine._Candidate)['check_key'])
        await rejects(engine._Candidate, '_fields', tuple(reversed(engine._Candidate._fields)))
        await rejects(cg, '_VALIDATOR_TYPES', (float,) * 7)
        await rejects(proof.key_rule, 'keys', ('seconds',))
        hot = proof.schema._compiled
        with patch.dict(hot.__globals__, {'_issuperset': frozenset({'seconds'}).issuperset}):
            with self.assertRaises(ValueError):
                await self.f.hass.async_add_executor_job(proof.check)
        mapping = hot.__globals__['_interpreted']
        await rejects(mapping, '_track_seen', True)
        await rejects(cg, '_Bail', RuntimeError)
        await self._check(scalar, proof)
