"""Private native/YAML intent/receipt sidecar; no HA effects or grants.

Trusted callers supply durable PhaseContext, independently authenticated raw
observations and the actual synchronous NativeRegistryMutation return. These
Python types establish neither provenance, authorization nor exclusion. Keep
coordinator/editor locks and the external lease throughout every await.

One original NativeInputBaseline stays in PreparedStore. Sidecar events contain
only exact target rows, refs, hashes and identities. Full expected documents are
reconstructed from that baseline and accepted immutable receipts, NEVER current.
Reopened chains are read-only, including lost-ACK recovery. An unresolved intent
remains UNKNOWN: this module has no replay or result-only reconciliation API.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import asyncio
import hashlib
import json
import re
import stat
from typing import TYPE_CHECKING

from .native_registry import NativeRegistryMutation, PreparedRegistryChange
from .native_registry_transition import (
    NativeRegistryEffectAudit, prepared_registry_digest,
    validate_registry_transition, _loads, _json,
)
from .helper_native import NativeConfigMutation
from .helper_transaction import PreparedHelper
from .native_config_transition import (
    NativeConfigEffectAudit, prepared_helper_digest, validate_config_transition,
    _projection as _config_projection,
)
from .prepared import PreparedStore, canonical, decode_change
from .transaction import PhaseContext, Plan
from .yaml_transaction import PreparedYAML
from .accepted_input_state import AcceptedInputState, initial_sources, fold_yaml_calls

if TYPE_CHECKING:
    from .yaml_validation import ValidationReceipt

KEYS = ('core.entity_registry', 'core.device_registry', 'core.config_entries')
MAX_EVENTS = 4096  # 584 forward + reverse steps: 2337 events, including root.


class RegistryReceiptError(ValueError):
    """Sanitized refusal; never contains private document contents."""


def native_registry_receipt_store(hass, preparations):
    """Create one private native sidecar; caller owns initialization and locks."""
    from homeassistant.helpers.storage import Store
    from .persistence import VerifiedStore
    key = 'ha_entity_renamer.registry_receipts'
    writer = Store(hass, 1, key, private=True, minor_version=1, atomic_writes=True)
    return RegistryReceiptStore(VerifiedStore(hass.config.path('.storage', key), key,
        writer.async_save, hass.async_add_executor_job), preparations)


def _fail(reason):
    raise RegistryReceiptError('registry_receipt_' + reason) from None


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _digest(value):
    return canonical(value)[1]


def _event_id(value):
    # Derived PRIVATE STORAGE keys, never generated transaction operation IDs.
    return 'rr-' + _digest(value)


class RegistryReceiptStore:
    """Dedicated immutable sidecar over VerifiedStore's load/save interface.

PreparedStore has a 1000-preparation retention cap, insufficient for two events
per step at 584 entities. This separate private envelope uses the same 60 MiB
canonical bound, with 4096 events. Caller must supply one exclusively owned
VerifiedStore and serialize across processes; this lock is process-local only.
Any uncertain save permanently poisons this instance AND its PreparedStore.
"""

    def __init__(self, verified_store, preparations: PreparedStore):
        self.store = verified_store
        self.preparations = preparations
        self.rows = None
        self.poisoned = False
        self.lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self.lock:
            if self.rows is not None or self.poisoned:
                _fail('store_unavailable')
            raw, exists = await self.store.load()
            if not exists:
                self.rows = {}
                return
            canonical(raw)
            if (type(raw) is not dict or set(raw) != {'schema', 'events'}
                    or type(raw['schema']) is not int or raw['schema'] != 1
                    or type(raw['events']) is not dict or len(raw['events']) > MAX_EVENTS):
                _fail('store_invalid')
            for key, row in raw['events'].items():
                if (type(key) is not str or not re.fullmatch(r'rr-[0-9a-f]{64}', key)
                        or type(row) is not dict or set(row) != {'digest', 'payload'}
                        or row['digest'] != _digest(row['payload'])):
                    _fail('store_invalid')
            self.rows = deepcopy(raw['events'])

    def _ready(self):
        if self.rows is None or self.poisoned:
            _fail('store_unavailable')
        self.preparations._ready()

    def read(self, key: str) -> dict | None:
        self._ready()
        return deepcopy(self.rows.get(key))

    async def create(self, key: str, payload: dict) -> tuple[str, str]:
        if type(key) is not str or not re.fullmatch(r'rr-[0-9a-f]{64}', key):
            _fail('event_key_invalid')
        payload, digest = canonical(payload)
        async with self.lock:
            self._ready()
            if key in self.rows:
                _fail('event_exists_no_replay')
            if len(self.rows) >= MAX_EVENTS:
                _fail('event_limit')
            updated = {**self.rows, key: {'digest': digest, 'payload': payload}}
            envelope = {'schema': 1, 'events': updated}
            canonical(envelope)  # Enforce the aggregate 60 MiB limit before IO.
            try:
                await self.store.save(envelope)
            except BaseException:
                self.rows = None
                self.poisoned = True
                self.preparations.plans = None
                raise
            self.rows = updated
            return (key, digest)


@dataclass(frozen=True)
class RegistryRawObservation:
    """Caller-authenticated durable raw read, with dev/inode/full st_mode."""
    key: str
    raw: bytes
    identity: tuple[int, int, int]


@dataclass(frozen=True)
class RegistryIntent:
    """A successfully saved sidecar ref, not permission to perform a mutation."""
    reference: tuple[str, str]


class RegistryReceiptChain:
    """One live writer; reopened views only read immutable accepted evidence.

