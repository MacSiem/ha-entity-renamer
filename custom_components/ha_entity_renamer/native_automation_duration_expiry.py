"""Read-only attestations of native duration expiry in the active owned graph.

Uses the witness's EXISTING occurrence monitor. Neither this module nor its
callbacks run, edit, cancel, reschedule or replace any native job/Handle.
"""
from __future__ import annotations

import ast
import asyncio
from dataclasses import dataclass
import dis
import inspect
from pathlib import Path
import sys
import textwrap
from types import MappingProxyType

from .native_automation_state_binding import _need, _value
from .native_unload_code_proof import prove_methods, unchanged

# Read from the actual min222/core104222 containers, 2026-10-30. Each runtime
# still compiles and compares the actual source, methods and exact callsites.
# Other stdlib bodies are unsupported; no version-only admission.
_PYTHON_PINS = {
    (3, 13): {
        'asyncio/events.py': '43e8ff927e11361081edc622bb0f0173c0d8c8a1145f9bdd813e8848d5147224',
        'asyncio/base_events.py': 'a3c1e1a008f559a328c44ebfa5d7925845307018d96ee4f4ec0666ec89bd5641',
    },
    (3, 14): {
        'asyncio/events.py': '62e2c619c38407309c0d2c98638642174ee7be5b874676c0225392fed4a00b0f',
        'asyncio/base_events.py': '3e54aa78ed18af0ee5a93384a63fd522496d1805b56ab6561e54c4fdb0869127',
    },
}


def _sites(function, name, *, count=1, argument=None):
    """Exact call expressions, including both debug and ordinary Handle calls."""
    lines, first = inspect.getsourcelines(function)
    tree = ast.parse(textwrap.dedent(''.join(lines)))
    nodes = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if ((isinstance(target, ast.Name) and target.id == name)
                or (isinstance(target, ast.Attribute) and target.attr == name)):
            if argument is None or (node.args and isinstance(node.args[0], ast.Name)
                                    and node.args[0].id == argument):
                nodes.append(node)
    _need(len(nodes) == count, 'duration_expiry_callsite_unsupported')
    offsets = set()
    for node in nodes:
        lo, hi = first + node.lineno - 1, first + node.end_lineno - 1
        offsets.update(i.offset for i in dis.get_instructions(function, show_caches=True)
                       if i.positions.lineno is not None and lo <= i.positions.lineno <= hi
                       and i.opname in ('CALL', 'CALL_KW', 'CALL_FUNCTION_EX', 'CACHE'))
    _need(bool(offsets), 'duration_expiry_callsite_missing')
    return frozenset(offsets)


def _returns(function):
    return frozenset(i.offset for i in dis.get_instructions(function)
                     if i.opname in ('RETURN_VALUE', 'RETURN_CONST'))


@dataclass(eq=False)
class _Expiry:
    route: object
    pending: object
    row: tuple
    image: tuple
    state: str = 'pending'
    frame: object = None
    entry: int | None = None


