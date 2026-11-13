"""Pinned native Automation and Script callbacks for the controlled 11.1 route."""

from pathlib import Path

from .native_unload_code_proof import prove_methods

_PINS = {
    'components/automation/entity.py': '6535d1a79b1d490589f9b026e592f20635f91da7c04fd16f1a8ee7232cb936a3',
    'components/automation/util.py': '06bba3b86144c5e8c4ddc5fd99c62755bd13c38e6344f0941cd433cb776e1f83',
    'components/homeassistant/triggers/event.py': 'f07fd8d6ed17a4ac7a84086a6c77fa6bbf15acdea18829b901fd4d43b4f06aa1',
    'components/homeassistant/triggers/state.py': 'ee25bb4f98729fa7c9755440b7febcc97326c1225407cdf76f92ef6b5bf92ceb',
    'components/script/entity.py': '4cd2fe7bb68299e00d5d22c1f4284c52e490a9cf88f3a7f66670ac766c795d6a',
    'components/script/services.py': '21109f6f45128fac4d45d448bb37f26632db2b6203a93dd928ddcb85dc1d5770',
    'components/script/util.py': 'ee4700affe91152c727625c6a4baadab72f0ccdd751ee1f3fd91369230123a2d',
    'core.py': '5671149ec3405c4ec81eed05f58e3c392dafe0af141ec6ac7ea1a2b4b658d038',
    'helpers/entity.py': 'd1f254d6eeabc2d88b5327151a2cee9ee465d420da3fda61d720e8561b2d9ac3',
    'helpers/entity_component.py': '93586d2486e33b6f2f987f5cf163caeda700bf00a42f91b9dd3134c7a7efd9bf',
    'helpers/entity_platform.py': '18014aa32bd7b1f0a80044166d9a88d7250a96120d9a62116de6f13a318121c8',
    'helpers/event.py': '35a453cdc015a58ce9e49f2dec4da042babd261930a758eac97a7c2138ba0960',
    'helpers/script.py': '4e7e2e4ac0b2bb43e9f2d56c785e7851c7e01967eefb17e52ca1358ab9824efa',
    'helpers/trigger/__init__.py': '95dc9c2c2ac66d88b9d322e74881b26283b76d025bc684059731694e0b841586',
}


def prove(refs):
    import homeassistant

    return prove_methods(Path(homeassistant.__file__).resolve().parent, _PINS, refs)


def prove_automation_projection():
    from homeassistant.components.automation.entity import AutomationEntity
    from homeassistant.components.homeassistant.triggers import event, state
    from homeassistant.core import EventBus
    from homeassistant.helpers import event as track, trigger
    from homeassistant.helpers.entity import Entity

    prove(((AutomationEntity, '_async_trigger_if_enabled'),
           (AutomationEntity, '_async_attach_triggers'),
           (Entity, 'async_write_ha_state'),
           (trigger, 'async_initialize_triggers'), (trigger, '_trigger_action_wrapper'),
           (event, 'async_attach_trigger'), (state, 'async_attach_trigger'),
           (track, '_remove_listener'), (track, '_async_dispatch_entity_id_event'),
           (track, '_async_state_filter'), (track, 'process_state_match'),
           (EventBus, '_async_remove_listener')))
    if AutomationEntity.async_write_ha_state is not Entity.async_write_ha_state:
        raise ValueError('native_automation_state_writer_changed')


def script_reload_token(hass):
    from homeassistant.components.script import services, util
    from homeassistant.components.script.entity import ScriptEntity
    from homeassistant.core import HassJob, HassJobType, Service, ServiceRegistry, SupportsResponse
    from homeassistant.helpers.entity_component import EntityComponent
    from homeassistant.helpers.script import Script

    prove(((services, '_reload_service'), (util, 'async_process_config'),
           (util, '_prepare_script_config'), (util, '_create_script_entities'),
           (EntityComponent, 'async_prepare_reload'), (ScriptEntity, 'async_added_to_hass'),
           (ScriptEntity, 'async_will_remove_from_hass'), (Script, 'async_unload'),
           (ServiceRegistry, 'async_call'), (ServiceRegistry, '_execute_service')))
    registry = hass.services
    service = registry._services.get('script', {}).get('reload')
    job = getattr(service, 'job', None)
    if (type(registry) is not ServiceRegistry or registry._hass is not hass
            or type(service) is not Service or type(job) is not HassJob
            or job.target is not services._reload_service
            or job.job_type is not HassJobType.Coroutinefunction
            or service.supports_response is not SupportsResponse.NONE
            or service.schema is not services.RELOAD_SERVICE_SCHEMA
            or type(service.schema.schema) is not dict or service.schema.schema
            or registry.async_call.__self__ is not registry
            or registry.async_call.__func__ is not ServiceRegistry.async_call
            or registry._execute_service.__self__ is not registry
            or registry._execute_service.__func__ is not ServiceRegistry._execute_service):
        raise ValueError('native_script_reload_registration_changed')
    return registry, service, job, job.target, service.schema
