"""Native lifecycle for Entity Renamer's durable migration backend.

Imports remain lazy so pure planners can run without Home Assistant installed.
"""
from dataclasses import dataclass, field
import asyncio

from .const import DATA_RUNTIME, DATA_WEBSOCKETS, DOMAIN


def CONFIG_SCHEMA(config):
    from homeassistant.helpers.config_validation import config_entry_only_config_schema
    return config_entry_only_config_schema(DOMAIN)(config)


@dataclass
class Runtime:
    entry_id: str
    journal: object
    preparations: object
    operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    loaded: bool = True
    unloading: bool = False

    async def _recovery(self, hass, action, operation_id=None, fingerprint=None, *, guard, refresh):
        from .recovery_controller import RecoveryController
        def current():
            guard()
            if (hass.data.get(DATA_RUNTIME) is not self or not self.loaded or self.unloading):
                raise ValueError('recovery_runtime_unavailable')
        current()
        async with self.operation_lock:
            current()  # Fresh identity/role after the native FIFO lock wait.
            await refresh()
            current()
            controller = RecoveryController(hass, self, guard=current, refresh=refresh)
            if action == 'status':
                result = await controller.status()
            elif action == 'inspect':
                result = await controller.inspect(operation_id, fingerprint)
            elif action == 'restore':
                result = await controller.restore(operation_id, fingerprint)
            else:
                raise ValueError('recovery_invalid_action')
            current()
            return result

    async def recovery_status(self, hass, *, guard, refresh):
        return await self._recovery(hass, 'status', guard=guard, refresh=refresh)

    async def recovery_inspect(self, hass, operation_id, fingerprint, *, guard, refresh):
        return await self._recovery(hass, 'inspect', operation_id, fingerprint, guard=guard, refresh=refresh)

    async def recovery_restore(self, hass, operation_id, fingerprint, *, guard, refresh):
        return await self._recovery(hass, 'restore', operation_id, fingerprint, guard=guard, refresh=refresh)

    async def native_forward(self, hass, mapping, *, guard, refresh):
        """Private qualified component, not a public migration admission route.

        The same Runtime lock spans fresh authorization, prepare, native effects
        and drain. The native lease belongs to the owned operation task; request
        cancellation cannot release this lock while its IO still runs.
        """
        from .native_template_forward import NativeTemplateForward
        from .recovery_controller import _drain_restore as drain_effect
        def current():
            guard()
            if (hass.data.get(DATA_RUNTIME) is not self or not self.loaded or self.unloading):
                raise ValueError('forward_runtime_unavailable')
        current()
        async with self.operation_lock:
            current()
            await refresh()
            current()
            async def operation():
                session = await NativeTemplateForward.prepare(hass, mapping,
                    guard=current, refresh=refresh)
                try:
                    return await session.apply()
                finally:
                    session.close()
            record = await drain_effect(operation())
            current()
            # No Plan, receipt, prepared refs or source documents cross this
            # boundary. Global/family readiness is still explicitly false.
            return {'operation_id': record.operation_id,
                'fingerprint': record.plan.fingerprint, 'state': record.status,
                'reason': record.reason, 'migration_ready': False, 'family_complete': False}

    async def discover(self, hass, proposal, *, guard, refresh):
        """Server-owned read under the same lease used by migration/recovery.

        Trusted callers provide fresh authorization IO, never a request field.
        This neither creates preparations nor changes an original recovery plan.
        """
        from .discovery import async_discover
        def current():
            guard()
            if (hass.data.get(DATA_RUNTIME) is not self or not self.loaded
                    or self.unloading):
                raise ValueError('discovery_runtime_unavailable')
        current()
        async with self.operation_lock:
            current()
            await refresh()
            current()
            result = await async_discover(hass, proposal, current)
            current()
            await refresh()
            current()
            from .discovery_final_fence import async_finalize_discovery
            result = await async_finalize_discovery(hass,result,current)
            current()
            return result

    async def preview(self, hass, proposal, *, guard, refresh):
        """Use the very same discovery for the server's conflict preview."""
        from .discovery import compose_preview
        capture = await self.discover(hass, proposal, guard=guard, refresh=refresh)
        result = compose_preview(capture)
        from .discovery import DiscoveryCapture
        if type(capture) is DiscoveryCapture:
            from .discovery_final_fence import _Preview
            return _Preview(result,capture)
        return result


async def async_setup(hass, config):
    return True


async def async_setup_entry(hass, entry):
    from homeassistant.exceptions import ConfigEntryNotReady
    from .journal import native_journal
    from .prepared import native_prepared_store
    from .websocket_api import async_register

    if hass.data.get(DATA_RUNTIME) is not None:
        raise ConfigEntryNotReady('An Entity Renamer backend is already loaded')
    journal, preparations = native_journal(hass), native_prepared_store(hass)
    # Never turn a damaged or unconfirmed journal into an empty history.
    try:
        await journal.initialize()
        await preparations.initialize()
    except Exception as error:
        raise ConfigEntryNotReady('Migration recovery data could not be verified') from error
    runtime = Runtime(entry.entry_id, journal, preparations)
    entry.runtime_data = runtime
    if not hass.data.get(DATA_WEBSOCKETS):
        async_register(hass)
        hass.data[DATA_WEBSOCKETS] = True
    hass.data[DATA_RUNTIME] = runtime
    return True


async def async_unload_entry(hass, entry):
    runtime = hass.data.get(DATA_RUNTIME)
    if runtime is None or runtime.entry_id != entry.entry_id:
        return False
    # Returning False leaves native HA in FAILED_UNLOAD. Drain the current
    # operation while refusing new work, then let HA complete its lifecycle.
    runtime.unloading = True
    try:
        async with runtime.operation_lock:
            runtime.loaded = False
            del hass.data[DATA_RUNTIME]
    finally:
        runtime.unloading = False
    # Private preparations/journal survive reload/removal for explicit recovery.
    return True