class NativeDurationExpiry:
    """Per-job, one-shot native F occurrences; no independently owned monitor."""

    def __init__(self, capability):
        from .native_automation_duration_binding import _codes
        from homeassistant.core import HomeAssistant
        self.capability, self.witness, self.records = capability, None, {}
        self.refs = [(asyncio.Handle, '_run'), (asyncio.BaseEventLoop, '_run_once'),
                     (asyncio.TimerHandle, 'cancel')]
        pins = _PYTHON_PINS.get(sys.version_info[:2])
        _need(pins is not None, 'duration_expiry_python_unqualified')
        root = Path(inspect.getsourcefile(asyncio.Handle)).resolve().parent.parent
        self.methods, self.codes = prove_methods(root, pins, self.refs)
        self.signatures = tuple(_value(f) for f in self.methods)
        self.run_job = HomeAssistant.async_run_hass_job
        self.run_action = capability.native.event._run_async_call_action
        parent = capability.native.event.async_track_same_state
        nested = tuple(_codes(parent.__code__))
        self.expiry_code, = (c for c in nested if c.co_name == 'state_for_listener')
        self.clear_code, = (c for c in nested if c.co_name == 'clear_listener')
        # inspect accepts a nested code object; no fake function is created.
        self.sites = {
            'loop_handle': _sites(self.methods[1], '_run', count=2),
            'handle_callback': _sites(self.methods[0], 'run'),
            'timer_job': _sites(self.run_action, 'async_run_hass_job', argument='job'),
            'job_target': _sites(self.run_job, 'target'),
            'clear': _sites(self.expiry_code, 'clear_listener'),
            'action': _sites(self.expiry_code, 'async_run_hass_job', argument='job'),
            'cancel': _sites(self.clear_code, 'async_remove_state_for_cancel'),
            'remove': _sites(capability.native.event._remove_listener, 'remove'),
        }
        self.return_offsets = _returns(self.expiry_code)
        self.clear_returns = _returns(self.clear_code)
        self.remove_returns = _returns(capability.native.event._remove_listener)
        self.clear_entries = frozenset(i.offset for i in dis.get_instructions(self.clear_code)
                                       if i.opname == 'RESUME')
        self.check_sources()

    def check_sources(self):
        unchanged(self.refs, self.methods, self.codes)
        _need(tuple(_value(getattr(o, n)) for o, n in self.refs) == self.signatures
              and asyncio.TimerHandle._run is self.methods[0]
              and getattr(self.capability.hass.loop._run_once, '__func__', None) is self.methods[1],
              'duration_expiry_python_changed')
        # Native.check already binds complete Core methods and their globals.
        from homeassistant.core import HomeAssistant
        _need(HomeAssistant.async_run_hass_job is self.run_job
              and self.capability.native.event._run_async_call_action is self.run_action,
              'duration_expiry_native_changed')

    def attach(self, witness):
        _need(self.witness is None and not self.records
              and witness.automation_graph.duration is self.capability,
              'duration_expiry_owner_changed')
        self.witness = witness

    @staticmethod
    def membership(handle):
        loop = handle._loop
        return (sum(h is handle for h in loop._scheduled),
                sum(h is handle for h in loop._ready))

    def for_clear(self, binding, key, clear):
        found = [r for r in self.records.values() if r.route.binding is binding
                 and r.pending['key'] == key and r.pending['clear'] is clear]
        _need(len(found) <= 1, 'duration_expiry_duplicate_record')
        return found[0] if found else None

    def location(self, handle, binding):
        _need(not handle.cancelled(), 'duration_expiry_handle_cancelled')
        count = self.membership(handle)
        if count == (1, 0):
            return
        # Ready admission is only for an already registered active-route H.
        matches = [r for r in self.records.values() if r.route.binding is binding
                   and r.pending['handle'] is handle and r.state == 'pending']
        _need(count == (0, 1) and len(matches) == 1 and self.witness is not None
              and self.witness.automation_graph.live, 'duration_timer_not_scheduled')
        self._image_check(matches[0])

    def _image(self, route, p):
        from .native_automation_duration_binding import _cell, _retained_value, _history_signature
        b, h = route.binding, p['handle']
        # Native expiry changes ONLY these two cells. Other mutable maps are
        # validated by the normal full census/route snapshot, not by recursion
        # through a historical action closure.
        omitted = {id(_cell(p['clear'], n)) for n in
                   ('async_remove_state_for_cancel', 'async_remove_state_for_listener')}
        omitted.update(map(id, (b.listener_cells['period'], b.listener_cells['unsub_track_same'],
                                b.callbacks, b.original_keys)))
        functions = tuple(_retained_value(p[n], omitted) for n in
                          ('clear', 'cancel', 'cancel_listener', 'predicate', 'action'))
        jobs = tuple((id(p[n]), _retained_value(p[n].target, omitted), p[n].job_type,
                      p[n].name, p[n]._cancel_on_shutdown, _value(p[n]._cache))
                     for n in ('timer_job', 'same_job', 'cancel_job'))
        own = p['own']
        if own is None:
            from .native_automation_duration_binding import _cells
            history = _cells(p['action'])
        else:
            own.check()
            history = own.history()
        event = history['event']
        return (id(b), id(b.config), _value(b.config), tuple(b.config_ids), tuple(p['keys']),
                p['key'], id(p['entity_cell']), _value(p['entity_cell'].cell_contents),
                id(h), h.when(), id(h._loop), id(h._context), id(h._callback),
                tuple(map(id, h._args)), functions, jobs,
                id(event), event.event_type, id(event.context), id(event.origin), _value(event.data),
                id(history['from_s']), id(history['to_s']),
                _history_signature(event,history['from_s'],history['to_s']),
                None if own is None else (id(own), id(own.binding), id(own.change),
                    id(own.clear), id(own.action), id(own.predicate), own.approved_entity,
                    tuple(map(id, own.edges))))

    def _image_check(self, record):
        self.check_sources()
        _need(self._image(record.route, record.pending) == record.image,
              'duration_expiry_retained_changed')
        _need(record.pending['handle']._loop is self.capability.hass.loop
              and not record.pending['handle'].cancelled(), 'duration_expiry_handle_changed')

    def admit(self, route, pending, rows):
        if self.witness is None or not self.witness.automation_graph.live or not route.wrapper_written:
            return
        # Called only by existing synchronous, validated native ingress/install
        # lock sites. Never called by expiry completion to adopt an entire image.
        present = {p['timer_job'] for p in pending}
        for record in self.records.values():
            if record.route is not route or record.state != 'pending':
                continue
            if record.pending['timer_job'] not in present:
                _need(record.pending['handle'].cancelled(), 'duration_unobserved_expiry')
                record.state = 'cancelled'
        for p in pending:
            job = p['timer_job']
            row, = (r for r in rows if r[0] == p['key'] and r[1] == id(p['clear']))
            if job in self.records:
                record = self.records[job]
                _need(record.route is route and record.state == 'pending', 'duration_expiry_record_reused')
                self._image_check(record)
                record.row = row
            else:
                _need(self.membership(p['handle']) == (1, 0), 'duration_preinstall_ready')
                record = _Expiry(route, MappingProxyType(dict(p)), row, self._image(route, p))
                self.records[job] = record

    def _stack(self, record, frame):
        p, h = record.pending, record.pending['handle']
        native_globals = self.run_action.__globals__
        _need(frame.f_code is self.expiry_code and frame.f_globals is native_globals
              and frame.f_locals.get('clear_listener') is p['clear']
              and frame.f_locals.get('hass') is self.capability.hass
              and frame.f_locals.get('job') is p['same_job'], 'duration_expiry_frame_changed')
        job = frame.f_back
        _need(job is not None and job.f_code is self.run_job.__code__
              and job.f_globals is self.run_job.__globals__
              and job.f_lasti in self.sites['job_target']
              and job.f_locals.get('self') is self.capability.hass
              and job.f_locals.get('hassjob') is p['timer_job']
              and type(job.f_locals.get('args')) is tuple and len(job.f_locals['args']) == 1
              and frame.f_locals.get('now') is job.f_locals['args'][0],
              'duration_expiry_job_provenance')
        timer = job.f_back
        _need(timer is not None and timer.f_code is self.run_action.__code__
              and timer.f_globals is native_globals and timer.f_lasti in self.sites['timer_job']
              and timer.f_locals.get('hass') is self.capability.hass
              and timer.f_locals.get('job') is p['timer_job'], 'duration_expiry_timer_provenance')
        run = timer.f_back
        _need(run is not None and run.f_code is self.codes[0]
              and run.f_globals is self.methods[0].__globals__
              and run.f_lasti in self.sites['handle_callback']
              and run.f_locals.get('self') is h, 'duration_expiry_handle_provenance')
        loop = run.f_back
        _need(loop is not None and loop.f_code is self.codes[1]
              and loop.f_globals is self.methods[1].__globals__
              and loop.f_lasti in self.sites['loop_handle']
              and loop.f_locals.get('self') is h._loop and loop.f_locals.get('handle') is h,
              'duration_expiry_loop_provenance')
        _need(self.membership(h) == (0, 0) and not h.cancelled(), 'duration_expiry_membership')

    def _cancel_count(self, record):
        job, b = record.pending['cancel_job'], record.route.binding
        return tuple((key, sum(j is job for j in members)) for key, members in b.callbacks.items()
                     if any(j is job for j in members))

    def _cleanup(self, record):
        from .native_automation_duration_binding import _cells
        cells = _cells(record.pending['clear'])
        return cells['async_remove_state_for_cancel'], cells['async_remove_state_for_listener']

    def active(self, record, *, returning=False):
        """Validate a precise native stage of this SAME executing F frame."""
        _need(record.state == 'active' and record.frame is not None, 'duration_expiry_not_active')
        frame = sys._getframe(1)
        descendant = None
        while frame is not None and frame is not record.frame:
            descendant, frame = frame, frame.f_back
        _need(frame is record.frame, 'duration_expiry_frame_not_executing')
        self._stack(record, frame)
        self._image_check(record)
        cancel, timer = self._cleanup(record)
        p = record.pending
        present = self._cancel_count(record)
        before = ((p['key'], 1),)
        if returning or frame.f_lasti in self.return_offsets:
            _need(frame.f_lasti in self.return_offsets and cancel is None and timer is None and not present,
                  'duration_expiry_terminal_changed')
        elif frame.f_lasti == record.entry:
            _need(cancel is p['cancel'] and getattr(timer, '__self__', None) is p['handle']
                  and getattr(timer, '__func__', None) is self.methods[2] and present == before,
                  'duration_expiry_entry_changed')
        elif frame.f_lasti in self.sites['action']:
            _need(cancel is None and timer is None and not present, 'duration_expiry_action_stage_changed')
        elif frame.f_lasti in self.sites['clear']:
            # Clear has no await. Only exact native clear entry/cancel/return
            # stages may be inspected reentrantly; arbitrary gaps refuse.
            _need(descendant is not None and descendant.f_code is self.clear_code
                  and descendant.f_globals is self.run_action.__globals__
                  and timer is None, 'duration_expiry_clear_stage_changed')
            from .native_automation_duration_binding import _cell
            _need(all(_cell(p['clear'], n).cell_contents is descendant.f_locals.get(n) for n in
                      ('async_remove_state_for_cancel', 'async_remove_state_for_listener')),
                  'duration_expiry_clear_cells_changed')
            if cancel is None:
                _need(not present and descendant.f_lasti in self.clear_returns,
                      'duration_expiry_clear_terminal_changed')
            else:
                _need(cancel is p['cancel']
                      and (descendant.f_lasti in self.sites['cancel']
                           or descendant.f_lasti in self.clear_entries),
                      'duration_expiry_clear_cancel_changed')
                if present != before:
                    # The remove call may have completed its list write before
                    # clear stores None. Pin that exact original inner frame,
                    # callsite and arguments; absence alone grants nothing.
                    inner = sys._getframe(1)
                    while inner is not None and inner.f_back is not descendant:
                        inner = inner.f_back
                    remove = self.capability.native.event._remove_listener
                    _need(not present and descendant.f_lasti in self.sites['cancel']
                          and inner is not None and inner.f_code is remove.__code__
                          and inner.f_globals is remove.__globals__
                          and (inner.f_lasti in self.sites['remove']
                               or inner.f_lasti in self.remove_returns)
                          and inner.f_locals.get('hass') is self.capability.hass
                          and inner.f_locals.get('tracker') is record.route.binding.tracker
                          and inner.f_locals.get('keys') is p['keys']
                          and inner.f_locals.get('job') is p['cancel_job']
                          and inner.f_locals.get('callbacks') is record.route.binding.callbacks,
                          'duration_expiry_remove_stage_changed')
        else:
            _need(False, 'duration_expiry_native_stage_unsupported')
        return bool(present)

    def started(self, frame, offset):
        if frame.f_code is not self.expiry_code:
            return
        matches = [r for r in self.records.values()
                   if frame.f_locals.get('clear_listener') is r.pending['clear']]
        if not matches:
            return  # unrelated native same-state timer
        _need(len(matches) == 1, 'duration_expiry_ambiguous_job')
        record = matches[0]
        _need(self.witness is not None and self.witness.automation_graph.live
              and record.state == 'pending' and record.frame is None,
              'duration_expiry_duplicate_or_inactive')
        self._stack(record, frame)
        record.state, record.frame, record.entry = 'active', frame, frame.f_lasti
        self.active(record)
        self.capability.check_route(record.route)

    def ended(self, frame, failed):
        if frame.f_code is not self.expiry_code:
            return
        matches = [r for r in self.records.values()
                   if frame.f_locals.get('clear_listener') is r.pending['clear']]
        if not matches:
            return
        _need(len(matches) == 1, 'duration_expiry_ambiguous_return')
        record = matches[0]
        _need(not failed and record.state == 'active' and record.frame is frame,
              'duration_expiry_unwind_or_duplicate')
        self.active(record, returning=True)
        # Keep the record active through the full census so this exact row is
        # still projected to its PRE-expiry form; compare every other row.
        actual = self.capability.pending(record.route.binding)[1]
        _need(actual == record.route.duration_snapshot, 'duration_expiry_other_pending_changed')
        expected = record.route.duration_snapshot
        indexes = [i for i, row in enumerate(expected)
                   if row[0] == record.pending['key'] and row[1] == id(record.pending['clear'])]
        _need(len(indexes) == 1 and expected[indexes[0]] == record.row,
              'duration_expiry_expected_row_changed')
        index = indexes[0]
        # This is a DERIVED one-row projection, never lock()/capture current.
        record.route.duration_snapshot = (*expected[:index],
            (record.pending['key'], id(record.pending['clear']), None), *expected[index+1:])
        record.state, record.frame = 'terminal', None
        self.capability.check_route(record.route)
        self.witness._signal()

    def check_terminal(self, record):
        self._image_check(record)
        _need(record.frame is None and self._cleanup(record) == (None, None)
              and self.membership(record.pending['handle']) == (0, 0)
              and not self._cancel_count(record), 'duration_expiry_terminal_changed')

    def detach(self, witness):
        _need(self.witness is witness and not witness.automation_graph.live
              and not any(r.state == 'active' for r in self.records.values()),
              'duration_expiry_retire_changed')
        self.records.clear()
        self.witness = None

    def check_route(self, route):
        for record in self.records.values():
            if record.route is route and record.state == 'terminal':
                self.check_terminal(record)

    def pending(self):
        for record in self.records.values():
            if record.state == 'active':
                return True
            if record.state == 'pending' and self.membership(record.pending['handle']) == (0, 1):
                self._image_check(record)
                return True
        return False

    def before_rollback(self, route):
        # Install -> attempt has NO await. If that contract is violated, refuse
        # before even computing an undo plan; never resurrect an expired job.
        _need(all(r.state == 'pending' for r in self.records.values() if r.route is route),
              'duration_rollback_after_occurrence')
