"""Admin-only backend status. Mutation commands require complete native coverage."""
import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import callback
from .const import DATA_RUNTIME, DOMAIN, VERSION


@callback
def async_register(hass):
    websocket_api.async_register_command(hass, ws_status)
    websocket_api.async_register_command(hass, ws_discover)
    websocket_api.async_register_command(hass, ws_preview)


class _DiscoveryUnauthorized(PermissionError):
    pass


async def _read_discovery(hass, connection, message, *, preview):
    runtime = hass.data.get(DATA_RUNTIME)
    user_id = connection.user.id
    current_user = connection.user
    if runtime is None or not runtime.loaded or runtime.unloading:
        connection.send_error(message['id'], 'unavailable', 'Entity Renamer backend is not loaded')
        return

    def guard():
        if (current_user is None or not current_user.is_active or not current_user.is_admin
                or connection.user.id != user_id or not connection.user.is_admin
                or not connection.user.is_active):
            raise _DiscoveryUnauthorized()
        if (hass.data.get(DATA_RUNTIME) is not runtime or not runtime.loaded or runtime.unloading):
            raise ValueError('discovery_runtime_unavailable')

    async def refresh():
        nonlocal current_user
        guard()
        current_user = await hass.auth.async_get_user(user_id)
        guard()

    try:
        await refresh()
        if preview:
            result = await runtime.preview(hass, message['proposal'], guard=guard, refresh=refresh)
        else:
            capture = await runtime.discover(hass, message['proposal'], guard=guard, refresh=refresh)
            result = capture.summary()
        # The handler owns its final output decision even when backend IO is
        # supplied by another trusted consumer. Re-read role after that await.
        await refresh()
        guard()
    except _DiscoveryUnauthorized:
        connection.send_error(message['id'], 'unauthorized', 'Administrator permissions are required')
        return
    except Exception as error:
        reason = error.args[0] if type(error) is ValueError and error.args else ''
        if reason == 'discovery_invalid_proposal':
            code, detail = 'invalid_proposal', 'The rename proposal is invalid'
        elif reason == 'discovery_runtime_unavailable':
            code, detail = 'unavailable', 'Entity Renamer backend was unloaded'
        else:
            code, detail = 'discovery_unavailable', 'Dependency discovery could not be verified'
        connection.send_error(message['id'], code, detail)
        return
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


@websocket_api.websocket_command({vol.Required('type'): DOMAIN + '/status'})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_status(hass, connection, message):
    user_id = connection.user.id
    runtime = hass.data.get(DATA_RUNTIME)
    if runtime is None or not runtime.loaded or runtime.unloading:
        connection.send_error(message['id'], 'unavailable', 'Entity Renamer backend is not loaded')
        return
    try:
        active = await runtime.journal.active()
        user = await hass.auth.async_get_user(user_id)
    except Exception:
        connection.send_error(message['id'], 'recovery_unavailable', 'Migration recovery status is unavailable')
        return
    if (user is None or not user.is_active or not user.is_admin
            or connection.user.id != user_id or not connection.user.is_admin):
        connection.send_error(message['id'], 'unauthorized', 'Administrator permissions are required')
        return
    if hass.data.get(DATA_RUNTIME) is not runtime or not runtime.loaded or runtime.unloading:
        connection.send_error(message['id'], 'unavailable', 'Entity Renamer backend was unloaded')
        return
    connection.send_result(message['id'], {
        'version': VERSION,
        'migration_ready': False,
        'reason': 'dependency_coverage_incomplete',
        'recovery_required': bool(active),
        'operations': [{'operation_id': record.operation_id, 'status': record.status,
                        'direction': record.direction} for record in active],
    })
