"""Real native scheduled helper reload versus product settle under its lease.

No hand-made waiter Task, tracking-set edits, patched native methods or timeouts.
The observed native await cycle wakes only the test's diagnostic Future; cleanup
cancels the read-only owner and lets the actual scheduled reload finish.
Owner runs each case on each pinned Core in its own FIFO-owned process.
"""
import asyncio
import hashlib
import importlib
import json
from pathlib import Path
import sys
from types import CodeType
import unittest

import test_native_helper_quiescence as fixture
import test_native_template_consumers as consumers
import test_receipted_helper_native as receipted

_PINS = {
    '2026.10.2': ('1fe2fcfb91fbb6edfb4450dad3ae5ebc4e325664770fedbd607d103d91ce4f19',
                 '221e2672e9a8f0ec12fa62edbfdc2ef6d986331855797615c81ecbe99f8840db'),
    '2025.6.0': ('1b4eee1e4fb701669a4ed941f32bb36a977ce446dfa2c8d1ffb0beadc5a031a2',
                 '306340f94f1387bd6b73162cbc2c37ae1c095025d34b318fcf10b8b6c40dcffb'),
}


def _codes(code):
    yield code
    for value in code.co_consts:
        if isinstance(value, CodeType):
            yield from _codes(value)


def _await_frames(task):
    value = task.get_coro()
    seen = set()
    while value is not None and id(value) not in seen:
        seen.add(id(value))
        frame = getattr(value, 'cr_frame', None) or getattr(value, 'gi_frame', None)
        if frame is not None:
            yield frame
        value = getattr(value, 'cr_await', None) or getattr(value, 'gi_yieldfrom', None)


