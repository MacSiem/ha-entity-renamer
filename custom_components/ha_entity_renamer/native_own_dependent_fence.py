"""Private identity fence for one actual Automation or Script entity.

Only an accepted chain row, or the same live node's audited registry event,
may account for the selected entity ID moving. No current row becomes a new
ORIGINAL, and all other native population/object edges remain exact.
"""
from __future__ import annotations

from dataclasses import replace
import json

from .native_registry import _content
from .transaction import Refused


def _need(value, reason):
    if not value:
        raise Refused(reason) from None


class NativeOwnDependentFence:
    def __init__(self, root, change, sample, *, native_readd=False):
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        _need(change.before.get('platform') in ('automation', 'script') and len(change.mapping) == 1,
              'own_dependent_scope_required')
        self.root, self.change, self.node = root, change, None
        self.native_readd = native_readd
        self.domain = change.before['platform']
        self.old, self.new = next(iter(change.mapping.items()))
        rows, blockers, tokens = sample
        self.row_order = {(row.domain, row.native_key): index for index, row in enumerate(rows)}
        _need(len(self.row_order) == len(rows) and not blockers,
              'own_dependent_initial_inventory_unqualified')
        matches = [row for row in rows if row.domain == self.domain and row.registry_id == change.identity]
        _need(len(matches) == 1 and matches[0].native_key in
              (change.before['unique_id'], change.after['unique_id']),
              'own_dependent_registry_binding_changed')
        self.original_row = matches[0]
        self.key = self.original_row.native_key
        component = root.hass.data.get(DATA_INSTANCES, {}).get(self.domain)
        entities = [entity for entity in component.entities if entity.unique_id == self.key] if component else []
        _need(len(entities) == 1, 'own_dependent_entity_missing')
        self.entity = entities[0]
        token_rows = [token for domain, _, group in tokens if domain == self.domain
                      for token in group if token[1] == id(self.entity)]
        _need(len(token_rows) == 1 and token_rows[0][0] == self.original_row.entity_id,
              'own_dependent_original_token_missing')
        self.original_token = token_rows[0]
        self.current_token = self.original_token
        self.adopted_node = None
        self.accepted_script_service = None
        self.automation_service = None
        if self.domain == 'automation':
            from .native_own_automation_service import capture
            self.automation_service = capture(root.hass)

    def check_sources(self):
        if self.automation_service is not None:
            from .native_own_automation_service import capture, check
            check(self.root.hass, self.automation_service)
            current = capture(self.root.hass)
            _need(all(actual is expected for actual, expected in zip(current[:9], self.automation_service[:9])),
                  'own_automation_service_source_changed')

    def rows(self):
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        if self.automation_service is not None:
            from .native_own_automation_service import check
            check(self.root.hass, self.automation_service)
        from .native_source_handoff import NativeSourceHandoff
        docs = self.root.chain.accepted_documents()['core.entity_registry']['data']['entities']
        rows = [dict(row) for row in docs]
        selected = [index for index, row in enumerate(rows) if row['id'] == self.change.identity]
        _need(len(selected) == 1, 'own_dependent_accepted_row_missing')
        index = selected[0]
        accepted = rows[index]
        from homeassistant.helpers import entity_registry as er
        registry = self.root.hass.data[er.DATA_REGISTRY]
        current = registry.async_get(self.entity.entity_id)
        _need(current is not None and current is self.entity.registry_entry and current.id == self.change.identity
              and self.entity.entity_id in (self.old, self.new), 'own_dependent_actual_owner_changed')
        actual = json.loads(_orjson_bytes_default_encoder(current.as_storage_fragment))
        expected = self.change.before if current.entity_id == self.old else self.change.after
        if _content(actual) != _content(expected):
            from .native_registry_transition import _native_script_unique_move
            alternate = dict(self.change.before, previous_unique_id=self.change.after['unique_id'])
            node = self.node
            live = (node is not None and node.target == self.old
                    and node.script_service_move is not None
                    and node.witness is not None and node.witness.source_handoff.new_row is current)
            if live:
                move = node.script_service_move
                if move.phase == 'captured':
                    _need(node.witness.source_handoff.script_dispatch_image(node) is self.entity,
                          'own_dependent_dispatch_image_changed')
                    move.inventory.check_service(installed_row=current)
                else:
                    move.check_moved()
            receipted = _content(accepted) == _content(actual)
            cutover = None if node is None else getattr(node, 'script_cutover', None)
            recreated = (self.native_readd and cutover is not None and cutover.phase == 'added'
                         and cutover.target_entity is self.entity and cutover.mutation is not None
                         and cutover.mutation.identity == self.change.identity)
            _need(_native_script_unique_move(self.change.before, self.change.after)
                  and current.entity_id == self.old and _content(actual) == _content(alternate)
                  and (live or receipted or recreated), 'own_dependent_native_row_changed')
        if accepted['entity_id'] != current.entity_id:
            node = self.node
            handoff = None if node is None or node.witness is None else node.witness.source_handoff
            readded = (self.native_readd and node is not None and node.witness is not None
                       and node.witness.source_handoff is None and node.witness.finished
                       and node.witness.mutation is not None and node.target == current.entity_id
                       and node.witness.mutation.identity == self.change.identity)
            cutover = None if node is None else getattr(node, 'script_cutover', None)
            recreated = (self.native_readd and self.domain == 'script' and cutover is not None
                         and cutover.phase == 'added' and cutover.mutation is not None
                         and cutover.mutation.identity == self.change.identity
                         and cutover.target_entity is self.entity and node.target == current.entity_id)
            retained = (type(handoff) is NativeSourceHandoff and handoff.source is self.entity
                        and handoff.attempted and handoff.new_row is current and handoff.error is None
                        and handoff.registry_calls == handoff.api_calls == 1)
            _need(node is not None and node.root is self.root and node.change == self.change
                  and node.phase in ('registry_attempted', 'observed_effect')
                  and self.root.chain.active is node.intent and (readded or recreated or retained),
                  'own_dependent_unacknowledged_row_unqualified')
            rows[index] = actual
        else:
            _need(_content(accepted) == _content(actual), 'own_dependent_accepted_row_changed')
        return tuple(rows)

    def rebind_after_controlled_reload(self):
        """Accept the selected native entity recreated by our checked reload."""
        from homeassistant.components.automation import AutomationEntity
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        _need(self.native_readd and self.domain == 'automation', 'own_dependent_rebind_unqualified')
        transition = getattr(self.root, 'controlled_automation', None)
        component = self.root.hass.data.get(DATA_INSTANCES, {}).get('automation')
        matches = ([] if component is None else [entity for entity in component.entities
                   if entity.registry_entry is not None and entity.registry_entry.id == self.change.identity])
        _need(transition is not None and len(matches) == 1 and type(matches[0]) is AutomationEntity
              and matches[0].unique_id == self.original_row.native_key
              and matches[0].entity_id in (self.old, self.new)
              and matches[0]._is_enabled is transition.expected_enabled[matches[0].unique_id],
              'own_dependent_rebind_unqualified')
        self.entity = matches[0]
        self.current_token = None
        admission = getattr(self.root, 'own_record_admission', None)
        if admission is not None:
            admission.entity, admission.script = self.entity, self.entity.action_script

    def rebind_after_controlled_script_reload(self):
        """Accept only the same UUID and actual owned service after HA reload."""
        from homeassistant.components.script import ScriptEntity
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        from .native_own_id_inventory import NativeOwnIDInventory
        _need(self.native_readd and self.domain == 'script', 'own_dependent_rebind_unqualified')
        component = self.root.hass.data.get(DATA_INSTANCES, {}).get('script')
        matches = ([] if component is None else [entity for entity in component.entities
                   if entity.registry_entry is not None and entity.registry_entry.id == self.change.identity])
        _need(len(matches) == 1 and type(matches[0]) is ScriptEntity
              and matches[0].entity_id in (self.old, self.new), 'own_dependent_rebind_unqualified')
        entity = matches[0]
        target = self.old if entity.entity_id == self.new else self.new
        inventory = NativeOwnIDInventory.capture(self.root.hass,
            {entity.entity_id: target}, moving_change=self.change, allow_running=True)
        _need(inventory.selected_entity is entity, 'own_dependent_rebind_unqualified')
        self.entity, self.current_token = entity, None
        admission = getattr(self.root, 'own_record_admission', None)
        if admission is not None:
            admission.entity, admission.script, admission.inventory = entity, entity.script, inventory
            admission.controlled_service = None

    def normalize(self, sample):
        rows, blockers, tokens = sample
        _need(not blockers, 'own_dependent_inventory_changed')
        normalized = []
        seen = 0
        for row in rows:
            if row.domain == self.domain and row.registry_id == self.change.identity:
                _need(row.native_key == self.entity.unique_id and row.disposition == 'loaded'
                      and row.entity_id == self.entity.entity_id, 'own_dependent_population_changed')
                row = replace(row, native_key=self.original_row.native_key,
                              entity_id=self.original_row.entity_id,
                              registry_row_sha256=self.original_row.registry_row_sha256)
                seen += 1
            normalized.append(row)
        _need(seen == 1, 'own_dependent_population_changed')
        normalized_tokens, found = [], 0
        for domain, component, group in tokens:
            replacement = []
            for token in group:
                if domain == self.domain and token[1] == id(self.entity):
                    _need(token[0] == self.entity.entity_id, 'own_dependent_native_object_changed')
                    if self.native_readd:
                        node = self.node
                        if self.current_token is None:
                            self.current_token = token
                        if (node is not None and self.adopted_node is not node
                                and node.witness is not None and node.witness.finished
                                and node.target == self.entity.entity_id):
                            self.current_token = token
                            self.adopted_node = node
                        _need(self.current_token == token, 'own_dependent_native_object_changed')
                        replacement.append(self.original_token)
                        found += 1
                        continue
                    _need(token[2:4] == self.original_token[2:4], 'own_dependent_native_object_changed')
                    from .native_registry_transition import _native_script_unique_move
                    if _native_script_unique_move(self.change.before, self.change.after):
                        move = None if self.node is None else self.node.script_service_move
                        if move is not None and move.phase == 'captured':
                            # The owning guard also runs before the native registry call.
                            # At that seam the audited old service is still authoritative.
                            import asyncio
                            handoff = move.handoff
                            before = (handoff.registry_calls == handoff.api_calls == 0
                                and handoff.new_row is None
                                and handoff.registry.async_get(handoff.old) is handoff.row
                                and handoff.registry.async_get(handoff.new) is None)
                            dispatch = (handoff.in_native and handoff.attempted
                                and handoff.registry_calls == handoff.api_calls == 1
                                and handoff.new_row is not None
                                and handoff.registry.async_get(handoff.new) is handoff.new_row
                                and handoff.registry.async_get(handoff.old) is None
                                and self.entity.registry_entry is handoff.new_row
                                and self.entity.entity_id == handoff.new)
                            _need(asyncio.current_task() is move.task and move.node is self.node
                                  and handoff.source is self.entity and (before or dispatch),
                                  'own_dependent_service_preflight_changed')
                            move.inventory.check_service(installed_row=handoff.new_row if dispatch else None)
                            expected_service = self.original_token[5:]
                        elif move is not None:
                            move.check_moved()
                            expected_service = (id(move.inventory.service_registry), id(move.replacement),
                                id(move.replacement.job), id(move.replacement.job.target),
                                id(move.replacement.schema))
                        else:
                            expected_service = self.accepted_script_service or self.original_token[5:]
                        _need(token[5:] == expected_service, 'own_dependent_service_owner_changed')
                        token = (*token[:5], *self.original_token[5:])
                    token = (self.original_token[0], *token[1:4], self.original_token[4], *token[5:])
                    found += 1
                replacement.append(token)
            normalized_tokens.append((domain, component, tuple(sorted(replacement))))
        _need(found == 1, 'own_dependent_native_object_changed')
        _need(all((row.domain, row.native_key) in self.row_order for row in normalized),
              'own_dependent_population_changed')
        normalized.sort(key=lambda row: self.row_order[row.domain, row.native_key])
        return tuple(normalized), blockers, tuple(normalized_tokens)


