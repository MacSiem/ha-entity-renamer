"""One synchronous Script service handoff inside the authenticated native event.

This object is not an admission API. The registry node and native source
handoff own its only execution point; entering either native service API makes
any exception uncertain and forbids local reversal or replay.
"""
from __future__ import annotations

import asyncio

from .transaction import Refused


def _need(value, reason):
    if not value:
        raise Refused('own_script_service_move_' + reason) from None


class NativeOwnScriptServiceMove:
    @classmethod
    async def capture(cls, node, handoff):
        from .native_template_state_registry_effect import NativeTemplateStateRegistryEffect
        from .native_source_handoff import NativeSourceHandoff
        from .native_own_id_inventory import NativeOwnIDInventory
        from .native_own_script_service_audit import NativeOwnScriptServiceAudit
        from .native_own_script_service_recipients import NativeOwnScriptServiceRecipients
        from .native_registry_transition import _native_script_unique_move

        _need(type(node) is NativeTemplateStateRegistryEffect
              and type(handoff) is NativeSourceHandoff and handoff.change is node.change
              and handoff.source is node.dependent_entity
              and _native_script_unique_move(node.change.before, node.change.after)
              and node.root.chain.active is node.intent and node.phase == 'captured'
              and handoff.old == node.source and handoff.new == node.target
              and not handoff.attempted and asyncio.current_task() is node.task,
              'scope_unqualified')
        inventory = NativeOwnIDInventory.capture(node.root.hass, {node.source: node.target}, moving_change=node.change)
        _need(inventory.selected_entity is handoff.source and not inventory.stable_service
              and len(inventory.service_table) > 1
              and inventory.service_key == node.source.removeprefix('script.'),
              'inventory_unqualified')
        audit = await NativeOwnScriptServiceAudit.capture(node.root.hass, inventory, guard=node.guard)
        recipients = await NativeOwnScriptServiceRecipients.capture(
            node.root.hass, inventory, guard=node.guard, scene_fence=node.root.scene_fence)
        inventory.check()
        audit.check()
        recipients.check()
        _need(node.root.chain.active is node.intent and node.phase == 'captured'
              and not handoff.attempted and asyncio.current_task() is node.task,
              'scope_changed')
        obj = object.__new__(cls)
        obj.node, obj.handoff, obj.inventory = node, handoff, inventory
        obj.audit, obj.recipients, obj.task = audit, recipients, node.task
        obj.phase, obj.replacement = 'captured', None
        return obj

    def before(self):
        node, handoff = self.node, self.handoff
        _need(asyncio.current_task() is self.task and self.phase == 'captured'
              and node.root.chain.active is node.intent and node.phase in ('captured', 'registry_attempted')
              and not handoff.attempted, 'preflight_changed')
        node.guard()
        self.inventory.check()
        self.audit.check()
        self.recipients.check()

    def authorize_registry(self, io, change, target):
        """Validate the one native unique-ID request at its synchronous seam."""
        node, handoff, inv = self.node, self.handoff, self.inventory
        _need(asyncio.current_task() is self.task and self.phase == 'captured'
              and node.root.chain.active is node.intent and node.phase == 'registry_attempted'
              and handoff.in_native and handoff.attempted and handoff.api_calls == 0
              and handoff.registry_calls == 0 and handoff.new_row is None
              and io is handoff.io and change is node.change and change is handoff.change
              and target is handoff.target and target['entity_id'] == node.target
              and target['unique_id'] == node.target.removeprefix('script.')
              and handoff.registry.async_get(node.source) is handoff.row
              and handoff.registry.async_get(node.target) is None,
              'registry_seam_changed')
        node.guard()
        inv.check_service()
        self.audit.check()
        self.recipients.check()

    def move_in_registry_dispatch(self):
        """Move only this proven owner after the real registry row exists."""
        from homeassistant.components.script import ScriptEntity
        from homeassistant.core import HassJob, Service, SupportsResponse
        from homeassistant.helpers import service as service_helper
        from .native_script_config_transport import _graph

        node, handoff, inv = self.node, self.handoff, self.inventory
        row = handoff.new_row
        _need(asyncio.current_task() is self.task and self.phase == 'captured'
              and node.root.chain.active is node.intent and node.phase == 'registry_attempted'
              and handoff.in_native and handoff.attempted and handoff.registry_calls == 1
              and handoff.api_calls == 1 and row is not None
              and handoff.registry.async_get(handoff.new) is row
              and handoff.registry.async_get(handoff.old) is None
              and row.id == node.change.identity and row.entity_id == node.target
              and row.unique_id == node.target.removeprefix('script.')
              and handoff.source is inv.selected_entity
              and handoff.source.registry_entry is row
              and handoff.source.entity_id == node.target,
              'native_dispatch_unqualified')
        node.guard()
        # The registry has committed and the original entity is already bound
        # to that exact row. Even if a service API fails, Core's native handler
        # now sees the new row and cannot unload or stop the retained runs.
        inv.check_service(installed_row=row)
        self.audit.check(installed_row=row)
        self.recipients.check(installed_row=row)
        old, new = inv.service_key, row.unique_id
        from .native_registry_transition import _native_script_default_name_move
        _need(old != new and node.source == 'script.' + old and node.target == 'script.' + new
              and inv.default_name == _native_script_default_name_move(node.change.before, node.change.after)
              and (not inv.default_name or row.original_name == new)
              and len(inv.service_table) > 1 and new not in inv.service_table
              and ('script', new) not in inv.description_cache,
              'destination_changed')
        # The native registry event has already committed. From this point any
        # exception leaves UNKNOWN to the existing intent; no local undo.
        self.phase = 'attempted'
        inv.unique_storage['unique_id'] = new
        inv.unique_storage['__attr_unique_id'] = new
        if inv.default_name:
            inv.script_storage['name'] = new
            inv.unique_storage['__attr_name'] = new
            inv.unique_storage['name'] = new
        inv.service_registry.async_remove('script', old)
        _need(old not in inv.service_table and new not in inv.service_table,
              'native_remove_unconfirmed')
        inv.service_registry.async_register('script', new, inv.service_target,
            schema=inv.service_schema, supports_response=inv.service_response)
        replacement = inv.service_table.get(new)
        _need(type(replacement) is Service and replacement is not inv.service
              and type(replacement.job) is HassJob and replacement.job.target is inv.service_target
              and getattr(replacement.job.target, '__self__', None) is inv.selected_entity
              and getattr(replacement.job.target, '__func__', None) is ScriptEntity._service_handler
              and replacement.schema is inv.service_schema
              and replacement.supports_response is SupportsResponse.OPTIONAL,
              'native_register_unconfirmed')
        wanted_name = inv.description_name
        schema_description = dict(inv.description, name=wanted_name)
        service_helper.async_set_service_schema(inv.hass, 'script', new, schema_description)
        description = inv.description_cache.get(('script', new))
        _need(type(description) is dict and description.get('name') is wanted_name
              and description.get('description') is inv.description.get('description')
              and description.get('fields') is inv.description.get('fields')
              and _graph(inv.description) == inv.description_graph
              and inv.description_cache.get(('script', old)) is inv.description,
              'native_description_unconfirmed')
        del inv.description_cache[('script', old)]
        self.replacement, self.description = replacement, description
        self.description_graph = _graph(description)
        self.phase = 'moved'

    def check_moved(self):
        from homeassistant.helpers import service as service_helper
        from .native_script_config_transport import _graph
        inv, node = self.inventory, self.node
        active = node.root.chain.active is node.intent
        accepted = False
        if not active and node.root.chain.active is None and node.phase == 'observed_effect':
            from .native_registry import _content
            from homeassistant.helpers.json import _orjson_bytes_default_encoder
            import json
            rows = node.root.chain.accepted_documents()['core.entity_registry']['data']['entities']
            accepted = any(row.get('id') == node.change.identity
                           and _content(row) == _content(json.loads(
                               _orjson_bytes_default_encoder(self.handoff.new_row.as_storage_fragment)))
                           for row in rows)
        import inspect
        from homeassistant.helpers.entity import Entity
        from .native_unload_code_proof import unchanged
        from .native_own_id_inventory import _script_attr_descriptor
        unchanged(self.audit.refs, self.audit.methods, self.audit.codes)
        _need(asyncio.current_task() is self.task and self.phase == 'moved'
              and (active or accepted)
              and inv.hass.services is inv.service_registry
              and inv.service_registry._services is inv.service_domains
              and inv.service_domains.get('script') is inv.service_table
              and inv.service_table.get(node.target.removeprefix('script.')) is self.replacement
              and inv.service_key not in inv.service_table
              and sum(value is self.replacement for table in inv.service_domains.values()
                      if type(table) is dict for value in table.values()) == 1
              and self.replacement.job.target is inv.service_target
              and self.replacement.schema is inv.service_schema
              and self.replacement.supports_response is inv.service_response
              and inv.selected_entity.__dict__ is inv.unique_storage
              and inspect.getattr_static(Entity, 'unique_id') is self.audit.unique_descriptor
              and self.audit.unique_descriptor.func is self.audit.unique_getter
              and self.audit.unique_getter.__code__ is self.audit.unique_code
              and all(_script_attr_descriptor(inv.selected_entity, name) is descriptor
                      and descriptor.fset.__code__ is code
                      for name, descriptor, code in zip(('unique_id', 'name'), self.audit.attr_descriptors,
                                                        self.audit.attr_codes, strict=True))
              and _script_attr_descriptor(inv.selected_entity, 'unique_id') is inv.unique_attr_descriptor
              and '_attr_unique_id' not in inv.unique_storage
              and inv.unique_storage.get('unique_id') is self.handoff.new_row.unique_id
              and inv.selected_entity.unique_id is self.handoff.new_row.unique_id
              and inv.unique_storage.get('__attr_unique_id') is self.handoff.new_row.unique_id
              and inv.selected_entity._attr_unique_id is self.handoff.new_row.unique_id
              and (not inv.default_name or (inspect.getattr_static(Entity, 'name') is self.audit.name_descriptor
                   and self.audit.name_descriptor.func is self.audit.name_getter
                   and self.audit.name_getter.__code__ is self.audit.name_code
                   and inv.selected_script.__dict__ is inv.script_storage
                   and inv.script_storage.get('name') is self.handoff.new_row.unique_id
                   and _script_attr_descriptor(inv.selected_entity, 'name') is inv.name_attr_descriptor
                   and '_attr_name' not in inv.unique_storage
                   and inv.unique_storage.get('__attr_name') is self.handoff.new_row.unique_id
                   and inv.selected_entity._attr_name is self.handoff.new_row.unique_id
                   and inv.unique_storage.get('name') is self.handoff.new_row.unique_id
                   and self.handoff.new_row.original_name == self.handoff.new_row.unique_id))
              and inv.hass.data.get(service_helper.SERVICE_DESCRIPTION_CACHE) is inv.description_cache
              and inv.description_cache.get(('script', self.handoff.new_row.unique_id)) is self.description
              and ('script', inv.service_key) not in inv.description_cache
              and sum(value is self.description for value in inv.description_cache.values()) == 1
              and _graph(self.description) == self.description_graph
              and _graph(inv.description) == inv.description_graph,
              'native_owner_changed')
        inv.service_event_census.check()
        self.recipients.check_retained(invoke_guard=False)
        from .native_own_id_inventory import _script_storage_owners
        _script_storage_owners(inv.hass, inv.selected_entity, inv.selected_script,
                               include_name=inv.default_name)
        if inv.automation_service is not None:
            from .native_own_automation_service import check
            check(inv.hass, inv.automation_service)
