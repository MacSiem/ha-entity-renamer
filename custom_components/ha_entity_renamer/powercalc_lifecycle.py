"""Typed Powercalc lifecycle root in the existing registry receipt store.

A root records ORIGINAL and a plan; it authorizes no unload, mutation, retry,
startup or setup. Later phases must bind real native effects and persist their
own intents and receipts under the same root. A reopened root is read-only.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json

from .native_registry import PreparedRegistryChange
from .native_registry_receipts import RegistryReceiptChain, RegistryReceiptStore, _digest, _event_id
from .powercalc_activation_lease import PowercalcActivationLease
from .prepared import canonical
from .powercalc_composition import PowercalcComposition
from .powercalc_previous_state_payload import GroupMove


class PowercalcLifecycleError(ValueError):
    pass


def _need(condition, reason):
    if not condition:
        raise PowercalcLifecycleError('powercalc_lifecycle_' + reason) from None


def _original(chain):
    _need(type(chain) is RegistryReceiptChain and type(chain.store) is RegistryReceiptStore,
          'registry_chain_required')
    baseline, composition = chain.preparations.resolve_composition(chain.plan.native_inputs)
    _need(baseline == chain.baseline and type(composition) is PowercalcComposition
          and baseline.receipt == chain.original_receipt, 'same_original_required')
    return composition


def _moves(chain, composition):
    changes = [change for change in chain.changes if type(change) is PreparedRegistryChange
               and change.kind == 'entity' and change.before['entity_id'] != change.after['entity_id']]
    mapping, uuids, targets = {}, {}, set()
    for change in changes:
        before, after = change.before, change.after
        _need(before['id'] == after['id'] == change.identity
              and all(before.get(field) == after.get(field) for field in
                      ('unique_id', 'platform', 'config_entry_id', 'config_subentry_id'))
              and before['entity_id'] not in mapping and after['entity_id'] not in targets,
              'registry_mapping')
        mapping[before['entity_id']] = after['entity_id']
        uuids[before['entity_id']] = change.identity
        targets.add(after['entity_id'])
    _need(not any(source != target and target in mapping for source, target in mapping.items()),
          'registry_mapping_overlap')
    moves = []
    for group in composition.previous.groups:
        _need(group.entity_id not in uuids or uuids[group.entity_id] == group.registry_id,
              'group_uuid')
        for name, uuid in group.members:
            _need(name not in uuids or uuids[name] == uuid, 'member_uuid')
        moves.append(GroupMove(group.entity_id, mapping.get(group.entity_id, group.entity_id),
            tuple((name, mapping.get(name, name)) for name, _ in group.members)))
    return tuple(moves)


def _root(chain, composition):
    moves = _moves(chain, composition)
    payload = {'kind': 'powercalc_lifecycle_root', 'schema': 1, 'registry_root': chain.root_id,
            'chain_root_digest': chain.root_digest, 'operation_id': chain.operation_id,
            'fingerprint': chain.plan.fingerprint, 'native_inputs': list(chain.plan.native_inputs),
            'original_receipt_sha256': composition.previous.receipt_sha256,
            'original_sources_sha256': composition.evidence.powercalc_sources.sha256,
            'candidate_sources_sha256': composition.evidence.powercalc_candidate_sources.sha256,
            'groups': [asdict(group) for group in composition.previous.groups],
            'moves': [asdict(move) for move in moves]}
    # Dataclass members contain tuples; canonical accepts a JSON tree only.
    return canonical(json.loads(json.dumps(payload, allow_nan=False)))[0]


def _reverse_spec(chain, composition, forward_reference, forward_head):
    """Derive inverse root and directional mappings from the SAME original."""
    from .powercalc_previous_state_original import PowercalcGroupOriginal
    _need(type(forward_reference) in (list, tuple) and len(forward_reference) == 2
          and type(forward_head) is str, 'reverse_forward_reference')
    forward_root = _root(chain, composition)
    forward_id = _event_id(['powercalc_lifecycle', chain.root_id, chain.plan.fingerprint])
    _need(list(forward_reference) == [forward_id, _digest(forward_root)]
          and chain.store.read(forward_id) == {'digest': _digest(forward_root), 'payload': forward_root}
          and chain._setup_positions.get(forward_head) == {i: 'forward' for i in range(len(chain.plan.steps))},
          'reverse_forward_chain_required')
    groups, reverse_moves = [], []
    for original, move in zip(composition.previous.groups, _moves(chain, composition), strict=True):
        member_ids = dict(original.members)
        _need(tuple(member_ids) == tuple(old for old, _ in move.members), 'reverse_member_binding')
        groups.append(PowercalcGroupOriginal(move.after, original.registry_id,
            tuple((new, member_ids[old]) for old, new in move.members), original.full_total))
        reverse_moves.append(GroupMove(move.after, move.before,
            tuple((new, old) for old, new in move.members)))
    root = {'kind': 'powercalc_lifecycle_reverse_root', 'schema': 1,
            'registry_root': chain.root_id, 'operation_id': chain.operation_id,
            'fingerprint': chain.plan.fingerprint, 'forward_root': list(forward_reference),
            'forward_head': forward_head, 'groups': [asdict(group) for group in groups],
            'moves': [asdict(move) for move in reverse_moves]}
    return canonical(json.loads(json.dumps(root, allow_nan=False)))[0], tuple(groups), tuple(reverse_moves)


def validate_reverse_root(chain, reference):
    """Read-only validation; never grants the live inverse capability."""
    _need(type(reference) in (list, tuple) and len(reference) == 2, 'reverse_root_reference')
    row = chain.store.read(reference[0])
    _need(row is not None and row['digest'] == reference[1] == _digest(row['payload'])
          and row['payload'].get('kind') == 'powercalc_lifecycle_reverse_root', 'reverse_root_missing')
    value = row['payload']
    root, groups, moves = _reverse_spec(chain, _original(chain), value.get('forward_root'), value.get('forward_head'))
    expected_id = _event_id(['powercalc_lifecycle_rollback', chain.operation_id,
                             chain.plan.fingerprint, value['forward_head']])
    _need(reference[0] == expected_id and value == root, 'reverse_root_invalid')
    return root, groups, moves


@dataclass(frozen=True)
class PowercalcLifecycleView:
    root_reference: tuple[str, str]
    root: dict
    composition: PowercalcComposition

    @property
    def writable(self):
        return False


class PowercalcLifecycle:
    """One live root tied to a task-owned full Powercalc activation lease."""
    def __init__(self):
        raise PowercalcLifecycleError('powercalc_lifecycle_start_required')

    @classmethod
    async def start(cls, lease):
        _need(cls is PowercalcLifecycle and type(lease) is PowercalcActivationLease,
              'typed_lease_required')
        lease._task_check()
        lease.check()
        chain = lease.chain
        _need(chain.writable and chain.active is None and
              chain.reconstruct_inputs().head == chain.root_digest, 'original_chain_head_required')
        composition = _original(chain)
        _need(composition.evidence.sha256 == lease.evidence.sha256, 'lease_evidence_changed')
        root = _root(chain, composition)
        identity = _event_id(['powercalc_lifecycle', chain.root_id, chain.plan.fingerprint])
        reference = await chain.store.create(identity, root)
        lease.check()
        _need(chain.store.read(identity) == {'digest': _digest(root), 'payload': root}
              and reference == (identity, _digest(root)), 'root_readback')
        obj = object.__new__(cls)
        obj.lease, obj.chain, obj.composition = lease, chain, composition
        obj.root, obj.root_reference = root, reference
        obj.direction, obj.groups, obj.moves = 'forward', composition.previous.groups, _moves(chain, composition)
        # Live issuance only. A reopened lifecycle never acquires unload
        # authority merely because an old receipt happens to exist on disk.
        obj._unloaded = {}
        obj._unload_states = {}
        return obj

    @classmethod
    async def start_reverse(cls, lease):
        """Start one fresh inverse root on the accepted forward chain."""
        from .powercalc_reverse_admission import PowercalcReverseAdmission
        _need(cls is PowercalcLifecycle and type(lease) is PowercalcActivationLease
              and type(lease.reverse_admission) is PowercalcReverseAdmission,
              'reverse_admission_required')
        lease._task_check()
        lease.check()
        admission, chain = lease.reverse_admission, lease.chain
        admission._scope()
        _need(admission.chain is chain and admission.state.same_evidence(lease.state)
              and chain.active is None and chain.store.read(admission.reverse_root) is None,
              'accepted_forward_head_required')
        composition = _original(chain)
        root, groups, reverse_moves = _reverse_spec(
            chain, composition, admission.forward.root_reference, admission.state.head)
        reference = await chain.store.create(admission.reverse_root, root)
        lease.check()
        _need(chain.store.read(admission.reverse_root) == {'digest': _digest(root), 'payload': root}
              and reference == (admission.reverse_root, _digest(root)), 'reverse_root_readback')
        obj = object.__new__(cls)
        obj.lease, obj.chain, obj.composition = lease, chain, composition
        obj.root, obj.root_reference = root, reference
        obj.direction, obj.groups, obj.moves = 'rollback', tuple(groups), tuple(reverse_moves)
        obj._unloaded, obj._unload_states = {}, {}
        return obj

    @staticmethod
    def reopen(chain):
        """Validate the root using only retained ORIGINAL; never return a writer."""
        composition = _original(chain)
        root = _root(chain, composition)
        key = _event_id(['powercalc_lifecycle', chain.root_id, chain.plan.fingerprint])
        digest = _digest(root)
        _need(chain.store.read(key) == {'digest': digest, 'payload': root}, 'root_changed_or_absent')
        return PowercalcLifecycleView((key, digest), root, composition)

    def check(self):
        self.lease.check()
        _need(self.lease.chain is self.chain and self.chain.writable
              and self.chain.store.read(self.root_reference[0]) ==
                  {'digest': self.root_reference[1], 'payload': self.root}, 'live_root_changed')
        if self.direction == 'forward':
            _need(_root(self.chain, _original(self.chain)) == self.root, 'live_root_changed')
        else:
            admission = self.lease.reverse_admission
            _need(admission is not None and admission.chain is self.chain
                  and self.root['forward_root'] == list(admission.forward.root_reference)
                  and self.root['forward_head'] == admission.state.head,
                  'reverse_root_changed')
            root, groups, moves = validate_reverse_root(self.chain, self.root_reference)
            _need(root == self.root and groups == self.groups and moves == self.moves,
                  'reverse_root_changed')

    async def unload_group(self, group_id):
        """Unload one proven native group after a durable intent.

        This phase alone cannot rename or reactivate anything. Uncertain native
        completion leaves the intent unresolved; no automatic replay or repair.
        Child producers remain native/active; a later typed phase must account
        for their actual samples before any group setup.
        """
        import asyncio
        import json
        from decimal import Decimal
        from pathlib import Path
        import homeassistant
        from homeassistant.config_entries import ConfigEntries, ConfigEntryState
        from homeassistant.core import State, HassJob, HassJobType
        from homeassistant.helpers import event
        from homeassistant.helpers.entity_platform import EntityPlatform, async_get_platforms
        from homeassistant.helpers.json import json_bytes
        from .native_input_baseline import _drained, _loads
        from .native_unload_code_proof import prove_methods, unchanged
        from .native_utility_setup_audit import _SOURCES
        from .powercalc_source_binding import PowercalcVendor

        self.lease._task_check()
        self.check()
        originals = [row for row in self.groups if row.entity_id == group_id]
        _need(len(originals) == 1, 'original_group_required')
        original = originals[0]
        vendor = PowercalcVendor(self.lease.hass, with_profiles=True)
        hass = self.lease.hass
        matches = [entity for platform in async_get_platforms(hass, 'powercalc')
                   for entity in platform.entities.values() if entity.entity_id == group_id]
        _need(len(matches) == 1 and type(matches[0]) is vendor.classes['GroupedEnergySensor'],
              'active_group_required')
        group = matches[0]
        entry = group.platform.config_entry
        _need(any(entry is item for item in self.lease.entries) and entry.state is ConfigEntryState.LOADED
              and group.registry_entry is not None and group.registry_entry.id == original.registry_id
              and group._entities == {name for name, _ in original.members}, 'group_registry_binding')
        store = group._prev_state_store
        _need(type(store) is vendor.classes['PreviousStateStore']
              and hass.data.get('powercalc_group_storage') is store, 'previous_store_binding')
        old_entities = tuple(entity for platform in async_get_platforms(hass, 'powercalc')
                             if platform.config_entry is entry for entity in platform.entities.values())
        _need(any(group is item for item in old_entities), 'entry_platform_membership')
        ha_refs = ((ConfigEntries, 'async_unload'), (EntityPlatform, 'async_reset'),
                   (event, 'async_track_state_change_event'), (event, '_remove_listener'),
                   (event, '_async_dispatch_entity_id_event'), (event, '_async_state_filter'))
        vendor_refs = ((vendor.classes['PreviousStateStore'], 'set_entity_state'),
                       (vendor.classes['PreviousStateStore'], 'cleanup_entity_states'))
        pins = dict(_SOURCES[self.chain.original_receipt.version])
        pins.update(vendor.ha_pins)
        from .powercalc_source_binding import _pins
        def prove():
            vendor.sources()
            return (prove_methods(Path(homeassistant.__file__).resolve().parent, pins, ha_refs),
                    prove_methods(vendor.root, _pins(), vendor_refs))
        proofs = await _drained(hass.async_add_executor_job, prove)
        self.check()
        _need(entry.state is ConfigEntryState.LOADED and group._prev_state_store is store,
              'preflight_changed')

        def snapshot():
            _need(group.registry_entry is not None and group.registry_entry.id == original.registry_id
                  and group._prev_state_store is store and hass.data.get('powercalc_group_storage') is store
                  and type(group._native_value_exact) is Decimal and group._native_value_exact.is_finite()
                  and type(group._rounding_digits) is int
                  and isinstance(group.native_unit_of_measurement, str),
                  'group_snapshot_identity')
            previous = store.states.get(group_id, {})
            _need(type(previous) is dict and set(previous) <= {name for name, _ in original.members}
                  and all(state is None or type(state) is State and state.entity_id == name
                          for name, state in previous.items()), 'previous_snapshot')
            payload = {name: None if state is None else state.json_fragment
                       for name, state in sorted(previous.items())}
            return {'group_id': group_id, 'group_uuid': original.registry_id,
                    'entry_id': entry.entry_id, 'full_total': str(group._native_value_exact),
                    'precision': group._rounding_digits, 'unit': str(group.native_unit_of_measurement),
                    'previous': _loads(json_bytes(payload))}

        from .powercalc_tracker_lease import PowercalcTrackerLease
        tracker = PowercalcTrackerLease(hass, group_id)
        try:
            old_callbacks = tracker.issued[2]
            old_jobs = tuple(job for jobs in old_callbacks.values() for job in jobs
                             if any(getattr(job.target, '__self__', None) is old for old in old_entities))
            _need(old_jobs and all(type(job) is HassJob and job.job_type is HassJobType.Callback
                  for job in old_jobs)
                  and any(getattr(job.target, '__self__', None) is group for job in old_jobs),
                  'actual_old_group_jobs_required')
            before = snapshot()
            key = _event_id([self.root_reference[0], 'unload', original.registry_id, 'intent'])
            intent = {'kind': 'powercalc_group_unload_intent', 'schema': 1,
                      'root': list(self.root_reference), 'source_chain': self.lease.state.head,
                      'before': before}
            reference = await self.chain.store.create(key, intent)
            self.check()
            _need(entry.state is ConfigEntryState.LOADED and group._prev_state_store is store,
                  'pre_unload_changed')
            # Native API operates on the exact pre-acquired lock; no replacement of
            # callback, Event, State, scheduler or integration method occurs.
            task = asyncio.create_task(self.lease.manager.async_unload(entry.entry_id, _lock=False))
            cancelled = False
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    cancelled = True
                except Exception:
                    break
            if cancelled:
                if task.done() and not task.cancelled():
                    task.exception()
                raise asyncio.CancelledError
            result = task.result()
            self.check()
            _need(result is True and entry.state is ConfigEntryState.NOT_LOADED, 'native_unload_failed')
            # Bound native group jobs are synchronous callbacks; after their
            # exact removers return no old callback remains to drain. Avoid a
            # global task drain while holding native ConfigEntry locks.
            _need(entry.state is ConfigEntryState.NOT_LOADED
                  and all(not getattr(entity, '_on_remove', None) for entity in old_entities)
                  and not any(entity is old for platform in async_get_platforms(hass, 'powercalc')
                              for entity in platform.entities.values() for old in old_entities),
                  'native_group_not_quiesced')
            for tracker_key in (event._TRACK_STATE_CHANGE_DATA, event._TRACK_STATE_REPORT_DATA):
                current_tracker = hass.data.get(tracker_key)
                if current_tracker is not None:
                    _need(not any(getattr(job.target, '__self__', None) is old
                                  for jobs in current_tracker.callbacks.values() for job in jobs
                                  for old in old_entities), 'old_group_listener_left')
            _need(not any(job is old for jobs in old_callbacks.values() for job in jobs for old in old_jobs),
                  'original_dispatcher_old_group_jobs_left')
            after = snapshot()
            placeholder = hass.states.get(group_id)
            _need(type(placeholder) is State, 'native_unload_placeholder_required')
            placeholder_json = placeholder.as_dict_json.decode('utf-8')
            placeholder_reported = placeholder.last_reported.isoformat()
            unchanged(ha_refs, *proofs[0])
            unchanged(vendor_refs, *proofs[1])
            vendor.bindings()
            self.check()
            tracker.close()  # Quiesce our job before a receipt may claim success.
            result_key = _event_id([self.root_reference[0], 'unload', original.registry_id, 'receipt'])
            receipt = {'kind': 'powercalc_group_unload_receipt', 'schema': 1,
                       'root': list(self.root_reference), 'intent': list(reference),
                       'source_chain': self.lease.state.head, 'before': before, 'after': after,
                       'entry_id': entry.entry_id, 'native_state': 'not_loaded',
                       'placeholder_json': placeholder_json,
                       'placeholder_last_reported': placeholder_reported}
            receipt_ref = await self.chain.store.create(result_key, receipt)
            self.check()
            _need(self.chain.store.read(result_key) == {'digest': _digest(receipt), 'payload': receipt},
                  'unload_receipt_readback')
            _need(hass.states.get(group_id) is placeholder
                  and placeholder.as_dict_json.decode('utf-8') == placeholder_json
                  and placeholder.last_reported.isoformat() == placeholder_reported,
                  'unload_placeholder_changed')
            _need(group_id not in self._unloaded, 'duplicate_live_unload')
            self._unloaded[group_id] = (receipt_ref, self.lease.state.head, entry)
            self._unload_states[group_id] = (placeholder, placeholder_json, placeholder_reported)
            return receipt_ref
        finally:
            if not tracker.closed:
                tracker.close()
