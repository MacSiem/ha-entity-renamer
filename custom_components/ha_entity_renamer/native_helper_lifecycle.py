"""Private, durable ordinary-helper activation lifecycle, outside Coordinator.

No endpoints, baseline refresh, retry, inverse overlay or foreign-state cleanup.
Installed trusted IO must hold the native/editor/coordinator locks and lease
through every await. A reopened lifecycle is ALWAYS read-only. An intent without
an acknowledged receipt is UNKNOWN, even when today's runtime looks correct.
Original native inputs/ValidationReceipt remain mandatory; setup after migration
uses an independently accepted native receipt chain, never original raw bytes.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import asdict, dataclass
import json
import re

from .helper_plan import NATIVE_FIELDS
from .helper_transaction import PreparedHelper
from .native_helper_transaction import _require_inactive
from .native_registry_receipts import RegistryReceiptStore
from .prepared import canonical, decode_change
from .transaction import Plan


class HelperLifecycleError(ValueError):
    """Sanitized refusal: no private values in errors."""


def _fail(reason):
    raise HelperLifecycleError('helper_lifecycle_' + reason) from None


def _digest(value):
    return canonical(value)[1]


def _id(value):
    return 'rr-' + _digest(value)


def native_helper_lifecycle_store(hass, preparations):
    """Separate private sidecar using the existing verified immutable event store."""
    from homeassistant.helpers.storage import Store
    from .persistence import VerifiedStore
    key = 'ha_entity_renamer.helper_lifecycle'
    writer = Store(hass, 1, key, private=True, minor_version=1, atomic_writes=True)
    return RegistryReceiptStore(VerifiedStore(hass.config.path('.storage', key), key,
        writer.async_save, hass.async_add_executor_job), preparations)


def stored_lifecycle_plan(store, root_id):
    """Recover the immutable plan even before the inner journal was created.

    Read-only discovery only. Caller still reopens the lifecycle and verifies
    original preparations, activation, inputs and pending outcomes before use.
    """
    from .journal import decode
    row = store.read(root_id)
    try:
        root = row['payload']
        binding = root['binding']
        operation = binding['operation_id']
        if (row['digest'] != _digest(root) or root['kind'] != 'helper_lifecycle_root'
                or type(root['schema']) is not int or root['schema'] not in (1, 2)):
            _fail('root_binding_mismatch')
        raw_plan = binding['plan']
        record = {'operation_id': operation, 'plan': raw_plan, 'status': 'prepared',
                  'phases': ['pending'] * len(raw_plan['steps']), 'direction': 'forward', 'reason': ''}
        plan = decode({'schema': 1, 'operations': {operation: record}})[operation].plan
        if (plan.fingerprint != binding['fingerprint']
                or list(plan.native_inputs) != binding['native_inputs']
                or set(binding['preparations']) != {s.prepared_ref for s in plan.steps}):
            _fail('root_binding_mismatch')
        return plan
    except (KeyError, TypeError, AttributeError):
        _fail('root_binding_mismatch')


@dataclass(frozen=True)
class RestoreAuthorization:
    """Trusted chain verifier must authenticate all fields against durable state.

    This is a binding, not a grant. Inner applied/rolled_back is explicitly NOT
    full readiness. The accepted head must include every intended migration or
    rollback receipt, no unresolved native intent, and independent full raw reads.
    """
    operation_id: str
    fingerprint: str
    head: str
    status: str


def _activation(manager, entry_id):
    entry = manager.async_get_entry(entry_id)
    try:
        state = entry.state.value
        disabled = getattr(entry.disabled_by, 'value', entry.disabled_by)
        if (state not in ('loaded', 'not_loaded')
                or disabled is not None and type(disabled) is not str
                or type(entry.pref_disable_new_entities) is not bool
                or type(entry.pref_disable_polling) is not bool
                or state == 'loaded' and disabled is not None):
            _fail('activation_unsupported')
        return {'entry_id': entry_id, 'domain': entry.domain, 'state': state,
                'disabled_by': disabled,
                'pref_disable_new_entities': entry.pref_disable_new_entities,
                'pref_disable_polling': entry.pref_disable_polling}
    except AttributeError:
        _fail('activation_unknown')


class NativeHelperLifecycle:
    """Single live writer with mandatory fresh, installed IO callbacks.

    guard(): synchronous authorization, locks and external lease; raises on loss.
    verify_original_native_inputs(native_inputs, original_receipt): async exact
      ORIGINAL full-three physical/manager/YAML fence; returns exactly True.
      Use existing original fence APIs, NEVER capture a replacement baseline.
    settle(): async native idle barrier scoped to relevant tasks/listeners. Must
      avoid global async_block_till_done while native editor locks are held.
    verify_inactive(entry_id): async independent domain/platform/consumer proof
      of no callbacks/tasks/active entities (restored placeholders permitted).
    verify_runtime(entry_id, original_activation, restore_authorization): async
      fresh domain dependencies/entities/state/activation verification against
      the accepted chain's expected documents (not adapter cached snapshots).
    verify_accepted_native_chain(restore_authorization): async independently
      authenticate chain, inner terminal journal, exact operation/plan/head and
      full native source fence. Returns exactly True; refuses pending intent.

    All callbacks are REQUIRED trusted installed IO, not client functions. False,
    None, missing fields and unsupported versions fail closed. No cached True.
    No callback can itself authorize a replay of a pending lifecycle intent.
    """

    def __init__(self, store, manager, operation_id, plan, receipt, prepared_digests,
                 *, guard, verify_original_native_inputs, settle,
                 verify_inactive, verify_runtime, verify_accepted_native_chain,
                 source_authorization=None, runtime_baselines=None):
        if (type(store) is not RegistryReceiptStore or type(plan) is not Plan
                or type(operation_id) is not str or not operation_id):
            _fail('binding_invalid')
        plan.validate()
        if not plan.native_inputs:
            _fail('original_native_inputs_required')
        # This resolves ORIGINAL immutable bytes + ORIGINAL ValidationReceipt;
        # no IO capture, second baseline or current-only preparation exists here.
        store.preparations.resolve_native_inputs(plan.native_inputs, receipt)
        callbacks = (guard, verify_original_native_inputs, settle, verify_inactive,
                     verify_runtime, verify_accepted_native_chain)
        if not all(callable(item) for item in callbacks):
            _fail('installed_io_required')
        if (type(prepared_digests) is not dict
                or set(prepared_digests) != {s.prepared_ref for s in plan.steps}):
            _fail('preparation_binding_invalid')
        ids = set()
        self.expected_metadata = {}
        self.helper_changes = {}
        for step in plan.steps:
            if step.adapter != 'helpers':
                continue
            payload = store.preparations.resolve(step.prepared_ref,
                                                  prepared_digests[step.prepared_ref])
            change = decode_change(payload)
            if (step.caused_by or type(change) is not PreparedHelper
                    or change.before['domain'] not in NATIVE_FIELDS
                    or (change.identity, change.before_revision, change.after_revision)
                       != (step.identity, step.before_revision, step.after_revision)):
                _fail('ordinary_helper_binding_required')
            key = change.before['identity']['entry_id']
            identity = change.before['identity']
            metadata = {'domain': change.before['domain'], **{field: identity[field]
                for field in ('disabled_by', 'pref_disable_new_entities', 'pref_disable_polling')}}
            if key in self.expected_metadata and self.expected_metadata[key] != metadata:
                _fail('preparation_activation_conflict')
            self.expected_metadata[key] = metadata
            if key in self.helper_changes:
                _fail('duplicate_helper_preparation')
            self.helper_changes[key] = change
            ids.add(key)
        if not ids:
            _fail('helpers_required')
        self.ids = tuple(sorted(ids))
        self.store, self.manager = store, manager
        self.operation_id, self.plan, self.receipt = operation_id, plan, receipt
        self.guard = guard
        self.verify_original_native_inputs = verify_original_native_inputs
        self.settle, self.verify_inactive = settle, verify_inactive
        self.verify_runtime = verify_runtime
        self.verify_accepted_native_chain = verify_accepted_native_chain
        self.root_id = _id(['helper_lifecycle', operation_id, plan.fingerprint])
        self.binding = {'operation_id': operation_id, 'fingerprint': plan.fingerprint,
                        'native_inputs': list(plan.native_inputs),
                        'plan': canonical(json.loads(json.dumps(asdict(plan), allow_nan=False)))[0],
                        'preparations': deepcopy(prepared_digests)}
        self.source_authorization = source_authorization
        if source_authorization is not None:
            if type(source_authorization) is not RestoreAuthorization:
                _fail('accepted_native_chain_required')
            self._auth_shape(asdict(source_authorization))
            if source_authorization.status != 'applied':
                _fail('rollback_requires_applied_source')
            # A reverse lifecycle starts from the already accepted FORWARD
            # image of this same operation, never a fresh current baseline.
            # Its independent root preserves the first activation history.
            self.binding['source_authorization'] = asdict(source_authorization)
            self.root_id = _id(['helper_lifecycle_rollback', self.binding])
        if runtime_baselines is not None and type(runtime_baselines) is not tuple:
            _fail('runtime_baseline_type')
        self._runtime_baselines = runtime_baselines
        self.root = None
        self.writable = False
        self.failed = False
        self.lock = asyncio.Lock()

    def runtime_baselines(self):
        """Immutable historical observations; readback never grants write access."""
        return self._runtime_baselines or ()

    def _bind_runtime_baselines(self, original, values):
        from .utility_meter_observers import (encode_runtime_baseline,
            verify_baseline_binding)
        from .native_registry import PreparedRegistryChange
        required = {row['entry_id'] for row in original
                    if row['domain'] == 'utility_meter' and row['state'] == 'loaded'}
        encoded, seen = [], set()
        for value in values:
            document = encode_runtime_baseline(value)
            key, payload = value.entry_id, document['payload']
            if key not in required or key in seen or payload['core'] != self.receipt.version:
                _fail('runtime_baseline_scope')
            change = self.helper_changes[key]
            verify_baseline_binding(value, change.before, change.after)
            # Authenticate the source's physical identity and exact rename using
            # ORIGINAL immutable registry preparations, not a current row capture.
            matches = []
            for step in self.plan.steps:
                if step.adapter != 'registry':
                    continue
                candidate = decode_change(self.store.preparations.resolve(
                    step.prepared_ref, self.binding['preparations'][step.prepared_ref]))
                if type(candidate) is not PreparedRegistryChange or candidate.kind != 'entity':
                    continue
                before, after = candidate.before, candidate.after
                fields = ('id', 'unique_id', 'platform', 'config_entry_id', 'config_subentry_id')
                if (candidate.identity == payload['source_identity'][0]
                        and [before.get(f) for f in fields] == payload['source_identity']
                        and [after.get(f) for f in fields] == payload['source_identity']
                        and before.get('entity_id') == payload['source_before']
                        and after.get('entity_id') == payload['source_after']
                        and (candidate.identity, candidate.before_revision, candidate.after_revision)
                            == (step.identity, step.before_revision, step.after_revision)):
                    matches.append(candidate)
            if len(matches) != 1:
                _fail('runtime_baseline_registry_binding')
            seen.add(key)
            encoded.append(document)
        if seen != required:
            _fail('runtime_baseline_required')
        return sorted(encoded, key=lambda row: row['entry_id'])

    def _authorize(self):
        if self.guard() is False:
            _fail('authorization_rejected')

    async def _verify(self, callback, *args):
        self._authorize()
        if await callback(*args) is not True:
            _fail('fresh_verification_rejected')
        self._authorize()

    async def _original(self):
        # Re-resolve the immutable original on EVERY original fence check.
        self.store.preparations.resolve_native_inputs(self.plan.native_inputs, self.receipt)
        if self.source_authorization is None:
            await self._verify(self.verify_original_native_inputs,
                               self.plan.native_inputs, self.receipt)
        else:
            await self._verify(self.verify_accepted_native_chain, self.source_authorization)

    def _slot(self, stage, entry_id, kind):
        return _id([self.root_id, stage, entry_id, kind])

    def _read(self, key):
        row = self.store.read(key)
        if row is None:
            return None
        if row != {'digest': _digest(row['payload']), 'payload': row['payload']}:
            _fail('event_digest_mismatch')
        return row['payload']

    @classmethod
    async def start(cls, *args, **kwargs):
        obj = cls(*args, **kwargs)
        obj._authorize()
        original = [_activation(obj.manager, key) for key in obj.ids]
        for row in original:
            if {field: row[field] for field in obj.expected_metadata[row['entry_id']]} != obj.expected_metadata[row['entry_id']]:
                _fail('preparation_activation_conflict')
        await obj._original()
        if original != [_activation(obj.manager, key) for key in obj.ids]:
            _fail('activation_changed')
        values = obj._runtime_baselines or ()
        encoded = obj._bind_runtime_baselines(original, values)
        obj._runtime_baselines = tuple(sorted(values, key=lambda value: value.entry_id))
        obj.root = {'kind': 'helper_lifecycle_root', 'schema': 2 if encoded else 1,
                    'binding': obj.binding, 'original': original}
        if encoded:
            obj.root['runtime_baselines'] = encoded
        if obj.source_authorization is not None and encoded:
            # Reverse uses the first durable forward observation. A newly captured
            # counter cannot replace A0/S0 even with the same configuration Plan.
            forward = obj._read(_id(['helper_lifecycle', obj.operation_id, obj.plan.fingerprint]))
            binding = {k: v for k, v in obj.binding.items() if k != 'source_authorization'}
            if (not forward or forward.get('kind') != 'helper_lifecycle_root'
                    or forward.get('schema') != 2 or forward.get('binding') != binding
                    or forward.get('original') != original
                    or forward.get('runtime_baselines') != encoded):
                _fail('original_runtime_baseline_required')
        await obj.store.create(obj.root_id, obj.root)
        # Loss of root ACK cannot produce a writable object.
        obj._authorize()
        obj.writable = True
        return obj

    @classmethod
    def reopen(cls, *args, **kwargs):
        obj = cls(*args, **kwargs)
        obj.root = obj._read(obj.root_id)
        if (type(obj.root) is not dict
                or set(obj.root) != ({'kind', 'schema', 'binding', 'original'} |
                    ({'runtime_baselines'} if obj.root.get('schema') == 2 else set()))
                or obj.root['kind'] != 'helper_lifecycle_root'
                or type(obj.root['schema']) is not int or obj.root['schema'] not in (1, 2)
                or obj.root['binding'] != obj.binding
                or type(obj.root['original']) is not list
                or [row.get('entry_id') for row in obj.root['original']] != list(obj.ids)):
            _fail('root_binding_mismatch')
        for row in obj.root['original']:
            if (set(row) != {'entry_id', 'domain', 'state', 'disabled_by',
                             'pref_disable_new_entities', 'pref_disable_polling'}
                    or row['domain'] not in NATIVE_FIELDS
                    or row['state'] not in ('loaded', 'not_loaded')
                    or type(row['pref_disable_new_entities']) is not bool
                    or type(row['pref_disable_polling']) is not bool
                    or row['disabled_by'] is not None and type(row['disabled_by']) is not str
                    or row['state'] == 'loaded' and row['disabled_by'] is not None):
                _fail('root_activation_invalid')
            if {field: row[field] for field in obj.expected_metadata[row['entry_id']]} != obj.expected_metadata[row['entry_id']]:
                _fail('preparation_activation_conflict')
        from .utility_meter_observers import decode_runtime_baseline
        raw = obj.root.get('runtime_baselines', [])
        if type(raw) is not list or (obj.root['schema'] == 2 and not raw):
            _fail('runtime_baseline_layout')
        values = tuple(decode_runtime_baseline(row) for row in raw)
        if obj._bind_runtime_baselines(obj.root['original'], values) != raw:
            _fail('runtime_baseline_binding')
        if obj._runtime_baselines is not None and obj._runtime_baselines != values:
            _fail('runtime_baseline_conflict')
        obj._runtime_baselines = values
        obj.inspect()  # Validate graph, including dangling intents.
        return obj

    def _expected_activation(self, original, state):
        return dict(original, state=state)

    def _assert_activation(self, original, state):
        if _activation(self.manager, original['entry_id']) != self._expected_activation(original, state):
            _fail('activation_conflict')

    def _inactive(self, original):
        self._assert_activation(original, 'not_loaded')
        _require_inactive(self.manager.async_get_entry(original['entry_id']))

    def inspect(self):
        """Read-only durable status. Receipt ACK loss is inspectable, never replayed."""
        if self.root is None:
            _fail('root_unavailable')
        if self._read(self.root_id) != self.root:
            _fail('root_binding_mismatch')
        result = {'unload': {}, 'setup': {}, 'pending': [], 'full_ready': False}
        previous = _digest(self.root)
        incomplete = False
        for stage in ('unload', 'setup'):
            for original in self.root['original']:
                key = original['entry_id']
                intent = self._read(self._slot(stage, key, 'intent'))
                receipt = self._read(self._slot(stage, key, 'receipt'))
                if intent is None:
                    if receipt is not None:
                        _fail('receipt_without_intent')
                    incomplete = True
                    result[stage][key] = 'pending'
                    continue
                if incomplete:
                    _fail('event_order_invalid')
                expected = {'kind': 'helper_lifecycle_intent', 'root': self.root_id,
                            'stage': stage, 'entry_id': key, 'previous': previous,
                            'original': original}
                if stage == 'setup':
                    auth = intent.get('authorization')
                    self._auth_shape(auth)
                    expected['authorization'] = auth
                if intent != expected:
                    _fail('intent_binding_mismatch')
                if receipt is None:
                    incomplete = True
                    result[stage][key] = 'unknown'
                    result['pending'].append({'stage': stage, 'entry_id': key})
                    continue
                expected_receipt = {'kind': 'helper_lifecycle_receipt',
                    'root': self.root_id, 'intent': _digest(intent),
                    'stage': stage, 'entry_id': key,
                    'api_called': original['state'] == 'loaded',
                    'state': 'not_loaded' if stage == 'unload' else original['state']}
                if receipt != expected_receipt:
                    _fail('receipt_binding_mismatch')
                previous = _digest(receipt)
                result[stage][key] = 'verified'
        result['head'] = previous
        result['inactive_receipts_complete'] = all(v == 'verified' for v in result['unload'].values())
        result['activation_receipts_complete'] = all(v == 'verified' for v in result['setup'].values())
        return result

    def _auth_shape(self, auth):
        if (type(auth) is not dict
                or set(auth) != {'operation_id', 'fingerprint', 'head', 'status'}
                or auth['operation_id'] != self.operation_id
                or auth['fingerprint'] != self.plan.fingerprint
                or auth['status'] not in ('applied', 'rolled_back')
                or type(auth['head']) is not str
                or not re.fullmatch(r'[0-9a-f]{64}', auth['head'])):
            _fail('accepted_native_chain_required')

    async def quiesce(self):
        """All helpers get durable inactive receipts; disabled entries never unload."""
        await self._run('unload', None)

    async def restore(self, authorization):
        """Restore only originally loaded helpers, after accepted native migration."""
        if type(authorization) is not RestoreAuthorization:
            _fail('accepted_native_chain_required')
        self._auth_shape(asdict(authorization))
        await self._run('setup', authorization)

    async def _run(self, stage, authorization):
        async with self.lock:
            if not self.writable or self.failed:
                _fail('reopen_or_failed_read_only')
            try:
                status = self.inspect()
                if status['pending']:
                    _fail('unknown_unresolved_intent')
                if stage == 'setup' and not status['inactive_receipts_complete']:
                    _fail('inactive_receipts_required')
                for original in self.root['original']:
                    key = original['entry_id']
                    if status[stage][key] == 'verified':
                        continue  # acknowledged receipt, never repeat the API
                    await self._entry(stage, original, authorization)
                    status = self.inspect()
                await self._source(stage, authorization)
                if stage == 'unload':
                    for original in self.root['original']:
                        await self._verify(self.verify_inactive, original['entry_id'])
                    self.require_quiescent()
                else:
                    for original in self.root['original']:
                        await self._verify(self.verify_runtime, original['entry_id'],
                                           deepcopy(original), authorization)
                    self._authorize()
                    for original in self.root['original']:
                        self._assert_activation(original, original['state'])
                        if original['state'] == 'not_loaded':
                            self._inactive(original)
            except BaseException:
                # Even pre-effect errors forbid continuing from stale expectations.
                self.failed = True
                raise

    async def _source(self, stage, authorization):
        if stage == 'unload':
            await self._original()
        else:
            await self._verify(self.verify_accepted_native_chain, authorization)

    async def _entry(self, stage, original, authorization):
        key, active = original['entry_id'], original['state'] == 'loaded'
        self._authorize()
        await self._source(stage, authorization)
        self._assert_activation(original, 'not_loaded' if stage == 'setup' else original['state'])
        if stage == 'setup' or not active:
            self._inactive(original)
            await self._verify(self.verify_inactive, key)
            self._inactive(original)
        previous = self.inspect()['head']
        intent = {'kind': 'helper_lifecycle_intent', 'root': self.root_id,
                  'stage': stage, 'entry_id': key, 'previous': previous,
                  'original': original}
        if stage == 'setup':
            intent['authorization'] = asdict(authorization)
        await self.store.create(self._slot(stage, key, 'intent'), intent)
        self._authorize()
        # A durable intent is not a source fence: check again after save's await.
        await self._source(stage, authorization)
        self._assert_activation(original, 'not_loaded' if stage == 'setup' else original['state'])
        if stage == 'setup' or not active:
            self._inactive(original)
        if active:
            api = self.manager.async_unload if stage == 'unload' else self.manager.async_setup
            if await api(key) is not True:
                _fail('native_call_unconfirmed')
            self._authorize()
        if await self.settle() is False:
            _fail('idle_barrier_rejected')
        self._authorize()
        if stage == 'unload':
            self._inactive(original)
            await self._verify(self.verify_inactive, key)
            self._inactive(original)
        else:
            self._assert_activation(original, original['state'])
            if not active:
                self._inactive(original)
                await self._verify(self.verify_inactive, key)
                self._inactive(original)
            await self._verify(self.verify_runtime, key, deepcopy(original), authorization)
            self._assert_activation(original, original['state'])
        # unload: same ORIGINAL inputs; setup: same ACCEPTED native chain.
        await self._source(stage, authorization)
        self._assert_activation(original, 'not_loaded' if stage == 'unload' else original['state'])
        if stage == 'unload' or not active:
            self._inactive(original)
        receipt = {'kind': 'helper_lifecycle_receipt', 'root': self.root_id,
                   'intent': _digest(intent), 'stage': stage, 'entry_id': key,
                   'api_called': active,
                   'state': 'not_loaded' if stage == 'unload' else original['state']}
        await self.store.create(self._slot(stage, key, 'receipt'), receipt)
        self._authorize()

    def require_quiescent(self):
        """Synchronous fresh guard for EVERY inner native adapter await boundary.

        Durable receipts alone are historical. Installed IO must additionally
        retain callback/platform exclusion while the Coordinator operates.
        """
        self._authorize()
        status = self.inspect()
        if (not status['inactive_receipts_complete'] or status['pending']
                or any(value == 'verified' for value in status['setup'].values())):
            _fail('not_in_quiescent_phase')
        for original in self.root['original']:
            self._inactive(original)

    async def reconcile(self, authorization=None):
        """Read-only fresh observations after crash/ACK loss. Never writes/effects.

        An unresolved API outcome remains unknown even if runtime now matches.
        A later HA restart may have independently reactivated helpers; report
        current evidence separately from historic acknowledged inactive receipts.
        """
        self._authorize()
        status = self.inspect()
        if authorization is not None:
            if type(authorization) is not RestoreAuthorization:
                _fail('accepted_native_chain_required')
            self._auth_shape(asdict(authorization))
        await self._source('unload' if authorization is None else 'setup', authorization)
        observations = {}
        for original in self.root['original']:
            key = original['entry_id']
            observations[key] = _activation(self.manager, key)
            if observations[key]['state'] == 'not_loaded':
                self._inactive(original)
                await self._verify(self.verify_inactive, key)
                self._inactive(original)
        await self._source('unload' if authorization is None else 'setup', authorization)
        if observations != {row['entry_id']: _activation(self.manager, row['entry_id'])
                            for row in self.root['original']}:
            _fail('activation_changed_during_reconcile')
        status['current_activation'] = observations
        status['read_only'] = True
        return status
