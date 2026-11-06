"""Read-only bounded census of native service-event recipients.

The bounded profile admits Recorder's actual MATCH_ALL listener and the
current Root's exact NativeEmptySceneFence service-event subscriptions.
Unknown service-event or match-all subscribers refuse before any effect. This
does not emit events, mutate a service, or authorize a registry attempt.
"""
from __future__ import annotations

import asyncio
import dis
from pathlib import Path
import queue
from types import CodeType, FunctionType

from .transaction import Refused


def _need(value, reason):
    if not value:
        raise Refused(reason) from None


def _codes(code):
    yield code
    for value in code.co_consts:
        if type(value) is CodeType:
            yield from _codes(value)


class NativeOwnScriptServiceRecipients:
    @classmethod
    async def capture(cls, hass, inventory, *, guard, scene_fence=None):
        import homeassistant
        from homeassistant.components.recorder import core, get_instance
        from homeassistant.const import __version__
        from homeassistant.core import HomeAssistant
        from .native_own_id_inventory import NativeOwnIDInventory
        from .native_empty_scene import NativeEmptySceneFence
        from .native_unload_code_proof import prove_methods
        from .yaml_validation import _settled_executor

        pins = {'2025.6.0': '3fb0f3f71fbdf98b746d546d8e6c754768264f18cbeeec63981980ae96096d84',
                '2026.10.4': '9ef798d0d07ef276f3e6a58e381a8f0d9c722af667570b0bdbbfe2eec3e171a8'}
        _need(type(hass) is HomeAssistant and type(inventory) is NativeOwnIDInventory
              and inventory.hass is hass and inventory.service_event_census is not None
              and __version__ in pins and callable(guard)
              and asyncio.current_task() is inventory.task
              and (scene_fence is None or type(scene_fence) is NativeEmptySceneFence
                   and scene_fence.hass is hass and not scene_fence.closed),
              'own_script_service_recipients_scope_unqualified')
        from .native_own_script_service_audit import _guard
        _guard(guard)
        inventory.check()
        try:
            recorder = get_instance(hass)
        except (KeyError, ValueError, AttributeError):
            raise Refused('own_script_recorder_unqualified') from None
        _need(type(recorder) is core.Recorder and recorder.hass is hass and recorder.is_alive()
              and type(recorder._queue) is queue.SimpleQueue and recorder._event_listener is not None,
              'own_script_recorder_unqualified')
        refs = [(core.Recorder, 'async_initialize')]
        methods, codes = await _settled_executor(hass, prove_methods,
            Path(homeassistant.__file__).resolve().parent,
            {'components/recorder/core.py': pins[__version__]}, refs)
        _guard(guard)
        inventory.check()
        obj = object.__new__(cls)
        obj.hass, obj.inventory, obj.recorder = hass, inventory, recorder
        obj.scene_fence = scene_fence
        obj.guard, obj.task = guard, asyncio.current_task()
        obj.refs, obj.methods, obj.codes = tuple(refs), methods, codes
        obj.queue, obj.unsubscribe = recorder._queue, recorder._event_listener
        obj.callback = obj._sample()
        target = obj.callback[1]
        obj.globals = tuple((name, name in target.__globals__, target.__globals__.get(name))
            for name in sorted({instruction.argval for instruction in dis.get_instructions(target)
                if instruction.opname in ('LOAD_GLOBAL', 'LOAD_NAME') and type(instruction.argval) is str}))
        obj.check()
        return obj

    def _sample(self):
        from homeassistant.components.recorder import core, get_instance
        from homeassistant.core import HassJob, HassJobType, MATCH_ALL, get_hassjob_callable_job_type
        from .yaml_runtime_observer import _registered_bus_remove
        recorder, bus = self.recorder, self.hass.bus
        _need(get_instance(self.hass) is recorder and recorder.is_alive()
              and recorder._queue is self.queue and type(self.queue) is queue.SimpleQueue
              and recorder._event_listener is self.unsubscribe,
              'own_script_recorder_changed')
        scene_entries = self._scene_recipients()
        try:
            entry, _ = _registered_bus_remove(self.hass, self.unsubscribe, MATCH_ALL)
        except ValueError:
            raise Refused('own_script_recorder_listener_changed') from None
        _need(len(bus._match_all_listeners) == 1 and bus._match_all_listeners[0] is entry,
              'own_script_service_match_all_unsupported')
        job, event_filter = entry
        target = getattr(job, 'target', None)
        _need(type(job) is HassJob and type(job._cache) is dict,
              'own_script_recorder_listener_changed')
        job_type = (job._cache['job_type'] if 'job_type' in job._cache
                    else get_hassjob_callable_job_type(target))
        _need(job_type is HassJobType.Callback and event_filter is None
              and type(target) is FunctionType and target.__globals__ is vars(core)
              and target.__qualname__ == 'Recorder.async_initialize.<locals>._event_listener'
              and any(target.__code__ is code for code in _codes(core.Recorder.async_initialize.__code__)),
              'own_script_recorder_listener_changed')
        try:
            cells = dict(zip(target.__code__.co_freevars,
                (cell.cell_contents for cell in target.__closure__ or ()), strict=True))
        except ValueError:
            raise Refused('own_script_recorder_listener_changed') from None
        _need(set(cells) == {'entity_filter', 'exclude_event_types', 'queue_put'}
              and cells['entity_filter'] is recorder.entity_filter
              and cells['exclude_event_types'] is recorder.exclude_event_types
              and type(cells['exclude_event_types']) is set
              and getattr(cells['queue_put'], '__self__', None) is self.queue
              and getattr(cells['queue_put'], '__name__', None) == 'put_nowait',
              'own_script_recorder_listener_changed')
        return (job, target, event_filter, self.unsubscribe, *scene_entries)

    def _scene_recipients(self):
        return self.scene_recipients(self.hass, self.scene_fence)

    @staticmethod
    def scene_recipients(hass, fence):
        """Prove exactly the current Root's two real scene-latch entries."""
        from homeassistant.core import HassJob, HassJobType, get_hassjob_callable_job_type
        from . import native_empty_scene
        from .yaml_runtime_observer import _registered_bus_remove
        bus = hass.bus
        names = ('service_removed', 'service_registered')
        if fence is None:
            _need(all(not bus._listeners.get(name) for name in names),
                  'own_script_service_listener_unsupported')
            return ()
        _need(type(fence) is native_empty_scene.NativeEmptySceneFence
              and fence.hass is hass and not fence.closed and not fence.changed
              and type(fence.removers) is list
              and tuple(name for name, _ in fence.removers) ==
                  ('call_service', 'service_registered', 'service_removed',
                   'scene_reloaded', 'state_changed', 'entity_registry_updated'),
              'own_script_scene_listener_unqualified')
        fence.check()
        removers = dict(fence.removers)
        result, callback = [], None
        for name in names:
            remove = removers[name]
            try:
                entry, _ = _registered_bus_remove(hass, remove, name)
            except ValueError:
                raise Refused('own_script_scene_listener_changed') from None
            entries = bus._listeners.get(name)
            _need(type(entries) is list and len(entries) == 1 and entries[0] is entry,
                  'own_script_service_listener_unsupported')
            job, event_filter = entry
            _need(type(job) is HassJob and type(job._cache) is dict and event_filter is None,
                  'own_script_scene_listener_unqualified')
            target = job.target
            job_type = (job._cache['job_type'] if 'job_type' in job._cache
                        else get_hassjob_callable_job_type(target))
            _need(job_type is HassJobType.Callback and type(target) is FunctionType
                  and target.__globals__ is vars(native_empty_scene)
                  and target.__qualname__ == 'NativeEmptySceneFence.__init__.<locals>.observed'
                  and any(target.__code__ is code for code in _codes(native_empty_scene.NativeEmptySceneFence.__init__.__code__))
                  and target.__code__.co_freevars == ('self',)
                  and target.__closure__ is not None and len(target.__closure__) == 1
                  and target.__closure__[0].cell_contents is fence
                  and (callback is None or target is callback),
                  'own_script_scene_listener_unqualified')
            callback = target
            result.extend((remove, entry, job, target, event_filter))
        return tuple(result)

    def check_retained(self, *, invoke_guard=True):
        """Recheck recipients after an owned native service move, without
        asking the pre-move inventory to still own the retired service key.
        """
        from .native_own_script_service_audit import _guard
        from .native_unload_code_proof import unchanged
        _need(asyncio.current_task() is self.task, 'own_script_service_recipients_task_changed')
        if invoke_guard:
            _guard(self.guard)
        unchanged(self.refs, self.methods, self.codes)
        current = self._sample()
        _need(len(current) == len(self.callback)
              and all(value is saved for value, saved in zip(current, self.callback, strict=True))
              and all((name in current[1].__globals__) is present
                      and (not present or current[1].__globals__[name] is value)
                      for name, present, value in self.globals),
              'own_script_service_recipients_changed')

    def check(self, *, installed_row=None):
        from .native_own_script_service_audit import _guard
        _guard(self.guard)
        self.inventory.check_service(installed_row=installed_row)
        self.check_retained(invoke_guard=False)
