"""Admin-only discovery and narrowly qualified server-owned recovery commands."""
from contextlib import asynccontextmanager

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import callback
from .const import DATA_RUNTIME, DOMAIN, VERSION


@callback
def async_register(hass):
    websocket_api.async_register_command(hass, ws_status)
    websocket_api.async_register_command(hass, ws_discover)
    websocket_api.async_register_command(hass, ws_preview)
    websocket_api.async_register_command(hass, ws_import_preview)
    websocket_api.async_register_command(hass, ws_recovery_inspect)
    websocket_api.async_register_command(hass, ws_recovery_restore)


class _DiscoveryUnauthorized(PermissionError):
    pass


def _send_discovery_error(connection,message,error):
    if isinstance(error,_DiscoveryUnauthorized):
        code,detail = 'unauthorized','Administrator permissions are required'
    else:
        reason = error.args[0] if type(error) is ValueError and error.args else ''
        if reason == 'discovery_invalid_proposal':
            code,detail = 'invalid_proposal','The rename proposal is invalid'
        elif reason == 'discovery_runtime_unavailable':
            code,detail = 'unavailable','Entity Renamer backend was unloaded'
        else:
            code,detail = 'discovery_unavailable','Dependency discovery could not be verified'
    connection.send_error(message['id'],code,detail)


@asynccontextmanager
async def _discovery_output_lease(runtime):
    from . import Runtime
    if type(runtime) is Runtime:
        async with runtime.operation_lock:
            yield
    else:
        yield


async def _read_discovery(hass, connection, message, *, preview, imported=False):
    runtime = hass.data.get(DATA_RUNTIME)
    user_id = connection.user.id
    current_user = connection.user
    native_user_source = None
    if runtime is None or not runtime.loaded or runtime.unloading:
        connection.send_error(message['id'], 'unavailable', 'Entity Renamer backend is not loaded')
        return

    def guard():
        if native_user_source is not None:
            manager,store = native_user_source
            # Real AuthStore is the backing source read by async_get_user.
            # Detect removal/replacement during the final SQL/files await too.
            if (hass.auth is not manager or manager._store is not store
                    or type(store._users) is not dict
                    or store._users.get(user_id) is not current_user):
                raise _DiscoveryUnauthorized()
        if (current_user is None or not current_user.is_active or not current_user.is_admin
                or connection.user.id != user_id or not connection.user.is_admin
                or not connection.user.is_active):
            raise _DiscoveryUnauthorized()
        if (hass.data.get(DATA_RUNTIME) is not runtime or not runtime.loaded or runtime.unloading):
            raise ValueError('discovery_runtime_unavailable')

    async def refresh():
        nonlocal current_user,native_user_source
        guard()
        current_user = await hass.auth.async_get_user(user_id)
        from homeassistant.auth import AuthManager
        if type(hass.auth) is AuthManager:
            native_user_source = (hass.auth,hass.auth._store)
        guard()

    try:
        await refresh()
        if preview:
            result = await runtime.preview(hass, message['proposal'], guard=guard, refresh=refresh)
        else:
            capture = await runtime.discover(hass, message['proposal'], guard=guard, refresh=refresh)
            result = capture.summary()
    except Exception as error:
        _send_discovery_error(connection,message,error)
        return
    # Only the native Runtime's real operation lock is our read lease.
    # Explicit trusted alternate backends retain their handler-owned auth.
    async with _discovery_output_lease(runtime):
        try:
            guard()  # Identity/liveness may have changed while awaiting acquire.
            # The handler owns its final output decision even when backend IO is
            # supplied by another trusted consumer. Re-read role after that await.
            await refresh()
            guard()
            from .discovery_final_fence import _Preview, async_finalize_discovery
            if preview:
                if type(result) is _Preview:
                    from .discovery import compose_preview, compose_import_preview
                    capture = await async_finalize_discovery(hass,result.capture,guard)
                    result = (compose_import_preview(capture) if imported else compose_preview(capture))
            else:
                capture = await async_finalize_discovery(hass,capture,guard)
                result = capture.summary()
            guard()
        except Exception as error:
            _send_discovery_error(connection,message,error)
        else:
            connection.send_result(message['id'], result)


@websocket_api.websocket_command({vol.Required('type'): DOMAIN + '/discover',
                                  vol.Required('proposal'): dict})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_discover(hass, connection, message):
    await _read_discovery(hass, connection, message, preview=False)


@websocket_api.websocket_command({vol.Required('type'): DOMAIN + '/preview',
                                  vol.Required('proposal'): dict})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_preview(hass, connection, message):
    await _read_discovery(hass, connection, message, preview=True)


