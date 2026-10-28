"""Current pinned-pair transfer of actual energy fields at one vendor seam.

No retained clocks/W producers, group migration, accepted source-ID activation,
cache/restore seeding or continuity claim. The caller owns the existing admin
lease; this component participates in ONE existing _Reload service call.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
import math

from .native_registry_receipts import RegistryReceiptChain
from .native_powercalc_accepted_consumers import _accepted_sources
from .native_unload_code_proof import unchanged
from .powercalc_source_binding import _json, _pins
from .powercalc_runtime_observer import _guard


def _need(ok, reason):
    if not ok:
        raise ValueError('powercalc_exact_fields_' + reason) from None


_FIELDS = ('_state', '_last_valid_state', '_last_integration_time',
           '_last_integration_trigger', '_unit_of_measurement')


def _values(entity, integration):
    values = tuple(getattr(entity, name) for name in _FIELDS)
    full, valid, clock, trigger, unit = values
    _need(all(type(v) is Decimal and v.is_finite() for v in (full, valid)), 'finite_live_fields')
    _need(type(clock) is datetime and clock.tzinfo is not None
          and clock.utcoffset() is not None, 'actual_anchor')
    _need(type(trigger) is integration._IntegrationTrigger, 'actual_trigger')
    _need(unit is None or isinstance(unit, str), 'actual_unit')
    return values


def _encoded(values):
    full, valid, clock, trigger, unit = values
    return {'full': str(full), 'last_valid': str(valid), 'anchor': clock.isoformat(),
            'trigger': trigger.name, 'unit': unit,
            'field_process_ids': [id(value) for value in values]}


@dataclass(frozen=True)
class PowercalcExactFieldsReceipt:
    evidence_sha256: str
    original_runtime_token: bytes
    chain_head: str
    source_hashes_json: bytes
    scope_bindings_json: bytes
    transfers_json: bytes
    exact_fields_transfer_verified: bool = field(default=True, init=False)
    continuity_verified: bool = field(default=False, init=False)
    counter_ownership_verified: bool = field(default=False, init=False)
    reload_ownership_verified: bool = field(default=False, init=False)
    migration_ready: bool = field(default=False, init=False)
    family_complete: bool = field(default=False, init=False)
    status: str = field(default='UNKNOWN', init=False)


@dataclass(frozen=True)
class PowercalcExactFieldsReloadWitness:
    lifecycle: object
    global_rename: object
    exact_fields: PowercalcExactFieldsReceipt
    exact_fields_transfer_verified: bool = field(default=True, init=False)
    continuity_verified: bool = field(default=False, init=False)
    counter_ownership_verified: bool = field(default=False, init=False)
    reload_ownership_verified: bool = field(default=False, init=False)
    migration_ready: bool = field(default=False, init=False)
    family_complete: bool = field(default=False, init=False)
    status: str = field(default='UNKNOWN', init=False)


class _ExactFieldsPolicy:
    """Typed chain-bound request, never a caller-selected mutation callback."""
    def __init__(self, chain, evidence, original, guard):
        _need(type(chain) is RegistryReceiptChain
              and chain.active is None and chain.original_receipt.version == '2026.10.2',
              'live_current_chain')
        self.chain, self.evidence, self.original, self.guard = chain, evidence, original, guard
        self.receipt = chain.original_receipt
        self.state = chain.reconstruct_inputs()
        self.source_hashes_json = None
        self.check()

    def check(self):
        _guard(self.guard)
        _need(self.chain.active is None
              and self.chain.original_receipt is self.receipt
              and self.state.same_evidence(self.chain.reconstruct_inputs()), 'same_chain_head')
        self.evidence.assert_receipt(self.receipt)

    def prove(self, vendor):
        # Executor-only source fence. Guard/loop checks run in scope.check on
        # the event loop before and after this job, never inside the executor.
        from .native_utility_setup_audit import _SOURCES
        _accepted_sources(self.chain, self.state, self.receipt)
        pins = dict(_SOURCES['2026.10.2'])
        pins.update(vendor.ha_pins)
        self.source_hashes_json = _json({'vendor': _pins(), 'ha': pins})


class _ExactFieldsOwner:
    """Live component of _Reload, using its code proof and causal observation."""
    def __init__(self, scope, policy):
        _need(type(policy) is _ExactFieldsPolicy
              and scope.receipt is policy.receipt and scope.evidence is policy.evidence
              and scope.original is policy.original, 'same_scope_inputs')
        self.scope, self.policy = scope, policy
        self.cls = scope.vendor.classes['VirtualEnergySensor']
        self.integration = scope.vendor.integration
        self.vrefs = ((self.cls, '_schedule_max_sub_interval_exceeded_if_state_is_numeric'),)
        self.hrefs = ((self.integration.IntegrationSensor, 'async_added_to_hass'),
                      (self.integration.IntegrationSensor,
                       '_schedule_max_sub_interval_exceeded_if_state_is_numeric'))
        self.old = {name: entity for name, entity in scope.old.items() if type(entity) is self.cls}
        _need(len(self.old) == 3, 'exact_integral_slice')
        self.rows, self.targets, self.open, self.complete = {}, {}, {}, set()
        self.transferred = set()
        self.bound = None

    def bind_codes(self):
        scope = self.scope
        self.vm = tuple(scope.vendor_methods[scope.vendor_refs.index(ref)] for ref in self.vrefs)
        self.vc = tuple(scope.vendor_codes[scope.vendor_refs.index(ref)] for ref in self.vrefs)
        self.hm = tuple(scope.ha_methods[scope.ha_refs.index(ref)] for ref in self.hrefs)
        self.hc = tuple(scope.ha_codes[scope.ha_refs.index(ref)] for ref in self.hrefs)
        return {self.vc[0]: 'exact_vendor_schedule', self.hc[1]: 'exact_base_schedule'}

    def bind_original(self, bound):
        # Called only after the existing second ORIGINAL binder, after all IO.
        _need(self.bound is None and self.scope.listener_owner is not None,
              'second_original_and_global_owner')
        self.bound = bound
        self.bindings = {name: (entity.registry_entry.id, entity.unique_id,
                               entity.platform.config_entry, entity._sensor_source_id,
                               entity._source_entity)
                         for name, entity in self.old.items()}
        self.check()

    def check(self):
        _need(self.scope.error is None, 'scope_poisoned')
        self.policy.check()
        unchanged(self.vrefs, self.vm, self.vc)
        unchanged(self.hrefs, self.hm, self.hc)
        _need(self.scope.evidence is self.policy.evidence
              and self.scope.original is self.policy.original, 'scope_inputs_changed')
        if self.bound is not None:
            self.require_sources_current()

    def require_sources_current(self):
        # External physical inputs must stay live. Owned Powercalc sources may
        # temporarily be absent only after their observed native removal; their
        # ORIGINAL registry identity remains fixed throughout the same reload.
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers.entity import EntityPlatformState
        from .powercalc_global_listener_ownership import _OriginalPhysicalSource
        scope = self.scope
        owner = scope.listener_owner
        registry = scope.hass.data[er.DATA_REGISTRY]
        _need(registry is owner.source_registry
              and scope.hass.config_entries is owner.source_entry_manager,
              'exact_physical_managers_changed')
        active = owner.active_sources()
        for name, original in owner.sources.items():
            row = registry.async_get(name)
            _need(row is not None and _OriginalPhysicalSource.from_row(row) == original,
                  'exact_physical_source_changed')
            entity = active.get(name)
            if entity is None:
                old = scope.old.get(name)
                _need(original.platform == 'powercalc' and old is not None
                      and name in scope.removed and scope.started == 1
                      and old._platform_state is EntityPlatformState.REMOVED
                      and not old._on_remove and type(old) is owner.source_classes[name]
                      and old.unique_id == original.unique_id,
                      'unobserved_physical_source_absence')
                continue
            _need(type(entity) is owner.source_classes[name]
                  and entity.hass is scope.hass and entity.entity_id == name
                  and entity.unique_id == original.unique_id and entity.registry_entry is row
                  and entity.platform.entities.get(name) is entity
                  and entity.platform.config_entry is owner.source_entries[name]
                  and (original.config_entry_id is None or
                       scope.hass.config_entries.async_get_entry(original.config_entry_id)
                           is owner.source_entries[name]), 'exact_physical_runtime_changed')

    def _clock_cancelled(self, entity):
        callback = entity._max_sub_interval_exceeded_callback
        handle = getattr(callback, '__self__', None)
        _need(type(handle) is asyncio.TimerHandle and handle._loop is self.scope.loop
              and callback == handle.cancel and math.isfinite(handle.when())
              and handle.cancelled(), 'old_clock_not_natively_cancelled')
        _need(entity.entity_id in self.scope.cancelled
              and entity.entity_id in self.scope.last_active, 'actual_native_cancel_unobserved')
        return handle

    def observe(self, tag, frame, phase):
        scope = self.scope
        entity = frame.f_locals.get('self')
        name = getattr(entity, 'entity_id', None)
        if name not in self.old or entity is self.old.get(name):
            return
        _need(type(entity) is self.cls and entity.hass is scope.hass, 'foreign_replacement_type')
        self.check()
        _need(self.bound is not None and scope.started == 1
              and scope.causal.get(None) is scope.marker, 'foreign_schedule_provenance')
        parent = frame.f_back
        if tag == 'exact_vendor_schedule':
            # Ordinary later schedules may occur after setup; never transfer twice.
            setup = parent is not None and parent.f_code is self.hc[0]
            if not setup:
                _need(name in self.complete and self.targets.get(name) is entity,
                      'first_schedule_outside_added')
                return
            _need(type(asyncio.current_task()) is asyncio.Task, 'foreign_schedule_provenance')
            _need(parent.f_locals.get('self') is entity, 'added_instance')
            if phase == 'start':
                self._transfer(entity, frame)
            else:
                _need(self.open.get(name) == (entity, asyncio.current_task(), 'base_return'),
                      'base_schedule_not_completed')
                _need(all(a is b for a, b in zip(
                    _values(entity, self.integration), self.rows[name]['live_values'], strict=True)),
                    'post_seam_fields_changed')
                del self.open[name]
                self.complete.add(name)
                if self.scope.clock_owner is not None:
                    self.scope.clock_owner.after_exact_schedule(entity)
            return
        if name in self.complete:
            _need(self.targets.get(name) is entity, 'foreign_replacement_type')
            return  # Native timer callbacks may run without a Task; no transfer authority.
        _need(type(asyncio.current_task()) is asyncio.Task, 'foreign_schedule_provenance')
        _need(parent is not None and parent.f_code is self.vc[0]
              and parent.f_locals.get('self') is entity
              and parent.f_back is not None and parent.f_back.f_code is self.hc[0]
              and parent.f_back.f_locals.get('self') is entity, 'base_vendor_added_stack')
        expected = 'vendor_enter' if phase == 'start' else 'base_enter'
        _need(self.open.get(name) == (entity, asyncio.current_task(), expected),
              'one_first_base_schedule')
        live = self.rows[name]['live_values']
        _need(all(a is b for a, b in zip(_values(entity, self.integration), live, strict=True))
              and all(a is b for a, b in zip(_values(self.old[name], self.integration), live, strict=True)),
              'before_base_readback_changed')
        self.open[name] = (entity, asyncio.current_task(),
                           'base_enter' if phase == 'start' else 'base_return')
        self.rows[name]['receipt']['base_' + phase] = _encoded(_values(entity, self.integration))

    def _transfer(self, entity, frame):
        from homeassistant.helpers.entity import EntityPlatformState
        scope = self.scope
        name = entity.entity_id
        old = self.old[name]
        uuid, unique_id, config_entry, sensor_source, source = self.bindings[name]
        _need(name not in self.transferred and name not in self.open
              and old._platform_state is EntityPlatformState.REMOVED
              and not old._on_remove and name in scope.removed
              and entity._platform_state is EntityPlatformState.ADDING
              and entity.registry_entry is old.registry_entry
              and entity.registry_entry.id == uuid and entity.unique_id == unique_id
              and entity.platform.config_entry is config_entry
              and entity.platform.platform_name == 'powercalc'
              and old._sensor_source_id == entity._sensor_source_id == sensor_source
              and old._source_entity == entity._source_entity == source
              and old._max_sub_interval == entity._max_sub_interval
              and old._unit_prefix == entity._unit_prefix
              and old._unit_time == entity._unit_time
              and type(old._method) is type(entity._method)
              and not old._filter_outliers and not entity._filter_outliers,
              'atomic_exact_class_setup_seam')
        ancestor = frame.f_back
        try:
            while ancestor is not None:
                _need(ancestor.f_code.co_qualname != 'EventBus.async_fire_internal',
                      'source_dispatch_at_transfer')
                ancestor = ancestor.f_back
        finally:
            del ancestor
        timer = self._clock_cancelled(old)
        live = _values(old, self.integration)  # Actual live objects, never baseline JSON.
        before = _values(entity, self.integration)
        row = {'entity_id': name, 'uuid': uuid, 'unique_id': unique_id,
               'old_process_id': id(old), 'new_process_id': id(entity),
               'task_process_id': id(asyncio.current_task()),
               'old_cancelled_timer_process_id': id(timer), 'old_deadline': timer.when(),
               'old_live': _encoded(live), 'new_before': _encoded(before)}
        # Mark before the first assignment: even partial/post-seam failure is
        # reported as an effect and is never disguised as a numeric rollback.
        self.transferred.add(name)
        self.rows[name] = {'live_values': live, 'receipt': row}
        self.targets[name] = entity
        for field_name, value in zip(_FIELDS, live, strict=True):
            setattr(entity, field_name, value)
        after = _values(entity, self.integration)
        _need(all(a is b for a, b in zip(after, live, strict=True)), 'actual_field_assignment_readback')
        row['new_after'] = _encoded(after)
        if self.scope.clock_owner is not None:
            self.scope.clock_owner.after_exact_transfer(entity)
        self.open[name] = (entity, asyncio.current_task(), 'vendor_enter')

    def finish(self, current):
        self.check()
        _need(self.complete == self.transferred == set(self.old) and not self.open,
              'incomplete_exact_fields_seams')
        for name, old in self.old.items():
            _need(current[name] is self.targets[name] and self.scope.added[name] is current[name],
                  'actual_replacement_inventory')
            self._clock_cancelled(old)
        scope = self.scope
        bindings = {'operation_id': self.policy.chain.operation_id,
                    'plan_fingerprint': self.policy.chain.plan.fingerprint,
                    'root_id': self.policy.chain.root_id, 'head': self.policy.state.head,
                    'evidence_sha256': scope.evidence.sha256,
                    'vendor_code_process_ids': [id(code) for code in self.vc],
                    'ha_code_process_ids': [id(code) for code in self.hc],
                    'original_source_bindings': [[row.entity_id, row.registry_id, row.source,
                                                  row.source_registry_id] for row in self.bound.rows]}
        _need(type(self.policy.source_hashes_json) is bytes, 'source_proof_required')
        return PowercalcExactFieldsReceipt(scope.evidence.sha256, scope.original.structural_token,
            self.policy.state.head, self.policy.source_hashes_json, _json(bindings),
            _json([self.rows[name]['receipt'] for name in sorted(self.rows)]))

    def annotate(self, error):
        error.add_note('powercalc_exact_fields_effects=' + str(len(self.transferred))
                       + ';no_numeric_rollback_or_continuity')
        if self.rows:
            # Local refusal evidence only; never a successful transfer receipt.
            error.add_note('powercalc_exact_fields_partial_receipt=' + _json(
                [self.rows[name]['receipt'] for name in sorted(self.rows)]).decode())


async def async_reload_with_powercalc_exact_fields(hass, chain, evidence, original, *, guard):
    """One same-chain reload; only exact seam fields verified, never continuity."""
    from .powercalc_reload_witness import async_witness_unchanged_powercalc_reload
    policy = _ExactFieldsPolicy(chain, evidence, original, guard)
    return await async_witness_unchanged_powercalc_reload(hass, policy.receipt, evidence, original,
        guard=guard, _manage_global_rename=True, _exact_fields=policy)
