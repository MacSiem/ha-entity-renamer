"""Task-owned Powercalc phase session over the existing native witness.

This is an internal lifecycle participant, not an endpoint or a grant to
rename. It captures the existing actual-source proof before a group unload and
keeps its native event observers and clocks alive across receipted phases.
"""
from __future__ import annotations

import asyncio

from .powercalc_activation_lease import PowercalcActivationLease
from .powercalc_exact_fields import _ExactFieldsPolicy
from .powercalc_lifecycle import PowercalcLifecycle
from .powercalc_reload_witness import _prepare_reload_scope


class PowercalcPhaseError(ValueError):
    pass


def _need(value, reason):
    if not value:
        raise PowercalcPhaseError('powercalc_phase_' + reason) from None


async def _drain_native(coroutine):
    """Finish one issued native call before propagating owner cancellation."""
    task = asyncio.create_task(coroutine)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if cancelled:
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


class PowercalcPhaseSession:
    def __init__(self):
        raise PowercalcPhaseError('powercalc_phase_capture_required')

    @classmethod
    async def capture(cls, lifecycle):
        _need(cls is PowercalcPhaseSession and type(lifecycle) is PowercalcLifecycle
              and type(lifecycle.lease) is PowercalcActivationLease, 'live_lifecycle_required')
        lease = lifecycle.lease
        lease._task_check()
        lifecycle.check()
        policy = _ExactFieldsPolicy(lease.chain, lease.evidence, lease.original, lifecycle.check)
        policy.bind_lease(lease)
        scope, bound, platforms, before, rename_before, prove = await _prepare_reload_scope(
            lease.hass, lease.receipt, lease.evidence, lease.original, guard=lifecycle.check,
            _manage_global_rename=True, _exact_fields=policy, _retain_producers=True,
            _operand_clock=True, _reverse_admission=lease.reverse_admission)
        lifecycle.check()
        obj = object.__new__(cls)
        obj.lifecycle, obj.lease, obj.scope, obj.policy = lifecycle, lease, scope, policy
        obj.bound, obj.platforms, obj.before = bound, platforms, before
        obj.rename_before, obj.prove = rename_before, prove
        obj.owner, obj.loop = asyncio.current_task(), asyncio.get_running_loop()
        obj.token, obj.phase, obj.closed = None, 'captured', False
        obj.activation = None
        if lease.reverse_admission is not None:
            _need(lease.reverse_phase is None, 'reverse_phase_already_bound')
            lease.reverse_phase = obj
        try:
            obj.token = scope.causal.set(scope.marker)
            scope.install()
            scope.check()
            scope.listener_owner.detach()
            # Direct typed lifecycle calls replace the service's root call.
            # Each effect must still have its own durable intent and actual
            # native callback evidence; this flag does not stand in for either.
            scope.started = 1
            obj.phase = 'detached'
            obj.check()
            return obj
        except BaseException:
            if lease.reverse_phase is obj:
                lease.reverse_phase = None
            try:
                if scope.listener_owner is not None:
                    scope.listener_owner.abort()
            finally:
                try:
                    if scope.clock_owner is not None:
                        scope.clock_owner.close()
                finally:
                    try:
                        if scope.producer_owner is not None:
                            scope.producer_owner.close()
                    finally:
                        scope.close()
                        if obj.token is not None:
                            scope.causal.reset(obj.token)
            raise

    def check(self):
        _need(not self.closed and asyncio.current_task() is self.owner
              and asyncio.get_running_loop() is self.loop and self.token is not None
              and self.scope.causal.get(None) is self.scope.marker
              and self.lease is self.lifecycle.lease, 'live_owner_required')
        self.lifecycle.check()
        self.scope.check()

    async def unload_group(self, group_id):
        self.check()
        _need(self.phase == 'detached', 'group_unload_phase')
        # The lifecycle creates its own durable intent before the native call.
        # An exception after the attempt remains unresolved, never retried.
        self.phase = 'unload_attempted'
        result = await self.lifecycle.unload_group(group_id)
        self.check()
        self.phase = 'group_inactive'
        return result

    async def quiesce_sources(self):
        """Unload the remaining exact native graph under one durable intent.

        Existing native callback witnesses retain actual producers and operand
        clocks. This method neither renames an ID nor authorizes activation.
        """
        from homeassistant.helpers.entity_platform import async_get_platforms
        from .native_registry_receipts import _digest, _event_id

        self.check()
        _need(self.phase == 'group_inactive', 'group_must_be_inactive')
        scope, lease = self.scope, self.lease
        platforms = tuple(async_get_platforms(lease.hass, 'powercalc'))
        current = {name: entity for platform in platforms
                   for name, entity in platform.entities.items()}
        expected = {name: entity for name, entity in scope.old.items() if name not in scope.removed}
        _need(current == expected and all(current[name] is entity for name, entity in expected.items())
              and len(platforms) == len({id(platform) for platform in platforms}),
              'remaining_native_graph_changed')
        entries = tuple(entry for entry in lease.entries if entry.state.value == 'loaded')
        _need(all(entry.state.value in ('loaded', 'not_loaded') for entry in lease.entries),
              'entry_state_changed')
        key = _event_id([self.lifecycle.root_reference[0], 'source_quiesce', 'intent'])
        payload = {'kind': 'powercalc_source_quiesce_intent', 'schema': 1,
                   'root': list(self.lifecycle.root_reference), 'source_chain': lease.state.head,
                   'remaining_uuids': sorted(entity.registry_entry.id for entity in expected.values()),
                   'entries': sorted(entry.entry_id for entry in entries)}
        reference = await lease.chain.store.create(key, payload)
        self.check()
        _need(self.phase == 'group_inactive' and
              lease.chain.store.read(key) == {'digest': _digest(payload), 'payload': payload},
              'quiesce_intent_changed')
        self.phase = 'quiesce_attempted'
        first_call = len(scope.calls)

        async def drained(coroutine):
            task = asyncio.create_task(coroutine)
            cancelled = False
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    cancelled = True
                except Exception:
                    break
            if cancelled:
                if not task.cancelled():
                    task.exception()
                raise asyncio.CancelledError
            return task.result()

        for platform in platforms:
            self.check()
            _need(all(name in expected and expected[name] is entity
                      for name, entity in platform.entities.items()), 'foreign_platform_entity')
            await drained(platform.async_reset())
            self.check()
        for entry in entries:
            self.check()
            _need(entry.state.value == 'loaded', 'native_entry_changed_before_unload')
            _need(await drained(lease.manager.async_unload(entry.entry_id, _lock=False)) is True
                  and entry.state.value == 'not_loaded', 'native_entry_unload_failed')
            self.check()
        # An unload ACK may precede genuine retained producer descendants.
        # Drain only this session's observed tasks without propagating caller
        # cancellation into those already-issued native operations.
        await drained(scope.settle_descendants())
        self.check()
        calls = scope.calls[first_call:]
        _need(all(calls.count(['reset', 'start', id(platform)]) >= 1
                  and calls.count(['reset', 'return', id(platform)]) ==
                      calls.count(['reset', 'start', id(platform)])
                  for platform in platforms)
              and all(calls.count(['unload', 'start', entry.entry_id]) == 1
                      and calls.count(['unload', 'return', entry.entry_id]) == 1
                      for entry in entries), 'native_quiesce_callbacks_unconfirmed')
        _need(scope.removed == set(scope.old) and not scope.inflight
              and all(entry.state.value == 'not_loaded' for entry in lease.entries),
              'native_graph_not_quiescent')
        self.check()
        result_key = _event_id([self.lifecycle.root_reference[0], 'source_quiesce', 'receipt'])
        receipt = {'kind': 'powercalc_source_quiesce_receipt', 'schema': 1,
                   'root': list(self.lifecycle.root_reference), 'intent': list(reference),
                   'source_chain': lease.state.head,
                   'removed_uuids': sorted(entity.registry_entry.id for entity in scope.old.values()),
                   'entries': sorted(entry.entry_id for entry in lease.entries)}
        result = await lease.chain.store.create(result_key, receipt)
        self.check()
        _need(lease.chain.store.read(result_key) == {'digest': _digest(receipt), 'payload': receipt},
              'quiesce_receipt_readback')
        self.phase = 'sources_inactive'
        return result

    def _activation_aliases(self):
        """Resolve only already-accepted ORIGINAL registry movements."""
        from homeassistant.helpers import entity_registry as er
        from .native_registry import PreparedRegistryChange
        registry = er.async_get(self.lease.hass)
        positions = self.lease.chain._setup_positions.get(self.lease.state.head)
        _need(type(positions) is dict, 'accepted_activation_prefix_required')
        aliases = {}
        for index, step in enumerate(self.lease.chain.plan.steps):
            if step.adapter != 'registry':
                continue
            change = self.lease.chain._original_change(index)
            if type(change) is not PreparedRegistryChange or change.kind != 'entity' or not change.mapping:
                continue
            old, new = change.before['entity_id'], change.after['entity_id']
            if self.lifecycle.direction == 'rollback':
                old, new = new, old
            if old not in self.scope.old:
                continue
            # The existing exact/clock owners presently key their energy and
            # power instances by original ID. The group has its own typed PSS
            # and restore proof. Other owned-ID movements remain unqualified.
            _need(old == self.scope.group_id and positions.get(index) == self.lifecycle.direction
                  and change.identity == self.before[old]['uuid']
                  and registry.async_get(old) is None
                  and registry.async_get(new) is not None
                  and registry.async_get(new).id == change.identity
                  and new not in aliases, 'accepted_group_id_required')
            aliases[new] = old
        return aliases

    def verify_activation_preflight(self, *, hooks=None):
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
        from . import native_restore_state_payload as codec
        from .native_registry_receipts import _digest, _event_id
        from .powercalc_pss_handoff import _snapshot, _unloads
        from .powercalc_restore_handoff import _captured

        self.check()
        _need(self.phase == 'sources_inactive' and self.activation is None
              and all(entry.state.value == 'not_loaded' for entry in self.lease.entries),
              'inactive_activation_required')
        aliases = self._activation_aliases()
        moves = self.lifecycle.moves
        _need(len(moves) == 1 and moves[0].before == self.scope.group_id,
              'original_group_required')
        move = moves[0]
        unloads = _unloads(self.lifecycle, yaml_hooks=hooks)
        chain = self.lease.chain
        reverse = self.lifecycle.direction == 'rollback'
        pss_label = 'pss_reverse' if reverse else 'pss_forward'
        restore_label = 'restore_reverse' if reverse else 'restore_forward'
        pss_key = _event_id([self.lifecycle.root_reference[0], pss_label, 'receipt'])
        restore_key = _event_id([self.lifecycle.root_reference[0], restore_label, move.before, 'receipt'])
        pss, restore = chain.store.read(pss_key), chain.store.read(restore_key)
        _need(pss is not None and restore is not None
              and pss['digest'] == _digest(pss['payload'])
              and restore['digest'] == _digest(restore['payload'])
              and pss['payload'].get('kind') == 'powercalc_' + pss_label + '_receipt'
              and restore['payload'].get('kind') == ('powercalc_group_restore_reverse_receipt'
                                                      if reverse else 'powercalc_group_restore_receipt')
              and pss['payload'].get('root') == list(self.lifecycle.root_reference)
              and restore['payload'].get('root') == list(self.lifecycle.root_reference)
              and pss['payload'].get('source_chain') in chain._setup_positions
              and restore['payload'].get('source_chain') in chain._setup_positions
              and restore['payload'].get('unload') == list(unloads[move.before][:2])
              and restore['payload'].get('pss') == [pss_key, pss['digest']]
              and pss['payload'].get('group_totals', {}).get(move.before)
                  == restore['payload'].get('full_total') == unloads[move.before][2]['full_total'],
              'accepted_group_handoffs_required')
        intent_ref = restore['payload'].get('intent')
        _need(type(intent_ref) is list and len(intent_ref) == 2, 'restore_intent_reference')
        restore_intent = chain.store.read(intent_ref[0])
        _need(restore_intent is not None and restore_intent['digest'] == intent_ref[1]
              and restore_intent['digest'] == _digest(restore_intent['payload'])
              and restore_intent['payload'].get('kind') == ('powercalc_group_restore_reverse_intent'
                                                             if reverse else 'powercalc_group_restore_intent')
              and restore_intent['payload'].get('root') == list(self.lifecycle.root_reference),
              'restore_original_intent_required')
        restore_data = self.lease.hass.data.get(DATA_RESTORE_STATE)
        _need(restore_data is not None and move.after not in restore_data.entities
              and move.after in restore_data.last_states
              and _captured(restore_data.last_states[move.after])
                  == codec._canonical(restore_intent['payload']['after'])
              and _snapshot(self.scope.previous_store).get(move.after)
                  == pss['payload'].get('target', {}).get(move.after),
              'native_group_restore_changed')
        return aliases

    async def activate_yaml(self, hooks):
        """Run the pinned vendor setup suffix under the existing YAML intent."""
        from importlib import import_module
        from pathlib import Path
        from homeassistant.config_entries import ConfigEntries
        from homeassistant.helpers import reload as reload_helper
        from .native_registry_receipts import _digest, _event_id
        from .native_unload_code_proof import prove_methods, unchanged
        from .native_utility_setup_audit import _SOURCES
        from .powercalc_source_binding import _pins
        from .powercalc_yaml_adapter import _PowercalcYAMLHooks
        from .yaml_validation import _settled_executor

        _need(type(hooks) is _PowercalcYAMLHooks and hooks.adapter.phase is self
              and self.policy.activation_hooks is hooks and hooks.intent is self.lease.pending,
              'typed_live_yaml_activation_required')
        aliases = self.verify_activation_preflight(hooks=hooks)
        _need(self.lease.receipt.version == '2026.10.4', 'activation_core_unsupported')
        native = import_module('custom_components.powercalc')
        vendor_refs = tuple((native, name) for name in ('get_global_configuration',
            '_async_load_yaml_sensor', 'setup_domain_groups', 'create_standby_group'))
        ha_refs = ((reload_helper, 'async_integration_yaml_config'), (ConfigEntries, 'async_setup'))
        pins = dict(_SOURCES[self.lease.receipt.version])
        pins['helpers/reload.py'] = '398babe3ee681c7211ea819279c5921a0b3bbb6a1e89e07d4534a2fce2a2ef34'
        def prove():
            self.scope.vendor.sources()
            return (prove_methods(self.scope.vendor.root, _pins(), vendor_refs),
                    prove_methods(self.scope.vendor.ha_root, pins, ha_refs))
        proofs = await _settled_executor(self.lease.hass, prove)
        await hooks.checkpoint()
        _need(native.async_integration_yaml_config is reload_helper.async_integration_yaml_config,
              'vendor_reload_binding_changed')
        config = await reload_helper.async_integration_yaml_config(self.lease.hass, 'powercalc')
        await hooks.checkpoint()
        from homeassistant.util.yaml.objects import NodeDictClass
        _need(type(config) in (dict, NodeDictClass) and 'powercalc' in config,
              'native_yaml_configuration_required')
        unchanged(vendor_refs, *proofs[0])
        unchanged(ha_refs, *proofs[1])
        _need(self.verify_activation_preflight(hooks=hooks) == aliases, 'activation_preflight_changed')
        moves = self.lifecycle.moves
        _need(len(moves) == 1 and moves[0].before == self.scope.group_id,
              'original_group_required')
        target_group = moves[0].after
        _need(aliases.get(target_group, target_group) == moves[0].before,
              'accepted_group_alias_required')
        key = _event_id([self.lifecycle.root_reference[0], 'source_setup', 'intent'])
        payload = {'kind': 'powercalc_source_setup_intent', 'schema': 1,
                   'root': list(self.lifecycle.root_reference),
                   'yaml_intent': list(hooks.intent.reference), 'source_chain': self.lease.state.head,
                   'aliases': sorted([new, old] for new, old in aliases.items()),
                   'entries': sorted(entry.entry_id for entry in self.lease.entries),
                   'original_entities': {name: {key: row[key] for key in ('uuid', 'unique_id', 'class')}
                                         for name, row in sorted(self.before.items())}}
        reference = await self.lease.chain.store.create(key, payload)
        await hooks.checkpoint()
        _need(self.lease.chain.store.read(key) == {'digest': _digest(payload), 'payload': payload}
              and self.verify_activation_preflight(hooks=hooks) == aliases, 'setup_intent_changed')
        self.phase = 'activation_attempted'
        self.scope.activation_aliases = aliases
        self.scope.group_id = target_group
        data = self.lease.hass.data.get('powercalc')
        _need(type(data) is dict, 'native_vendor_data_required')
        data[native.DATA_USED_UNIQUE_IDS] = []
        data[native.DATA_CONFIGURED_ENTITIES] = {}
        data[native.DATA_ANALYTICS] = {}
        data[native.DOMAIN_CONFIG] = native.get_global_configuration(self.lease.hass, config)
        for sensor_config in config['powercalc'].get(native.CONF_SENSORS, []):
            await hooks.checkpoint()
            sensor_config.update({native.DISCOVERY_TYPE: native.PowercalcDiscoveryType.USER_YAML})
            await _drain_native(native._async_load_yaml_sensor(self.lease.hass, sensor_config, config))
            await _drain_native(self.scope.settle_descendants())
            await hooks.checkpoint()
        group_entries = tuple(row[2] for row in self.lifecycle._unloaded.values())
        _need(len(group_entries) == 1, 'group_setup_entry_required')
        ordered = tuple(entry for entry in self.lease.entries
                        if all(entry is not group for group in group_entries)) + group_entries
        for entry in ordered:
            await hooks.checkpoint()
            _need(entry.state.value == 'not_loaded'
                  and await _drain_native(self.lease.manager.async_setup(entry.entry_id, _lock=False)) is True,
                  'native_entry_setup_failed')
            await hooks.checkpoint()
        global_config = native.get_global_configuration(self.lease.hass, config)
        native.setup_domain_groups(self.lease.hass, global_config)
        await _drain_native(native.create_standby_group(self.lease.hass, global_config))
        await _drain_native(self.scope.settle_descendants())
        await hooks.checkpoint()
        unchanged(vendor_refs, *proofs[0])
        unchanged(ha_refs, *proofs[1])
        self.activation = {'intent': reference, 'aliases': aliases, 'target_group': target_group}
        self.phase = 'activated'
        current = self.verify_activation()
        _need(['store_clean', 'return', target_group] in self.scope.calls,
              'native_previous_store_cleanup_unobserved')
        proof = await _settled_executor(self.lease.hass, self.prove)
        _need(proof == (self.scope.vendor_methods, self.scope.vendor_codes,
                        self.scope.ha_methods, self.scope.ha_codes), 'final_native_proof_changed')
        await hooks.checkpoint()
        exact = self.scope.exact_owner.finish(current)
        producer = self.scope.producer_owner.finish(current)
        clock = self.scope.clock_owner.finish(current)
        after, _ = self.scope.rename_listeners()
        global_owner = self.scope.listener_owner.finish(after, accepted_group=self)
        self.check()
        result_key = _event_id([self.lifecycle.root_reference[0], 'source_setup', 'receipt'])
        import json
        from .powercalc_native_snapshot import native_snapshot
        receipt = {'kind': 'powercalc_source_setup_receipt', 'schema': 1,
                   'root': list(self.lifecycle.root_reference), 'intent': list(reference),
                   'yaml_intent': list(hooks.intent.reference), 'source_chain': self.lease.state.head,
                   'aliases': sorted([new, old] for new, old in aliases.items()),
                   'target_group': target_group,
                   'entities': {name: native_snapshot(self.scope.snapshot(entity))
                                for name, entity in sorted(current.items())},
                   'exact_transfers': json.loads(exact.transfers_json),
                   'producer_operations': json.loads(producer.native_operations_json),
                   'producer_results': json.loads(producer.calculations_json),
                   'clock_steps': json.loads(clock.steps_json),
                   'timer_proofs': json.loads(clock.timer_proofs_json),
                   'store_clean_calls': [call for call in self.scope.calls if call[0] == 'store_clean']}
        result = await self.lease.chain.store.create(result_key, receipt)
        await hooks.checkpoint()
        _need(self.lease.chain.store.read(result_key) == {'digest': _digest(receipt), 'payload': receipt},
              'setup_receipt_readback')
        self.activation.update(receipt=result, exact=exact, producer=producer,
                               clock=clock, global_owner=global_owner)

    async def settle_activation(self):
        if self.phase in ('activation_attempted', 'activated'):
            await _drain_native(self.scope.settle_descendants())

    def verify_activation(self):
        self.check()
        _need(self.phase in ('activated', 'active_receipted') and type(self.activation) is dict
              and all(entry.state.value == 'loaded' for entry in self.lease.entries),
              'native_activation_incomplete')
        _, current = self.scope.entities()
        expected = {self.activation['aliases'].get(name, name): entity for name, entity in current.items()}
        _need(len(expected) == len(current) and set(expected) == set(self.scope.old)
              and all(expected[name] is self.scope.added.get(name)
                      and expected[name] is not old and type(expected[name]) is type(old)
                      and expected[name].registry_entry.id == self.before[name]['uuid']
                      and expected[name].unique_id == self.before[name]['unique_id']
                      for name, old in self.scope.old.items()), 'activated_uuid_graph_changed')
        return expected

    def ack_activation(self, reference):
        self.check()
        _need(self.phase == 'activated' and self.activation is not None
              and self.activation.get('receipt') is not None
              and self.scope.listener_owner.committed
              and type(reference) is tuple and self.lease.state.head == reference[1],
              'accepted_yaml_activation_required')
        self.phase = 'active_receipted'

    def claim_activation(self, chain, intent, context):
        self.check()
        _need(chain is self.lease.chain and intent is self.lease.pending
              and chain.active is intent and self.phase == 'activated'
              and self.policy.activation_hooks is not None
              and self.policy.activation_hooks.intent is intent
              and self.policy.activation_hooks.context == context
              and self.activation is not None and self.activation.get('receipt') is not None,
              'live_activation_receipt_required')
        self.verify_activation()
        return self.activation['receipt']

    def close(self):
        _need(asyncio.current_task() is self.owner and asyncio.get_running_loop() is self.loop
              and not self.closed, 'owner_close_required')
        self.closed = True
        if self.lease.reverse_phase is self:
            self.lease.reverse_phase = None
        first = None
        def attempt(call):
            nonlocal first
            try:
                call()
            except BaseException as error:
                if first is None:
                    first = error
        # The original listener pair is restorable only before any native
        # unload was attempted. An incomplete effect must stay unresolved.
        if self.phase in ('captured', 'detached'):
            attempt(self.scope.listener_owner.abort)
        else:
            # A closed observer cannot attest that the inactive boundary
            # remains owned. Never let a retained receipt revive this writer.
            self.lifecycle._unloaded.clear()
            self.lifecycle._unload_states.clear()
        attempt(self.scope.clock_owner.close)
        attempt(self.scope.producer_owner.close)
        transition = getattr(self.scope, 'source_transition', None)
        if transition is not None:
            attempt(transition.close)
        attempt(self.scope.close)
        if self.token is not None:
            attempt(lambda: self.scope.causal.reset(self.token))
            self.token = None
        if first is not None:
            raise first
        _need(self.phase in ('captured', 'detached', 'active_receipted'),
              'incomplete_effect_requires_recovery')
