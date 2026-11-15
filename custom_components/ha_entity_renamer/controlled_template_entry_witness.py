"""Read-only registered Template entry evidence for a controlled reload."""
from .helper_plan import snapshot_equal
from .helper_transaction import PreparedHelper
from .transaction import Refused


def _need(value):
    if not value:
        raise Refused('controlled_template_entry_witness_unconfirmed')


def _check_live_issue(hass, entry_id):
    from homeassistant.helpers import issue_registry as ir
    manager = hass.data.get(ir.DATA_REGISTRY)
    _need(type(manager) is ir.IssueRegistry
          and ('template', 'composite_device_id_' + entry_id) not in manager.issues)


class ControlledTemplateEntryWitness:
    def __init__(self, hass, change, native, source_uuid, *, guard):
        _need(type(change) is PreparedHelper and change.before['domain'] == 'template'
              and len(change.mapping) == 1 and type(source_uuid) is str and callable(guard))
        change.plan()
        self.hass, self.change, self.native, self.guard = hass, change, native, guard
        self.entry_id = change.before['identity']['entry_id']
        self.source_uuid = source_uuid
        self.entry = hass.config_entries.async_get_entry(self.entry_id)
        _need(self.entry is not None)

    def sample(self, documents):
        from homeassistant.helpers import entity_platform as ep, entity_registry as er
        from .helper_native import read_entry
        from .native_config_transition import _projection
        from .native_template_consumers import _live_sensor, _registration
        from .native_utility_unload_audit import _row
        from .native_template_entry_lifecycle import no_devices
        from .yaml_validation_worker import template_entry_semantic
        _need(self.guard() in (None, True) and self.hass.config_entries.async_get_entry(self.entry_id) is self.entry)
        _check_live_issue(self.hass, self.entry_id)
        configs = [r for r in documents['core.config_entries']['data']['entries'] if r.get('entry_id') == self.entry_id]
        _need(len(configs) == 1)
        snapshot = _projection(configs[0])
        _need((snapshot_equal(snapshot, self.change.before) or snapshot_equal(snapshot, self.change.after))
              and snapshot_equal(read_entry(self.hass, self.entry_id), snapshot))
        rows = [r for r in documents['core.entity_registry']['data']['entities'] if r.get('config_entry_id') == self.entry_id]
        _need(len(rows) == 1 and rows[0].get('platform') == 'template'
              and rows[0].get('unique_id') == self.entry_id and rows[0].get('disabled_by') is None
              and rows[0].get('device_id') is None and rows[0].get('config_subentry_id') is None)
        no_devices(documents['core.device_registry']['data'], self.entry_id)
        registry = self.hass.data.get(er.DATA_REGISTRY)
        _need(type(registry) is er.EntityRegistry)
        sources = [r for r in documents['core.entity_registry']['data']['entities'] if r.get('id') == self.source_uuid]
        _need(len(sources) == 1 and sources[0]['entity_id'] in set(self.change.mapping) | set(self.change.mapping.values()))
        source = registry.async_get(sources[0]['entity_id'])
        actual = registry.async_get(rows[0]['entity_id'])
        _need(source is not None and _row(source) == sources[0] and actual is not None and _row(actual) == rows[0])
        platforms = [p for p in ep.async_get_platforms(self.hass, 'template') if p.config_entry is self.entry]
        _need(self.entry.state.value == 'loaded' and all(type(p) is ep.EntityPlatform and p.hass is self.hass
              and all(task.done() for task in p._tasks) for p in platforms))
        entities = [entity for platform in platforms for entity in platform.entities.values()]
        _need(len(entities) == 1)
        entity = entities[0]
        _need(entity.entity_id == rows[0]['entity_id'] and entity.unique_id == self.entry_id
              and entity.registry_entry is actual and entity.platform in platforms)
        semantic, template, _ = _live_sensor(self.native, entity)
        dependencies, token = _registration(self.native, entity, template)
        old, new = next(iter(self.change.mapping.items()))
        expected = old if snapshot_equal(snapshot, self.change.before) else new
        _need(semantic == template_entry_semantic(snapshot) and dependencies == (expected,))
        return ('loaded', self.entry_id, rows[0]['id'], token)
