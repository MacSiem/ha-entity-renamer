"""Admin-only backend status. Mutation commands require complete native coverage."""
import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import callback
from .const import DATA_RUNTIME, DOMAIN, VERSION


@callback
def async_register(hass):
    websocket_api.async_register_command(hass, ws_status)


@websocket_api.websocket_command({vol.Required('type'): DOMAIN + '/status'})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_status(hass, connection, message):
    user_id = connection.user.id
    runtime = hass.data.get(DATA_RUNTIME)
    if runtime is None or not runtime.loaded:
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
    if hass.data.get(DATA_RUNTIME) is not runtime or not runtime.loaded:
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
