"""Read a sealed hybrid Script prefix while dependent YAML awaits reload."""
from __future__ import annotations

from .dependent_yaml_runtime import DependentYAMLRuntime, _need
from .transaction import Position


def _script_rows(before, after, prefix, old, new, active, position):
    """Select the sealed owner image without changing any other Script row."""
    _need(type(active) is bool and type(position) is Position and
          position in (Position.BEFORE, Position.AFTER))
    rows = list(before if position is Position.BEFORE else after)
    if active == (position is Position.AFTER):
        return rows
    source, target = (old, new) if active else (new, old)
    selected = [row for row in prefix if row[0] == target]
    _need(len(selected) == 1 and sum(row[0] == source for row in rows) == 1
          and not any(row[0] == target for row in rows))
    return sorted([row for row in rows if row[0] != source] + selected, key=lambda row: row[0])


class ControlledScriptPhases(DependentYAMLRuntime):
    def __init__(self, root, observed, *, position):
        from .controlled_script_composition import ControlledScriptComposition, is_controlled_script
        from .dependent_config_binding import accepted_dependent_prefix
        from .yaml_source_binding import _RuntimeSourceSampler
        _need(is_controlled_script(root.composition) and type(position) is Position)
        self.root = root
        self.program = root.composition.dependent_config_program
        self.hass, self.evidence, self.observed, self.receipt = root.hass, root.evidence, observed, root.receipt
        self.sampler = _RuntimeSourceSampler.bind(self.evidence, observed)
        self.allow_running = True
        self.positions = tuple((domain, position) for domain in ('automation', 'script'))
        self.active, self.unresolved = None, False
        self.own_fence = root.own_dependent_fence
        self.mask = accepted_dependent_prefix(root.chain, self.program).active
        self.script_mask = self.mask
        self.sample = self._sample(self.positions)
        _need(not self.sample[1])

    def _sample(self, positions):
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        from .dependent_config_program import _digest
        from .yaml_runtime_observer import NativeYAMLRuntimeReadback
        from .yaml_source_binding import _runtime_rows_sample
        from .yaml_validation_worker import _normal
        script_edges = [(old, new) for old, new in self.program.mapping
                        if old.startswith('script.') and new.startswith('script.')]
        _need(len(script_edges) == 1)
        old, new = script_edges[0]
        before, after = dict(self.program.rows(frozenset())), dict(self.program.rows(frozenset(self.program.source_order)))
        prefix = dict(self.program.rows(self.script_mask))
        expected = {}
        for domain, position in positions:
            expected[domain] = list((before if position is Position.BEFORE else after)[domain])
        expected['script'] = _script_rows(before['script'], after['script'], prefix['script'],
            old.split('.', 1)[1], new.split('.', 1)[1], old in self.mask, dict(positions)['script'])
        observer = NativeYAMLRuntimeReadback(self.hass)
        for domain in ('automation', 'script'):
            component = self.hass.data.get(DATA_INSTANCES, {}).get(domain)
            actual = observer._projection(domain)[0] if component is not None else []
            _need(_normal(actual) == expected[domain])
        moves = (('script', old.split('.', 1)[1], new.split('.', 1)[1]),) if old in self.mask else ()
        sampled = _runtime_rows_sample(self.hass, self.evidence, self.own_fence.rows(),
            expected_projections=tuple((domain, _digest(expected[domain])) for domain in ('automation', 'script')),
            source_key_moves=moves)
        return self.own_fence.normalize(sampled)

    def check(self):
        cutover = getattr(self.root, 'controlled_script_cutover', None)
        if cutover is not None and cutover.phase in ('suspending', 'suspended', 'removing', 'removed', 'registry_attempted', 'adding', 'resuming', 'added'):
            cutover._scope()
            if cutover.phase not in ('suspending', 'removing', 'adding', 'resuming'):
                cutover._foreign()
            return
        super().check()
        admission = getattr(self.root, 'own_record_admission', None)
        if admission is not None and admission.controlled_service is not None:
            admission.check_execution()

    def accept_registry(self, change):
        from .dependent_config_binding import accepted_dependent_prefix
        self.mask = accepted_dependent_prefix(self.root.chain, self.program).active
        if change.before.get('platform') == 'script':
            self.script_mask = self.mask
        self.sample = self._sample(self.positions)
        _need(not self.sample[1])
