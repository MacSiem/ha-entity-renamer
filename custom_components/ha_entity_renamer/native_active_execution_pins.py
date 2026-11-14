"""Native run, queue, frame and cache pins for the active execution profile."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import partial

from .transaction import Refused


def _need(value, reason='native_active_execution_unqualified'):
    if not value:
        raise Refused(reason) from None


def _frames(coro):
    seen = set()
    while coro is not None and id(coro) not in seen:
        seen.add(id(coro))
        frame = getattr(coro, 'cr_frame', None) or getattr(coro, 'gi_frame', None)
        if frame is not None:
            yield frame
        coro = getattr(coro, 'cr_await', None) or getattr(coro, 'gi_yieldfrom', None)


def _locate(code, run):
    matches = [(task, frame) for task in asyncio.all_tasks()
               for frame in _frames(task.get_coro())
               if frame.f_code is code and frame.f_locals.get('self') is run]
    _need(len(matches) == 1, 'native_active_execution_frame_unowned')
    return matches[0]


def _variables_graph(variables):
    from homeassistant.helpers.script_variables import ScriptRunVariables
    from .native_script_config_transport import _graph
    result, seen = [], set()
    while variables is not None:
        _need(type(variables) is ScriptRunVariables and id(variables) not in seen,
              'native_active_execution_variable_scope')
        seen.add(id(variables))
        _need(getattr(variables, '_parallel_data', None) is None,
              'native_active_execution_parallel_variables')
        result.append((id(variables), id(variables._local_data), _graph(variables._local_data)))
        variables = variables._previous
    return tuple(result)


@dataclass(frozen=True, eq=False)
class _RunPin:
    run: object
    task: object
    frame: object
    variables: object
    variables_graph: tuple
    context: object
    stop: object
    stopped: object
    action: object
    step: int
    started: bool
    acquired: bool | None

    @classmethod
    def capture(cls, run, task, frame, *, queued):
        _need(not task.done() and not run._stop.done())
        return cls(run, task, frame, run._variables, _variables_graph(run._variables), run._context, run._stop, run._stopped,
                   getattr(run, '_action', None), run._step, run._started,
                   run.lock_acquired if queued else None)

    def check(self):
        run = self.run
        _need(not self.task.done() and run._variables is self.variables
              and _variables_graph(run._variables) == self.variables_graph
              and run._context is self.context and run._stop is self.stop
              and run._stopped is self.stopped and not self.stop.done()
              and getattr(run, '_action', None) is self.action
              and run._step == self.step and run._started is self.started
              and (self.acquired is None or run.lock_acquired is self.acquired),
              'native_active_execution_run_changed')


@dataclass(frozen=True, eq=False)
class ActiveWaitPins:
    root: object
    child: object
    queue: object
    root_sequence: object
    child_sequence: object
    root_listener: object
    listener: object
    caches: tuple
    queue_waiters: tuple
    parent: _RunPin
    queued: _RunPin
    waiting: _RunPin
    handle: object
    deadline: float
    timeout: object
    unsubscribe: object

    @classmethod
    def capture(cls, root):
        from homeassistant.const import __version__
        from homeassistant.helpers.script import Script, _ScriptRun, _QueuedScriptRun
        from .native_script_config_transport import _cache_names, _graph

        _need(__version__ in ('2025.6.0', '2026.10.2', '2026.10.4'))
        _need(type(root) is Script and root.top_level and root.script_mode == 'queued'
              and not getattr(root, '_unloaded', False) and type(root._runs) is list
              and len(root._runs) == 2 and type(root._sequence_scripts) is dict
              and set(root._sequence_scripts) == {0})
        _need(all(not getattr(root, name) for name in _cache_names()
                  if name != '_sequence_scripts'))
        child = root._sequence_scripts[0]
        _need(type(child) is Script and not child.top_level and child._hass is root._hass
              and child.domain == root.domain and not getattr(child, '_unloaded', False)
              and type(child._runs) is list and len(child._runs) == 1
              and all(not getattr(child, name) for name in _cache_names()))
        listener = child.change_listener
        _need(type(listener) is partial and not listener.keywords and listener.args == (child,)
              and getattr(listener.func, '__self__', None) is root
              and getattr(listener.func, '__func__', None) is Script._chain_change_listener)
        parent, queued = root._runs
        waiting, = child._runs
        _need(type(parent) is _QueuedScriptRun and type(queued) is _QueuedScriptRun
              and type(waiting) is _ScriptRun and parent._script is root
              and queued._script is root and waiting._script is child
              and parent.lock_acquired and not queued.lock_acquired
              and parent._started and not queued._started and waiting._started
              and parent._step == 0 and waiting._step == 0
              and parent._action is root.sequence[0]
              and child.sequence is parent._action.get('sequence')
              and waiting._action is child.sequence[0]
              and 'wait_for_trigger' in waiting._action
              and root._queue_lck.locked())
        parent_task, parent_frame = _locate(_QueuedScriptRun.async_run.__code__, parent)
        queue_task, queue_frame = _locate(_QueuedScriptRun.async_run.__code__, queued)
        wait_task, frame = _locate(_ScriptRun._async_wait_with_optional_timeout.__code__, waiting)
        handle, timeout = frame.f_locals['timeout_handle'], frame.f_locals['timeout_future']
        _need(handle is not None and timeout is not None and not handle.cancelled()
              and not timeout.done() and handle.when() > root._hass.loop.time())
        waiters = tuple(root._queue_lck._waiters or ())
        _need(len(waiters) == 1 and not waiters[0].done(),
              'native_active_execution_foreign_queue')
        caches = tuple((script, name, getattr(script, name), _graph(getattr(script, name)))
                       for script in (root, child) for name in _cache_names())
        obj = cls(root, child, root._queue_lck, root.sequence, child.sequence,
                  root.change_listener, listener, caches, waiters,
                  _RunPin.capture(parent, parent_task, parent_frame, queued=True),
                  _RunPin.capture(queued, queue_task, queue_frame, queued=True),
                  _RunPin.capture(waiting, wait_task, frame, queued=False),
                  handle, handle.when(), timeout, frame.f_locals['unsub'])
        obj.check()
        return obj

    def check(self, *, allow_elapsed=False):
        from homeassistant.helpers.script import _ScriptRun, _QueuedScriptRun
        from .native_script_config_transport import _graph
        self.parent.check()
        self.queued.check()
        self.waiting.check()
        _need(self.root._queue_lck is self.queue and self.queue.locked()
              and self.root.sequence is self.root_sequence
              and self.child.sequence is self.child_sequence
              and self.root.change_listener is self.root_listener
              and self.root.script_mode == 'queued'
              and self.root._hass is self.child._hass and self.root.domain == self.child.domain
              and self.root.enabled and self.child.enabled
              and not getattr(self.root, '_unloaded', False)
              and not getattr(self.child, '_unloaded', False)
              and self.child.change_listener is self.listener
              and tuple(self.queue._waiters or ()) == self.queue_waiters
              and all(not waiter.done() for waiter in self.queue_waiters)
              and all(getattr(script, name) is cache and _graph(cache) == image
                      for script, name, cache, image in self.caches)
              and self.root._sequence_scripts.get(0) is self.child
              and tuple(self.root._runs) == (self.parent.run, self.queued.run)
              and tuple(self.child._runs) == (self.waiting.run,)
              and self.parent.action is self.root.sequence[0]
              and self.parent.action.get('sequence') is self.child.sequence
              and self.waiting.action is self.child.sequence[0],
              'native_active_execution_graph_changed')
        _need(_locate(_QueuedScriptRun.async_run.__code__, self.parent.run) == (self.parent.task, self.parent.frame)
              and _locate(_QueuedScriptRun.async_run.__code__, self.queued.run) == (self.queued.task, self.queued.frame),
              'native_active_execution_task_changed')
        task, frame = _locate(_ScriptRun._async_wait_with_optional_timeout.__code__, self.waiting.run)
        _need(task is self.waiting.task and frame is self.waiting.frame
              and frame.f_locals['timeout_handle'] is self.handle
              and frame.f_locals['timeout_future'] is self.timeout
              and frame.f_locals['unsub'] is self.unsubscribe and not self.handle.cancelled()
              and not self.timeout.done() and self.handle.when() == self.deadline
              and (allow_elapsed or self.deadline > self.root._hass.loop.time()),
              'native_active_execution_wait_changed')

    def check_program(self):
        """Retained configured owner, independently of natural run progress."""
        from .native_script_config_transport import _graph
        _need(self.root._queue_lck is self.queue
              and self.root.sequence is self.root_sequence
              and self.child.sequence is self.child_sequence
              and self.root.change_listener is self.root_listener
              and self.child.change_listener is self.listener
              and self.root.script_mode == 'queued'
              and self.root._hass is self.child._hass and self.root.domain == self.child.domain
              and self.root.enabled and self.child.enabled
              and not getattr(self.root, '_unloaded', False)
              and not getattr(self.child, '_unloaded', False)
              and self.root._sequence_scripts.get(0) is self.child
              and self.parent.action is self.root.sequence[0]
              and self.parent.action.get('sequence') is self.child.sequence
              and self.waiting.action is self.child.sequence[0]
              and all(getattr(script, name) is cache and _graph(cache) == image
                      for script, name, cache, image in self.caches),
              'native_active_execution_program_changed')


def capture_census(script):
    from .native_script_config_transport import _idle_census
    if script.is_running or script._runs:
        return ActiveWaitPins.capture(script)
    return _idle_census(script)


def check_census(script, census, *, allow_elapsed=False):
    from .native_script_config_transport import _idle_census
    if type(census) is ActiveWaitPins:
        _need(census.root is script)
        census.check(allow_elapsed=allow_elapsed)
    else:
        _need(_idle_census(script) == census, 'native_active_execution_idle_changed')