def qualify_dependent_own_changes(hass, changes, *, guard, native_readd=False):
    """Qualify one Automation or one positively owned native Script service."""
    from homeassistant.helpers.entity_component import DATA_INSTANCES
    from .native_registry import NativeRegistryIO
    from .native_source_handoff import NativeSourceHandoff
    from .native_active_execution_pins import capture_census
    from .yaml_runtime_observer import NativeYAMLRuntimeReadback

    changes = tuple(changes)
    own = [change for change in changes if change.before.get('platform') in ('automation', 'script')]
    _need(len(own) == 1 and len(changes) in (1, 2)
          and not any(change.before.get('platform') == 'template' for change in changes),
          'own_dependent_mapping_scope_unqualified')
    io = NativeRegistryIO(hass)
    registry = io.registries['entity']
    # Prove the complete known Automation/Script program census, including
    # opaque callers. An unresolved system/dynamic service refuses this slice.
    from .native_automation_config_transport import _call_scope
    from .yaml_validation_worker import _normal
    runtime = NativeYAMLRuntimeReadback(hass)
    projections = {}
    for domain in ('automation', 'script'):
        component = hass.data.get(DATA_INSTANCES, {}).get(domain)
        projections[domain] = _normal(runtime._projection(domain)[0]) if component is not None else []
    _call_scope(hass, projections, projections)
    from .native_registry_transition import _native_script_unique_move
    for change in changes:
        script_move = change is own[0] and _native_script_unique_move(change.before, change.after)
        excluded = ('entity_id', 'modified_at', 'name') if (
            native_readd and change is own[0] and change.before.get('platform') == 'automation'
            and change.runtime_before_json) else ('entity_id', 'modified_at')
        _need(change.kind == 'entity' and len(change.mapping) == 1
              and (script_move or {key: value for key, value in change.before.items()
                   if key not in excluded}
                  == {key: value for key, value in change.after.items()
                      if key not in excluded}),
              'own_dependent_registry_change_unqualified')
        old, new = next(iter(change.mapping.items()))
        before, after = registry.async_get(old), registry.async_get(new)
        _need((before is None) != (after is None), 'own_dependent_position_ambiguous')
        row = before if before is not None else after
        target = change.after if before is not None else change.before
        direct = (native_readd and change is own[0] and
                  (row.platform == 'automation' or row.platform == 'script' and script_move))
        _need(row.id == change.identity and (direct or NativeSourceHandoff.supports_source(io, change, target)),
              'own_dependent_handoff_unqualified')
        if change is own[0]:
            domain = row.platform
            component = hass.data.get(DATA_INSTANCES, {}).get(domain)
            entities = [entity for entity in component.entities if entity.registry_entry is row] if component else []
            _need(len(entities) == 1, 'own_dependent_entity_unqualified')
            entity = entities[0]
            NativeYAMLRuntimeReadback(hass)._projection(domain)
            if domain == 'automation':
                from .native_own_automation_service import capture
                capture(hass)
            if not direct:
                capture_census(entity.action_script if domain == 'automation' else entity.script)
            elif domain == 'automation':
                from homeassistant.components.automation import AutomationEntity
                _need(type(entity) is AutomationEntity and type(entity._is_enabled) is bool,
                      'own_automation_readd_unqualified')
            else:
                from homeassistant.components.script import ScriptEntity
                _need(type(entity) is ScriptEntity, 'own_script_recreate_unqualified')
            if domain == 'script':
                _need(script_move or change.after['entity_id'] == 'script.' + entity.unique_id,
                      'own_script_service_rename_unsupported')
                from .native_own_id_inventory import NativeOwnIDInventory
                NativeOwnIDInventory.capture(hass, {row.entity_id: target['entity_id']},
                                             stable_change=None if script_move else change,
                                             moving_change=change if script_move else None,
                                             allow_running=native_readd)
        guard()
    return own[0]