class _NativeWaitFacts:
    def __init__(self, case, entry):
        from homeassistant import core, config_entries
        self.case, self.hass, self.entry = case, case.f.hass, entry
        self.lock, self.manager = entry.setup_lock, self.hass.config_entries
        self.owner = self.reload = self.lease = None
        self.error = None
        self.reload_seen = self.hass.loop.create_future()
        self.cycle_seen = self.hass.loop.create_future()
        self.methods = (core.HomeAssistant.async_block_till_done,
                        core.HomeAssistant._await_and_log_pending,
                        config_entries.ConfigEntries.async_schedule_reload,
                        config_entries.ConfigEntries.async_reload)
        self.fixed_codes = tuple(method.__code__ for method in self.methods)
        self.lock_code = asyncio.Lock.acquire.__code__
        self.tool = None

    def prove(self):
        from homeassistant import core, config_entries
        from homeassistant.const import __version__
        self.case.assertIn(__version__, _PINS)
        self.case.assertIs(type(self.hass), core.HomeAssistant)
        self.case.assertIs(type(self.manager), config_entries.ConfigEntries)
        self.case.assertEqual((core.HomeAssistant.async_block_till_done,
                               core.HomeAssistant._await_and_log_pending,
                               config_entries.ConfigEntries.async_schedule_reload,
                               config_entries.ConfigEntries.async_reload), self.methods)
        for module, sha in zip((core, config_entries), _PINS[__version__], strict=True):
            path = Path(module.__file__).resolve()
            raw = path.read_bytes()
            self.case.assertEqual(hashlib.sha256(raw).hexdigest(), sha)
            source_codes = {c.co_qualname: c for c in _codes(compile(raw, str(path), 'exec'))}
            for method in self.methods:
                if method.__module__ == module.__name__:
                    self.case.assertEqual(method.__code__, source_codes[method.__qualname__])
        self.case.assertEqual(tuple(m.__code__ for m in self.methods), self.fixed_codes)
        self.case.assertIs(self.lock, self.entry.setup_lock)
        self.case.assertIs(type(self.lock), asyncio.Lock)

    def observe(self, code, offset, value):
        try:
            frame = sys._getframe()
            while frame is not None and frame.f_code is not code:
                frame = frame.f_back
            if frame is None:
                raise AssertionError('native monitored frame not found')
            task = asyncio.current_task()
            if code is self.lock_code and frame.f_locals.get('self') is self.lock:
                matches = [f for f in _await_frames(task)
                           if f.f_code is self.fixed_codes[3]
                           and f.f_locals.get('self') is self.manager
                           and f.f_locals.get('entry_id') == self.entry.entry_id]
                if matches and task is not self.owner:
                    self.case.assertIs(type(task), asyncio.Task)
                    self.reload = task
                    if not self.reload_seen.done():
                        self.reload_seen.set_result(task)
            if code is self.fixed_codes[1] and task is self.owner:
                pending = frame.f_locals.get('pending', ())
                if self.reload is not None and self.reload in pending:
                    self.case.assertIn(self.reload, self.hass._tasks)
                    self.case.assertNotIn(self.reload, self.hass._background_tasks)
                    self.case.assertTrue(self.lock.locked())
                    self.case.assertTrue(self.lease.owns_entry(self.entry))
                    self.case.assertIn(self.reload._fut_waiter, self.lock._waiters)
                    self.case.assertFalse(self.reload._fut_waiter.done())
                    if not self.cycle_seen.done():
                        self.cycle_seen.set_result(True)
        except BaseException as error:
            self.error = error
            if not self.cycle_seen.done():
                self.cycle_seen.set_result(False)

    def install(self):
        for ident in range(6):  # These are Python's six actual monitoring slots.
            try:
                sys.monitoring.use_tool_id(ident, 'native foreground settle facts')
            except ValueError:
                continue
            self.tool = ident
            break
        if self.tool is None:
            raise AssertionError('no native monitoring slot available')
        sys.monitoring.register_callback(self.tool, sys.monitoring.events.PY_YIELD, self.observe)
        for code in (self.lock_code, self.fixed_codes[1]):
            sys.monitoring.set_local_events(self.tool, code, sys.monitoring.events.PY_YIELD)

    def close(self):
        if self.tool is not None:
            for code in (self.lock_code, self.fixed_codes[1]):
                sys.monitoring.set_local_events(self.tool, code, 0)
            sys.monitoring.register_callback(self.tool, sys.monitoring.events.PY_YIELD, None)
            sys.monitoring.free_tool_id(self.tool)


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeForegroundSettleTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.NativeHelperQuiescenceTests.asyncSetUp
    _before_start = receipted.NativeReceiptedHelperTests._before_start
    _flush_documents = fixture.NativeHelperQuiescenceTests._flush_documents
    prepare = consumers.NativeTemplateConsumerTests.prepare
    emit = consumers.NativeTemplateConsumerTests.emit
    with_yaml = False

    async def _case(self, kind):
        await self.prepare('entry')
        await self.emit(5)
        module = lambda name: importlib.import_module(fixture.fixtures.PACKAGE + '.' + name)
        hass = self.f.hass
        entry = hass.config_entries.async_get_entry(self.entry_id)
        original = module('helper_native').read_entry(hass, self.entry_id)
        source_uuid = self.f.entity.async_get(self.source_id).id
        recorder = module('recorder_guard').NativeRecorderGuard(hass)
        history = module('history_guard').NativeHistoryGuard(recorder)
        source_history = await history.capture({self.source_id: self.source_id})
        consumer_history = await history.capture({self.consumer.entity_id: self.consumer.entity_id})
        self.assertEqual(len(source_history['state_ids']), 1)
        self.assertEqual(len(consumer_history['state_ids']), 1)
        facts = _NativeWaitFacts(self, entry)
        await hass.async_add_executor_job(facts.prove)

        async def owner():
            facts.lease = await module('native_helper_activation_lease').NativeHelperActivationLease.acquire(
                hass, hass.config_entries, (entry,), guard=lambda: None)
            try:
                # This public Core API itself creates the real foreground task.
                hass.config_entries.async_schedule_reload(self.entry_id)
                reload = await facts.reload_seen
                self.assertIn(reload, hass._tasks)
                self.assertNotIn(reload, hass._background_tasks)
                if kind == 'recorder':
                    return await recorder.snapshot({self.source_id: self.source_id})
                return await module('native_registry').NativeRegistryIO(hass).settle()
            finally:
                facts.lease.release()

        cycle = False
        try:
            facts.install()
            facts.owner = asyncio.create_task(owner())
            await asyncio.wait((facts.owner, facts.cycle_seen), return_when=asyncio.FIRST_COMPLETED)
            if facts.cycle_seen.done():
                cycle = facts.cycle_seen.result()
                facts.owner.cancel()  # Read-only work, before the blocked reload can act.
            result = await asyncio.gather(facts.owner, return_exceptions=True)
            if cycle:
                self.assertIsInstance(result[0], asyncio.CancelledError)
            elif isinstance(result[0], BaseException):
                raise result[0]
            elif kind == 'recorder':
                self.assertEqual(set(result[0]), {'states', 'statistics'})
                self.assertEqual(result[0]['states'][self.source_id]['metadata_id'],
                                 source_history['state_ids'][0])
            self.assertIsNone(facts.error)
        finally:
            if facts.owner is not None and not facts.owner.done():
                facts.owner.cancel()
                await asyncio.gather(facts.owner, return_exceptions=True)
            facts.close()
            if facts.reload is not None:
                self.assertTrue(await facts.reload)
        self.assertIsNone(facts.error)
        self.assertIs(hass.config_entries.async_get_entry(self.entry_id), entry)
        self.assertEqual(entry.state.value, 'loaded')
        self.assertFalse(entry.setup_lock.locked())
        self.assertEqual(module('helper_native').read_entry(hass, self.entry_id), original)
        await self.emit(7)
        self.assertEqual(self.f.entity.async_get(self.source_id).id, source_uuid)
        self.assertEqual(self.f.entity.async_get(self.consumer.entity_id).id, self.consumer_uuid)
        self.assertTrue(await history.verify(source_history))
        self.assertTrue(await history.verify(consumer_history))
        await hass.async_add_executor_job(facts.prove)
        print(json.dumps({'kind': 'native_foreground_settle', 'operation': kind,
                          'native_reload_foreground': True, 'observed_wait_cycle': cycle}))
        self.assertFalse(cycle, 'product global drain waits on native reload blocked by its own helper lease')

    async def test_real_foreground_helper_reload_does_not_block_recorder_snapshot(self):
        await self._case('recorder')

    async def test_real_foreground_helper_reload_does_not_block_registry_settle(self):
        await self._case('registry')
