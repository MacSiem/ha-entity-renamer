"""Bind one native restore dump to its complete physical Store image.

This is transient evidence, not a lock against later external writers or a
power-loss durability guarantee. The native dump remains responsible for data.
"""
import asyncio
import json
import sys

from .native_input_baseline import _loads

_LIMIT = 64 * 1024 * 1024


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


class DumpReceipt:
    def __init__(self, store):
        from homeassistant.helpers.storage import Store
        self.store = store
        self.code = Store.async_save.__code__
        self.task = None
        self.payloads = []
        self.returns = []
        self.foreign = False
        self.tool = next((i for i in range(6) if sys.monitoring.get_tool(i) is None), None)
        if self.tool is None:
            raise ValueError('restore_save_observer_unavailable')

    def __enter__(self):
        sys.monitoring.use_tool_id(self.tool, 'renamer.restore_save_receipt')
        def started(code, offset):
            frame = sys._getframe(1)
            if frame.f_locals.get('self') is not self.store:
                return
            if asyncio.current_task() is not self.task:
                self.foreign = True
                return
            from homeassistant.helpers.json import json_bytes
            raw = json_bytes({'version': self.store.version,
                              'minor_version': self.store.minor_version,
                              'key': self.store.key, 'data': frame.f_locals['data']})
            self.payloads.append(_canonical(_loads(raw, limit=_LIMIT)))
        def returned(code, offset, result):
            frame = sys._getframe(1)
            if frame.f_locals.get('self') is self.store:
                self.returns.append(asyncio.current_task())
        sys.monitoring.register_callback(self.tool, sys.monitoring.events.PY_START, started)
        sys.monitoring.register_callback(self.tool, sys.monitoring.events.PY_RETURN, returned)
        sys.monitoring.set_local_events(self.tool, self.code,
                                        sys.monitoring.events.PY_START | sys.monitoring.events.PY_RETURN)
        return self

    def verify(self, raw):
        if (self.foreign or len(self.payloads) != 1 or self.returns != [self.task]
                or raw is None or _canonical(_loads(raw, limit=_LIMIT)) != self.payloads[0]):
            raise ValueError('restore_save_physical_readback_mismatch')

    def __exit__(self, *args):
        sys.monitoring.set_local_events(self.tool, self.code, 0)
        sys.monitoring.register_callback(self.tool, sys.monitoring.events.PY_START, None)
        sys.monitoring.register_callback(self.tool, sys.monitoring.events.PY_RETURN, None)
        sys.monitoring.free_tool_id(self.tool)