start() must run before any effect. prepared_digests is trusted backend metadata
for the ORIGINAL immutable preparations, not a client-selected resolver. All
steps are plain registry/helpers followed by at most one aggregate YAML step.
Listeners, interleaved YAML and unknown predecessors remain unsupported.
"""

    def __init__(self, store: RegistryReceiptStore, operation_id: str, plan: Plan,
                 receipt: ValidationReceipt, prepared_digests: dict[str, str], *, yaml_original_metadata=None):
        if (type(store) is not RegistryReceiptStore or type(plan) is not Plan
                or type(operation_id) is not str or not operation_id):
            _fail('binding_invalid')
        plan.validate()
        if not plan.native_inputs:
            _fail('gap_original_native_inputs')
        yaml_steps = [i for i, step in enumerate(plan.steps) if step.adapter == 'yaml']
        if yaml_steps and yaml_steps != [len(plan.steps) - 1]:
            _fail('gap_yaml_order')
        for step in plan.steps:
            if step.caused_by:
                _fail('gap_native_listeners')
            if step.adapter not in ('registry', 'helpers', 'yaml'):
                _fail({'helper': 'gap_config_entry_mapping', 'yaml': 'gap_yaml_predecessor',
                       'dashboard': 'gap_nonnative_predecessor',
                       'energy': 'gap_nonnative_predecessor'}.get(step.adapter, 'gap_unknown_predecessor'))
        self.store, self.preparations = store, store.preparations
        self.operation_id, self.plan = operation_id, plan
        self.baseline = self.preparations.resolve_native_inputs(plan.native_inputs, receipt)
        self.version = receipt.version
        self.root_id = _event_id(['root', operation_id, plan.fingerprint])
        self.original_receipt = receipt
        self.yaml_index = yaml_steps[0] if yaml_steps else None
        self.changes, bindings = [], []
        self._helper_origins = {}  # Immutable target-row bytes, never current IO.
        original_config = None
        if (type(prepared_digests) is not dict
                or set(prepared_digests) != {s.prepared_ref for s in plan.steps}):
            _fail('preparation_binding_invalid')
        for step in plan.steps:
            digest = prepared_digests[step.prepared_ref]
            original_payload = self.preparations.resolve(step.prepared_ref, digest)
            if step.adapter in ('helpers', 'yaml') and _digest(original_payload) != digest:
                _fail('preparation_binding_invalid')
            change = decode_change(original_payload)
            expected_type = {'helpers': PreparedHelper, 'yaml': PreparedYAML,
                             'registry': PreparedRegistryChange}[step.adapter]
            if type(change) is not expected_type:
                _fail('preparation_mismatch')
            if (change.identity != step.identity
                    or change.before_revision != step.before_revision
                    or change.after_revision != step.after_revision):
                _fail('preparation_mismatch')
            if step.adapter == 'registry':
                if type(change) is not PreparedRegistryChange or change.kind not in ('entity', 'device'):
                    _fail('preparation_mismatch')
                binding = {'ref': step.prepared_ref, 'digest': digest,
                           'registry_digest': prepared_registry_digest(change)}
            elif step.adapter == 'yaml':
                change.checked()
                if change.receipt != receipt:
                    _fail('yaml_original_receipt_mismatch')
                binding = {'ref': step.prepared_ref, 'digest': digest,
                           'yaml_digest': _digest(json.loads(_json(asdict(change))))}
            else:
                if type(change) is not PreparedHelper:
                    _fail('preparation_mismatch')
                entry_id = change.before['identity']['entry_id']
                if original_config is None:
                    original_config = _loads(next(item.raw for item in self.baseline.files
                        if item.key == 'core.config_entries'))
                rows, index = self._row(original_config, 'config', entry_id)
                if _json(_config_projection(rows[index])) != _json(change.before):
                    _fail('preparation_source_mismatch')
                self._helper_origins[len(self.changes)] = _json(rows[index])
                binding = {'ref': step.prepared_ref, 'digest': digest,
                           'helper_digest': prepared_helper_digest(change),
                           'entry_id': entry_id, 'baseline_row_digest': _sha(_json(rows[index]))}
            self.changes.append(change)
            bindings.append(binding)
        self.root = {'kind': 'registry_chain', 'schema': 1,
                     'operation_id': operation_id, 'fingerprint': plan.fingerprint,
                     'native_inputs': list(plan.native_inputs), 'bindings': bindings}
        if self.yaml_index is not None:
            # start requires original-fenced metadata; reopen reads its immutable root.
            if yaml_original_metadata is None:
                saved = store.read(self.root_id)
                if saved is None:
                    _fail('yaml_original_metadata_required')
                yaml_original_metadata = saved['payload'].get('yaml_original_metadata')
            from .accepted_input_state import validate_yaml_metadata
            metadata = validate_yaml_metadata(receipt, self.changes[self.yaml_index],
                                              yaml_original_metadata)
            self.root.update(schema=2, original_receipt_sha256=_sha(self.baseline.receipt_json),
                             yaml_original_metadata=metadata)
        self.root_digest = _digest(self.root)
        self.writable = False
        self.active = None
        self._cache = None  # Derived only from acknowledged immutable durable refs.

    @classmethod
    async def start(cls, store: RegistryReceiptStore, operation_id: str, plan: Plan,
                    receipt: ValidationReceipt, prepared_digests: dict[str, str], *,
                    yaml_original_metadata=None) -> RegistryReceiptChain:
        yaml_indices = [i for i, step in enumerate(plan.steps) if step.adapter == 'yaml']
        if yaml_indices and yaml_indices != [len(plan.steps) - 1]:
            _fail('gap_yaml_order')
        yaml_steps = [plan.steps[i] for i in yaml_indices]
        if yaml_steps:
            from .accepted_input_state import capture_yaml_original_metadata
            step = yaml_steps[-1]
            change = decode_change(store.preparations.resolve(
                step.prepared_ref, prepared_digests[step.prepared_ref]))
            if type(change) is not PreparedYAML:
                _fail('preparation_mismatch')
            captured = capture_yaml_original_metadata(receipt, change)
            if yaml_original_metadata is not None and canonical(yaml_original_metadata)[0] != captured:
                _fail('yaml_original_metadata_mismatch')
            yaml_original_metadata = captured
        obj = cls(store, operation_id, plan, receipt, prepared_digests,
                  yaml_original_metadata=yaml_original_metadata)
        obj._initial()  # Refuse absence before creating any sidecar state.
        await store.create(obj.root_id, obj.root)
        obj.writable = True
        return obj

    @classmethod
    def reopen(cls, store: RegistryReceiptStore, operation_id: str, plan: Plan,
               receipt: ValidationReceipt, prepared_digests: dict[str, str]) -> RegistryReceiptChain:
        obj = cls(store, operation_id, plan, receipt, prepared_digests)
        row = store.read(obj.root_id)
        if row != {'digest': obj.root_digest, 'payload': obj.root}:
            _fail('root_binding_mismatch')
        obj._reconstruct()  # Includes pending intent detection and graph checks.
        return obj

    def _context(self, context):
        if (type(context) is not PhaseContext
                or context.record.operation_id != self.operation_id
                or context.record.plan != self.plan):
            _fail('phase_binding_mismatch')
        direction = context.record.direction
        context.require_effect(context.step, direction)
        if context.record.status != ('applying' if direction == 'forward' else 'rolling_back'):
            _fail('phase_status_mismatch')
        binding = self.root['bindings'][context.index]
        result = {'index': context.index, 'key': context.step.key,
                  'identity': context.step.identity, 'prepared_ref': context.step.prepared_ref,
                  'prepared_digest': binding['digest'],
                  'direction': direction, 'phases': list(context.record.phases),
                  'status': context.record.status}
        name = {'helpers': 'helper_digest', 'yaml': 'yaml_digest',
                'registry': 'registry_digest'}[context.step.adapter]
        result[name] = binding[name]
        return result

    def _original_change(self, index):
        """Re-resolve immutable original; never validate mutable cached helpers."""
        if self.plan.steps[index].adapter == 'yaml':
            binding = self.root['bindings'][index]
            payload = self.preparations.resolve(binding['ref'], binding['digest'])
            change = decode_change(payload)
            if (_digest(payload) != binding['digest'] or type(change) is not PreparedYAML
                    or _digest(json.loads(_json(asdict(change.checked())))) != binding['yaml_digest']
                    or change != self.changes[index]):
                _fail('preparation_mismatch')
            return change
        if self.plan.steps[index].adapter != 'helpers':
            binding = self.root['bindings'][index]
            payload = self.preparations.resolve(binding['ref'], binding['digest'])
            change = decode_change(payload)
            if (_digest(payload) != binding['digest']
                    or type(change) is not PreparedRegistryChange
                    or prepared_registry_digest(change) != binding['registry_digest']
                    or change != self.changes[index]):
                _fail('preparation_mismatch')
            return change
        binding = self.root['bindings'][index]
        payload = self.preparations.resolve(binding['ref'], binding['digest'])
        if _digest(payload) != binding['digest']:
            _fail('preparation_binding_invalid')
        change = decode_change(payload)
        step = self.plan.steps[index]
        if (type(change) is not PreparedHelper
                or prepared_helper_digest(change) != binding['helper_digest']
                or change.before['identity']['entry_id'] != binding['entry_id']
                or (change.identity, change.before_revision, change.after_revision)
                   != (step.identity, step.before_revision, step.after_revision)):
            _fail('preparation_mismatch')
        # Revalidate the original target, not every multi-MiB baseline document
        # once per helper per source check. No source is derived from current.
        origin = self._helper_origins[index]
        if (_sha(origin) != binding['baseline_row_digest']
                or _json(_config_projection(_loads(origin))) != _json(change.before)):
            _fail('preparation_source_mismatch')
        return change

    def _slot(self, kind, index, direction):
        return _event_id([self.root_id, kind, index, direction])

    def _initial(self):
        states = {}
        for item in self.baseline.files:
            if item.raw is None:
                _fail('gap_absent_native_file')
            states[item.key] = {'document': _loads(item.raw), 'sha256': item.sha256,
                                'identity': list(item.identity)}
        return states

    @staticmethod
    def _row(document, kind, identity):
        plural = {'entity': 'entities', 'device': 'devices', 'config': 'entries'}[kind]
        identity_key = 'entry_id' if kind == 'config' else 'id'
        rows = document['data'][plural]
        indices = [i for i, row in enumerate(rows) if row.get(identity_key) == identity]
        if len(indices) != 1:
            _fail('identity_mismatch')
        return rows, indices[0]

    def _summary(self, states):
        return {key: {'sha256': state['sha256'], 'identity': state['identity'],
                      'document_sha256': _sha(_json(state['document']))}
                for key, state in states.items()}

    def _advance(self, states, payload, change):
        """Replay an already accepted immutable delta, never a fresh image."""
        if type(change) is PreparedHelper:
            return self._advance_config(states, payload, change)
        key = 'core.' + change.kind + '_registry'
        result = deepcopy(states)
        rows, index = self._row(result[key]['document'], change.kind, change.identity)
        if payload['source_index'] != index or type(payload['source_index']) is not int:
            _fail('native_order_mismatch')
        stamp = payload['mutation']
        if (stamp['kind'], stamp['identity']) != (change.kind, change.identity):
            _fail('mutation_binding_mismatch')
        before, after = _loads(stamp['before_json'].encode()), _loads(stamp['after_json'].encode())
        if _json(rows[index]) != _json(before) or after.get('id') != change.identity:
            _fail('mutation_source_mismatch')
        rows[index] = after
        if change.kind == 'entity' and before['entity_id'] != after['entity_id']:
            rows.append(rows.pop(index))
        actual_index = next(i for i, row in enumerate(rows) if row.get('id') == change.identity)
        if payload['result_index'] != actual_index or type(payload['result_index']) is not int:
            _fail('native_order_mismatch')
        observed = payload['result']
        audit = payload['audit']
        binding = payload['binding']
        if audit != asdict(NativeRegistryEffectAudit(
                change.kind, change.identity,
                'forward' if binding['direction'] == 'forward' else 'reverse',
                states[key]['sha256'], observed[key]['sha256'],
                prepared_registry_digest(change), before.get('modified_at'), after.get('modified_at'))):
            _fail('audit_binding_mismatch')
        # Reopening must enforce the same semantic preparation boundary as the
        # live writer. Stored hashes alone cannot replace that check. These
        # canonical bytes validate reconstructed content only; actual physical
        # raw hashes above remain independently bound and checked by _observe.
        source_content = _json(states[key]['document'])
        result_content = _json(result[key]['document'])
        content_audit = NativeRegistryEffectAudit(change.kind, change.identity,
            'forward' if binding['direction'] == 'forward' else 'reverse',
            _sha(source_content), _sha(result_content), prepared_registry_digest(change),
            before.get('modified_at'), after.get('modified_at'))
        validate_registry_transition(source_content, result_content, change,
            version=self.version, direction=content_audit.direction,
            native_effect_audit=content_audit)
        for item_key in KEYS:
            identity = observed[item_key]['identity']
            if (type(identity) is not list or len(identity) != 3
                    or any(type(value) is not int or value < 0 for value in identity)
                    or identity[1] == 0 or not stat.S_ISREG(identity[2])
                    or identity[0] != states[item_key]['identity'][0]
                    or identity[2] != states[item_key]['identity'][2]
                    or type(observed[item_key]['sha256']) is not str
                    or not re.fullmatch(r'[0-9a-f]{64}', observed[item_key]['sha256'])):
                _fail('file_identity_invalid')
            if item_key != key and observed[item_key] != self._summary(states)[item_key]:
                _fail('foreign_native_delta')
            result[item_key]['sha256'] = observed[item_key]['sha256']
            result[item_key]['identity'] = observed[item_key]['identity']
        if self._summary(result) != observed:
            _fail('reconstruction_mismatch')
        return result

    def _advance_config(self, states, payload, change):
        """Replay exact accepted full target rows; retain entries list order."""
        key = 'core.config_entries'
        entry_id = change.before['identity']['entry_id']
        result = deepcopy(states)
        rows, index = self._row(result[key]['document'], 'config', entry_id)
        if (type(payload['source_index']) is not int or payload['source_index'] != index
                or type(payload['result_index']) is not int or payload['result_index'] != index):
            _fail('native_order_mismatch')
        mutation = NativeConfigMutation(**payload['mutation'])
        audit = NativeConfigEffectAudit(**payload['audit'])
        rows[index] = _loads(mutation.after_json.encode())
        direction = 'forward' if payload['binding']['direction'] == 'forward' else 'reverse'
        if (audit.source_sha256 != states[key]['sha256']
                or audit.result_sha256 != payload['result'][key]['sha256']):
            _fail('audit_binding_mismatch')
        source_raw, result_raw = _json(states[key]['document']), _json(result[key]['document'])
        content_audit = NativeConfigEffectAudit(audit.entry_id, audit.direction,
            _sha(source_raw), _sha(result_raw), audit.prepared_sha256,
            audit.modified_at_before, audit.modified_at_after)
        validate_config_transition(source_raw, result_raw, change, version=self.version,
            direction=direction, native_effect_audit=content_audit, native_mutation=mutation)
        observed = payload['result']
        for item_key in KEYS:
            identity = observed[item_key]['identity']
            if (type(identity) is not list or len(identity) != 3
                    or any(type(value) is not int or value < 0 for value in identity)
                    or identity[1] == 0 or not stat.S_ISREG(identity[2])
                    or identity[0] != states[item_key]['identity'][0]
                    or identity[2] != states[item_key]['identity'][2]
                    or type(observed[item_key]['sha256']) is not str
                    or not re.fullmatch(r'[0-9a-f]{64}', observed[item_key]['sha256'])):
                _fail('file_identity_invalid')
            if item_key != key and observed[item_key] != self._summary(states)[item_key]:
                _fail('foreign_native_delta')
            result[item_key]['sha256'] = observed[item_key]['sha256']
            result[item_key]['identity'] = identity
        if self._summary(result) != observed:
            _fail('reconstruction_mismatch')
        return result

    def _cache_key(self):
        self.store._ready()
        return tuple((key, row['digest'], _digest(row['payload']))
                     for key, row in self.store.rows.items())

    def _remember(self, result):
        self._cache = (self._cache_key(), result)
        return result

    def _reconstruct(self):
        if self.store.read(self.root_id) != {'digest': self.root_digest, 'payload': self.root}:
            _fail('root_binding_mismatch')
        # Even a cached reconstruction must resolve the ORIGINAL preparations.
        for index in range(len(self.plan.steps)):
            self._original_change(index)
        for row in self.store.rows.values():
            if row['digest'] != _digest(row['payload']):
                _fail('event_digest_mismatch')
        token = self._cache_key()
        if self._cache is not None and self._cache[0] == token:
            return self._cache[1]
        states, completed = self._initial(), {}
        yaml_sources = self._initial_sources() if self.yaml_index is not None else None
        head, sequence, pending = self.root_digest, 0, None
        events = []
        self.store._ready()
        for key, row in self.store.rows.items():
            payload = row['payload']
            if type(payload) is dict and payload.get('chain') == self.root_id:
                events.append((key, row))
        while events:
            intents = [(key, row) for key, row in events
                       if row['payload'].get('kind') in ('registry_intent', 'yaml_intent')
                       and row['payload'].get('sequence') == sequence]
            if len(intents) != 1:
                _fail('chain_graph_invalid')
            ikey, irow = intents[0]
            intent = irow['payload']
            binding = intent['binding']
            index, direction = binding['index'], binding['direction']
            if (type(index) is not int or not 0 <= index < len(self.plan.steps)
                    or direction not in ('forward', 'rollback')
                    or intent['source_chain'] != head or intent['source'] != self._summary(states)
                    or ikey != self._slot('intent', index, direction)):
                _fail('intent_binding_mismatch')
            yaml = self.plan.steps[index].adapter == 'yaml'
            if (intent['kind'] != ('yaml_intent' if yaml else 'registry_intent')
                    or yaml and intent.get('input_source') != yaml_sources):
                _fail('intent_binding_mismatch')
            context = self._historical_context(binding)
            if self._context(context) != binding:
                _fail('intent_binding_mismatch')
            self._phase_state(context, completed)
            events.remove((ikey, irow))
            rkey = self._slot('receipt', index, direction)
            matches = [(key, row) for key, row in events if key == rkey]
            if not matches:
                pending = (ikey, irow['digest'])
                if events:
                    _fail('unresolved_chain_fork')
                break
            if len(matches) != 1:
                _fail('chain_graph_invalid')
            _, rrow = matches[0]
            receipt = rrow['payload']
            if (receipt.get('kind') != ('yaml_receipt' if yaml else 'registry_receipt')
                    or receipt.get('intent') != [ikey, irow['digest']]
                    or receipt.get('binding') != binding
                    or receipt.get('sequence') != sequence
                    or receipt.get('source_chain') != head):
                _fail('receipt_binding_mismatch')
            if yaml:
                if receipt.get('result') != self._summary(states):
                    _fail('foreign_native_delta')
                yaml_sources = fold_yaml_calls(self._original_change(index), yaml_sources,
                    receipt['calls'], direction, receipt.get('call_operation_ids'))
                if receipt.get('input_result') != yaml_sources:
                    _fail('yaml_reconstruction_mismatch')
            else:
                states = self._advance(states, receipt, self._original_change(index))
                if yaml_sources is not None:
                    for native_key, native_value in states.items():
                        yaml_sources['.storage/' + native_key][:4] = [
                            native_value['sha256'], *native_value['identity']]
            completed[index] = direction
            events.remove((rkey, rrow))
            head, sequence = rrow['digest'], sequence + 1
        return self._remember((states, head, sequence, completed, pending))

    def _historical_context(self, binding):
        from .transaction import Record
        return PhaseContext(Record(self.operation_id, self.plan, binding['status'],
                                   tuple(binding['phases']), direction=binding['direction']),
                            binding['index'])

    def _phase_state(self, context, completed):
        forward = context.record.direction == 'forward'
        for index, phase in enumerate(context.record.phases):
            if index == context.index:
                expected = None if forward else 'forward'
                if completed.get(index) != expected:
                    _fail('phase_receipt_mismatch')
                continue
            expected = {'verified': 'forward', 'undone': 'rollback', 'pending': None}.get(phase, 'invalid')
            if expected == 'invalid' or completed.get(index) != expected:
                _fail('phase_receipt_mismatch')
            if forward and phase != ('verified' if index < context.index else 'pending'):
                _fail('phase_order_mismatch')
            if not forward and index > context.index and phase not in ('pending', 'undone'):
                _fail('phase_order_mismatch')

    def _observe(self, observations, states):
        if (type(observations) is not tuple or len(observations) != 3
                or any(type(obs) is not RegistryRawObservation for obs in observations)
                or tuple(obs.key for obs in observations) != KEYS):
            _fail('observations_invalid')
        for obs in observations:
            identity = obs.identity
            if (type(identity) is not tuple or len(identity) != 3
                    or any(type(value) is not int or value < 0 for value in identity)
                    or identity[1] == 0 or not stat.S_ISREG(identity[2])):
                _fail('file_identity_invalid')
            expected = states[obs.key]
            document = _loads(obs.raw)
            if (list(identity) != expected['identity'] or _sha(obs.raw) != expected['sha256']
                    or _json(document) != _json(expected['document'])):
                _fail('source_observation_mismatch')

    def expected(self, context: PhaseContext,
                 observations: tuple[RegistryRawObservation, ...]) -> str:
        """Read-only full-image check; never releases YAML fences or closes AC."""
        if (type(context) is not PhaseContext or context.record.operation_id != self.operation_id
                or context.record.plan != self.plan):
            _fail('phase_binding_mismatch')
        states, head, _, completed, pending = self._reconstruct()
        if pending is not None:
            _fail('unknown_unresolved_intent')
        for index, phase in enumerate(context.record.phases):
            expected = {'verified': 'forward', 'undone': 'rollback', 'pending': None}.get(phase, 'invalid')
            if expected == 'invalid' or completed.get(index) != expected:
                _fail('phase_receipt_mismatch')
        self._observe(observations, states)
        return head  # Evidence chain digest only; not authorization.

    def verify_initial(self, observations):
        """Read-only preflight before Coordinator creates its journal record."""
        states, head, sequence, completed, pending = self._reconstruct()
        if sequence or completed or pending is not None:
            _fail('initial_chain_not_pristine')
        self._observe(observations, states)
        return head

    def accepted_documents(self):
        """Detached runtime expectations from ORIGINAL + accepted deltas only.

        Pending intent adds no delta. These images are not live observations,
        effect authorization or a replacement for any physical source fence.
        """
        states, _, _, _, _ = self._reconstruct()
        return {key: deepcopy(value['document']) for key, value in states.items()}

    def verify_intent_source(self, intent, context, observations):
        """Final full source fence after intent ACK, immediately before API."""
        if not self.writable or intent is not self.active:
            _fail('intent_not_live')
        binding = self._context(context)
        states, _, _, _, pending = self._reconstruct()
        row = self.store.read(intent.reference[0])
        if (pending != intent.reference or row['digest'] != intent.reference[1]
                or row['payload']['binding'] != binding):
            _fail('intent_binding_mismatch')
        self._observe(observations, states)

    def position(self, context, observations):
        """Read-only phase proof, including receipt ACK before journal ACK.

        A confirmed receipt may precede verified/undone in the journal. This
        permits reconciliation of that exact effect, never a new writer/replay.
        An unresolved sidecar intent still refuses even if raw looks correct.
        """
        if (type(context) is not PhaseContext
                or context.record.operation_id != self.operation_id
                or context.record.plan != self.plan):
            _fail('phase_binding_mismatch')
        states, _, _, completed, pending = self._reconstruct()
        if pending is not None:
            _fail('unknown_unresolved_intent')
        unresolved = 0
        for index, phase in enumerate(context.record.phases):
            actual = completed.get(index)
            if phase == 'intent' and context.record.direction == 'forward':
                allowed = (None, 'forward')
                unresolved += 1
            elif phase == 'undo_intent' and context.record.direction == 'rollback':
                allowed = ('forward', 'rollback')
                unresolved += 1
            else:
                expected = {'verified': 'forward', 'undone': 'rollback', 'pending': None}
                if phase not in expected:
                    _fail('phase_receipt_mismatch')
                allowed = (expected[phase],)
            if actual not in allowed:
                _fail('phase_receipt_mismatch')
        if unresolved > 1:
            _fail('phase_receipt_mismatch')
        self._observe(observations, states)
        return 'after' if completed.get(context.index) == 'forward' else 'before'

    async def begin(self, context: PhaseContext,
                    observations: tuple[RegistryRawObservation, ...]) -> RegistryIntent:
        if not self.writable:
            _fail('reopen_read_only')
        binding = self._context(context)
        if context.step.adapter == 'yaml':
            _fail('yaml_requires_aggregate_api')
        states, head, sequence, completed, pending = self._reconstruct()
        if pending is not None or self.active is not None:
            _fail('unknown_unresolved_intent')
        self._phase_state(context, completed)
        self._observe(observations, states)
        payload = {'kind': 'registry_intent', 'chain': self.root_id,
                   'binding': binding, 'source_chain': head, 'sequence': sequence,
                   'source': self._summary(states)}
        ref = await self.store.create(self._slot('intent', context.index,
                                                context.record.direction), payload)
        self.active = RegistryIntent(ref)
        self._remember((states, head, sequence, completed, ref))
        return self.active

    async def complete(self, intent: RegistryIntent, context: PhaseContext,
                       before: tuple[RegistryRawObservation, ...],
                       after: tuple[RegistryRawObservation, ...], *,
                       mutation: NativeRegistryMutation | NativeConfigMutation,
                       audit: NativeRegistryEffectAudit | NativeConfigEffectAudit) -> tuple[str, str]:
        """Persist receipt only from actual call rows + full independent reads.

        No effect is performed here. Caller performs fresh auth/lock checks after
        begin's await, and must persist verified/undone only after this succeeds.
        A readback lacking the original mutation stamp cannot complete an intent.
        """
        if not self.writable or type(intent) is not RegistryIntent or intent is not self.active:
            _fail('intent_not_live')
        binding = self._context(context)
        if context.step.adapter == 'yaml':
            _fail('yaml_requires_aggregate_api')
        states, head, sequence, completed, pending = self._reconstruct()
        row = self.store.read(intent.reference[0])
        if (pending != intent.reference or row['digest'] != intent.reference[1]
                or row['payload']['binding'] != binding):
            _fail('intent_binding_mismatch')
        self._observe(before, states)
        change = self._original_change(context.index)
        config = type(change) is PreparedHelper
        kind = 'config' if config else change.kind
        identity = change.before['identity']['entry_id'] if config else change.identity
        key = 'core.config_entries' if config else 'core.' + change.kind + '_registry'
        if config:
            if type(mutation) is not NativeConfigMutation or type(audit) is not NativeConfigEffectAudit:
                _fail('mutation_invalid')
        elif (type(mutation) is not NativeRegistryMutation
                or (mutation.kind, mutation.identity) != (change.kind, change.identity)
                or type(audit) is not NativeRegistryEffectAudit):
            _fail('mutation_invalid')
        if (type(after) is not tuple or len(after) != 3
                or any(type(obs) is not RegistryRawObservation for obs in after)
                or tuple(obs.key for obs in after) != KEYS):
            _fail('observations_invalid')
        result_states = deepcopy(states)
        for obs in after:
            result_states[obs.key] = {'document': _loads(obs.raw), 'sha256': _sha(obs.raw),
                                      'identity': list(obs.identity)}
        self._observe(after, result_states)  # Strict identity shape, including bool rejection.
        for obs in after:
            old = states[obs.key]
            if (obs.identity[0] != old['identity'][0] or obs.identity[2] != old['identity'][2]
                    or obs.key != key and result_states[obs.key] != old):
                _fail('foreign_native_delta')
        source = next(obs.raw for obs in before if obs.key == key)
        result = next(obs.raw for obs in after if obs.key == key)
        direction = 'forward' if binding['direction'] == 'forward' else 'reverse'
        if config:
            validate_config_transition(source, result, change, version=self.version,
                direction=direction, native_effect_audit=audit, native_mutation=mutation)
        else:
            validate_registry_transition(source, result, change, version=self.version,
                                         direction=direction, native_effect_audit=audit)
        for document, stamp in ((states[key]['document'], mutation.before_json),
                                (result_states[key]['document'], mutation.after_json)):
            rows, index = self._row(document, kind, identity)
            if _json(rows[index]) != _json(_loads(stamp.encode())):
                _fail('mutation_observation_mismatch')
        payload = {'kind': 'registry_receipt', 'chain': self.root_id,
                   'binding': binding, 'source_chain': head, 'sequence': sequence,
                   'intent': list(intent.reference), 'mutation': asdict(mutation),
                   'audit': asdict(audit), 'result': self._summary(result_states)}
        payload['source_index'] = self._row(states[key]['document'], kind, identity)[1]
        payload['result_index'] = self._row(result_states[key]['document'], kind, identity)[1]
        if self._summary(self._advance(states, payload, change)) != self._summary(result_states):
            _fail('reconstruction_mismatch')
        ref = await self.store.create(self._slot('receipt', context.index,
                                                context.record.direction), payload)
        self.active = None
        self._remember((result_states, ref[1], sequence + 1,
                        {**completed, context.index: binding['direction']}, None))
        return ref

    def _initial_sources(self):
        return initial_sources(self.original_receipt,
                               self.root.get('yaml_original_metadata', {}))

    def reconstruct_inputs(self):
        """Original graph plus accepted durable deltas; pending is UNKNOWN."""
        states, head, _, _, pending = self._reconstruct()
        if pending is not None:
            _fail('unknown_unresolved_intent')
        sources = self._initial_sources()
        # Follow the same validated sequence, never dict insertion order.
        rows = sorted((row['payload'] for row in self.store.rows.values()
                       if row['payload'].get('chain') == self.root_id
                       and row['payload'].get('kind') == 'yaml_receipt'),
                      key=lambda payload: payload['sequence'])
        for payload in rows:
            sources = fold_yaml_calls(self._original_change(payload['binding']['index']),
                sources, payload['calls'], payload['binding']['direction'],
                payload['call_operation_ids'])
        for key, value in states.items():
            path = '.storage/' + key
            sources[path][:4] = [value['sha256'], *value['identity']]
        return AcceptedInputState(self.baseline.receipt_json, head, sources,
            {key: deepcopy(value['document']) for key, value in states.items()},
            evidence_check=lambda: self._check_inputs_head(head))

    def _check_inputs_head(self, expected_head):
        # Re-resolve immutable preparations even when the caller kept a state
        # object across awaits. A native call may be currently pending in this
        # SAME writer; YAML partial calls require their dedicated live hook.
        _, head, _, _, pending = self._reconstruct()
        if head != expected_head:
            _fail('input_state_stale')
        if pending is not None:
            row = self.store.read(pending[0])
            if (not self.writable or self.active is None
                    or self.active.reference != pending
                    or row['payload']['kind'] != 'registry_intent'):
                _fail('unknown_unresolved_intent')

    def live_yaml_inputs(self, intent, context, calls=()):
        """Only this live hook may fence actual partial calls; never recovery.

        Caller passes direct synchronous mutator returns, under its same locks.
        The returned state cannot be serialized/reopened as accepted evidence.
        """
        if not self.writable or intent is not self.active or context.step.adapter != 'yaml':
            _fail('intent_not_live')
        binding = self._context(context)
        states, head, _, _, pending = self._reconstruct()
        row = self.store.read(intent.reference[0])
        if (pending != intent.reference or row['digest'] != intent.reference[1]
                or row['payload']['binding'] != binding):
            _fail('intent_binding_mismatch')
        sources = deepcopy(row['payload']['input_source'])
        change = self._original_change(context.index)
        reverse = binding['direction'] == 'rollback'
        operations = self._yaml_operations(change, reverse)
        sources = fold_yaml_calls(change, sources, calls, binding['direction'],
                                  operations, partial=True)
        for key, value in states.items():
            sources['.storage/' + key][:4] = [value['sha256'], *value['identity']]
        return AcceptedInputState(self.baseline.receipt_json, head, sources,
            {key: deepcopy(value['document']) for key, value in states.items()},
            live_check=lambda: self._check_live_yaml(intent, context))

    def _check_live_yaml(self, intent, context):
        if not self.writable or intent is not self.active:
            _fail('intent_not_live')
        self._context(context)
        if self._reconstruct()[-1] != intent.reference:
            _fail('intent_binding_mismatch')

    @staticmethod
    def _yaml_operations(change, reverse):
        from .yaml_transaction import _digest as yaml_digest
        rows = tuple(reversed(change.files)) if reverse else change.files
        return ['yaml_' + yaml_digest((change.identity, row.path, reverse))[:64]
                for row in rows]

    async def begin_yaml(self, context, observations, *, input_state):
        """One YAML-last effect, caller already checked whole inputs/runtime BEFORE."""
        if not self.writable or context.step.adapter != 'yaml':
            _fail('yaml_intent_not_live')
        binding = self._context(context)
        states, head, sequence, completed, pending = self._reconstruct()
        if pending is not None or self.active is not None:
            _fail('unknown_unresolved_intent')
        self._phase_state(context, completed)
        expected = self.reconstruct_inputs()
        if type(input_state) is not AcceptedInputState or not input_state.same_evidence(expected):
            _fail('yaml_input_binding_mismatch')
        input_state.assert_current(self.original_receipt)
        self._observe(observations, states)
        payload = {'kind': 'yaml_intent', 'chain': self.root_id, 'binding': binding,
                   'source_chain': head, 'sequence': sequence, 'source': self._summary(states),
                   'input_source': expected.sources()}
        ref = await self.store.create(self._slot('intent', context.index,
                                                context.record.direction), payload)
        self.active = RegistryIntent(ref)
        return self.active

    async def complete_yaml(self, intent, context, before, after, *, calls):
        """After independent runtime verification; actual calls alone never prove runtime.

        No reload or manager mutation is allowed within this effect. Caller
        supplies full independently authenticated manager/disk observations.
        """
        live = self.live_yaml_inputs(intent, context, calls)
        states, head, sequence, completed, pending = self._reconstruct()
        self._observe(before, states)
        self._observe(after, states)  # Any reload native effect is unsupported.
        binding = self._context(context)
        change = self._original_change(context.index)
        operations = self._yaml_operations(change, binding['direction'] == 'rollback')
        source = self.store.read(intent.reference[0])['payload']['input_source']
        result = fold_yaml_calls(change, source, calls, binding['direction'], operations)
        live.assert_current(self.original_receipt)
        payload = {'kind': 'yaml_receipt', 'chain': self.root_id, 'binding': binding,
                   'source_chain': head, 'sequence': sequence, 'intent': list(intent.reference),
                   'calls': canonical(json.loads(_json(list(calls))))[0],
                   'call_operation_ids': operations,
                   'input_result': result, 'result': self._summary(states)}
        ref = await self.store.create(self._slot('receipt', context.index,
                                                context.record.direction), payload)
        self.active = None
        self._cache = None
        self._reconstruct()
        return ref
