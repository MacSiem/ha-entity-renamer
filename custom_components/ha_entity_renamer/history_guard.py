"""Read-only historical continuity fingerprints through native Recorder sessions.

New points beyond captured primary-key ceilings are allowed. Existing values,
change timestamps, attributes and both statistics tables must stay exact.
HA legitimately advances last_reported_ts on the last unchanged state when the
entity reports again (including rename); this heartbeat field is not fingerprinted.
Concurrent retention/purge is a conflict, never silently accepted as migration.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json


class HistoryConflict(ValueError):
    pass


def _encoded(value):
    def special(item):
        if isinstance(item, datetime):
            return {'datetime': item.isoformat()}
        if isinstance(item, (bytes, bytearray, memoryview)):
            return {'bytes': bytes(item).hex()}
        raise HistoryConflict('unsupported_history_value')
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(',', ':'),
                      default=special).encode()


def _read(recorder, state_ids, statistic_ids, expected=None):
    from sqlalchemy import select, func
    from homeassistant.components.recorder.db_schema import (
        States, StateAttributes, Statistics, StatisticsShortTerm)
    from homeassistant.components.recorder.util import session_scope

    definitions = (('states', States, States.state_id, States.metadata_id, state_ids),
                   ('short_term', StatisticsShortTerm, StatisticsShortTerm.id,
                    StatisticsShortTerm.metadata_id, statistic_ids),
                   ('hourly', Statistics, Statistics.id, Statistics.metadata_id, statistic_ids))
    result = {}
    with session_scope(session=recorder.get_session(), read_only=True) as session:
        for name, model, primary, metadata, ids in definitions:
            ceiling = (expected[name]['ceiling'] if expected is not None else
                       session.scalar(select(func.max(primary)).where(metadata.in_(ids))))
            count, digest = 0, hashlib.sha256()
            if ceiling is not None:
                columns = [column for column in model.__table__.columns
                           if not (name == 'states' and column.name == 'last_reported_ts')]
                query = select(*columns).where(
                    metadata.in_(ids), primary <= ceiling).order_by(primary)
                for row in session.execute(query.execution_options(yield_per=1000)):
                    raw = _encoded(tuple(row))
                    digest.update(len(raw).to_bytes(8, 'big'))
                    digest.update(raw)
                    count += 1
            result[name] = {'ceiling': ceiling, 'count': count, 'sha256': digest.hexdigest()}
        ceiling = result['states']['ceiling']
        count, digest = 0, hashlib.sha256()
        if ceiling is not None:
            query = select(*StateAttributes.__table__.columns).where(
                StateAttributes.attributes_id.in_(select(States.attributes_id).where(
                    States.metadata_id.in_(state_ids), States.state_id <= ceiling))
            ).order_by(StateAttributes.attributes_id)
            for row in session.execute(query.execution_options(yield_per=1000)):
                raw = _encoded(tuple(row))
                digest.update(len(raw).to_bytes(8, 'big'))
                digest.update(raw)
                count += 1
        result['attributes'] = {'count': count, 'sha256': digest.hexdigest()}
    return result


class NativeHistoryGuard:
    def __init__(self, recorder_guard):
        self.guard = recorder_guard
        self.recorder = recorder_guard.instance

    async def capture(self, mapping):
        metadata = await self.guard.snapshot(mapping)
        states = sorted({row['metadata_id'] for row in metadata['states'].values()})
        statistics = sorted({row['id'] for row in metadata['statistics'].values()})
        content = await self.recorder.async_add_executor_job(_read, self.recorder, states, statistics)
        if await self.guard.snapshot(mapping) != metadata:
            raise HistoryConflict('history_metadata_changed_during_capture')
        return {'schema': 1, 'state_ids': states, 'statistic_ids': statistics, 'content': content}

    async def verify(self, snapshot):
        if (type(snapshot) is not dict or set(snapshot) != {'schema', 'state_ids', 'statistic_ids', 'content'}
                or snapshot['schema'] != 1):
            raise HistoryConflict('invalid_history_snapshot')
        for key in ('state_ids', 'statistic_ids'):
            ids = snapshot[key]
            if (type(ids) is not list or len(ids) > 10000
                    or any(type(value) is not int or value < 1 for value in ids)
                    or sorted(set(ids)) != ids):
                raise HistoryConflict('invalid_history_identity')
        expected = snapshot['content']
        if type(expected) is not dict or set(expected) != {'states', 'short_term', 'hourly', 'attributes'}:
            raise HistoryConflict('invalid_history_content')
        for name, row in expected.items():
            fields = {'count', 'sha256'} | ({'ceiling'} if name != 'attributes' else set())
            if (type(row) is not dict or set(row) != fields or type(row['count']) is not int
                    or row['count'] < 0 or type(row['sha256']) is not str
                    or len(row['sha256']) != 64 or any(c not in '0123456789abcdef' for c in row['sha256'])):
                raise HistoryConflict('invalid_history_content')
            if name != 'attributes' and row['ceiling'] is not None:
                if type(row['ceiling']) is not int or row['ceiling'] < 1:
                    raise HistoryConflict('invalid_history_ceiling')
        await self.guard.snapshot({})  # Native Recorder commit barrier, no SQL writes.
        current = await self.recorder.async_add_executor_job(
            _read, self.recorder, snapshot['state_ids'], snapshot['statistic_ids'], expected)
        return current == expected
