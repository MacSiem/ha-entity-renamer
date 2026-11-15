"""Local CPython emission witness for queued native state events.

The shared hook observes only the pinned EventBus method. It does not replace
HA methods, chain profilers, construct events or retain the event queue.
"""
import sys

_NAME = 'ha_entity_renamer.utility_source_emissions'
_tool = None
_code = None
_witnesses = []


def _started(code, offset):
    frame = sys._getframe(1)
    try:
        for witness in tuple(_witnesses):
            try:
                if frame.f_locals.get('self') is witness.bus:
                    witness.observe(frame)
            except BaseException:
                # An observer never throws into the native emitter.
                witness.poison('emission_observer_failed')
    finally:
        del frame


class NativeSourceEmissions:
    """One live subscription to the existing process-local monitoring hook."""

    def __init__(self, code, bus, observe, poison):
        global _tool, _code
        self.bus, self.observe, self.poison = bus, observe, poison
        self.live = False
        if not _witnesses:
            monitor = sys.monitoring
            # CPython owns this finite pool. Never take another tool's slot.
            available = [key for key in range(6) if monitor.get_tool(key) is None]
            if not available:
                raise ValueError('utility_source_emission_monitor_unavailable')
            key = available[0]
            monitor.use_tool_id(key, _NAME)
            try:
                monitor.register_callback(key, monitor.events.PY_START, _started)
                monitor.set_local_events(key, code, monitor.events.PY_START)
            except BaseException:
                monitor.set_local_events(key, code, 0)
                monitor.register_callback(key, monitor.events.PY_START, None)
                monitor.free_tool_id(key)
                raise
            _tool, _code = key, code
        if code is not _code:
            raise ValueError('utility_source_emission_native_code_changed')
        _witnesses.append(self)
        self.live = True
        try:
            self.check()
        except BaseException:
            self.close()
            raise

    def check(self):
        monitor = sys.monitoring
        if (not self.live or self not in _witnesses or _tool is None
                or monitor.get_tool(_tool) != _NAME or monitor.get_events(_tool) != 0
                or monitor.get_local_events(_tool, _code) != monitor.events.PY_START):
            raise ValueError('utility_source_emission_monitor_lost')

    def close(self):
        global _tool, _code
        if not self.live:
            return
        self.live = False
        _witnesses.remove(self)
        if not _witnesses:
            monitor = sys.monitoring
            if monitor.get_tool(_tool) == _NAME:
                monitor.set_local_events(_tool, _code, 0)
                monitor.register_callback(_tool, monitor.events.PY_START, None)
                monitor.free_tool_id(_tool)
            _tool = _code = None
