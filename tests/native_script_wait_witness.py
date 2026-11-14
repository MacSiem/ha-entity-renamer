"""Read-only actual Script wait census, reused from the Automation wait test."""
import asyncio
import sys


class NativeScriptWaitWitness:
    def __init__(self, hass, script):
        from homeassistant.helpers.script import _ScriptRun
        self.hass, self.script = hass, script
        self.code = _ScriptRun._async_wait_with_optional_timeout.__code__
        self.ready, self.held = hass.loop.create_future(), {}
        self.tool = next(index for index in range(6) if sys.monitoring.get_tool(index) is None)
        sys.monitoring.use_tool_id(self.tool, 'actual native Script wait ownership')
        sys.monitoring.register_callback(self.tool, sys.monitoring.events.PY_START, self._started)
        sys.monitoring.set_local_events(self.tool, self.code, sys.monitoring.events.PY_START)

    def _started(self, code, offset):
        frame = sys._getframe(1)
        run = frame.f_locals.get('self')
        if getattr(run, '_script', None) is self.script and not self.ready.done():
            self.held.update(run=run, task=asyncio.current_task(), stop=run._stop,
                handle=frame.f_locals['timeout_handle'], future=frame.f_locals['timeout_future'],
                unsub=frame.f_locals['unsub'])
            self.held['deadline'] = self.held['handle'].when()
            self.held['context'] = self.held['handle']._context
            self.ready.set_result(None)

    def assert_live(self, case):
        held, script = self.held, self.script
        case.assertTrue(script.is_running)
        case.assertTrue(any(run is held['run'] for run in script._runs))
        case.assertIs(held['run']._stop, held['stop'])
        case.assertFalse(held['task'].done())
        case.assertFalse(held['handle'].cancelled())
        case.assertFalse(held['future'].done())
        case.assertEqual(held['handle'].when(), held['deadline'])
        case.assertIs(held['handle']._context, held['context'])
        node = held['task'].get_coro()
        for _ in range(128):
            frame = getattr(node, 'cr_frame', None)
            if frame is not None and frame.f_code is self.code and frame.f_locals.get('self') is held['run']:
                case.assertIs(frame.f_locals['timeout_handle'], held['handle'])
                case.assertIs(frame.f_locals['timeout_future'], held['future'])
                case.assertIs(frame.f_locals['unsub'], held['unsub'])
                return
            node = getattr(node, 'cr_await', None) or getattr(node, 'gi_yieldfrom', None)
            if node is None:
                break
        case.fail('original native Script wait frame no longer suspended')

    def close(self):
        sys.monitoring.set_local_events(self.tool, self.code, 0)
        sys.monitoring.register_callback(self.tool, sys.monitoring.events.PY_START, None)
        sys.monitoring.free_tool_id(self.tool)
