"""Task-local native dashboard fence over one accepted ORIGINAL chain."""
from __future__ import annotations

from .discovery import _plain
from .lovelace_native import NativeLovelace, revision, durable_snapshot
from .native_discovery_consumers import _inventory
from .native_input_baseline import _drained
from .transaction import Refused


def _need(value, reason):
    if not value:
        raise Refused('dashboard_phase_' + reason)


class DashboardPhase:
    def __init__(self):
        raise Refused('dashboard_phase_capture_required')

    @classmethod
    async def capture(cls, hass, observed, chain, *, guard):
        _need(observed.dashboard.status == 'COMPLETE', 'native_evidence_required')
        native = NativeLovelace(hass)
        token = getattr(observed.dashboard.value, '_native_inventory_token', None)
        _need(token is not None and _inventory(hass, native, observed.version) == token,
              'inventory_changed')
        state = chain.reconstruct_inputs()
        sources = {path: (sha, dev, ino) for path, sha, dev, ino in state.source_files}
        changes = {step.identity: chain._original_change(index)
                   for index, step in enumerate(chain.plan.steps) if step.adapter == 'dashboard'}
        _need(len(changes) == sum(step.adapter == 'dashboard' for step in chain.plan.steps),
              'duplicate_dashboard')
        _, _, _, completed, pending = chain._reconstruct()
        _need(pending is None, 'unresolved_intent')
        rows = {}
        for row in observed.dashboard.value.snapshots:
            path = '.storage/' + row.dashboard.key
            if row.file_identity is None:
                _need(path not in sources, 'unexpected_file')
            else:
                dev, ino, sha = row.file_identity
                _need(sources.get(path) == (sha, dev, ino), 'source_mismatch')
            config = _plain(row.config)
            change = changes.get(row.dashboard.identity)
            if change is not None:
                index = next(i for i, step in enumerate(chain.plan.steps)
                             if step.adapter == 'dashboard' and step.identity == row.dashboard.identity)
                direction = completed.get(index)
                expected = change.after if direction == 'forward' else change.before.config
                _need(direction in (None, 'forward', 'rollback') and config == expected,
                      'accepted_image_mismatch')
            rows[row.dashboard.identity] = (row.dashboard, config, row.revision, row.file_identity)
        _need(set(changes) <= set(rows), 'dashboard_missing')
        for row in observed.dashboard.value.yaml_sources:
            dev, ino = row.file_identity[:2]
            _need(sources.get(row.filename) == (row.sha256, dev, ino), 'yaml_source_mismatch')
        guard()
        await _drained(hass.async_add_executor_job, lambda: state.assert_current(chain.original_receipt))
        guard()
        obj = object.__new__(cls)
        from pathlib import Path
        obj.hass, obj.native, obj.version, obj.token = hass, native, observed.version, token
        obj.storage_root = Path(hass.config.config_dir) / '.storage'
        obj.config_root = Path(hass.config.config_dir)
        obj.yaml_routes = tuple((row.route, row.filename) for row in observed.dashboard.value.yaml_sources)
        obj.chain, obj.rows, obj.active = chain, rows, None
        obj.check()
        return obj

    def check(self):
        _need(_inventory(self.hass, self.native, self.version) == self.token, 'inventory_changed')
        for identity, (descriptor, config, expected, _) in self.rows.items():
            manager = self.native._manager(descriptor)
            self.native._settled(manager)
            actual = None if manager._data is None else manager._data.get('config')
            allowed = {expected}
            if self.active is not None and self.active[0] == identity:
                allowed.add(self.active[1])
            _need(revision(actual) in allowed, 'native_cache_changed')
            if revision(actual) == expected:
                _need(actual == config, 'native_cache_changed')
            elif self.active is not None:
                _need(actual == self.active[2], 'native_cache_changed')

    def physical(self):
        for descriptor, config, expected, identity in tuple(self.rows.values()):
            data, actual = durable_snapshot(self.storage_root / descriptor.key, descriptor.key)
            value = None if data is None else data.get('config')
            _need(actual == identity and revision(value) == expected and value == config, 'physical_changed')

    def arm(self, change, direction):
        _need(self.active is None and direction in ('forward', 'rollback'), 'already_active')
        identity = change.before.dashboard.identity
        _need(identity in self.rows, 'dashboard_missing')
        expected = change.after_revision if direction == 'forward' else change.before.revision
        desired = change.after if direction == 'forward' else change.before.config
        self.active = (identity, expected, desired)
        self.check()

    def accept(self, change, observation):
        _need(self.active is not None and self.active[0] == change.before.dashboard.identity
              and observation.snapshot.revision == self.active[1], 'effect_unconfirmed')
        snapshot = observation.snapshot
        self.rows[self.active[0]] = (snapshot.dashboard, snapshot.config,
                                     snapshot.revision, snapshot.file_identity)
        self.active = None
        self.check()

    async def verify_yaml(self, state, plan, mapping, change, *, original, guard):
        """Re-read actual YAML managers against the sealed before/after image."""
        import os
        from .dashboard_yaml_semantics import qualify_dashboard_yaml
        from .native_discovery_consumers import _freeze
        from .yaml_plan import Limits, _Reader
        files = {row.path: row for row in (() if change is None else change.files)}
        for route, path in self.yaml_routes:
            def read():
                state.assert_current(self.chain.original_receipt)
                reader = _Reader(self.config_root, Limits())
                try:
                    return reader.read(path)[0]
                finally:
                    os.close(reader.root)
            raw = await _drained(self.hass.async_add_executor_job, read)
            guard(); self.check()
            before_bytes = files[path].before if path in files else raw
            before, after = qualify_dashboard_yaml(plan, path, before_bytes, mapping)
            expected = before if original else after
            manager = self.native._managers().get(route)
            actual = await manager.async_load(False)
            guard(); self.check()
            _need(revision(_plain(_freeze(actual))) == revision(expected), 'yaml_native_image_changed')
            await _drained(self.hass.async_add_executor_job,
                           lambda: state.assert_current(self.chain.original_receipt))
            guard(); self.check()


class DashboardConsumerFence:
    """Keep Energy unchanged while dashboard effects follow the receipt chain."""
    def __init__(self, hass, observed, phase):
        from .discovery_final_fence import _ConsumerFence
        self.phase = phase
        self.energy = _ConsumerFence(hass, observed, _plain, names=('energy',))

    def check(self):
        self.phase.check()
        self.energy.check()

    def physical(self):
        self.phase.physical()
        self.energy.physical()
