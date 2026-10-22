"""Private immutable server preparations for migration and crash recovery.

Only trusted backend planners create these payloads. Clients receive IDs/digests,
never arbitrary config patches or local filesystem paths. Retention must preserve
all preparations referenced by an unresolved journal operation.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
import re

_ID = re.compile(r'[A-Za-z0-9_-]{1,80}\Z')
_MAX_PLANS = 1000
_MAX_BYTES = 60 * 1024 * 1024


def canonical(value):
    """Bound a detached JSON tree before serialization/hashing/storage."""
    from .reference_planner import Context, Consumer, plan_references
    checked = plan_references(value, {}, Context(Consumer.AUTOMATION, ())).value
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
