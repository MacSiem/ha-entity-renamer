"""Native synchronous census still rejects intervening foreign period drift."""
import sys
import unittest
from datetime import timedelta

import test_native_automation_duration_prefix_root as literal
import test_native_template_mixed_yaml_server as mixed


@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeAutomationCensusBracketTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = literal.NativeAutomationDurationPrefixRootTests.asyncSetUp
    _before_start = literal.NativeAutomationDurationPrefixRootTests._before_start
    _flush_documents = literal.NativeAutomationDurationPrefixRootTests._flush_documents
    prepare = literal.NativeAutomationDurationPrefixRootTests.prepare
    emit = literal.NativeAutomationDurationPrefixRootTests.emit
    _runtime = literal.NativeAutomationDurationPrefixRootTests._runtime
    module = literal.NativeAutomationDurationPrefixRootTests.module
    stimulus = literal.NativeAutomationDurationPrefixRootTests.stimulus
    data = literal.NativeAutomationDurationPrefixRootTests.data

    async def test_two_source_fences_bracket_both_native_censuses_and_refuse_period_drift(self):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        _, mapping, _, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        old, _ = next(iter(mapping.items()))
        (root / 'mixed_automations.yaml').write_text(
            '- id: mixed_source\n  alias: Census source\n  initial_state: true\n'
            '  triggers:\n  - trigger: state\n    entity_id: ' + old + '\n'
            '    from: "16"\n    to: "17"\n    for: {seconds: 300}\n'
            '  actions:\n  - event: census_source_result\n')
        await hass.services.async_call('automation', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        source = next(e for e in hass.data[DATA_INSTANCES]['automation'].entities
                      if e.unique_id == 'mixed_source')
        state = self.module('native_automation_state_binding')
        duration = self.module('native_automation_duration_binding')
        native = await hass.async_add_executor_job(state._Native, hass)
        cap = await hass.async_add_executor_job(duration.NativeAutomationDurationCapability, hass, native)
        await self.stimulus(16)
        await self.stimulus(17)
        await hass.async_block_till_done()
        before = cap.capture(source)
        pending, = cap.pending(before)[0]
        handle, context = pending['handle'], pending['handle']._context
        deadline = handle.when()
        original_period = before.listener_cells['period'][old]
        callback_image = tuple((key, id(members), tuple(map(id, members)))
                               for key, members in before.callbacks.items())
        code_check = duration.NativeAutomationDurationCapability.check_sources.__code__
        code_read = duration.NativeAutomationDurationCapability._original_checked.__code__
        tool = next(k for k in range(6) if sys.monitoring.get_tool(k) is None)
        sys.monitoring.use_tool_id(tool, 'duration census bracket regression')
        checks, reads = [], []
        injection = None
        def started(code, offset):
            frame = sys._getframe(1)
            if code is code_check and frame.f_locals.get('self') is cap:
                checks.append(True)
        def returned(code, offset, value):
            frame = sys._getframe(1)
            if code is code_read and frame.f_locals.get('self') is cap:
                reads.append(True)
                if injection == 'period' and len(reads) == 1:
                    frame.f_locals['b'].listener_cells['period'][old] = timedelta(seconds=301)
                elif injection == 'source' and len(reads) == 1:
                    # Tamper only with the capability's private source-ref
                    # inventory, not HA/Core code or its callback queue.
                    cap.refs.append(cap.refs[0])
        try:
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, started)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, returned)
            sys.monitoring.set_local_events(tool, code_check, sys.monitoring.events.PY_START)
            sys.monitoring.set_local_events(tool, code_read, sys.monitoring.events.PY_RETURN)
            after = cap.capture(source)
            self.assertEqual((len(checks), len(reads)), (2, 2))
            still, = cap.pending(after)[0]
            self.assertIs(still['handle'], handle)
            self.assertIs(still['handle']._context, context)
            self.assertEqual(still['handle'].when(), deadline)
            checks.clear(); reads.clear(); injection = 'period'
            with self.assertRaisesRegex(ValueError, 'duration_period_changed'):
                cap.capture(source)
            self.assertEqual(len(reads), 1)
            before.listener_cells['period'][old] = original_period
            checks.clear(); reads.clear(); injection = 'source'
            with self.assertRaises(ValueError):
                cap.capture(source)
            self.assertEqual((len(checks), len(reads)), (2, 2))
        finally:
            if injection == 'source' and len(cap.refs) > len(cap.methods):
                cap.refs.pop()
            before.listener_cells['period'][old] = original_period
            sys.monitoring.set_local_events(tool, code_check, 0)
            sys.monitoring.set_local_events(tool, code_read, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, None)
            sys.monitoring.free_tool_id(tool)
        after = cap.capture(source)
        restored, = cap.pending(after)[0]
        self.assertIs(restored['handle'], handle)
        self.assertIs(restored['handle']._context, context)
        self.assertEqual(restored['handle'].when(), deadline)
        self.assertEqual(tuple((key, id(members), tuple(map(id, members)))
                               for key, members in after.callbacks.items()), callback_image)