class NativeOwnDependentAdmission:
    """Review-held native actor/run/caller census, checked before record."""
    def __init__(self, fence):
        from .native_active_execution_pins import capture_census
        from .native_own_id_inventory import NativeOwnIDInventory
        self.fence, self.entity = fence, fence.entity
        self.script = (self.entity.action_script if fence.domain == 'automation' else self.entity.script)
        self.census = None if fence.native_readd else capture_census(self.script)
        self.automation_service = fence.automation_service
        self.caller_bus = self.caller_listeners = None
        self.controlled_service = None
        if fence.domain == 'automation':
            from collections import defaultdict
            from homeassistant.core import EventBus
            from .native_own_id_inventory import _foreign_static_callers
            bus = fence.root.hass.bus
            _need(type(bus) is EventBus and type(bus._listeners) is defaultdict,
                  'own_dependent_caller_bus_unqualified')
            self.caller_bus, self.caller_listeners = bus, bus._listeners
            _foreign_static_callers(fence.root.hass, self.caller_listeners,
                                    frozenset((fence.old, fence.new)))
        target = (fence.change.before['entity_id'] if self.entity.entity_id == fence.change.after['entity_id']
                  else fence.change.after['entity_id'])
        from .native_registry_transition import _native_script_unique_move
        moving = _native_script_unique_move(fence.change.before, fence.change.after)
        self.inventory = (NativeOwnIDInventory.capture(fence.root.hass,
            {self.entity.entity_id: target}, stable_change=None if moving else fence.change,
            moving_change=fence.change if moving else None, allow_running=fence.native_readd)
            if fence.domain == 'script' else None)
        if self.inventory is not None and self.inventory.service_event_census is not None:
            self.inventory.service_event_census.project_scene_fence(fence.root.scene_fence)

    def bind_scene_session(self, session):
        """Bind the review owner to the same entity in the fresh execution root."""
        controlled_script = self.fence.native_readd and self.fence.domain == 'script'
        census = None if self.inventory is None else self.inventory.service_event_census
        if not controlled_script and census is None:
            return
        from .native_template_state_server import NativeTemplateStateServer
        other = getattr(session, 'own_dependent_fence', None)
        _need(type(session) is NativeTemplateStateServer and session.bound and not session.closed
              and self.fence.root.closed and session.plan is self.fence.root.plan
              and type(other) is NativeOwnDependentFence and other.root is session
              and other.entity is self.entity and other.change == self.fence.change
              and session.hass is self.fence.root.hass,
              'own_dependent_review_actor_changed')
        if census is not None:
            census.attach_scene_fence(session.scene_fence)
        if controlled_script:
            self.fence = other

    def check_execution(self):
        from .native_active_execution_pins import check_census
        fence = self.fence
        _need(self.entity is fence.entity
              and (fence.native_readd or
                   (self.entity.action_script if fence.domain == 'automation' else self.entity.script) is self.script),
              'own_dependent_review_actor_changed')
        if self.census is not None:
            check_census(self.script, self.census)
        if self.caller_bus is not None:
            from .native_own_id_inventory import _foreign_static_callers
            _need(fence.root.hass.bus is self.caller_bus
                  and self.caller_bus._listeners is self.caller_listeners,
                  'own_dependent_caller_bus_changed')
            _foreign_static_callers(fence.root.hass, self.caller_listeners,
                                    frozenset((fence.old, fence.new)))
        if self.controlled_service is not None:
            table, key, old_key, service, job, target, schema, response = self.controlled_service
            _need(fence.domain == 'script' and fence.root.hass.services._services.get('script') is table
                  and table.get(key) is service and old_key not in table
                  and service.job is job and job.target is target
                  and getattr(target, '__self__', None) is self.entity
                  and service.schema is schema and service.supports_response is response,
                  'own_dependent_controlled_service_changed')
        if self.inventory is not None:
            # Source/program prefixes may advance naturally after one own
            # registry receipt; the direct native service must still be this
            # exact pinned owner before EVERY subsequent effect.
            self.inventory.check_service()

    def check(self):
        self.check_execution()
        fence = self.fence
        if self.automation_service is not None:
            from .native_own_automation_service import check
            check(fence.root.hass, self.automation_service)
        if self.inventory is not None:
            from .native_own_id_inventory import _sample
            entity, service, callers, signature = _sample(
                self.inventory.hass, self.inventory.old, self.inventory.new,
                stable_service=self.inventory.stable_service, allow_running=self.inventory.allow_running)
            _need(entity is self.inventory.selected_entity and entity.script is self.inventory.selected_script
                  and service is self.inventory.service and service.job is self.inventory.service_job
                  and service.job.target is self.inventory.service_target
                  and service.schema is self.inventory.service_schema
                  and service.supports_response is self.inventory.service_response
                  and callers == self.inventory.callers
                  and signature == self.inventory.signature, 'own_dependent_review_caller_changed')
