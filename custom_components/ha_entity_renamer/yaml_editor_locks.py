"""Share native HA YAML editor locks without replacing HTTP handlers.

This protects native editor read/modify/write cycles only. External filesystem
writers still require byte/inode checks; runtime activation is a separate gate.
"""
from contextlib import asynccontextmanager
import asyncio
import inspect


class EditorLockConflict(ValueError):
    pass


class NativeYAMLEditorLocks:
    def __init__(self, hass):
        from homeassistant.const import __version__
        from homeassistant.components.config.view import BaseEditConfigView
        if __version__ not in ('2025.6.0', '2026.10.2'):
            raise EditorLockConflict('unsupported_editor_runtime')
        self.hass = hass
        self.base_class = BaseEditConfigView

    def enumerate(self):
        """Resolve the actual registered view, never create a replacement lock."""
        http = getattr(self.hass, 'http', None)
        if http is None:
            raise EditorLockConflict('native_http_unavailable')
        found = {}
        for route in http.app.router.routes():
            if route.method not in ('POST', 'DELETE', 'PUT', 'PATCH'):
                continue
            path = route.resource.canonical
            handler = route.handler
            if not inspect.isfunction(handler):
                if path.startswith('/api/config/') and '{config_key}' in path:
                    raise EditorLockConflict('unknown_editor_handler')
                continue
            view = inspect.getclosurevars(handler).nonlocals.get('view')
            if not isinstance(view, self.base_class):
                if path.startswith('/api/config/') and '{config_key}' in path:
                    raise EditorLockConflict('unknown_editor_view')
                continue
            if (handler.__module__ != 'homeassistant.components.http.view'
                    or type(view.mutation_lock) is not asyncio.Lock
                    or type(view.path) is not str or not view.path):
                raise EditorLockConflict('unsupported_editor_layout')
            previous = found.get(view.name)
            item = (view.path, view.name, view, view.mutation_lock)
            if previous is not None and previous != item:
                raise EditorLockConflict('ambiguous_editor_identity')
            found[view.name] = item
        return tuple(sorted(found.values(), key=lambda item: (item[0], item[1])))

    @asynccontextmanager
    async def hold(self, guard):
        """Acquire deterministically; revalidate identity and role after awaits.

        Caller must not await a global HA task drain while holding these locks:
        another native editor request may itself be waiting for this lease.
        """
        guard()
        baseline = self.enumerate()
        acquired = []
        try:
            for _, _, _, lock in baseline:
                if any(lock is held for held in acquired):
                    continue
                await lock.acquire()
                acquired.append(lock)
                guard()
            if self.enumerate() != baseline:
                raise EditorLockConflict('editor_inventory_changed')
            guard()
            yield baseline
        finally:
            for lock in reversed(acquired):
                lock.release()
