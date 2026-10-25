"""DRAFT: own UtilityMeter registry-ID placeholder compatibility, no authority.

Install beside existing native modules only after owner integration/review.
No async methods, lifecycle calls, storage, restore payload or counter writes.
An exception after mutate begins leaves the EXISTING registry intent UNKNOWN.
A stamp is immutable evidence; parsing it never constructs a live writer.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import hashlib
import inspect
import json

from .native_registry import NativeRegistryIO, NativeRegistryMutation, PreparedRegistryChange
from .native_registry_transition import _json, _loads, prepared_registry_digest
from .native_registry_receipts import RegistryReceiptChain, RegistryIntent
from .native_helper_lifecycle import NativeHelperLifecycle
from .native_helper_activation_lease import NativeHelperActivationLease
from .transaction import PhaseContext

PINS = ('2025.6.0', '2026.10.2')
SCHEMA = 'own_helper_placeholder_effect_v1'


class PlaceholderRejected(ValueError):
    pass


def _need(value, reason):
    if not value:
        raise PlaceholderRejected('own_placeholder_' + reason) from None


def _hash(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _sync(callback, *args):
    result = callback(*args)
    if inspect.isawaitable(result):
        if inspect.iscoroutine(result):
            result.close()
        _need(False, 'synchronous_hook_required')
    return result


def _guard(callback):
    result = _sync(callback)
    _need(result is None or result is True, 'guard_rejected')


def _native(value):
    from homeassistant.helpers.json import _orjson_bytes_default_encoder
    return _orjson_bytes_default_encoder(value).decode('utf-8')


def _fragment(row):
    return None if row is None else _native(row.as_storage_fragment)


def _state(state):
    # Preserve ALL native body/context/timestamps. Do not rebuild State.from_dict,
    # strip timestamps, reserialize the nested state or synthesize a context.
    return None if state is None else state.as_dict_json.decode('utf-8')


def _installed_effect_methods(version):
    # Reuse existing source pins/code walker; no new configurable source list.
    from pathlib import Path
    import sys
    import homeassistant
    from homeassistant import core
    from homeassistant.helpers import entity_registry as er, entity
    from .native_utility_setup_audit import _SOURCES, _code_objects
    _need(sys.implementation.name == 'cpython' and sys.version_info[:2] in
          ((3, 13), (3, 14)), 'unsupported_python')
    root = Path(homeassistant.__file__).resolve().parent
    methods = (core.StateMachine.async_remove, er.RegistryEntry.write_unavailable_state,
               er.EntityRegistry.async_update_entity, entity.Entity._Entity__async_remove_impl)
    if version == '2026.10.2':
        methods += (er.async_get_legacy_friendly_name,)
    pins, compiled = {}, {}
    for relative in ('core.py', 'helpers/entity_registry.py', 'helpers/entity.py'):
        path = root / relative
        _need(path.resolve() == path and path.is_file(), 'installed_path')
        raw = path.read_bytes()
        pins[relative] = hashlib.sha256(raw).hexdigest()
        _need(pins[relative] == _SOURCES[version][relative], 'installed_source_pin')
        compiled[str(path)] = tuple(_code_objects(compile(raw, str(path), 'exec',
            dont_inherit=True, optimize=sys.flags.optimize)))
    for method in methods:
        code = getattr(method, '__code__', None)
        _need(code is not None and code.co_filename in compiled, 'native_code_path')
        expected = [item for item in compiled[code.co_filename]
                    if item.co_qualname == code.co_qualname]
        _need(len(expected) == 1 and code == expected[0], 'native_code_changed')
    return pins, methods


def _attrs(row, version, hass):
    """Read-only transcription of pinned RegistryEntry.write_unavailable_state.

    No fake hass or captured async_set calls. Actual write uses native method.
    Current source uses its pinned public naming helper, never a cached formula.
    """
    attrs = {'restored': True}
    if row.capabilities is not None:
        attrs.update(row.capabilities)
    device_class = row.device_class or row.original_device_class
    if device_class is not None:
        attrs['device_class'] = device_class
    icon = row.icon or row.original_icon
    if icon is not None:
        attrs['icon'] = icon
    if version == '2025.6.0':
        name = row.name or row.original_name
        if name is not None:
            attrs['friendly_name'] = name
    else:
        from homeassistant.helpers.entity_registry import async_get_legacy_friendly_name
        name = async_get_legacy_friendly_name(hass, row)
        if name:
            attrs['friendly_name'] = name
    if row.supported_features is not None:
        attrs['supported_features'] = row.supported_features
    if row.unit_of_measurement is not None:
        attrs['unit_of_measurement'] = row.unit_of_measurement
    return json.loads(_native(attrs))


@dataclass(frozen=True)
class PlaceholderEffectStamp:
    """Private immutable body. JSON contains exact native state JSON strings.

    serialized scope/body are detached evidence, NEVER reopenable authority.
    No constructor of OwnHelperPlaceholder accepts a serialized stamp.
    """
    scope_json: str
    mutation_json: str
    body_json: str

    def encode(self):
        return _json({'schema': SCHEMA, **asdict(self)})


def _parsed(stamp):
    _need(type(stamp) is PlaceholderEffectStamp, 'stamp_type')
    result = []
    for text in (stamp.scope_json, stamp.mutation_json, stamp.body_json):
        _need(type(text) is str, 'stamp_json_type')
        result.append(_loads(text.encode('utf-8')))
    return result


def decode_effect(raw):
    """Pure bounded decoding; returns evidence, never a writer or acceptance."""
    item = _loads(raw)
    _need(type(item) is dict and set(item) ==
          {'schema', 'scope_json', 'mutation_json', 'body_json'}
          and item['schema'] == SCHEMA, 'stamp_shape')
    stamp = PlaceholderEffectStamp(item['scope_json'], item['mutation_json'], item['body_json'])
    _parsed(stamp)
    return stamp


def _state_shape(raw, entity_id, attrs):
    _need(type(raw) is str, 'native_state_missing')
    row = _loads(raw.encode('utf-8'))
    _need(type(row) is dict and row.get('entity_id') == entity_id
          and row.get('state') == 'unavailable'
          and type(row.get('attributes')) is dict and row['attributes'].get('restored') is True
          and _json(row.get('attributes')) == _json(attrs), 'native_state_mismatch')
    _need(type(row.get('context')) is dict
          and set(row['context']) == {'id', 'parent_id', 'user_id'}
          and all(type(row.get(key)) is str for key in
                  ('last_changed', 'last_updated', 'last_reported')), 'native_state_body_incomplete')


def validate_effect(stamp, *, expected_scope, change, mutation,
                    validate_unload_witness):
    """Pure evidence/replay validator, not current runtime authorization.

    expected_scope MUST be independently reconstructed from ORIGINAL plan +
    durable registry intent + accepted lifecycle root/unload refs. Never use
    json.loads(stamp.scope_json) as expected_scope. Owner hook validates copied
    lifecycle witness via validate_lifecycle_receipt_witness and exact ref graph.
    The hook is REQUIRED; no guessed empty callback or True fallback.
    """
    scope, actual_mutation, body = _parsed(stamp)
    _need(type(scope) is dict and set(scope) == {'version', 'source_pins', 'operation_id',
          'fingerprint', 'chain_root', 'registry_intent', 'phase', 'lifecycle_root',
          'unload_receipt', 'entry_id', 'registry_uuid', 'prepared_digest', 'direction',
          'source', 'target', 'source_attrs', 'source_state_json', 'target_attrs'}, 'scope_shape')
    _need(type(expected_scope) is dict and _json(scope) == _json(expected_scope), 'original_scope_mismatch')
    _need(callable(validate_unload_witness), 'unload_validator_required')
    _need(_sync(validate_unload_witness, scope) is True, 'unload_witness_rejected')
    _need(type(change) is PreparedRegistryChange and type(mutation) is NativeRegistryMutation,
          'prepared_mutation_types')
    from .native_utility_setup_audit import _SOURCES
    _need(scope['version'] in PINS, 'version_pin')
    _need(scope['source_pins'] == {key: _SOURCES[scope['version']][key]
          for key in ('core.py', 'helpers/entity_registry.py', 'helpers/entity.py')}, 'source_pins')
    _need(scope['version'] in PINS and scope['prepared_digest'] == prepared_registry_digest(change)
          and change.kind == 'entity' and mutation.kind == 'entity'
          and mutation.identity == change.identity == scope['registry_uuid'], 'prepared_binding')
    _need(_json(actual_mutation) == _json(asdict(mutation)), 'mutation_mismatch')
    source, target = ((change.before, change.after) if scope['direction'] == 'forward'
                      else (change.after, change.before))
    _need(scope['direction'] in ('forward', 'rollback')
          and (scope['source'], scope['target']) == (source['entity_id'], target['entity_id'])
          and source['config_entry_id'] == scope['entry_id'] == source['unique_id']
          and source['platform'] == 'utility_meter' and source['config_subentry_id'] is None,
          'own_helper_scope')
    before_row, after_row = json.loads(mutation.before_json), json.loads(mutation.after_json)
    # Exact modified_at is preserved and independently accepted by the normal
    # registry transition validator; this subeffect must not normalize it.
    _need(before_row['id'] == after_row['id'] == scope['registry_uuid']
          and before_row['entity_id'] == scope['source']
          and after_row['entity_id'] == scope['target'], 'mutation_rows')
    _need(type(body) is dict and set(body) == {'capture', 'events', 'finish'}
          and type(body['events']) is list, 'body_shape')
    capture, final = body['capture'], body['finish']
    _snapshot_shape(capture, scope)
    _snapshot_shape(final, scope)
    _need(capture['source_row'] == mutation.before_json
          and capture['target_row'] is None
          and capture['target_state'] is None and capture['target_reserved'] is False,
          'capture_binding')
    _state_shape(capture['source_state'], scope['source'], scope['source_attrs'])
    _need(capture['source_state'] == scope['source_state_json'], 'source_state_body_changed')
    _need(final['source_row'] is None and final['target_row'] == mutation.after_json
          and final['source_state'] is None and final['source_reserved'] is False,
          'finish_binding')
    _state_shape(final['target_state'], scope['target'], scope['target_attrs'])
    _need(final['target_reserved'] is False, 'target_reservation')
    if scope['version'] == '2025.6.0':
        _need(len(body['events']) == 2, 'legacy_events')
        remove, write = body['events']
        _event(remove, 'StateMachine.async_remove', scope)
        _event(write, 'RegistryEntry.write_unavailable_state', scope)
        _need(remove['result_json'] == 'true' and write['result_json'] == 'null', 'native_returns')
        _need(remove['before']['source_row'] is None
              and remove['before']['target_row'] == mutation.after_json
              and remove['before']['source_state'] == capture['source_state']
              and remove['before']['source_reserved'] is capture['source_reserved']
              and remove['before']['target_state'] is None
              and remove['before']['target_reserved'] is False, 'remove_preimage')
        _need(remove['after']['source_state'] is None
              and remove['after']['source_reserved'] is False
              and remove['after']['target_state'] is None
              and remove['after']['target_reserved'] is False
              and remove['after'] == write['before'], 'remove_clear')
        _need(write['after'] == final, 'write_final_changed')
        _need(remove['source_check'] == 'same_preimage'
              and write['source_check'] == 'absent_after_owned_remove'
              and remove['same_returned_row'] is True and write['same_returned_row'] is True,
              'live_identity_checks_missing')
    else:
        _need(body['events'] == [], 'current_manual_effect_forbidden')
    return True


def _snapshot_shape(item, scope):
    _need(type(item) is dict and set(item) == {'source_state', 'target_state',
          'source_reserved', 'target_reserved', 'source_row', 'target_row'}, 'snapshot_shape')
    _need(type(item['source_reserved']) is bool and type(item['target_reserved']) is bool,
          'reservation_type')
    for key in ('source_row', 'target_row', 'source_state', 'target_state'):
        _need(item[key] is None or type(item[key]) is str, 'snapshot_value_type')


def _event(item, api, scope):
    _need(type(item) is dict and set(item) == {'api', 'before', 'after', 'result_json',
          'source_check', 'same_returned_row'} and item['api'] == api, 'event_shape')
    _snapshot_shape(item['before'], scope)
    _snapshot_shape(item['after'], scope)
    _need(item['before']['source_row'] is None
          and item['after']['source_row'] is None
          and item['before']['target_row'] == item['after']['target_row'], 'event_registry_changed')


class OwnHelperPlaceholder:
    """Single-use ephemeral node; same task only; no deepcopy/reopen/serialization.

    verify_live_unload_state(lifecycle, unload_ref, actual_state) is a REQUIRED
    owner integration hook: bind the object retained at actual real-unload
    boundary to this receipt; never replace it with states.get during capture.
    Missing original object continuity refuses; durable receipt alone is weaker.
    fresh_source_fence(scope) is mandatory trusted owner IO: exact durable phase,
    original full-three/YAML/manager stamps after observation/intention's last
    await. It runs only BEFORE mutation; it cannot authorize from a fresh capture.
    live_guard() retains the real coordinator/editor/admin/external lease.
    validate_unload_witness(scope) validates ORIGINAL root/receipt graph, no API.
    """
    def __init__(self):
        _need(False, 'capture_required')

    def __copy__(self):
        _need(False, 'live_node_not_copyable')

    def __deepcopy__(self, memo):
        _need(False, 'live_node_not_copyable')

    def __reduce__(self):
        _need(False, 'live_node_not_serializable')

    @classmethod
    def capture(cls, *, io, lifecycle, chain, context, intent, observations,
                live_guard, fresh_source_fence, validate_unload_witness, verify_live_unload_state):
        from homeassistant.const import __version__
        from homeassistant.core import HomeAssistant, State, StateMachine
        from homeassistant.helpers import entity_registry as er
        _need(cls is OwnHelperPlaceholder and type(io) is NativeRegistryIO
              and type(lifecycle) is NativeHelperLifecycle
              and type(chain) is RegistryReceiptChain and type(context) is PhaseContext
              and type(intent) is RegistryIntent and chain.active is intent
              and callable(live_guard) and callable(fresh_source_fence)
              and callable(validate_unload_witness) and callable(verify_live_unload_state), 'installed_binding')
        obj = object.__new__(cls)
        obj.io, obj.lifecycle, obj.chain, obj.context, obj.intent = io, lifecycle, chain, context, intent
        obj.live_guard, obj.fence, obj.witness = live_guard, fresh_source_fence, validate_unload_witness
        obj.task, obj.loop = asyncio.current_task(), asyncio.get_running_loop()
        obj.version, obj.phase, obj.failed = __version__, 'capturing', False
        _need(obj.task is not None and obj.version in PINS, 'installed_runtime')
        obj.change = chain._original_change(context.index)
        _need(type(obj.change) is PreparedRegistryChange and obj.change.kind == 'entity'
              and bool(obj.change.mapping), 'own_rename_required')
        obj.entry_id = obj.change.before['config_entry_id']
        _need(lifecycle.own_registry_changes.get(obj.entry_id) == obj.change, 'own_preparation')
        obj.entry = lifecycle.manager.async_get_entry(obj.entry_id)
        obj.lease = lifecycle.activation_lease
        _need(type(obj.lease) is NativeHelperActivationLease, 'exact_lease_required')
        obj.hass, obj.registry = io.hass, io.registries['entity']
        obj.machine = obj.hass.states
        _need(type(obj.hass) is HomeAssistant and type(obj.machine) is StateMachine
              and type(obj.registry) is er.EntityRegistry
              and obj.hass.data.get(er.DATA_REGISTRY) is obj.registry, 'native_state_machine')
        obj.source_pins, obj.native_methods = _installed_effect_methods(obj.version)
        obj.io_mutate = io.mutate
        _need(getattr(obj.io_mutate, '__func__', None) is NativeRegistryIO.mutate
              and obj.io_mutate.__self__ is io, 'installed_native_io')
        source, target = ((obj.change.before, obj.change.after) if context.record.direction == 'forward'
                          else (obj.change.after, obj.change.before))
        obj.source, obj.target = source['entity_id'], target['entity_id']
        obj._live()
        chain.verify_intent_source(intent, context, observations)
        binding = chain._context(context)
        original = next((row for row in lifecycle.root['original'] if row['entry_id'] == obj.entry_id), None)
        _need(original is not None and original['domain'] == 'utility_meter'
              and original['state'] == 'loaded', 'real_unload_required')
        unload_slot = lifecycle._slot('unload', obj.entry_id, 'receipt')
        unload = lifecycle.store.read(unload_slot)
        _need(unload is not None and unload['payload']['api_called'] is True, 'real_unload_receipt')
        accepted = chain.accepted_documents()['core.entity_registry']['data']['entities']
        source_row = obj.registry.async_get(obj.source)
        _need(source_row is not None and source_row.id == obj.change.identity, 'source_row')
        accepted_row = [row for row in accepted if row['id'] == obj.change.identity]
        _need(len(accepted_row) == 1
              and _json(json.loads(_fragment(source_row))) == _json(accepted_row[0]),
              'source_not_accepted_original_image')
        obj.source_row = source_row
        obj.preimage = obj.machine.get(obj.source)
        _need(type(obj.preimage) is State, 'actual_source_placeholder_required')
        _need(_sync(verify_live_unload_state, lifecycle, (unload_slot, unload['digest']), obj.preimage) is True,
              'live_unload_state_lineage_required')
        obj.source_body = _state(obj.preimage)
        obj.source_attrs = _attrs(source_row, obj.version, obj.hass)
        _state_shape(obj.source_body, obj.source, obj.source_attrs)
        _need(obj.registry.async_get(obj.target) is None
              and obj.machine.get(obj.target) is None
              and obj.machine.async_available(obj.target), 'target_collision')
        obj.scope = {'version': obj.version, 'source_pins': obj.source_pins, 'operation_id': chain.operation_id,
            'fingerprint': chain.plan.fingerprint, 'chain_root': chain.root_id,
            'registry_intent': list(intent.reference), 'phase': binding,
            'lifecycle_root': [lifecycle.root_id, _hash(lifecycle.root)],
            'unload_receipt': [unload_slot, unload['digest']],
            'entry_id': obj.entry_id, 'registry_uuid': obj.change.identity,
            'prepared_digest': prepared_registry_digest(obj.change),
            'direction': context.record.direction, 'source': obj.source, 'target': obj.target,
            'source_attrs': obj.source_attrs, 'source_state_json': obj.source_body,
            # Target attrs computed after mutation from the exact current row.
            'target_attrs': None}
        obj.binding_json = _json(obj.scope).decode('ascii')
        _need(_sync(obj.witness, json.loads(obj.binding_json)) is True, 'unload_witness_rejected')
        _need(_sync(obj.fence, json.loads(obj.binding_json)) is True, 'fresh_source_fence_rejected')
        obj._live()
        obj.capture_body = obj._snapshot()
        _need(obj.capture_body['source_state'] == obj.source_body, 'capture_drift')
        obj.phase = 'captured'
        return obj

    def _live(self):
        from homeassistant.const import __version__
        from homeassistant import core
        from homeassistant.helpers import entity_registry as er, entity
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
        from .native_helper_transaction import _require_inactive
        _need(not self.failed and asyncio.current_task() is self.task
              and asyncio.get_running_loop() is self.loop and __version__ == self.version,
              'live_task_or_pin_changed')
        _guard(self.live_guard)
        current_methods = (core.StateMachine.async_remove, er.RegistryEntry.write_unavailable_state,
                           er.EntityRegistry.async_update_entity, entity.Entity._Entity__async_remove_impl)
        if self.version == '2026.10.2':
            current_methods += (er.async_get_legacy_friendly_name,)
        _need(all(a is b for a, b in zip(current_methods, self.native_methods, strict=True))
              and self.io.mutate.__func__ is self.io_mutate.__func__
              and self.io.mutate.__self__ is self.io, 'native_method_replaced')
        self.lease._task_check()
        self.lease.check()
        _need(self.lifecycle.activation_lease is self.lease
              and self.lease.manager is self.lifecycle.manager
              and self.lease.owns_entry(self.entry), 'lease_binding_changed')
        self.lifecycle.require_quiescent()
        _require_inactive(self.entry, self.lease)
        _need(self.hass.states is self.machine and self.hass.config_entries is self.lifecycle.manager
              and self.lifecycle.manager.async_get_entry(self.entry_id) is self.entry
              and self.io.registries['entity'] is self.registry
              and self.lifecycle.writable and not self.lifecycle.failed
              and self.chain.writable and self.chain.active is self.intent
              and self.chain.original_receipt == self.lifecycle.receipt
              and self.chain.plan == self.lifecycle.plan
              and self.chain.operation_id == self.lifecycle.operation_id
              and self.chain.preparations is self.lifecycle.store.preparations,
              'live_scope_changed')
        self.chain._context(self.context)
        _need(self.chain._original_change(self.context.index) == self.change
              and self.lifecycle.own_registry_changes.get(self.entry_id) == self.change,
              'original_change_drift')
        row = self.chain.store.read(self.intent.reference[0])
        _need(row is not None and row['digest'] == self.intent.reference[1]
              and row['payload']['binding'] == self.chain._context(self.context), 'intent_drift')
        data = self.hass.data.get(DATA_RESTORE_STATE)
        _need(data is not None and self.source not in data.entities and self.target not in data.entities,
              'restore_entity_active')
        for platform in async_get_platforms(self.hass, self.entry.domain):
            _need(not (platform.config_entry is self.entry and platform.entities), 'helper_platform_active')

    def _snapshot(self):
        return {'source_state': _state(self.machine.get(self.source)),
                'target_state': _state(self.machine.get(self.target)),
                'source_reserved': self.source in self.machine._reservations,
                'target_reserved': self.target in self.machine._reservations,
                'source_row': _fragment(self.registry.async_get(self.source)),
                'target_row': _fragment(self.registry.async_get(self.target))}

    def _returned(self):
        self._live()
        _need(self.registry.async_get(self.source) is None
              and self.registry.async_get(self.target) is self.returned_row
              and _fragment(self.returned_row) == self.mutation.after_json, 'returned_row_changed')

    def mutate(self):
        """One actual NativeRegistryIO.mutate + legacy effects in one sync body."""
        _need(self.phase == 'captured', 'single_use')
        try:
            self._live()
            _need(_sync(self.fence, json.loads(self.binding_json)) is True, 'fresh_source_fence_rejected')
            self._live()
            _need(self.registry.async_get(self.source) is self.source_row
                  and self.machine.get(self.source) is self.preimage
                  and self._snapshot() == self.capture_body, 'source_changed')
            target = self.change.after if self.context.record.direction == 'forward' else self.change.before
            self.phase = 'effect_started'  # no retry even if native call throws
            self.mutation = self.io.mutate(self.change, target)
            _need(type(self.mutation) is NativeRegistryMutation
                  and self.mutation.kind == 'entity' and self.mutation.identity == self.change.identity
                  and self.mutation.before_json == self.capture_body['source_row'], 'actual_mutation_unconfirmed')
            self.returned_row = self.registry.async_get(self.target)
            actual_after = json.loads(self.mutation.after_json)
            _need(actual_after['id'] == self.change.identity
                  and actual_after['entity_id'] == self.target
                  and actual_after['config_entry_id'] == self.entry_id
                  and actual_after['unique_id'] == self.entry_id
                  and actual_after['platform'] == 'utility_meter', 'actual_returned_identity')
            self._returned()
            self.scope['target_attrs'] = _attrs(self.returned_row, self.version, self.hass)
            self.events = []
            if self.version == '2025.6.0':
                _need(self.machine.get(self.source) is self.preimage
                      and _state(self.preimage) == self.source_body
                      and self.machine.get(self.target) is None
                      and self.machine.async_available(self.target), 'post_mutation_collision')
                before = self._snapshot()
                _need(before['source_reserved'] is self.capture_body['source_reserved'], 'reservation_changed')
                result = self.machine.async_remove(self.source)
                after = self._snapshot()
                self.events.append({'api': 'StateMachine.async_remove', 'before': before, 'after': after,
                    'result_json': _json(result).decode('ascii'), 'source_check': 'same_preimage',
                    'same_returned_row': True})
                _need(result is True and after['source_state'] is None
                      and not after['source_reserved'] and self.machine.async_available(self.source),
                      'remove_not_confirmed_unknown')
                self._returned()
                _need(self.machine.get(self.target) is None and self.machine.async_available(self.target),
                      'target_collision_unknown')
                before = self._snapshot()
                result = self.returned_row.write_unavailable_state(self.hass)
                after = self._snapshot()
                self.events.append({'api': 'RegistryEntry.write_unavailable_state', 'before': before, 'after': after,
                    'result_json': _json(result).decode('ascii'), 'source_check': 'absent_after_owned_remove',
                    'same_returned_row': True})
                _need(result is None, 'write_native_return_unknown')
                self._returned()
                _state_shape(after['target_state'], self.target, self.scope['target_attrs'])
                self.target_object = self.machine.get(self.target)
            else:
                # Existing Core listener runs only after this synchronous body.
                # No manual remove/write on current, even if state is still old.
                self.target_object = None
            self.phase = 'mutated'
            return self.mutation
        except BaseException:
            self.failed = True
            raise

    def finish(self):
        """After caller's existing settle; before registry receipt completion.

        Caller keeps real guards across settle/flush/observe/receipt ACK and uses
        finish stamp only in the SAME current registry intent/receipt lineage.
        """
        _need(self.phase == 'mutated', 'finish_after_mutate_once')
        try:
            self._returned()
            final = self._snapshot()
            if self.version == '2025.6.0':
                _need(self.machine.get(self.target) is self.target_object, 'target_object_changed_unknown')
            _need(final['source_state'] is None and not final['source_reserved']
                  and self.machine.async_available(self.source), 'source_not_clear_unknown')
            _state_shape(final['target_state'], self.target, self.scope['target_attrs'])
            stamp = PlaceholderEffectStamp(_json(self.scope).decode('ascii'),
                _json(asdict(self.mutation)).decode('ascii'),
                _json({'capture': self.capture_body, 'events': self.events, 'finish': final}).decode('ascii'))
            validate_effect(stamp, expected_scope=self.scope, change=self.change,
                            mutation=self.mutation, validate_unload_witness=self.witness)
            self.phase = 'finished'
            return stamp
        except BaseException:
            self.failed = True
            raise
