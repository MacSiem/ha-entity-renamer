"""Ephemeral ownership of actual native helper setup locks, never authority.

Installed caller must independently bind the exact entry set to the ORIGINAL
Plan/root and hold the admin/editor/coordinator guard and all full-source fences.
No serialization/reopen, lifecycle root, IO capture, replacement baseline, grant,
HA monkeypatch or native field mutation. Only native Lock.acquire/release and
explicit setup/unload calls occur here. Synchronous foreign config/disabled_by
edits remain possible; independent full-three/config/YAML fences must reject them.
"""
from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant


class NativeHelperActivationLeaseError(ValueError):
    """Sanitized refusal without entry titles, configuration or private IDs."""


def _fail(reason: str) -> None:
    raise NativeHelperActivationLeaseError('helper_activation_lease_' + reason) from None


def _authorize(guard: Callable[[], object]) -> None:
    result = guard()
    if inspect.isawaitable(result):
        if inspect.iscoroutine(result):
            result.close()
        _fail('synchronous_guard_required')
    if result is not None and result is not True:
        _fail('guard_rejected')


def _idle(lock: asyncio.Lock) -> bool:
    # Match CPython Lock.acquire's immediate path exactly: a completed but NOT
    # cancelled waiter still owns queue priority. locked() alone is insufficient.
    return (not lock.locked() and (lock._waiters is None or
            all(waiter.cancelled() for waiter in lock._waiters)))


