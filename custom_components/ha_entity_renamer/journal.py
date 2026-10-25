"""Local durable journal using the family's verified HA Store adapter."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import re

from .transaction import Plan, Record, Step

_OPERATION = re.compile(r'[a-zA-Z0-9_-]{1,80}\Z')
_PHASES = {'pending', 'intent', 'verified', 'undo_intent', 'undone'}
_STATUSES = {'prepared', 'applying', 'unknown', 'ready', 'applied', 'rolling_back', 'rollback_ready', 'rolled_back'}


def decode(value):
    """Reject a damaged journal instead of treating recovery state as empty."""
    if type(value) is not dict or set(value) != {'schema', 'operations'} or value['schema'] != 1:
        raise ValueError('invalid_journal')
    if type(value['operations']) is not dict or len(value['operations']) > 10000:
        raise ValueError('invalid_journal_operations')
    result = {}
    for key, raw in value['operations'].items():
        if type(key) is not str or not _OPERATION.fullmatch(key) or type(raw) is not dict:
            raise ValueError('invalid_journal_operation')
        data = dict(raw)
        plan_data = dict(data.pop('plan'))
        steps = []
        for raw_step in plan_data['steps']:
            step_data = dict(raw_step)
            causes = step_data.get('caused_by', [])
            if type(causes) is not list or any(type(key) is not str for key in causes):
                raise ValueError('invalid_journal_native_dependencies')
            step_data['caused_by'] = tuple(causes)
            steps.append(Step(**step_data))
        plan_data['steps'] = tuple(steps)
        if 'native_inputs' in plan_data:
            if type(plan_data['native_inputs']) is not list:
                raise ValueError('invalid_journal_native_inputs')
            plan_data['native_inputs'] = tuple(plan_data['native_inputs'])
        for field in ('required_scopes', 'covered_scopes', 'verification_adapters'):
            if type(plan_data[field]) is not list or any(type(item) is not str for item in plan_data[field]):
                raise ValueError('invalid_journal_plan')
            plan_data[field] = tuple(plan_data[field])
        plan = Plan(**plan_data)
        plan.validate()
        if type(data.get('phases')) is not list:
            raise ValueError('invalid_journal_phases')
        data['phases'] = tuple(data['phases'])
        record = Record(plan=plan, **data)
        if (record.operation_id != key or record.status not in _STATUSES
                or record.direction not in ('forward', 'rollback')
                or len(record.phases) != len(plan.steps) or not set(record.phases) <= _PHASES
                or type(record.reason) is not str):
            raise ValueError('invalid_journal_record')
        if record.status == 'applied' and (set(record.phases) != {'verified'} or record.direction != 'forward'):
            raise ValueError('invalid_applied_record')
        if record.status == 'rolled_back' and (not set(record.phases) <= {'pending', 'undone'} or record.direction != 'rollback'):
            raise ValueError('invalid_rollback_record')
        result[key] = record
    return result


class DurableJournal:
    """Singleton per HA runtime; never advances memory before disk readback."""

    def __init__(self, store):
        self.store = store
        self.records = None
        self.lock = asyncio.Lock()

    async def initialize(self):
        async with self.lock:
            if self.records is not None:
                raise ValueError('journal_already_initialized')
            payload, exists = await self.store.load()
            self.records = decode(payload) if exists else {}

    def _ready(self):
        if self.records is None:
            raise ValueError('journal_unavailable')

    async def load(self, operation_id):
        async with self.lock:
            self._ready()
            return self.records.get(operation_id)

    async def active(self):
        async with self.lock:
            self._ready()
            return tuple(record for record in self.records.values() if record.status not in ('applied', 'rolled_back'))

    async def _write(self, record, *, create):
        async with self.lock:
            self._ready()
            if type(record.operation_id) is not str or not _OPERATION.fullmatch(record.operation_id):
                raise ValueError('invalid_operation_id')
            previous = self.records.get(record.operation_id)
            if (create and previous is not None) or (not create and previous is None):
                raise ValueError('journal_operation_conflict')
            if previous is not None and previous.plan.fingerprint != record.plan.fingerprint:
                raise ValueError('journal_plan_conflict')
            updated = {**self.records, record.operation_id: record}
            payload = {'schema': 1, 'operations': {key: asdict(row) for key, row in updated.items()}}
            # Round-trip through the same decoder used after a restart.
            import json
            decoded = decode(json.loads(json.dumps(payload, allow_nan=False)))
            try:
                await self.store.save(payload)
            except BaseException:
                # The write may have landed. Force recovery from disk in a new
                # runtime, never return stale in-memory status or replay writes.
                self.records = None
                raise
            self.records = decoded

    async def create(self, record):
        await self._write(record, create=True)

    async def save(self, record):
        await self._write(record, create=False)


def native_journal(hass):
    from homeassistant.helpers.storage import Store
    from .persistence import VerifiedStore
    key = 'ha_entity_renamer.migrations'
    writer = Store(hass, 1, key, private=True, minor_version=1, atomic_writes=True)
    verified = VerifiedStore(hass.config.path('.storage', key), key,
                             writer.async_save, hass.async_add_executor_job)
    return DurableJournal(verified)
