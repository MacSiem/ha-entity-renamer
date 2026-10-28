"""Private immutable server preparations for migration and crash recovery.

Only trusted backend planners create these payloads. Clients receive IDs/digests,
never arbitrary config patches or local filesystem paths. Retention must preserve
all preparations referenced by an unresolved journal operation.
"""
from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
import hashlib
import json
import re

_ID = re.compile(r'[A-Za-z0-9_-]{1,80}\Z')
_MAX_PLANS = 1000
_MAX_BYTES = 60 * 1024 * 1024


def canonical(value):
    """Bound a detached JSON tree before serialization/hashing/storage."""
    from .reference_planner import Context, Consumer, Limits, plan_references
    checked = plan_references(value, {}, Context(Consumer.AUTOMATION, ()),
                              limits=Limits(max_string_length=_MAX_BYTES)).value
    raw = json.dumps(checked, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(raw.encode()) > _MAX_BYTES:
        raise ValueError('preparation_size_limit')
    return json.loads(raw), hashlib.sha256(raw.encode()).hexdigest()


def decode(value):
    if (type(value) is not dict or set(value) != {'schema', 'plans'}
            or value['schema'] != 1 or type(value['plans']) is not dict
            or len(value['plans']) > _MAX_PLANS):
        raise ValueError('invalid_preparations')
    result = {}
    for key, row in value['plans'].items():
        if (type(key) is not str or not _ID.fullmatch(key) or type(row) is not dict
                or set(row) != {'digest', 'payload'}):
            raise ValueError('invalid_preparation')
        payload, digest = canonical(row['payload'])
        if row['digest'] != digest:
            raise ValueError('preparation_digest_mismatch')
        result[key] = {'digest': digest, 'payload': payload}
    return result


class PreparedStore:
    def __init__(self, store):
        self.store = store
        self.plans = None
        self.lock = asyncio.Lock()

    async def initialize(self):
        async with self.lock:
            if self.plans is not None:
                raise ValueError('preparations_already_initialized')
            raw, exists = await self.store.load()
            self.plans = decode(raw) if exists else {}

    def _ready(self):
        if self.plans is None:
            raise ValueError('preparations_unavailable')

    def resolve(self, plan_id, expected_digest):
        """Resolve only immutable content bound to a server-produced plan digest."""
        self._ready()
        row = self.plans.get(plan_id)
        if row is None or row['digest'] != expected_digest:
            raise ValueError('preparation_not_found_or_changed')
        return deepcopy(row['payload'])

    async def create_native_inputs(self, preparation_id, baseline, *, composition=None):
        """Persist one full batch baseline BEFORE constructing its Plan.

        Caller must capture once under the coordinator/editor locks before any
        effect. This does not refresh inputs or upgrade a legacy preparation.
        Only the opaque returned tuple belongs in Plan.native_inputs.
        """
        from .native_input_baseline import encode_baseline, baseline_digest
        raw = encode_baseline(baseline)
        native_digest = baseline_digest(baseline)
        payload = {'kind': 'native_input_baseline', 'schema': 1,
                   'codec': base64.b64encode(raw).decode('ascii'),
                   'baseline_digest': native_digest}
        if composition is not None:
            from .native_template_composition import encode_composition
            payload.update(schema=2, composition=encode_composition(composition, baseline))
        digest = await self.create(preparation_id, payload)
        return (preparation_id, digest, native_digest)

    def resolve_native_inputs(self, reference, original_receipt):
        """Reopen exact immutable bytes and ORIGINAL receipt, without HA effects."""
        from .native_input_baseline import decode_baseline, assert_baseline_receipt
        from .transaction import validate_native_input_ref, Refused
        try:
            validate_native_input_ref(reference)
        except Refused:
            raise ValueError('invalid_native_input_reference') from None
        payload = self.resolve(reference[0], reference[1])
        try:
            if (type(payload) is not dict or canonical(payload)[1] != reference[1]
                    or set(payload) != ({'kind', 'schema', 'codec', 'baseline_digest'} |
                        ({'composition'} if payload.get('schema') == 2 else set()))
                    or payload['kind'] != 'native_input_baseline'
                    or type(payload['schema']) is not int or payload['schema'] not in (1, 2)
                    or payload['baseline_digest'] != reference[2]
                    or type(payload['codec']) is not str):
                raise ValueError()
            baseline = decode_baseline(base64.b64decode(payload['codec'], validate=True))
            if baseline.digest != reference[2]:
                raise ValueError()
            assert_baseline_receipt(baseline, original_receipt)
            if payload['schema'] == 2:
                from .native_template_composition import decode_composition
                decode_composition(payload['composition'], baseline)
            return baseline
        except (ValueError, TypeError, KeyError):
            raise ValueError('native_input_preparation_rejected') from None

    def resolve_composition(self, reference):
        """Strict private reopen; schema1 stays readable but cannot bind reverse.

        The composition is already pinned by Plan.native_inputs[1]. The caller
        still needs actual current IO/authorization; decoding grants no writer.
        """
        from .native_input_baseline import decode_baseline
        from .native_template_composition import decode_composition
        from .transaction import validate_native_input_ref
        validate_native_input_ref(reference)
        payload = self.resolve(reference[0], reference[1])
        try:
            if (type(payload) is not dict or canonical(payload)[1] != reference[1]
                    or set(payload) != {'kind', 'schema', 'codec', 'baseline_digest', 'composition'}
                    or payload['kind'] != 'native_input_baseline'
                    or type(payload['schema']) is not int or payload['schema'] != 2):
                raise ValueError()
            baseline = decode_baseline(base64.b64decode(payload['codec'], validate=True))
            if baseline.digest != reference[2] or payload['baseline_digest'] != reference[2]:
                raise ValueError()
            composition = decode_composition(payload['composition'], baseline)
            # Shared existing validation also checks exact envelope and receipt.
            self.resolve_native_inputs(reference, baseline.receipt)
            return baseline, composition
        except (ValueError, TypeError, KeyError):
            raise ValueError('native_input_composition_rejected') from None

    async def create(self, plan_id, payload):
        if type(plan_id) is not str or not _ID.fullmatch(plan_id):
            raise ValueError('invalid_preparation_id')
        payload, digest = canonical(payload)
        async with self.lock:
            self._ready()
            previous = self.plans.get(plan_id)
            if previous is not None:
                if previous != {'digest': digest, 'payload': payload}:
                    raise ValueError('immutable_preparation_conflict')
                return digest
            if len(self.plans) >= _MAX_PLANS:
                raise ValueError('preparation_retention_limit')
            updated = {**self.plans, plan_id: {'digest': digest, 'payload': payload}}
            try:
                await self.store.save({'schema': 1, 'plans': updated})
            except BaseException:
                # The file may contain the new plan. Recovery reopens it;
                # this runtime must never pretend its stale memory is current.
                self.plans = None
                raise
            self.plans = updated
            return digest


def native_prepared_store(hass):
    from homeassistant.helpers.storage import Store
    from .persistence import VerifiedStore
    key = 'ha_entity_renamer.prepared'
    writer = Store(hass, 1, key, private=True, minor_version=1, atomic_writes=True)
    return PreparedStore(VerifiedStore(hass.config.path('.storage', key), key,
        writer.async_save, hass.async_add_executor_job))


def encode_change(change):
    """Explicit codecs; never deserialize arbitrary classes from a client field."""
    from dataclasses import asdict
    from .native_registry import PreparedRegistryChange
    from .lovelace_native import PreparedDashboard
    from .energy_native import PreparedEnergy
    from .helper_transaction import PreparedHelper
    from .yaml_transaction import PreparedYAML
    kinds = {PreparedRegistryChange: 'registry', PreparedDashboard: 'dashboard', PreparedEnergy: 'energy', PreparedHelper: 'helper', PreparedYAML: 'yaml'}
    if type(change) is PreparedYAML:
        change.checked()
    if type(change) is PreparedHelper:
        change.plan()
    if type(change) not in kinds:
        raise ValueError('unsupported_prepared_change')
    return canonical(json.loads(json.dumps({'kind': kinds[type(change)],
        'data': asdict(change)}, allow_nan=False)))[0]


def decode_change(row):
    from .native_registry import PreparedRegistryChange
    from .lovelace_native import Dashboard, Snapshot, PreparedDashboard, revision
    from .energy_native import EnergySnapshot, PreparedEnergy
    if type(row) is not dict or set(row) != {'kind', 'data'} or type(row['data']) is not dict:
        raise ValueError('invalid_prepared_change')
    data = deepcopy(row['data'])
    if row['kind'] == 'yaml':
        from .yaml_transaction import decode_yaml
        return decode_yaml(data)
    if row['kind'] == 'registry':
        return PreparedRegistryChange(**data)
    if row['kind'] == 'helper':
        from .helper_transaction import PreparedHelper
        change = PreparedHelper(**data)
        change.plan()
        return change
    if row['kind'] not in ('dashboard', 'energy'):
        raise ValueError('unsupported_prepared_change')
    before = data.pop('before')
    identity = before['file_identity']
    if identity is not None:
        if (type(identity) is not list or len(identity) != 3
                or type(identity[0]) is not int or type(identity[1]) is not int
                or type(identity[2]) is not str):
            raise ValueError('invalid_prepared_file_identity')
        before['file_identity'] = tuple(identity)
    if row['kind'] == 'dashboard':
        before['dashboard'] = Dashboard(**before['dashboard'])
        snapshot = Snapshot(**before)
        if revision(snapshot.config) != snapshot.revision:
            raise ValueError('invalid_prepared_revision')
        change = PreparedDashboard(snapshot, **data)
    else:
        snapshot = EnergySnapshot(**before)
        if revision(snapshot.value) != snapshot.revision:
            raise ValueError('invalid_prepared_revision')
        change = PreparedEnergy(snapshot, **data)
    if revision(change.after) != change.after_revision:
        raise ValueError('invalid_prepared_revision')
    return change