class NativeHelperActivationLease:
    """Finite, task-local scope owning a precise set of real installed locks.

    acquire() never suspends: precheck the WHOLE set, then advance each exact
    asyncio.Lock.acquire coroutine once and require immediate completion. No
    partial-lock waiting, event-loop callback or native API call can intervene.
    check() proves this live scope's acquisitions, manager/entry/lock identities,
    event loop, live owning task and supplied synchronous runtime/admin guard.
    Read-only checks may run in native child tasks; API/cleanup remain owner-only.

    The guard is required installed IO; test no-op guards prove choreography only.
    The object grants no migration authority. Ownership lasts from acquisition
    until context exit/release, including cleanup after guard/cancellation loss.
    """

    __slots__ = ('_hass', '_manager', '_entries', '_locks', '_owned', '_guard',
                 '_task', '_loop', '_live', '_entered', '_core')

    def __init__(self) -> None:
        _fail('acquisition_required')

    @classmethod
    async def acquire(cls, hass: HomeAssistant, manager: ConfigEntries,
                      entries: tuple[ConfigEntry, ...], *,
                      guard: Callable[[], object]) -> NativeHelperActivationLease:
        from homeassistant.config_entries import ConfigEntries, ConfigEntry
        from homeassistant.const import __version__
        from homeassistant.core import HomeAssistant
        from .helper_plan import NATIVE_FIELDS

        if (cls is not NativeHelperActivationLease
                or __version__ not in ('2025.6.0', '2026.10.2')
                or type(hass) is not HomeAssistant
                or type(manager) is not ConfigEntries
                or hass.config_entries is not manager or manager.hass is not hass
                or type(entries) is not tuple or not entries or not callable(guard)):
            _fail('installed_binding_required')
        _authorize(guard)
        if any(type(entry) is not ConfigEntry for entry in entries):
            _fail('native_entry_required')
        if (any(type(entry.entry_id) is not str or not entry.entry_id
                or entry.domain not in NATIVE_FIELDS for entry in entries)
                or len({entry.entry_id for entry in entries}) != len(entries)):
            _fail('entry_scope_invalid')
        entries = tuple(sorted(entries, key=lambda entry: entry.entry_id))
        locks = tuple(entry.setup_lock for entry in entries)
        if (any(type(lock) is not asyncio.Lock for lock in locks)
                or len({id(lock) for lock in locks}) != len(locks)):
            _fail('native_lock_required')
        obj = object.__new__(cls)
        obj._hass, obj._manager, obj._entries, obj._locks = hass, manager, entries, locks
        obj._owned = []
        obj._guard = guard
        obj._task, obj._loop = asyncio.current_task(), asyncio.get_running_loop()
        obj._live, obj._entered, obj._core = False, False, __version__
        if obj._task is None:
            _fail('task_required')
        try:
            obj._binding()
            # All checks BEFORE the first acquisition. Guard can cancel/refuse;
            # no partial ownership is then taken and no native API is called.
            if any(not _idle(lock) for lock in locks):
                _fail('busy')
            for lock in locks:
                coroutine = lock.acquire()
                try:
                    coroutine.send(None)
                except StopIteration as done:
                    if done.value is not True or not lock.locked():
                        _fail('acquisition_unknown')
                    obj._owned.append(lock)
                else:
                    # Impossible for exact asyncio.Lock after the synchronous
                    # precheck on the same event-loop task; never await it.
                    _fail('acquisition_suspended')
                finally:
                    coroutine.close()
            obj._live = True
            obj.check()
            return obj
        except BaseException:
            # Includes CancelledError raised by the required synchronous guard.
            obj._release()
            raise

    @property
    def entry_ids(self) -> tuple[str, ...]:
        self.check()
        return tuple(entry.entry_id for entry in self._entries)

    @property
    def manager(self) -> ConfigEntries:
        self.check()
        return self._manager

    def _binding(self) -> None:
        from homeassistant.const import __version__
        _authorize(self._guard)
        if (self._task.done()
                or asyncio.get_running_loop() is not self._loop
                or __version__ != self._core
                or self._hass.loop is not self._loop
                or self._hass.config_entries is not self._manager
                or self._manager.hass is not self._hass):
            _fail('runtime_changed')
        for entry, lock in zip(self._entries, self._locks, strict=True):
            if (self._manager.async_get_entry(entry.entry_id) is not entry
                    or entry.setup_lock is not lock):
                _fail('entry_changed')

    def check(self) -> None:
        """Synchronous boundary check; cannot be replaced by lock.locked()."""
        if not self._live or len(self._owned) != len(self._locks):
            _fail('not_owned')
        self._binding()
        if any(owned is not lock or not lock.locked() for owned, lock in
               zip(self._owned, self._locks, strict=True)):
            _fail('ownership_lost')

    def _entry(self, entry_id: str) -> ConfigEntry:
        self.check()
        if type(entry_id) is not str:
            _fail('entry_outside_scope')
        for entry in self._entries:
            if entry.entry_id == entry_id:
                return entry
        _fail('entry_outside_scope')

    async def async_setup(self, entry_id: str) -> bool:
        self._task_check()
        entry = self._entry(entry_id)
        # Otherwise manager.async_setup bootstraps the entire domain and its
        # async_setup_locked callbacks would reacquire our lock and deadlock.
        if entry.domain not in self._hass.config.components:
            _fail('component_bootstrap_required')
        self.check()
        result = await self._manager.async_setup(entry_id, _lock=False)
        self.check()
        return result

    async def async_unload(self, entry_id: str) -> bool:
        self._task_check()
        self._entry(entry_id)
        self.check()
        result = await self._manager.async_unload(entry_id, _lock=False)
        self.check()
        return result

    def _task_check(self) -> None:
        if (asyncio.current_task() is not self._task
                or asyncio.get_running_loop() is not self._loop):
            _fail('runtime_changed')

    async def __aenter__(self) -> NativeHelperActivationLease:
        self._task_check()
        if self._entered:
            _fail('scope_reentered')
        try:
            self.check()
            self._entered = True
            return self
        except BaseException:
            self._release()
            raise

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self._task_check()
        # Always release, even after cancellation/admin loss/stale manager. Never
        # require the guard to permit cleanup or touch replacement entry locks.
        try:
            if exc_type is None:
                self.check()
        finally:
            self._release()

    def _release(self) -> None:
        self._live = False
        while self._owned:
            lock = self._owned.pop()
            if lock.locked():
                lock.release()

    def release(self) -> None:
        """Explicit finite cleanup by the acquiring task, including before enter."""
        self._task_check()
        self._release()