def _recovery_authority(hass, connection, runtime):
    """Synchronous AuthStore identity/role checks across every native await.

    AuthManager.async_get_user is normal IO, never awaited through AuthStore.
    The store reference is used only for the final synchronous continuation.
    """
    user_id = connection.user.id
    current_user = connection.user
    native_source = None
    def guard():
        if native_source is not None:
            manager, store = native_source
            if (hass.auth is not manager or manager._store is not store
                    or type(store._users) is not dict
                    or store._users.get(user_id) is not current_user):
                raise _DiscoveryUnauthorized()
        if (current_user is None or not current_user.is_active or not current_user.is_admin
                or connection.user is not current_user or connection.user.id != user_id):
            raise _DiscoveryUnauthorized()
        if hass.data.get(DATA_RUNTIME) is not runtime or not runtime.loaded or runtime.unloading:
            raise ValueError('recovery_runtime_unavailable')
    async def refresh():
        nonlocal current_user, native_source
        guard()
        from homeassistant.auth import AuthManager
        if type(hass.auth) is not AuthManager:
            raise _DiscoveryUnauthorized()
        current_user = await hass.auth.async_get_user(user_id)
        native_source = (hass.auth, hass.auth._store)
        guard()
    return guard, refresh


async def _read_recovery(hass, connection, message, *, action):
    from . import Runtime
    runtime = hass.data.get(DATA_RUNTIME)
    if type(runtime) is not Runtime or not runtime.loaded or runtime.unloading:
        connection.send_error(message['id'], 'unavailable', 'Entity Renamer backend is not loaded')
        return
    guard, refresh = _recovery_authority(hass, connection, runtime)
    try:
        await refresh()
        if action == 'status':
            result = await runtime.recovery_status(hass, guard=guard, refresh=refresh)
        elif action == 'inspect':
            result = await runtime.recovery_inspect(hass, message['operation_id'], message['fingerprint'],
                                                    guard=guard, refresh=refresh)
        else:
            result = await runtime.recovery_restore(hass, message['operation_id'], message['fingerprint'],
                                                    guard=guard, refresh=refresh)
        # Native controller owns its final source/history admission; no extra
        # auth await can reopen that gap. The actual backing user is checked now.
        guard()
    except PermissionError:
        connection.send_error(message['id'], 'unauthorized', 'Administrator permissions are required')
    except Exception as error:
        unavailable = type(error) is ValueError and error.args == ('recovery_runtime_unavailable',)
        connection.send_error(message['id'], 'unavailable' if unavailable else 'recovery_unavailable',
                              'Migration recovery could not be verified')
    else:
        connection.send_result(message['id'], result)


@websocket_api.websocket_command({vol.Required('type'): DOMAIN + '/status'})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_status(hass, connection, message):
    await _read_recovery(hass, connection, message, action='status')


@websocket_api.websocket_command({vol.Required('type'): DOMAIN + '/recovery/inspect',
    vol.Required('operation_id'): vol.All(str, vol.Match(r'[A-Za-z0-9_-]{1,80}\Z')),
    vol.Required('fingerprint'): vol.All(str, vol.Match(r'[0-9a-f]{64}\Z'))})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_recovery_inspect(hass, connection, message):
    await _read_recovery(hass, connection, message, action='inspect')


@websocket_api.websocket_command({vol.Required('type'): DOMAIN + '/recovery/restore',
    vol.Required('operation_id'): vol.All(str, vol.Match(r'[A-Za-z0-9_-]{1,80}\Z')),
    vol.Required('fingerprint'): vol.All(str, vol.Match(r'[0-9a-f]{64}\Z'))})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_recovery_restore(hass, connection, message):
    await _read_recovery(hass, connection, message, action='restore')


@websocket_api.websocket_command({vol.Required('type'): DOMAIN + '/import/preview',
    vol.Required('manifest'): vol.All(str, vol.Length(max=4_000_000)),
    vol.Optional('entity_ids'): vol.All([str], vol.Length(max=10000)),
    vol.Optional('device_ids'): vol.All([str], vol.Length(max=10000))})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_import_preview(hass, connection, message):
    """Normalize the approved proposal format into the existing read-only route."""
    from .manifest import decode_import
    try:
        proposal = decode_import(message['manifest'], entity_ids=message.get('entity_ids'),
                                 device_ids=message.get('device_ids'))
    except ValueError as error:
        _send_discovery_error(connection, message, error)
        return
    await _read_discovery(hass, connection, {**message, 'proposal': proposal}, preview=True, imported=True)
