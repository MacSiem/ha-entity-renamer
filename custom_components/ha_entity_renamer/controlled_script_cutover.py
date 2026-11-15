"""Scoped native remove/recreate support for a sealed Script registry intent.

This private helper is usable only by the existing coordinator's active node.
An attempted removal is an effect even if no registry API was reached. It is
never compensated or replayed locally on an exception.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import logging

from .transaction import Refused

_LOGGER = logging.getLogger(__name__)


def _need(value, reason):
    if not value:
        raise Refused('controlled_script_cutover_' + reason) from None


async def _finish_native_call(operation, fence, after=None):
    """Fence inside the child, then drain the started native change."""
    async def guarded():
        fence()  # No await before the native coroutine begins.
        try:
            result = await operation()
        except BaseException:
            if after is not None:
                try:
                    after()
                except BaseException:
                    _LOGGER.exception('Nie udało się rozliczyć nowego listenera po błędzie')
            raise
        if after is not None:
            after()
        return result
    task = asyncio.create_task(guarded())
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except BaseException:
            break
    if cancelled:
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


def _native_removed_state(state):
    # Core 2025.6 and 2026.10 leave an enabled registry entity unavailable
    # with restored=true after async_remove(), rather than deleting State.
    return (state is not None and state.state == 'unavailable'
            and state.attributes.get('restored') is True)


def _retire_removed_state(hass, component, registry, source, removed_state):
    """Retire only our exact native removal placeholder after the ID move."""
    current = hass.states.get(source)
    if current is None:
        return
    _need(current is removed_state and _native_removed_state(current)
          and component.get_entity(source) is None and registry.async_get(source) is None,
          'removed_state_changed')
    _need(hass.states.async_remove(source) is True and hass.states.get(source) is None,
          'removed_state_retire_unconfirmed')


def _pending_registry_documents(original, observed, identity, before_row, after_row):
    """Allow only the actual selected native row while its receipt is pending."""
    def same(left, right):
        return json.dumps(left, sort_keys=True, separators=(',', ':'), allow_nan=False) == json.dumps(
            right, sort_keys=True, separators=(',', ':'), allow_nan=False)
    key = 'core.entity_registry'
    if set(original) != set(observed) or key not in original:
        return False
    if any(not same(original[name], observed[name]) for name in original if name != key):
        return False
    first, current = original[key], observed[key]
    if same(first, current):
        return True  # Native store has not flushed yet.
    if (type(first) is not dict or type(current) is not dict
            or not same({k: v for k, v in first.items() if k != 'data'},
                        {k: v for k, v in current.items() if k != 'data'})):
        return False
    left, right = first.get('data'), current.get('data')
    if (type(left) is not dict or type(right) is not dict
            or not same({k: v for k, v in left.items() if k != 'entities'},
                        {k: v for k, v in right.items() if k != 'entities'})):
        return False
    a, b = left.get('entities'), right.get('entities')
    if type(a) is not list or type(b) is not list:
        return False
    def select(rows):
        return [row for row in rows if type(row) is dict and row.get('id') == identity]
    old, new = select(a), select(b)
    return (len(old) == len(new) == 1 and same(old[0], before_row) and same(new[0], after_row)
            and same([row for row in a if row is not old[0]],
                     [row for row in b if row is not new[0]]))


def _retire_own_description(cache, old_slot, original, original_graph, current_graph, new_description):
    _need(cache.get(old_slot) is original and current_graph == original_graph
          and new_description is not original
          and sum(value is original for value in cache.values()) == 1,
          'old_description_changed')
    del cache[old_slot]


def _affected_state_automations(hass, source, target):
    from homeassistant.components.automation import AutomationEntity
    from homeassistant.helpers.entity_component import DATA_INSTANCES
    from homeassistant.util.yaml.objects import NodeDictClass, NodeListClass
    from .native_own_id_inventory import _ids
    component = hass.data.get(DATA_INSTANCES, {}).get('automation')
    result = []
    for entity in (() if component is None else component.entities):
        _need(type(entity) is AutomationEntity and type(entity._is_enabled) is bool,
              'automation_target_unqualified')
        if not entity._is_enabled:
            continue
        _need(type(entity._trigger_config) in (list, NodeListClass), 'automation_trigger_unqualified')
        affected = False
        for trigger in entity._trigger_config:
            _need(type(trigger) in (dict, NodeDictClass), 'automation_trigger_unqualified')
            if trigger.get('platform') == 'state' and set(_ids(trigger.get('entity_id'))) & {source, target, '*'}:
                affected = True
        if affected:
            result.append(entity)
    return tuple(result)


class ControlledScriptCutover:
    def __init__(self):
        raise Refused('controlled_script_cutover_root_capture_required')

    @classmethod
    async def capture(cls, node, program, active):
        from homeassistant.components.script import ScriptEntity, _create_script_entities
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        from .controlled_script_config import prepare_script
        from .native_template_state_registry_effect import NativeTemplateStateRegistryEffect
        from .native_own_id_inventory import NativeOwnIDInventory
        from .native_registry_transition import _native_script_unique_move
        from .yaml_runtime_observer import NativeYAMLRuntimeReadback
        from .yaml_validation_worker import _normal
        from homeassistant.helpers.json import _orjson_bytes_default_encoder

        root, change = node.root, node.change
        _need(type(node) is NativeTemplateStateRegistryEffect and root.chain.active is node.intent
              and node.phase == 'captured' and change.before.get('platform') == 'script'
              and _native_script_unique_move(change.before, change.after)
              and node.context.record.plan == root.plan and root.chain.writable,
              'scope_unqualified')
        node.guard()
        inventory = NativeOwnIDInventory.capture(root.hass, {node.source: node.target},
                                                  moving_change=change, allow_running=True)
        entity = inventory.selected_entity
        component = root.hass.data.get(DATA_INSTANCES, {}).get('script')
        _need(type(entity) is ScriptEntity and component is not None
              and component.get_entity(node.source) is entity, 'source_missing')
        source_row = node.io.document('entity')
        service_table = root.hass.services._services.get('script')
        target_key = node.target.removeprefix('script.')
        _need(type(service_table) is dict and target_key not in service_table, 'service_collision')
        observer = NativeYAMLRuntimeReadback(root.hass)
        targets = _affected_state_automations(root.hass, node.source, node.target)
        if targets:
            _need(getattr(root, 'controlled_automation', None) is not None,
                  'automation_transition_required')
        target_ids = {id(target) for target in targets}
        foreign = {}
        for domain in ('automation', 'script'):
            if domain not in root.hass.data[DATA_INSTANCES]:
                continue
            rows, tokens = observer._projection(domain)
            foreign[domain] = (tuple(_normal(row) for row in rows
                                     if domain != 'script' or row[0] != entity.unique_id),
                               tuple(token for token in tokens
                                     if (domain != 'script' or token[1] != id(entity))
                                     and (domain != 'automation' or token[1] not in target_ids)))
        inventory.check()
        target = await prepare_script(root.hass, program, active, target_key, guard=node.guard)
        entities = await _create_script_entities(root.hass, [target])
        node.guard()
        inventory.check()
        _need(type(entities) is list and len(entities) == 1 and type(entities[0]) is ScriptEntity
              and entities[0].unique_id == target_key, 'target_factory_unqualified')
        obj = object.__new__(cls)
        obj.node, obj.root, obj.change, obj.io = node, root, change, node.io
        obj.entity, obj.target_entity, obj.component = entity, entities[0], component
        obj.inventory, obj.source_document = inventory, source_row
        obj.device_document = node.io.document('device')
        obj.config_entries = json.loads(_orjson_bytes_default_encoder(root.hass.config_entries._data_to_save()))
        obj.config_entries_manager = root.hass.config_entries
        obj.foreign = foreign
        obj.automation_targets = targets
        obj.suspended_automations = []
        obj.automation_owners = {entity: entity.registry_entry for entity in targets}
        obj.old_key, obj.new_key, obj.service_table = entity.unique_id, target_key, service_table
        obj.foreign_services = {key: value for key, value in service_table.items()
                                if key not in (obj.old_key, obj.new_key)}
        obj.phase, obj.task, obj.mutation = 'captured', asyncio.current_task(), None
        return obj

    def _accepted_receipt_state(self):
        # complete() clears active before node.accepted(). Admit that narrow
        # interval only from the fully reconstructed exact own receipt.
        chain, node = self.root.chain, self.node
        if chain.active is not None or node.phase != 'observed_effect' or self.mutation is None:
            return None
        direction, index = node.context.record.direction, node.context.index
        row = chain.store.read(chain._slot('receipt', index, direction))
        if type(row) is not dict or type(row.get('payload')) is not dict:
            return None
        payload = row['payload']
        _, head, _, completed, pending = chain._reconstruct()
        if (pending is not None or head != row.get('digest') or completed.get(index) != direction
                or payload.get('kind') != 'registry_receipt'
                or payload.get('intent') != list(node.intent.reference)
                or payload.get('mutation') != asdict(self.mutation)):
            return None
        return chain.reconstruct_inputs()

    def _scope(self):
        from .native_template_yaml_server import _authorize, DATA_RUNTIME
        root = self.root
        _authorize(root.guard)
        _need(asyncio.current_task() is self.task and not root.closed and root.bound
              and root.hass.data.get(DATA_RUNTIME) is root.runtime and root.runtime.loaded
              and not root.runtime.unloading and root.runtime.operation_lock is root.lock
              and root.lock.locked()
              and root.runtime.journal is root.journal and root.runtime.preparations is root.preparations,
              'authority_changed')
        accepted = self._accepted_receipt_state() if root.chain.active is None else None
        _need(root.chain.active is self.node.intent or accepted is not None, 'authority_changed')
        root.scene_fence.check()
        root._preparation_current()
        # Keep unrelated managers and automation state checked even while the
        # one selected Script is intentionally absent. This bypasses only the
        # dependent sample that necessarily requires the selected live owner.
        from .native_template_forward import _UnchangedConsumers
        _UnchangedConsumers.check(root.consumers)
        if hasattr(root, 'controlled_automation'):
            root.controlled_automation.check_preserving_enabled()
        if accepted is not None:
            accepted.assert_current(root.receipt)
        elif self.mutation is None:
            self.node.state.assert_current(root.receipt)
        else:
            # A native store may already have flushed the attempted row before
            # its receipt. This detached observation grants no authority: the
            # complete registry receipt still validates actual before/after.
            fresh = self.node.state._native_observation_fence(root.receipt)
            original_sources, actual_sources = self.node.state.sources(), fresh.sources()
            _need(all(original_sources[path] == actual_sources[path]
                      for path in original_sources if path != '.storage/core.entity_registry')
                  and _pending_registry_documents(self.node.state.documents(), fresh.documents(),
                      self.change.identity, json.loads(self.mutation.before_json),
                      json.loads(self.mutation.after_json)), 'pending_native_source_changed')
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        _need(root.hass.config_entries is self.config_entries_manager
              and self.io.document('device') == self.device_document
              and json.loads(_orjson_bytes_default_encoder(root.hass.config_entries._data_to_save())) == self.config_entries,
              'foreign_native_inputs_changed')

    def _foreign(self):
        from .yaml_runtime_observer import NativeYAMLRuntimeReadback
        from .yaml_validation_worker import _normal
        observer = NativeYAMLRuntimeReadback(self.root.hass)
        for domain, expected in self.foreign.items():
            rows, tokens = observer._projection(domain)
            actual = (tuple(_normal(row) for row in rows if domain != 'script'
                            or row[0] not in (self.old_key, self.new_key)),
                      tuple(token for token in tokens
                            if (domain != 'script' or token[1] not in (id(self.entity), id(self.target_entity)))
                            and (domain != 'automation' or token[1] not in {id(item) for item in self.automation_targets})))
            _need(actual == expected, 'foreign_program_changed')
        _need(self.root.hass.services._services.get('script') is self.service_table,
              'service_table_changed')
        current_services = {key: value for key, value in self.service_table.items()
                            if key not in (self.old_key, self.new_key)}
        _need(current_services.keys() == self.foreign_services.keys()
              and all(current_services[key] is value for key, value in self.foreign_services.items()),
              'foreign_service_changed')
        from .native_registry import _content
        current = self.io.document('entity')
        before = self.source_document
        _need({key: value for key, value in current.items() if key != 'entities'}
              == {key: value for key, value in before.items() if key != 'entities'},
              'foreign_registry_changed')
        actual = {row['id']: row for row in current['entities']}
        expected = {row['id']: row for row in before['entities']}
        _need(set(actual) == set(expected) and all(actual[key] == expected[key]
              for key in expected if key != self.change.identity), 'foreign_registry_changed')
        if self.phase in ('suspended', 'removed', 'removing'):
            _need(actual[self.change.identity] == expected[self.change.identity], 'source_registry_changed')
        elif self.phase in ('registry_attempted', 'adding', 'added'):
            target = self.change.before if self.node.context.record.direction == 'rollback' else self.change.after
            actual_row = _content(actual[self.change.identity])
            expected_row = _content(target)
            if self.node.context.record.direction == 'rollback':
                expected_row['previous_unique_id'] = self.change.after['unique_id']
            _need(actual_row == expected_row, 'target_registry_changed')

    async def suspend_affected_automations(self):
        """Detach only recognized live state triggers, under the registry intent."""
        if not self.automation_targets:
            return
        self.inventory.check()
        transition = self.root.controlled_automation
        self.phase = 'suspending'
        for entity in self.automation_targets:
            self._scope()
            state = self.root.hass.states.get(entity.entity_id)
            _need(entity._is_enabled is True and state is not None and state.state == 'on'
                  and transition.expected_enabled.get(entity.unique_id) is True,
                  'automation_state_changed_before_suspend')
            # Record ownership before the first await: cancellation or an
            # exception may arrive after Core has already detached triggers.
            self.suspended_automations.append((entity, state))
            try:
                await self._call_automation(entity, state, enabled=True,
                    detach=entity._async_detach_triggers, expected=True, enable=False)
            finally:
                if self._owns_suspended_automation(entity, state) and entity._is_enabled is False:
                    transition.expected_enabled[entity.unique_id] = False
            await asyncio.sleep(0)
            self._scope()
            _need(entity._is_enabled is False and entity._async_detach_triggers is None
                  and self.root.hass.states.get(entity.entity_id) is state,
                  'automation_suspend_unconfirmed')
        self.phase = 'suspended'
        self._scope()

    async def resume_affected_automations(self):
        if not self.suspended_automations:
            return
        # Drain the already-emitted removal/add callbacks before reattaching.
        await asyncio.sleep(0)
        self._scope()
        transition = self.root.controlled_automation
        self.phase = 'resuming'
        for entity, original_state in self.suspended_automations:
            self._scope()
            _need(self.root.hass.states.get(entity.entity_id) is original_state
                  and entity._is_enabled is False and entity._async_detach_triggers is None,
                  'automation_foreign_state_changed')
            await self._call_automation(entity, original_state, enabled=False,
                detach=None, expected=False, enable=True)
            transition.expected_enabled[entity.unique_id] = True
            await asyncio.sleep(0)
            self._scope()
            _need(self.root.hass.states.get(entity.entity_id) is original_state
                  and entity._is_enabled is True and entity._async_detach_triggers is not None,
                  'automation_resume_unconfirmed')
        self.suspended_automations.clear()
        self.phase = 'added'
        self._scope()

    def _same_automation_owner(self, entity):
        transition = self.root.controlled_automation
        registry = self.io.registries['entity']
        original = self.automation_owners.get(entity)
        return (original is not None and entity.registry_entry is original
                and registry.async_get(entity.entity_id) is original
                and transition.component.get_entity(entity.entity_id) is entity)

    def _owns_suspended_automation(self, entity, state):
        """Do not restore a replaced owner, registry row or visible user state."""
        return (self._same_automation_owner(entity)
                and self.root.hass.states.get(entity.entity_id) is state)

    async def _call_automation(self, entity, state, *, enabled, detach, expected, enable):
        transition = self.root.controlled_automation
        def fence():
            _need(type(enabled) is bool and type(expected) is bool
                  and self._owns_suspended_automation(entity, state)
                  and entity._is_enabled is enabled
                  and entity._async_detach_triggers is detach
                  and transition.expected_enabled.get(entity.unique_id) is expected,
                  'automation_foreign_state_changed')
        def after_enable():
            # If a real OFF arrived while Core awaited trigger setup, it may
            # have preceded Core's assignment of the newly attached callback.
            # Detach only that callback on the same owner, synchronously.
            current = self.root.hass.states.get(entity.entity_id)
            attached = entity._async_detach_triggers
            if (self._same_automation_owner(entity) and current is not state
                    and current is not None and current.state == 'off'
                    and entity._is_enabled is False and attached is not None):
                attached()
                if entity._async_detach_triggers is attached:
                    entity._async_detach_triggers = None
        operation = entity._async_enable if enable else lambda: entity._async_disable(stop_actions=False)
        await _finish_native_call(operation, fence, after_enable if enable else None)

    def _warn_incomplete_automation(self, entity):
        """Keep incomplete trigger recovery visible even if the owner changed."""
        _LOGGER.error('Automation %s: przywrócenie triggerów niepotwierdzone', entity.entity_id)
        try:
            from homeassistant.components import persistent_notification
            persistent_notification.async_create(
                self.root.hass,
                f'Nie potwierdzono działania wyzwalaczy {entity.entity_id}. '
                'Sprawdź automatyzację i w razie potrzeby włącz ją ponownie. / '
                f'Triggers for {entity.entity_id} could not be verified. '
                'Check the automation and re-enable it if needed.',
                title='Entity Renamer',
                notification_id='ha_entity_renamer_trigger_' + entity.entity_id.replace('.', '_'))
        except Exception:
            _LOGGER.exception('Nie udało się pokazać ostrzeżenia dla %s', entity.entity_id)

    async def cleanup_suspended_automations(self):
        """Best effort only for our exact temporary changes; never replay the rename."""
        transition = self.root.controlled_automation
        for entity, state in self.suspended_automations:
            try:
                if not self._owns_suspended_automation(entity, state):
                    self._warn_incomplete_automation(entity)
                    continue
                if entity._is_enabled is False and entity._async_detach_triggers is None:
                    try:
                        await self._call_automation(entity, state, enabled=False,
                            detach=None, expected=transition.expected_enabled.get(entity.unique_id), enable=True)
                    except BaseException:
                        # A failed attach may have begun before raising. Do not
                        # attempt another attach or claim successful recovery.
                        pass
                if (self._owns_suspended_automation(entity, state)
                        and entity._is_enabled is True and entity._async_detach_triggers is not None):
                    transition.expected_enabled[entity.unique_id] = True
                    continue
                # If ownership still holds, make an incomplete attach visible
                # as OFF. Never write a replaced or externally edited actor.
                if self._owns_suspended_automation(entity, state):
                    await self._call_automation(entity, state, enabled=entity._is_enabled,
                        detach=entity._async_detach_triggers,
                        expected=transition.expected_enabled.get(entity.unique_id), enable=False)
                    if self._owns_suspended_automation(entity, state) and entity._is_enabled is False:
                        transition.expected_enabled[entity.unique_id] = False
                        entity.async_write_ha_state()
                self._warn_incomplete_automation(entity)
            except BaseException:
                _LOGGER.exception('Automation %s: nie udało się rozliczyć odłączonych triggerów', entity.entity_id)
                self._warn_incomplete_automation(entity)

    async def remove(self):
        self.node.guard()
        if self.phase == 'captured':
            self.inventory.check()
        else:
            _need(self.phase == 'suspended' and all(
                entity._is_enabled is False and entity._async_detach_triggers is None
                and self.root.hass.states.get(entity.entity_id) is state
                for entity, state in self.suspended_automations), 'automation_suspend_unconfirmed')
        _need(self.phase in ('captured', 'suspended') and self.root.chain.active is self.node.intent,
              'remove_not_authorized')
        self.phase = 'removing'
        await self.entity.async_remove()
        self.phase = 'removed'
        self._scope()
        removed_state = self.root.hass.states.get(self.node.source)
        _need(self.component.get_entity(self.node.source) is None
              and _native_removed_state(removed_state)
              and self.old_key not in self.service_table and self.new_key not in self.service_table
              and self.io.document('entity') == self.source_document,
              'remove_unconfirmed')
        self.removed_state = removed_state
        self._foreign()

    def authorize_registry(self, io, change, target):
        self._scope()
        _need(self.phase == 'removed' and io is self.io and change is self.change
              and target['entity_id'] == self.node.target and target['unique_id'] == self.new_key
              and io.document('entity') == self.source_document
              and self.root.hass.states.get(self.node.source) is self.removed_state
              and self.old_key not in self.service_table and self.new_key not in self.service_table,
              'registry_seam_changed')
        self._foreign()
        self.phase = 'registry_attempted'

    async def add(self, mutation):
        from homeassistant.components.script import ScriptEntity
        from homeassistant.core import Service
        self._scope()
        _need(self.phase == 'registry_attempted' and mutation.identity == self.change.identity,
              'mutation_unconfirmed')
        self.phase, self.mutation = 'adding', mutation
        await self.component.async_add_entities([self.target_entity])
        self.phase = 'added'
        self._scope()
        row = self.io.registries['entity'].async_get(self.node.target)
        actual = self.component.get_entity(self.node.target)
        service = self.service_table.get(self.new_key)
        _need(row is not None and row.id == self.change.identity and row.unique_id == self.new_key
              and actual is self.target_entity and type(actual) is ScriptEntity and actual.registry_entry is row
              and self.root.hass.states.get(self.node.target) is not None
              and type(service) is Service and getattr(service.job.target, '__self__', None) is actual
              and self.old_key not in self.service_table,
              'add_unconfirmed')
        self._foreign()
        # Core 2025.6 leaves the exact unavailable/restored State under the
        # old ID after the registry move. Remove only our pinned placeholder
        # while affected automations remain suspended and the intent is live.
        _retire_removed_state(self.root.hass, self.component,
                              self.io.registries['entity'], self.node.source,
                              self.removed_state)
        # Core leaves the removed service's description cache entry behind.
        # Remove only the exact old entry captured by this same live intent;
        # an altered/aliased description is foreign and must remain untouched.
        from homeassistant.helpers import service as service_helper
        from .native_own_id_inventory import _service_description
        from .native_script_config_transport import _graph
        inv = self.inventory
        cache, new_description, _, _ = _service_description(self.root.hass, actual)
        old_slot = ('script', self.old_key)
        _need(cache is inv.description_cache
              and self.root.hass.data.get(service_helper.SERVICE_DESCRIPTION_CACHE) is cache,
              'description_cache_changed')
        _retire_own_description(cache, old_slot, inv.description, inv.description_graph,
                                _graph(inv.description), new_description)
        await self.resume_affected_automations()
        fence = self.root.own_dependent_fence
        _need(fence.entity is self.entity and fence.node is self.node, 'fence_binding_changed')
        fence.entity = actual
        fence.current_token = None
