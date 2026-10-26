"""Observe HA's own metadata migration, never rewrite or merge Recorder data.

This guard proves metadata identity continuity, not complete historical content
or a new statistics cycle. Those remain separate runtime acceptance checks.
"""
from __future__ import annotations

from copy import deepcopy
from functools import partial


class RecorderConflict(ValueError):
    """The rename cannot preserve known Recorder identities."""


def prepare(mapping: dict[str, str], snapshot: dict) -> dict:
    """A free registry ID may still belong to a different historical series."""
    if set(snapshot) != {"states", "statistics"}:
        raise RecorderConflict("recorder_snapshot_invalid")
    for old, new in mapping.items():
        if old == new:
            raise RecorderConflict("recorder_mapping_not_changed")
        if new in snapshot["states"] or new in snapshot["statistics"]:
            raise RecorderConflict("recorder_destination_occupied")
        statistic = snapshot["statistics"].get(old)
        if statistic and statistic.get("source") != "recorder":
            raise RecorderConflict("external_statistic_not_supported")
    return deepcopy(snapshot)


def verify(mapping: dict[str, str], before: dict, after: dict) -> bool:
    """Require the same metadata primary keys, properties and no old aliases."""
    if set(after) != {"states", "statistics"}:
        return False
    for old, new in mapping.items():
        for kind in ("states", "statistics"):
            if old in after[kind]:
                return False
            previous = before[kind].get(old)
            current = after[kind].get(new)
            if previous is not None:
                if current != previous:
                    return False
            elif current is not None:
                # A newly recorded series is legitimate, but we cannot claim
                # it is a migrated old series. Validate native ownership.
                if kind == "statistics" and current.get("source") != "recorder":
                    return False
    return True


def _read_session(session, ids):
    from sqlalchemy import select
    from homeassistant.components.recorder.db_schema import StatesMeta, StatisticsMeta
    result = {"states": {}, "statistics": {}}
    for model, kind, identifier in ((StatesMeta, "states", "entity_id"),
                                    (StatisticsMeta, "statistics", "statistic_id")):
        rows = session.execute(select(model).where(getattr(model, identifier).in_(ids))).scalars()
        for row in rows:
            key = getattr(row, identifier)
            if key in result[kind]:
                raise RecorderConflict("recorder_ambiguous_metadata")
            result[kind][key] = {
                column.name: getattr(row, column.name)
                for column in model.__table__.columns if column.name != identifier
            }
    return result


def _read(instance, ids: tuple[str, ...]) -> dict:
    from homeassistant.components.recorder.util import session_scope
    with session_scope(session=instance.get_session(), read_only=True) as session:
        return _read_session(session, ids)


class NativeRecorderGuard:
    """No write method: registry events alone own HA's data migration."""

    def __init__(self, hass):
        from homeassistant.components.recorder import get_instance
        self.hass = hass
        self.instance = get_instance(hass)

    async def async_synchronize(self):
        # Native commit-before barrier only. Never await HA's global tracked
        # task set while a final reader holds the Renamer operation lock.
        # async_get_commit_future has an empty-queue fast path: a worker may
        # already have dequeued an event without marking its writes pending.
        # Always enqueue HA's own synchronization task behind prior events.
        # This performs no SQL mutation of our own; native Recorder owns commit.
        import asyncio
        from homeassistant.components.recorder.tasks import SynchronizeTask
        if not self.instance.is_alive():
            raise RecorderConflict("recorder_not_running")
        future = self.hass.loop.create_future()
        self.instance.queue_task(SynchronizeTask(future))
        await asyncio.wait_for(asyncio.shield(future), timeout=60)

    async def snapshot(self, mapping):
        # Preserve existing synchronization for the original snapshot consumers.
        # Registry callbacks must enqueue migration work before the barrier.
        await self.hass.async_block_till_done()
        await self.async_synchronize()
        ids = tuple(sorted(set(mapping) | set(mapping.values())))
        return await self.instance.async_add_executor_job(partial(_read, self.instance, ids))

    async def preflight(self, mapping):
        return prepare(mapping, await self.snapshot(mapping))

    async def readback(self, mapping, before):
        return verify(mapping, before, await self.snapshot(mapping))


async def async_final_comparison(guard, mapping, metadata, historical, physical):
    """Last read job: physical graph around finite SQL; no Recorder queue hold.

    A native synchronization barrier precedes the job. Recorder keeps processing
    its own queue while the read-only executor session runs. No future/loop wait
    or native mutation runs inside this job. This is point-in-time evidence,
    never DB/filesystem atomicity or a permission to mutate later.
    """
    from .history_guard import _read_session as read_history
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.util import session_scope
    if get_instance(guard.hass) is not guard.instance:
        raise RecorderConflict('recorder_instance_changed')
    await guard.async_synchronize()
    ids = tuple(sorted(set(mapping) | set(mapping.values())))
    def compare():
        physical()
        with session_scope(session=guard.instance.get_session(), read_only=True) as session:
            content = read_history(session, historical['state_ids'],
                                   historical['statistic_ids'], historical['content'])
            # Metadata is the last SQL read, after every finite content query.
            current = _read_session(session, ids)
        physical()
        return current == metadata and content == historical['content']
    import asyncio
    task = asyncio.ensure_future(guard.instance.async_add_executor_job(compare))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    if get_instance(guard.hass) is not guard.instance:
        raise RecorderConflict('recorder_instance_changed')
    return result
