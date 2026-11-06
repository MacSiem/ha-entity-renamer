"""Guarded native registry writes; no direct storage or Recorder writes.

HA-specific dependencies are lazy. Recovery inspection never flushes or replays.
A report is scoped evidence, never proof of complete historical continuity.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Optional


class RegistryConflict(ValueError):
    """Prepared state no longer matches live state; do not retry an effect."""


def _content(row):
    result = deepcopy(row)
    # HA changes only this audit timestamp in addition to requested fields.
    result.pop("modified_at", None)
    return result


def revision(row):
    return hashlib.sha256(json.dumps(_content(row), sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class PreparedRegistryChange:
    kind: str
    identity: str
    before_json: str
    after_json: str
    recorder_before_json: str = ""
    history_before_json: str = ""
    runtime_before_json: str = ""

    @property
    def before(self):
        return json.loads(self.before_json)

    @property
    def after(self):
        return json.loads(self.after_json)

    @property
    def before_revision(self):
        return revision(self.before)

    @property
    def after_revision(self):
        return revision(self.after)

    @property
    def mapping(self):
        if self.kind == "entity" and self.before["entity_id"] != self.after["entity_id"]:
            return {self.before["entity_id"]: self.after["entity_id"]}
        return {}


@dataclass(frozen=True)
class RegistryEvidence:
    position: str  # before / after / conflict / unknown
    identity: Optional[bool]
    runtime: Optional[bool]
    durability: Optional[bool]
    recorder_metadata: Optional[bool]
    complete_history: Optional[bool] = None
    reason: str = ""


@dataclass(frozen=True)
class NativeRegistryMutation:
    """Private synchronous native-call rows, including the exact audit values.

    Captured from the source entry and the entry returned by HA's single update,
    before yielding to any other loop job. Not a durable transition receipt or
    authorization: caller must bind its intent, full documents and readback.
    Never emit these private row bytes in status responses or telemetry.
    """
    kind: str
    identity: str
    before_json: str
    after_json: str


def _row(document, kind, identity):
    rows = [row for row in document.get("entities" if kind == "entity" else "devices", [])
            if row.get("id") == identity]
    if len(rows) != 1:
        raise RegistryConflict("identity_missing_or_ambiguous")
    return rows[0]


class NativeRegistryIO:
    """Narrow compatibility seam verified against HA 2025.6 / 2026.10.

    BaseRegistry has no flush API. Use its Store.async_save(_data_to_save()).
    Store swallows write errors; strictly read and fsync its actual file.
    Never use Store.async_load for evidence: it may return pending/cache data
    and can migrate or quarantine corrupt storage as a side effect.
    """

    def __init__(self, hass):
        from homeassistant.helpers import entity_registry, device_registry
        from homeassistant.const import __version__
        if __version__ not in ('2025.6.0', '2026.10.2', '2026.10.4'):
            raise RegistryConflict('unsupported_registry_runtime')
        self.hass = hass
        self.registries = {"entity": entity_registry.async_get(hass),
                           "device": device_registry.async_get(hass)}

    def running(self):
        from homeassistant.core import CoreState
        return self.hass.state is CoreState.running

    def document(self, kind):
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        # Decode native fragments/datetime/set exactly as HA does when saving.
        return json.loads(_orjson_bytes_default_encoder(
            self.registries[kind]._data_to_save()))

    def pending(self, kind):
        store = self.registries[kind]._store
        return (store._data is not None or store._write_lock.locked()
                or store._delay_handle is not None)

    async def settle(self, prepared=None):
        """Await native source registry processing, including its awaited re-add.

        The pinned callback schedules this task eagerly during the synchronous
        registry event dispatch. A completed callback needs no task fence. A
        suspended callback retains its exact code and source object in its
        coroutine chain while async_remove/async_add_entities and their awaited
        native hooks run. Await that actual task, never unrelated HA work.

        No-argument callers fence native registry callbacks only. Bridge callers
        additionally bind the immutable registry UUID(s) of their preparation.
        This is not persistence or arbitrary integration-background completion:
        Store save/raw disk and the independent runtime/source probes still run.
        """
        import asyncio
        from pathlib import Path
        import homeassistant
        from homeassistant.const import __version__
        from homeassistant.helpers.entity import Entity
        from homeassistant.helpers.entity_platform import EntityPlatform
        from .native_unload_code_proof import prove_methods, unchanged
        from .native_utility_setup_audit import _SOURCES

        refs = [(Entity, '_async_registry_updated'),
                (Entity, '_async_process_registry_update_or_remove'),
                (Entity, 'async_remove'), (Entity, 'add_to_platform_finish'),
                (EntityPlatform, 'async_add_entities'),
                (EntityPlatform, '_async_add_entities'),
                (EntityPlatform, '_async_add_entity')]
        if __version__ in ('2026.10.2', '2026.10.4'):
            refs.append((EntityPlatform, '_async_add_entity_impl'))
        methods, codes = prove_methods(Path(homeassistant.__file__).resolve().parent,
                                       _SOURCES[__version__], refs)
        identities = None
        if prepared is not None:
            identities = ({prepared.identity} if prepared.kind == 'entity' else
                          {row['id'] for row in self.document('entity')['entities']
                           if row.get('device_id') == prepared.identity})
        current = asyncio.current_task()
        observed = set()
        while True:
            unchanged(refs, methods, codes)
            pending = set()
            for task in asyncio.all_tasks(self.hass.loop):
                if task is current or task.done():
                    continue
                node, seen = task.get_coro(), set()
                while node is not None and id(node) not in seen:
                    seen.add(id(node))
                    frame = getattr(node, 'cr_frame', None) or getattr(node, 'gi_frame', None)
                    if frame is not None and frame.f_code is codes[1]:
                        source = frame.f_locals.get('self')
                        row = getattr(source, 'registry_entry', None)
                        if (getattr(source, 'hass', None) is self.hass and row is not None
                                and (identities is None or row.id in identities)):
                            bound = source._async_process_registry_update_or_remove
                            if getattr(bound, '__func__', None) is not methods[1]:
                                raise RegistryConflict('native_registry_callback_binding_changed')
                            pending.add(task)
                            observed.add(source)
                            break
                    node = (getattr(node, 'cr_await', None)
                            or getattr(node, 'gi_yieldfrom', None)
                            or getattr(node, 'ag_await', None))
            if not pending:
                # No await separates this empty scoped scan from source readback.
                # Failed native add can be logged/swallowed by EntityPlatform.
                for source in observed:
                    row = source.registry_entry
                    if (row is None or self.registries['entity'].async_get(row.entity_id) is not row
                            or (not row.disabled and
                                (source.entity_id != row.entity_id
                                 or source.platform.entities.get(row.entity_id) is not source
                                 or self.hass.states.get(row.entity_id) is None))):
                        raise RegistryConflict('native_registry_source_readback_failed')
                return
            # Cancellation of our inspection must not cancel a native mutation.
            # Each task awaits its natural removal/addition descendants itself.
            await asyncio.gather(*(asyncio.shield(task) for task in pending))

    def runtime(self, prepared, target, *, expected_documents=None):
        if prepared.kind == "device" or not prepared.mapping:
            from .native_name_observers import NativeNameObserver
            return NativeNameObserver(self).verify(prepared, target,
                                                  expected_documents=expected_documents)
        old, new = prepared.before["entity_id"], prepared.after["entity_id"]
        other = new if target["entity_id"] == old else old
        if self.hass.states.get(other) is not None:
            return False
        state = self.hass.states.get(target["entity_id"])
        present = (state is None) if target.get("disabled_by") else (state is not None)
        if present and prepared.before.get('name') != prepared.after.get('name'):
            from .native_registry_transition import (_native_script_default_name_move,
                                                     _native_script_unique_move)
            if (_native_script_unique_move(prepared.before, prepared.after)
                    and _native_script_default_name_move(prepared.before, prepared.after)):
                # The explicit registry name preserves the original visible
                # friendly name while the default source name tracks the key.
                wanted = prepared.before.get('name') or prepared.before['unique_id']
                return state is not None and state.attributes.get('friendly_name') == wanted
            from .native_name_observers import NativeNameObserver
            return NativeNameObserver(self).verify(prepared, target,
                                                   expected_documents=expected_documents)
        return present

    def capture_runtime(self, prepared):
        if (prepared.kind == "device" or not prepared.mapping
                or prepared.before.get('name') != prepared.after.get('name')):
            from .native_name_observers import NativeNameObserver
            return NativeNameObserver(self).capture(prepared)
        return ""

    def mutate(self, prepared, target):
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        registry = self.registries[prepared.kind]
        source = prepared.after if target == prepared.before else prepared.before
        previous = registry.async_get(source['entity_id'] if prepared.kind == 'entity'
                                      else prepared.identity)
        if previous is None or previous.id != prepared.identity:
            raise RegistryConflict('native_mutation_source_missing')
        before_json = _orjson_bytes_default_encoder(previous.as_storage_fragment).decode('utf-8')
        if prepared.kind == "entity":
            kwargs = {"name": target["name"]}
            if source["entity_id"] != target["entity_id"]:
                kwargs["new_entity_id"] = target["entity_id"]
            if source.get('unique_id') != target.get('unique_id'):
                from .native_own_script_service_move import NativeOwnScriptServiceMove
                from .controlled_script_cutover import ControlledScriptCutover
                from .native_registry_transition import _native_script_unique_move
                capability = getattr(self, '_script_service_capability', None)
                if (type(capability) not in (NativeOwnScriptServiceMove, ControlledScriptCutover)
                        or not _native_script_unique_move(prepared.before, prepared.after)):
                    raise RegistryConflict('native_unique_change_capability_required')
                capability.authorize_registry(self, prepared, target)
                kwargs['new_unique_id'] = target['unique_id']
                if source.get('original_name') != target.get('original_name'):
                    from .native_registry_transition import _native_script_default_name_move
                    if not _native_script_default_name_move(prepared.before, prepared.after):
                        raise RegistryConflict('native_default_name_change_unqualified')
                    kwargs['original_name'] = target['original_name']
            # Core 2026's public wrapper omits suggested_object_id. The exact
            # owned Script capability admits its pinned native writer once.
            from .native_registry_transition import _native_script_suggested_move, _native_script_unique_move
            if (_native_script_unique_move(prepared.before, prepared.after)
                    and _native_script_suggested_move(prepared.before, prepared.after)):
                from homeassistant.const import __version__
                if (__version__ not in ('2026.10.2', '2026.10.4')
                        or type(getattr(self, '_script_service_capability', None)) not in (NativeOwnScriptServiceMove, ControlledScriptCutover)):
                    raise RegistryConflict('native_suggested_change_capability_required')
                kwargs['suggested_object_id'] = target['suggested_object_id']
                updated = registry._async_update_entity(source['entity_id'], **kwargs)
            else:
                from .native_registry_transition import _powercalc_group_suggested_move
                if _powercalc_group_suggested_move(prepared.before, prepared.after):
                    from .powercalc_group_own_id import PowercalcGroupOwnID
                    from .controlled_powercalc_registry import ControlledPowercalcRegistryNode
                    capability = getattr(self, '_powercalc_group_capability', None)
                    if type(capability) not in (PowercalcGroupOwnID, ControlledPowercalcRegistryNode):
                        raise RegistryConflict('powercalc_group_suggested_capability_required')
                    capability.authorize_suggested(self, prepared, target)
                    kwargs['suggested_object_id'] = target['suggested_object_id']
                    updated = registry._async_update_entity(source['entity_id'], **kwargs)
                else:
                    updated = registry.async_update_entity(source["entity_id"], **kwargs)
        else:
            updated = registry.async_update_device(prepared.identity,
                                         name_by_user=target["name_by_user"])
        if updated is None or updated.id != prepared.identity:
            raise RegistryConflict('native_mutation_return_unconfirmed')
        after_json = _orjson_bytes_default_encoder(updated.as_storage_fragment).decode('utf-8')
        stamp = NativeRegistryMutation(prepared.kind, prepared.identity, before_json, after_json)
        if prepared.kind == 'device' or not prepared.mapping:
            # An unloaded config entry can retain HA's own restored unavailable
            # placeholder. Registry mutation alone does not refresh its name.
            # Rebuild only that verified native placeholder, never a live value.
            from .native_name_observers import NativeNameObserver
            observer = NativeNameObserver(self)
            baseline = json.loads(prepared.runtime_before_json)
            for row in observer._rows(prepared):
                saved = baseline['entities'][row.id]
                if saved['active'] or saved['inactive_state'] is None:
                    continue
                _, state, entry, active = observer._sample(row)
                from .helper_observers import require
                require(not active and state is not None
                        and state.state == saved['inactive_state']
                        and {k: v for k, v in state.attributes.items() if k != 'friendly_name'}
                            == saved['inactive_attributes'], 'name_inactive_refresh_conflict')
                row.write_unavailable_state(self.hass)
        return stamp

    async def flush(self, kind):
        registry = self.registries[kind]
        # Do not queue behind an earlier write with an unguarded stale payload.
        if registry._store._write_lock.locked():
            raise RegistryConflict("native_write_in_flight")
        await registry._store.async_save(registry._data_to_save())

    async def disk(self, kind):
        from pathlib import Path
        from .lovelace_native import durable_snapshot
        store = self.registries[kind]._store
        data, identity = await self.hass.async_add_executor_job(
            durable_snapshot, Path(store.path), store.key, store.version, store.minor_version)
        if identity is None:
            raise RegistryConflict('native_storage_missing')
        return data



class NativeRegistryBridge:
    """ONE shared coordinator lock must surround preparation/apply/recovery.

    recorder implements existing NativeRecorderGuard (read-only). Runtime probe
    may be injected for richer integration evidence; None remains UNKNOWN.
    Native device/name-only writes capture a durable runtime baseline; missing
    legacy baselines remain UNKNOWN. Injected probes remain a trusted IO seam.
    """

    def __init__(self, io, recorder, runtime_probe=None, *, history_guard=None,
                 native_images=None, automation_transition=None):
        self.io = io
        self.automation_transition = automation_transition
        self.recorder = recorder
        # Optional trusted immutable chain expectations allow preceding OWN
        # related registry steps; never derive this callback from current IO.
        self.runtime_probe = runtime_probe or (
            (lambda change, target: io.runtime(change, target,
                 expected_documents=native_images())) if native_images is not None else io.runtime)
        self.history_guard = history_guard
        self.native_images = native_images
        self._mutation_node = None

    def _matches(self, change, actual, expected):
        if _content(actual) == _content(expected):
            return True
        # Core records the preceding Script unique ID on the reverse call.
        # Accept that non-ORIGINAL field only with this live effect's exact
        # service/row witness or an already authenticated native receipt.
        from .native_registry_transition import _native_script_unique_move
        if (not _native_script_unique_move(change.before, change.after)
                or expected != change.before):
            return False
        directional = dict(expected, previous_unique_id=change.after['unique_id'])
        if _content(actual) != _content(directional):
            return False
        node = self._mutation_node
        if node is not None:
            from .native_template_state_registry_effect import NativeTemplateStateRegistryEffect
            if (type(node) is NativeTemplateStateRegistryEffect and node.change == change
                    and node.target == expected['entity_id'] and node.script_cutover is not None):
                from .controlled_script_cutover import ControlledScriptCutover
                cutover = node.script_cutover
                if (type(cutover) is ControlledScriptCutover and cutover.node is node
                        and cutover.phase == 'added' and cutover.mutation is not None
                        and cutover.mutation.identity == change.identity
                        and cutover.root.chain.active is node.intent
                        and json.loads(cutover.mutation.after_json) == actual
                        and cutover.target_entity.registry_entry is self.io.registries['entity'].async_get(node.target)):
                    cutover._scope()
                    cutover._foreign()
                    return True
            if (type(node) is NativeTemplateStateRegistryEffect and node.change == change
                    and node.target == expected['entity_id'] and node.script_service_move is not None):
                node.script_service_move.check_moved()
                handoff = node.script_service_move.handoff
                if handoff.new_row is self.io.registries['entity'].async_get(node.target):
                    return True
        if self.native_images is not None:
            docs = self.native_images()
            rows = docs['core.entity_registry']['data']['entities']
            return sum(row.get('id') == change.identity and _content(row) == _content(actual)
                       for row in rows) == 1
        return False

    def _history(self):
        if self.history_guard is None:
            from .history_guard import NativeHistoryGuard
            self.history_guard = NativeHistoryGuard(self.recorder)
        return self.history_guard

    def _live(self, change):
        return _row(self.io.document(change.kind), change.kind, change.identity)

    def _fresh(self, change, expected, guard):
        guard()
        if not self.io.running():
            raise RegistryConflict("home_assistant_not_running")
        document = self.io.document(change.kind)
        if not self._matches(change, _row(document, change.kind, change.identity), expected):
            raise RegistryConflict("registry_revision_changed")
        if change.kind == "entity":
            if change.mapping and expected.get('device_id') is not None:
                from .native_name_observers import NativeNameObserver
                NativeNameObserver(self.io).verify_readd_device(change)
            destination = change.after if expected == change.before else change.before
            if destination["entity_id"] != expected["entity_id"]:
                if any(row.get("entity_id") == destination["entity_id"]
                       for row in document["entities"]):
                    raise RegistryConflict("registry_destination_occupied")
            # A native Script key move also changes unique_id. The minimum
            # Core removes the old entity key *before* it checks this unique
            # collision, so recheck the actual native index at the final
            # synchronous fence. This is a refusal only, never authority to
            # perform the otherwise separately guarded service/registry effect.
            if (destination.get('platform') == 'script'
                    and destination.get('unique_id') != expected.get('unique_id')):
                from .native_registry_transition import _native_script_unique_move
                if not _native_script_unique_move(change.before, change.after):
                    raise RegistryConflict('registry_unique_change_unqualified')
                if (type(self.io) is not NativeRegistryIO
                        or self.io.registries['entity'].async_get_entity_id(
                            'script', 'script', destination['unique_id']) is not None):
                    raise RegistryConflict('registry_unique_destination_occupied')

    async def prepare(self, kind, identity, updates, guard):
        guard()
        if kind not in ("entity", "device") or not self.io.running():
            raise RegistryConflict("unsupported_registry_or_lifecycle")
        before = _row(self.io.document(kind), kind, identity)
        allowed = {"entity_id", "name"} if kind == "entity" else {"name_by_user"}
        if not updates or not set(updates) <= allowed:
            raise RegistryConflict("unsupported_native_update")
        if any(value is not None and not isinstance(value, str) for value in updates.values()):
            raise RegistryConflict("invalid_native_value")
        after = dict(before, **updates)
        if kind == "entity":
            from re import fullmatch
            target = after.get("entity_id")
            if (not isinstance(target, str) or not fullmatch(r"[a-z_][a-z0-9_]*\.[a-z0-9_]+", target)
                    or target.split(".")[0] != before["entity_id"].split(".")[0]):
                raise RegistryConflict("invalid_entity_id")
        if _content(before) == _content(after):
            raise RegistryConflict("unchanged_registry_patch")
        change = PreparedRegistryChange(kind, identity, json.dumps(before, sort_keys=True),
                                        json.dumps(after, sort_keys=True))
        self._fresh(change, before, guard)
        recorder_before, history_before = {}, {}
        if change.mapping:
            recorder_before = await self.recorder.preflight(change.mapping)
            self._fresh(change, before, guard)  # Guard and revision AFTER await.
            history_before = await self._history().capture(change.mapping)
            self._fresh(change, before, guard)
        from dataclasses import replace
        change = replace(change, recorder_before_json=json.dumps(recorder_before, sort_keys=True),
                         history_before_json=json.dumps(history_before, sort_keys=True))
        capture = getattr(self.io, "capture_runtime", None)
        if capture is not None:
            await self.io.settle(change)
            self._fresh(change, before, guard)
            change = replace(change, runtime_before_json=capture(change))
        evidence = await self.inspect(change)
        self._fresh(change, before, guard)
        if evidence.position != "before":
            raise RegistryConflict("before_state_unverified:" + evidence.reason)
        return change

    async def inspect(self, change):
        """Read-only recovery: BEFORE / AFTER / CONFLICT / UNKNOWN; no retry."""
        try:
            await self.io.settle(change)  # Deliver event listeners BEFORE Recorder barrier.
            recorder = await self.recorder.snapshot(change.mapping) if change.mapping else None
            history = (await self._history().verify(json.loads(change.history_before_json))
                       if change.mapping else None)
            try:
                live = self._live(change)
            except RegistryConflict:
                return RegistryEvidence("conflict", False, None, None, None,
                                        reason="registry_identity_missing_or_ambiguous")
            before, after = change.before, change.after
            if self._matches(change, live, before):
                position, target = "before", before
            elif self._matches(change, live, after):
                position, target = "after", after
            else:
                return RegistryEvidence("conflict", live.get("id") == change.identity,
                                        None, None, None, reason="registry_revision_conflict")
            if change.kind == "entity":
                other = after if position == "before" else before
                if other["entity_id"] != target["entity_id"] and any(
                        row.get("entity_id") == other["entity_id"]
                        for row in self.io.document("entity")["entities"]):
                    return RegistryEvidence("conflict", True, None, None, None,
                                            reason="registry_destination_occupied")
            runtime = self.runtime_probe(change, target)
            disk = await self.io.disk(change.kind)
            # Re-read after disk await; never combine evidence from different revisions.
            if self._live(change) != live or not self.io.running():
                return RegistryEvidence("unknown", None, None, None, None,
                                        reason="live_state_changed_during_readback")
            durable = self._matches(change, _row(disk, change.kind, change.identity), target)
            metadata = True
            if change.mapping:
                baseline = json.loads(change.recorder_before_json)
                from .recorder_guard import verify, verify_rollback
                metadata = (verify_rollback(change.mapping, baseline, recorder)
                            if position == "before" else
                            verify(change.mapping, baseline, recorder))
            runtime_after = self.runtime_probe(change, target)
            runtime = (runtime and runtime_after) if runtime is not None else None
            if self.io.pending(change.kind):
                return RegistryEvidence("unknown", True, runtime, durable, metadata,
                                        reason="native_persistence_pending")
            if (durable is not True or runtime is not True or metadata is not True
                    or (change.mapping and history is not True)):
                return RegistryEvidence("unknown", True, runtime, durable, metadata, history,
                                        reason="scoped_evidence_incomplete")
            return RegistryEvidence(position, True, True, True, True, history)
        except Exception:
            # Preserve cancellation on Python 3.9+ (BaseException).
            return RegistryEvidence("unknown", None, None, None, None,
                                    reason="native_readback_failed")

    async def apply(self, change, guard, *, before_mutation=None):
        return await self._write(change, change.before, change.after, guard,
                                 before_mutation=before_mutation)

    async def compensate(self, change, guard, *, before_mutation=None):
        return await self._write(change, change.after, change.before, guard,
                                 before_mutation=before_mutation)

    async def _write(self, change, source, target, guard, *, before_mutation=None):
        evidence = await self.inspect(change)
        self._fresh(change, source, guard)
        expected = "before" if source == change.before else "after"
        if evidence.position != expected:
            raise RegistryConflict("inspect_before_write:" + evidence.reason)
        node = None
        if before_mutation is not None:
            # Trusted whole-input/intent consumer runs AFTER the final async
            # runtime inspection. No await separates its final fence from API.
            node = await before_mutation()
            try:
                self._fresh(change, source, guard)
            except BaseException:
                from .native_template_state_registry_effect import NativeTemplateStateRegistryEffect
                if type(node) is NativeTemplateStateRegistryEffect:
                    cutover = getattr(node, 'script_cutover', None)
                    if cutover is None or cutover.phase == 'captured':
                        node.abort_before_attempt()
                elif type(node).__module__ == __package__ + '.controlled_powercalc_registry':
                    from .controlled_powercalc_registry import ControlledPowercalcRegistryNode
                    if type(node) is ControlledPowercalcRegistryNode:
                        node.abort_before_attempt()
                elif type(node).__module__ == __package__ + '.powercalc_group_own_id':
                    from .powercalc_group_own_id import PowercalcGroupOwnID
                    if type(node) is PowercalcGroupOwnID:
                        node.abort_before_attempt()
                elif type(node).__module__ == __package__ + '.powercalc_source_transition':
                    from .powercalc_source_transition import PowercalcSourceTransition
                    if type(node) is PowercalcSourceTransition:
                        node.abort_before_attempt()
                raise
        readd = None
        transition = self.automation_transition
        if (transition is not None and change.kind == 'entity' and change.mapping
                and change.before.get('platform') == 'automation'):
            try:
                # Follows the durable intent/final source fence without an await.
                readd = transition.begin_registry_readd(source['entity_id'],
                    target['entity_id'], change.identity)
            except BaseException:
                from .native_template_state_registry_effect import NativeTemplateStateRegistryEffect
                if type(node) is NativeTemplateStateRegistryEffect:
                    node.abort_before_attempt()
                raise
        try:
            # No await between fresh authorization/revision and one native mutation.
            if node is None:
                stamp = self.io.mutate(change, target)
            else:
                from .native_helper_placeholder_effect import OwnHelperPlaceholder
                from .native_template_state_registry_effect import NativeTemplateStateRegistryEffect
                accepted = (OwnHelperPlaceholder, NativeTemplateStateRegistryEffect)
                if type(node).__module__ == __package__ + '.controlled_powercalc_registry':
                    from .controlled_powercalc_registry import ControlledPowercalcRegistryNode
                    accepted += (ControlledPowercalcRegistryNode,)
                if type(node).__module__ == __package__ + '.powercalc_group_own_id':
                    from .powercalc_group_own_id import PowercalcGroupOwnID
                    accepted += (PowercalcGroupOwnID,)
                if type(node).__module__ == __package__ + '.powercalc_source_transition':
                    from .powercalc_source_transition import PowercalcSourceTransition
                    accepted += (PowercalcSourceTransition,)
                if (type(node) not in accepted or node.io is not self.io
                        or node.change != change or node.source != source['entity_id']
                        or node.target != target['entity_id']):
                    raise RegistryConflict('own_helper_effect_binding_mismatch')
                try:
                    stamp = node.mutate()
                except BaseException:
                    if type(node) is not OwnHelperPlaceholder:
                        await node.finish_failed_attempt()
                    raise
                if type(node) is not OwnHelperPlaceholder:
                    self._mutation_node = node
                    try:
                        await node.finish_native()
                    except BaseException:
                        self._mutation_node = None
                        raise
            try:
                await self.io.settle(change)
                if readd is not None:
                    await transition.finish_registry_readd(readd)
                    readd = None
                self._fresh(change, target, guard)
                await self.io.flush(change.kind)
                self._fresh(change, target, guard)
                evidence = await self.inspect(change)
                self._fresh(change, target, guard)
                if evidence.position != ("after" if expected == "before" else "before"):
                    raise RegistryConflict("effect_uncertain:" + evidence.reason)
                return stamp
            finally:
                self._mutation_node = None
        except BaseException:
            if readd is not None:
                # A cancelled/failed request must not restore initial_state
                # while Core's already-scheduled re-add is still in flight.
                drain = asyncio.create_task(self.io.settle(change))
                while not drain.done():
                    try:
                        await asyncio.shield(drain)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if drain.done() and not drain.cancelled() and drain.exception() is None:
                    try:
                        await transition.finish_registry_readd(readd)
                    except BaseException:
                        pass  # Preserve the original uncertain effect/failure.
            raise
        finally:
            self._mutation_node = None


class RegistryTransactionAdapter:
    """Bind trusted prepared refs to transaction.Step; no manifest-controlled patch.

    The resolver MUST load the immutable durable preparation authenticated by
    the server's journal/plan fingerprint. Full-scope verification is supplied
    separately; registry metadata evidence never closes history/reference AC.
    """

    def __init__(self, bridge, resolver, full_verify=None, full_verify_rollback=None):
        self.bridge = bridge
        self.resolver = resolver
        self.full_verify = full_verify
        self.full_verify_rollback = full_verify_rollback

    def _change(self, step):
        change = self.resolver(step.prepared_ref)
        if (not isinstance(change, PreparedRegistryChange)
                or change.identity != step.identity
                or change.before_revision != step.before_revision
                or change.after_revision != step.after_revision):
            raise RegistryConflict("step_preparation_mismatch")
        return change

    async def inspect(self, step):
        from .transaction import Position
        evidence = await self.bridge.inspect(self._change(step))
        return {"before": Position.BEFORE, "after": Position.AFTER}.get(
            evidence.position, Position.UNKNOWN)

    async def apply(self, step, guard):
        return await self.bridge.apply(self._change(step), guard)

    async def compensate(self, step, guard):
        return await self.bridge.compensate(self._change(step), guard)

    async def verify(self, plan):
        return False if self.full_verify is None else await self.full_verify(plan)

    async def verify_rollback(self, plan):
        return False if self.full_verify_rollback is None else await self.full_verify_rollback(plan)
