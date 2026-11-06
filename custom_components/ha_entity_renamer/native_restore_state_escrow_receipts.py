"""Private typed dual-key RestoreState effects under ORIGINAL helper lifecycle.

No new baseline, authority, recovery replay, startup gate, cleanup or fsync claim.
Installed callers retain native/editor/admin exclusion and the activation lease.
An immutable read-only witness can authenticate history, never complete an effect.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import inspect
import re

from . import native_restore_state_payload as codec
from .native_restore_state_escrow_io import EscrowIO, Readback, captured
from .native_registry_receipts import RegistryReceiptStore, _digest, _event_id
from .native_registry_transition import prepared_registry_digest
from .native_helper_lifecycle import (NativeHelperLifecycle,
    validate_lifecycle_receipt_witness)


class RestoreEscrowError(ValueError):
    """Sanitized failure; partial effects must remain UNKNOWN, never replayed."""


def _need(condition, reason):
    if not condition:
        raise RestoreEscrowError('restore_escrow_' + reason) from None


def _reference(value):
    _need(type(value) in (tuple, list) and len(value) == 2
          and type(value[0]) is str and re.fullmatch(r'rr-[0-9a-f]{64}', value[0])
          and type(value[1]) is str and re.fullmatch(r'[0-9a-f]{64}', value[1]),
          'reference_layout')
    return list(value)


def _read(store, reference):
    key, digest = _reference(reference)
    row = store.read(key)
    _need(type(row) is dict and set(row) == {'digest', 'payload'}
          and type(row['payload']) is dict and row['digest'] == digest
          and digest == _digest(row['payload']), 'event_digest_or_reference')
    return row['payload']


def _optional_row(raw, entity_id):
    if raw is None:
        return None
    value = codec._parse(raw)
    codec._shape(value)
    _need(codec._canonical(value) == raw and value['state']['entity_id'] == entity_id,
          'preimage_endpoint')
    return value


def _row_bytes(value, entity_id):
    if value is None:
        return None
    raw = codec._canonical(value)
    _optional_row(raw, entity_id)
    return raw


def _sync(callback):
    result = callback()
    if inspect.iscoroutine(result):
        result.close()
    _need(result is None, 'synchronous_guard_required')


def _lifecycle_binding(lifecycle, entry_id):
    _need(type(entry_id) is str and bool(entry_id), 'entry_id_layout')
    _need(type(lifecycle) is NativeHelperLifecycle
          and type(lifecycle.store) is RegistryReceiptStore,
          'original_lifecycle_required')
    # Re-resolves ORIGINAL native files and ValidationReceipt, never current IO.
    baseline = lifecycle.store.preparations.resolve_native_inputs(
        lifecycle.plan.native_inputs, lifecycle.receipt)
    lifecycle.inspect()
    change = lifecycle.own_registry_changes.get(entry_id)
    _need(change is not None and entry_id in lifecycle.helper_snapshots,
          'own_registry_preparation_required')
    before, after = change.before, change.after
    _need(before['config_entry_id'] == entry_id and before['unique_id'] == entry_id
          and before['platform'] in ('utility_meter', 'integration') and before['id'] == change.identity
          and all(after.get(k) == before.get(k) for k in
                  ('id', 'unique_id', 'platform', 'config_entry_id', 'config_subentry_id')),
          'helper_uuid_binding')
    root_ref = [lifecycle.root_id, _digest(lifecycle.root)]
    _read(lifecycle.store, root_ref)
    witness = [root_ref]
    for key in lifecycle.ids:
        for kind in ('intent', 'receipt'):
            slot = lifecycle._slot('unload', key, kind)
            row = lifecycle.store.read(slot)
            _need(row is not None, 'native_unload_receipt_required')
            witness.append([slot, row['digest']])
    unload_slot = lifecycle._slot('unload', entry_id, 'receipt')
    unload = lifecycle.store.read(unload_slot)
    _need(unload is not None and unload['payload'].get('state') == 'not_loaded',
          'native_unload_receipt_required')
    return {'original': deepcopy(lifecycle.binding),
            'original_receipt_sha256': hashlib.sha256(baseline.receipt_json).hexdigest(),
            'lifecycle_root': root_ref, 'lifecycle_witness': sorted(witness),
            'unload_receipt': [unload_slot, unload['digest']],
            'entry_id': entry_id, 'helper_uuid': change.identity,
            'registry_preparation_sha256': prepared_registry_digest(change),
            'helper_snapshots': deepcopy(list(lifecycle.helper_snapshots[entry_id]))}


def _slot(root_id, kind):
    return _event_id([root_id, 'restore_state_escrow', kind])


def _root_id(binding, direction):
    return _event_id(['restore_state_escrow', binding['lifecycle_root'],
                      binding['entry_id'], direction])


def _parse_root(store, context, root_ref, *, depth=0):
    """Strict offline graph validator; callbacks of readonly lifecycle never run."""
    _need(depth <= 1, 'forward_witness_recursion')
    root = _read(store, root_ref)
    _need(set(root) == {'kind', 'schema', 'binding', 'direction', 'payload',
                       'target_preimage', 'disk_preimage', 'forward_receipt'}
          and root['kind'] == 'restore_state_escrow_root'
          and type(root['schema']) is int and root['schema'] == 1,
          'root_layout')
    binding = root['binding']
    _need(type(binding) is dict and set(binding) == {'original',
          'original_receipt_sha256', 'lifecycle_root', 'lifecycle_witness',
          'unload_receipt', 'entry_id', 'helper_uuid',
          'registry_preparation_sha256', 'helper_snapshots'}, 'binding_layout')
    operation, plan, receipt, digests = context
    # Only ORIGINAL caller metadata is trusted. Everything from disk is rebound.
    life = validate_lifecycle_receipt_witness(store, operation, plan, receipt,
        digests, root_id=_reference(binding['lifecycle_root'])[0])
    expected_binding = _lifecycle_binding(life, binding['entry_id'])
    _need(binding == expected_binding, 'original_binding_mismatch')
    for ref in binding['lifecycle_witness']:
        _read(store, ref)
    _need(root_ref[0] == _root_id(binding, root['direction']), 'root_key')
    direction = 'rollback' if life.source_authorization is not None else 'forward'
    _need(root['direction'] == direction, 'direction_binding')
    payload = codec.deserialize(codec._canonical(root['payload']))
    _need(payload.core_version == receipt.version, 'native_version')
    change = life.own_registry_changes[binding['entry_id']]
    source, target = (change.before['entity_id'], change.after['entity_id'])
    if direction == 'rollback':
        source, target = target, source
    _need(codec._parse(payload.before_json)['state']['entity_id'] == source
          and codec._parse(payload.after_json)['state']['entity_id'] == target,
          'payload_endpoints')
    if change.before['platform'] == 'integration':
        _need(type(payload) is codec.PreparedIntegrationPayload, 'integration_payload_required')
        from .native_integration_unload_audit import (decode_integration_own_unload,
                                                    integration_own_unload_doc)
        unload = _read(store, binding['unload_receipt'])
        doc = integration_own_unload_doc(decode_integration_own_unload(unload['native_unload']))
        _need(codec._canonical(doc['capture']) == payload.capture_json
              and doc['capture']['entry_id'] == binding['entry_id']
              and doc['capture']['helper_uuid'] == binding['helper_uuid'],
              'integration_unload_capture_binding')
    else:
        _need(type(payload) is codec.PreparedPayload, 'utility_payload_required')
    expected_target = _row_bytes(root['target_preimage'], target)
    disk = root['disk_preimage']
    _need(type(disk) is dict and set(disk) == {source, target}, 'disk_preimage_layout')
    _row_bytes(disk[source], source)
    _need(_row_bytes(disk[target], target) == expected_target, 'disk_target_preimage')
    if direction == 'forward':
        _need(expected_target is None and root['forward_receipt'] is None,
              'forward_target_must_be_absent')
    else:
        # Even ABSENT reverse targets require this operation's complete forward
        # receipt, preserving original root/preparations rather than adopting bytes.
        ref = _reference(root['forward_receipt'])
        forward_event = _read(store, ref)
        _need(set(forward_event) == {'kind', 'root', 'intent', 'previous',
              'payload', 'readback'} and forward_event['kind'] == 'restore_state_escrow_receipt',
              'forward_receipt_layout')
        prior_root_ref = _reference(forward_event['root'])
        prior = _parse_root(store, context, prior_root_ref, depth=depth + 1)
        prior_status = _status(store, context, prior_root_ref, prior, depth=depth + 1)
        _need(prior_status['status'] == 'verified' and prior_status['receipt'] == ref
              and prior[0]['direction'] == 'forward', 'forward_receipt_required')
        prior_binding, old = prior[0]['binding'], prior[1]
        for field in ('original', 'original_receipt_sha256', 'entry_id', 'helper_uuid',
                      'registry_preparation_sha256', 'helper_snapshots'):
            _need(binding[field] == prior_binding[field] if field != 'original' else
                  {k: v for k, v in binding[field].items() if k != 'source_authorization'}
                    == prior_binding[field], 'forward_operation_binding')
        _need(expected_target is None or expected_target == old.before_json,
              'retained_target_not_owned')
        # payload.before is fresh B1 from the reverse native unload, never A0/S0.
    return root, payload, life


def _status(store, context, root_ref, parsed, *, depth=0):
    root, payload, life = parsed
    root_key = _reference(root_ref)[0]
    intent_key, receipt_key = _slot(root_key, 'intent'), _slot(root_key, 'receipt')
    intent_row, receipt_row = store.read(intent_key), store.read(receipt_key)
    expected_intent = {'kind': 'restore_state_escrow_intent', 'root': list(root_ref),
                       'previous': root['binding']['unload_receipt']}
    if intent_row is None:
        _need(receipt_row is None, 'receipt_without_intent')
        return {'status': 'unknown', 'pending': 'intent_ack_unavailable',
                'read_only': True, 'receipt': None}
    intent_ref = [intent_key, intent_row['digest']]
    _need(_read(store, intent_ref) == expected_intent, 'intent_binding')
    if receipt_row is None:
        return {'status': 'unknown', 'pending': 'effect_or_receipt_ack_unavailable',
                'read_only': True, 'receipt': None}
    receipt_ref = [receipt_key, receipt_row['digest']]
    expected_receipt = {'kind': 'restore_state_escrow_receipt', 'root': list(root_ref),
        'intent': intent_ref, 'previous': root['binding']['unload_receipt'],
        'payload': root['payload'],
        'readback': {'source': codec._parse(payload.before_json),
                     'target': codec._parse(payload.after_json),
                     'source_sha256': payload.before_digest,
                     'target_sha256': payload.after_digest}}
    _need(_read(store, receipt_ref) == expected_receipt, 'receipt_binding')
    return {'status': 'verified', 'pending': None, 'read_only': True,
            'receipt': receipt_ref}


def inspect_escrow(store, operation_id, plan, receipt, prepared_digests, root_reference):
    """History only, including ACK-loss reread. NEVER returns write authority."""
    _need(type(store) is RegistryReceiptStore, 'existing_receipt_store_required')
    ref = _reference(root_reference)
    context = operation_id, plan, receipt, prepared_digests
    parsed = _parse_root(store, context, ref)
    return _status(store, context, ref, parsed)


async def copy_escrow_witness(source, destination, operation_id, plan, receipt,
                              prepared_digests, root_reference):
    """Explicit immutable witness copy, including lifecycle root and original refs.

    Both stores must already resolve the ORIGINAL preparations/baseline. This
    performs no native write and cannot make either reopened object writable.
    A failed copy poisons its destination through RegistryReceiptStore itself.
    """
    _need(type(source) is RegistryReceiptStore and type(destination) is RegistryReceiptStore,
          'existing_receipt_store_required')
    context = operation_id, plan, receipt, prepared_digests
    keys = set()
    def collect(ref):
        parsed = _parse_root(source, context, ref)
        root, _, _ = parsed
        status = _status(source, context, ref, parsed)
        _need(status['status'] == 'verified', 'complete_forward_witness_required')
        keys.add(ref[0])
        keys.update(item[0] for item in root['binding']['lifecycle_witness'])
        keys.update((_slot(ref[0], 'intent'), _slot(ref[0], 'receipt')))
        if root['forward_receipt'] is not None:
            collect(_read(source, root['forward_receipt'])['root'])
    collect(_reference(root_reference))
    for key in sorted(keys):
        row = source.read(key)
        _read(source, [key, row['digest']])
        existing = destination.read(key)
        if existing is None:
            await destination.create(key, row['payload'])
            existing = destination.read(key)
        _need(existing == row, 'witness_conflict')
    inspect_escrow(destination, operation_id, plan, receipt, prepared_digests, root_reference)


@dataclass(frozen=True)
class RestoreEscrowIntent:
    """Live identity only: copied/reconstructed instances can NEVER execute."""
    root: tuple[str, str]
    reference: tuple[str, str]


class NativeRestoreStateEscrow:
    """One-shot installed effect. Hold the SAME native lease and task throughout.

    source_guard(): REQUIRED synchronous current full-three source/manager/UUID
    and placeholder provenance guard under held exclusion; raise or return None.
    lifecycle._original() additionally authenticates independent full-three IO
    before/after awaited operations against ORIGINAL or accepted reverse source.
    Call require_completed() in the real native rename guard, before EVERY
    own-registry mutation. A pending/read-only receipt does not satisfy it.
    """

    def __init__(self, lifecycle, entry_id, data, *, source_guard):
        _need(type(lifecycle) is NativeHelperLifecycle and callable(source_guard)
              and lifecycle.activation_lease is not None, 'live_native_scope_required')
        self.lifecycle, self.store = lifecycle, lifecycle.store
        self.entry_id, self.data = entry_id, data
        self.source_guard = source_guard
        self.active = self.completed = None
        self.failed = False
        self.root = self.root_reference = None
        self._started = False
        self._busy = False
        self._guard()
        self.io = EscrowIO(lifecycle.activation_lease, entry_id, data, guard=self._guard)

    def _context(self):
        life = self.lifecycle
        return life.operation_id, life.plan, life.receipt, life.binding['preparations']

    def _guard(self):
        life = self.lifecycle
        _need(not self.failed and life.writable and not life.failed,
              'failed_or_reopened_read_only')
        life.activation_lease._task_check()
        life.require_quiescent()
        self.store._ready()
        _sync(self.source_guard)
        # Fresh exact own UUID and source endpoint; a matching counter grants nothing.
        from homeassistant.helpers import entity_registry as er
        registry = life.manager.hass.data.get(er.DATA_REGISTRY)
        _need(type(registry) is er.EntityRegistry, 'native_registry_identity')
        change = life.own_registry_changes.get(self.entry_id)
        _need(change is not None, 'own_registry_preparation_required')
        before, after = change.before, change.after
        source, target = before['entity_id'], after['entity_id']
        if life.source_authorization is not None:
            source, target = target, source
        row = registry.async_get(source)
        _need(row is not None and row.id == change.identity
              and row.config_entry_id == self.entry_id and row.unique_id == self.entry_id
              and row.platform == before['platform'] and row.config_subentry_id is None
              and registry.async_get(target) is None, 'current_helper_uuid_or_target')

    async def _fresh(self):
        self._guard()
        await self.lifecycle._original()
        self._guard()

    def _poison(self):
        self.failed = True
        self.lifecycle.failed = True

    async def begin(self, *, disk_preimage, forward_receipt=None):
        """Capture actual post-unload StoredState; persist root then intent.

        disk_preimage is the caller's explicit independent raw projection of BOTH
        keys. EscrowIO rereads it under native Store._write_lock before any save
        and again before the effect. A stale disk source may differ from memory.
        """
        _need(not self._started and not self._busy, 'one_shot_no_replay')
        self._started = True
        self._busy = True
        try:
            await self._fresh()
            binding = _lifecycle_binding(self.lifecycle, self.entry_id)
            direction = 'rollback' if self.lifecycle.source_authorization is not None else 'forward'
            change = self.lifecycle.own_registry_changes[self.entry_id]
            source, target = change.before['entity_id'], change.after['entity_id']
            if direction == 'rollback':
                source, target = target, source
            source_object = self.data.last_states.get(source)
            _need(source_object is not None, 'post_unload_source_required')
            if change.before['platform'] == 'integration':
                _, witness = self.lifecycle._unload_witnesses[self.entry_id]
                payload = codec.prepare_integration_payload(source_object, target, witness=witness)
                self._integration_witness = witness
            else:
                payload = codec.prepare_payload(source_object, target)
            expected_target = (None if target not in self.data.last_states else
                               captured(self.data.last_states[target]))
            _need(type(disk_preimage) is dict and set(disk_preimage) == {source, target},
                  'explicit_disk_preimage_required')
            disk = {key: _optional_row(value, key) for key, value in disk_preimage.items()}
            root = {'kind': 'restore_state_escrow_root', 'schema': 1,
                    'binding': binding, 'direction': direction,
                    'payload': codec._parse(codec.serialize(payload)),
                    'target_preimage': _optional_row(expected_target, target),
                    'disk_preimage': disk,
                    'forward_receipt': _reference(forward_receipt) if forward_receipt is not None else None}
            key = _root_id(binding, direction)
            # Pure strict validation before native effect, including ownership of
            # a retained reverse old payload. Validate with a temporary read-only
            # projection, NEVER by inserting an unacknowledged row into live store.
            self._validate_candidate(root, key)
            await self.io._raw({source: payload.before_json, target: expected_target}, disk_preimage)
            await self._fresh()
            _need(self.data.last_states[source] is source_object, 'source_capture_identity')
            self.root_reference = await self.store.create(key, root)
            self.root = root
            await self._fresh()
            intent_payload = {'kind': 'restore_state_escrow_intent',
                'root': list(self.root_reference), 'previous': binding['unload_receipt']}
            ref = await self.store.create(_slot(key, 'intent'), intent_payload)
            await self._fresh()
            self._source_object, self._payload = source_object, payload
            self._expected_target = expected_target
            self._disk_preimage = deepcopy(disk_preimage)
            self.active = RestoreEscrowIntent(self.root_reference, ref)
            self.inspect()
            return self.active
        except BaseException:
            self._poison()
            raise
        finally:
            self._busy = False

    def _validate_candidate(self, root, key):
        # A transient readonly view shares ORIGINAL preparation resolver only.
        # It is never returned, initialized, persisted or used for live completion.
        view = RegistryReceiptStore(None, self.store.preparations)
        view.rows = deepcopy(self.store.rows)
        _need(key not in view.rows, 'one_shot_no_replay')
        view.rows[key] = {'digest': _digest(root), 'payload': deepcopy(root)}
        _parse_root(view, self._context(), [key, _digest(root)])

    def inspect(self):
        _need(self.root_reference is not None, 'root_ack_unavailable')
        return inspect_escrow(self.store, *self._context(), self.root_reference)

    async def execute(self, intent):
        """The only effect path: exact live identity, real IO, fresh source, receipt."""
        _need(not self._busy and type(intent) is RestoreEscrowIntent
              and intent is self.active and self.completed is None,
              'intent_not_live')
        self._busy = True
        try:
            await self._fresh()
            _need(self.inspect()['status'] == 'unknown'
                  and self.store.read(intent.reference[0])['digest'] == intent.reference[1],
                  'intent_binding')
            result = await self.io.escrow(self._payload,
                expected_source=self._source_object, expected_target=self._expected_target,
                disk_preimage=self._disk_preimage,
                witness=getattr(self, '_integration_witness', None))
            _need(type(result) is Readback and result.source == self._payload.before_json
                  and result.target == self._payload.after_json, 'actual_readback_required')
            await self._fresh()
            # Source fencing can await. Revalidate both native memory and actual
            # disk immediately before receipt rather than trusting stale Readback.
            before = codec._parse(self._payload.before_json)['state']['entity_id']
            after = codec._parse(self._payload.after_json)['state']['entity_id']
            post = {before: result.source, after: result.target}
            await self.io._raw(post, post)
            self._guard()
            receipt_payload = {'kind': 'restore_state_escrow_receipt',
                'root': list(self.root_reference), 'intent': list(intent.reference),
                'previous': self.root['binding']['unload_receipt'],
                'payload': self.root['payload'],
                'readback': {'source': codec._parse(result.source),
                    'target': codec._parse(result.target),
                    'source_sha256': self._payload.before_digest,
                    'target_sha256': self._payload.after_digest}}
            ref = await self.store.create(_slot(self.root_reference[0], 'receipt'), receipt_payload)
            await self._fresh()
            await self.io._raw(post, post)
            self._guard()
            _need(self.inspect()['receipt'] == list(ref), 'receipt_ack_binding')
            self.completed, self.active = intent, None
            return ref
        except BaseException:
            self._poison()
            raise
        finally:
            self._busy = False

    def require_completed(self, intent):
        """Fresh live caller gate immediately BEFORE registry rename. No IO replay."""
        _need(type(intent) is RestoreEscrowIntent and intent is self.completed
              and self.active is None and not self._busy, 'completed_live_identity_required')
        self._guard()
        _need(self.inspect()['status'] == 'verified', 'durable_receipt_required')
        before = codec._parse(self._payload.before_json)['state']['entity_id']
        after = codec._parse(self._payload.after_json)['state']['entity_id']
        self.io._boundary({before: self._payload.before_json, after: self._payload.after_json}, inactive=True)
