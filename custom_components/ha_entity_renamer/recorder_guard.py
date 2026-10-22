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


def _read(instance, ids: tuple[str, ...]) -> dict:
    """Read through HA's configured SQLAlchemy session on Recorder's executor."""
    from sqlalchemy import select
    from homeassistant.components.recorder.db_schema import StatesMeta, StatisticsMeta
    from homeassistant.components.recorder.util import session_scope

    result = {"states": {}, "statistics": {}}
    with session_scope(session=instance.get_session(), read_only=True) as session:
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


class NativeRecorderGuard:
    """No write method: registry events alone own HA's data migration."""

    def __init__(self, hass):
        from homeassistant.components.recorder import get_instance
        self.hass = hass
        self.instance = get_instance(hass)

    async def snapshot(self, mapping):
        # Registry callbacks must enqueue migration work before the commit barrier.
        await self.hass.async_block_till_done()
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
        ids = tuple(sorted(set(mapping) | set(mapping.values())))
        return await self.instance.async_add_executor_job(partial(_read, self.instance, ids))

    async def preflight(self, mapping):
        return prepare(mapping, await self.snapshot(mapping))

    async def readback(self, mapping, before):
        return verify(mapping, before, await self.snapshot(mapping))
