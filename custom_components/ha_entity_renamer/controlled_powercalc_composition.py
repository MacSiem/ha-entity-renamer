"""Detached Powercalc and controlled YAML evidence under one ORIGINAL.

This is a codec only; decoding it never authorizes a native effect or recovery.
"""
from dataclasses import dataclass, replace

from .native_template_state_composition import ControlledReloadComposition, TemplateStateComposition
from .native_template_composition import _history, _need, _typed
from .powercalc_composition import PowercalcComposition, _typed as _powercalc_typed
from .powercalc_previous_state_original import PowercalcPreviousOriginal
from .template_source_evidence import OriginalTemplateStateSources, CandidateTemplateStateSources
from .template_state_program import StateConfigProgram
from .yaml_plan import Plan
from .yaml_source_binding import OriginalSourceEvidence

PROFILE = 'template_controlled_powercalc_v1'


@dataclass(frozen=True)
class ControlledPowercalcComposition(ControlledReloadComposition):
    previous: PowercalcPreviousOriginal | None = None
    energy_original_physical: tuple[str, int, int, int, int, int] | None = None
    population: tuple[tuple[str, str, str], ...] = ()
    dependent_config_program: object | None = None

    def _assert_changed_scope(self, receipt):
        from .yaml_transaction import _assert_mixed_state_scope
        _need('powercalc' in receipt.changed_domains
              and set(receipt.unsupported_domains) <= {'powercalc', 'template'})
        scoped = replace(receipt,
            changed_domains=tuple(d for d in receipt.changed_domains if d != 'powercalc'),
            unsupported_domains=tuple(d for d in receipt.unsupported_domains if d != 'powercalc'),
            runtime_expected=tuple(row for row in receipt.runtime_expected
                                   if row[0] not in ('powercalc', 'template.config_entries')))
        if not scoped.changed_domains:
            _need(not scoped.unsupported_domains and not scoped.runtime_expected)
        elif scoped.changed_domains == ('template',):
            TemplateStateComposition._assert_changed_scope(self, scoped)
        else:
            _assert_mixed_state_scope(scoped, allow_dependent_only=True)
        _need(bool(receipt.proposed_files))

    def assert_baseline(self, baseline):
        import json
        TemplateStateComposition.assert_baseline(self, baseline, _mixed_powercalc=True)
        rows = json.loads(next(row.raw for row in baseline.files
                               if row.key == 'core.entity_registry'))['data']['entities']
        selected = [row for row in rows if row.get('entity_id') in dict(self.mapping)]
        _need(len(selected) == len(self.mapping))
        dependent = [row for row in selected if row.get('platform') in ('template', 'automation', 'script')]
        if dependent:
            from .dependent_config_program import DependentConfigProgram, verified_script_key_moves
            program = self.dependent_config_program
            _need(len(dependent) == 1 and dependent[0].get('platform') == 'script'
                  and type(program) is DependentConfigProgram
                  and dependent[0]['entity_id'] in verified_script_key_moves(self.evidence, self.yaml_plan, dict(self.mapping)))
            program.assert_sources(baseline.receipt, self.evidence, plan=self.yaml_plan,
                mapping=dict(self.mapping), source_order=tuple(old for old, _ in self.mapping))
        else:
            _need(self.dependent_config_program is None)
        _need(type(self.population) is tuple and bool(self.population)
              and self.population == tuple(sorted(self.population))
              and all(type(row) is tuple and len(row) == 3 and all(type(value) is str and value for value in row)
                      for row in self.population)
              and len({row[0] for row in self.population}) == len(self.population)
              and len({row[1] for row in self.population}) == len(self.population))
        from .controlled_powercalc_population import POWER_KINDS, COUNTER_KINDS
        catalog = {row['id']: row for row in rows if row.get('platform') == 'powercalc'}
        _need(all(uuid in catalog and catalog[uuid]['entity_id'] == entity_id
                  and catalog[uuid].get('disabled_by') is None
                  and kind in POWER_KINDS | COUNTER_KINDS
                  for entity_id, uuid, kind in self.population))
        _need(type(self.previous) is PowercalcPreviousOriginal)
        groups = {(group.entity_id, group.registry_id) for group in self.previous.groups}
        _need(groups == {(eid, uid) for eid, uid, kind in self.population
                         if kind == 'energy_positive_delta'})
        _need(all(catalog[uid]['entity_id'] not in dict(self.mapping) or (eid, uid) in groups
                  for eid, uid, _ in self.population))
        PowercalcComposition(self.evidence, self.previous, self.prepared_digests,
            self.source_history, self.consumer_history, self.evidence.powercalc_sources.sha256,
            self.energy_original_physical).assert_baseline(baseline, _mixed_template=True)


def encode_controlled_powercalc(value, baseline):
    _need(type(value) is ControlledPowercalcComposition)
    value.assert_baseline(baseline)
    result = {'schema': 1, 'profile': PROFILE,
        'evidence': _powercalc_typed(value.evidence, OriginalSourceEvidence, encode=True),
        'original_sources': _typed(value.original_sources, OriginalTemplateStateSources, encode=True),
        'candidate_sources': _typed(value.candidate_sources, CandidateTemplateStateSources, encode=True),
        'state_config_program': _typed(value.state_config_program, StateConfigProgram, encode=True),
        'yaml_plan': _typed(value.yaml_plan, Plan, encode=True),
        'mapping': _typed(value.mapping, tuple[tuple[str, str], ...], encode=True),
        'prepared_digests': _typed(value.prepared_digests, tuple[tuple[str, str], ...], encode=True),
        'source_history': _history(value.source_history), 'consumer_history': _history(value.consumer_history),
        'coverage_sha256': value.coverage_sha256,
        'physical_consumers': _typed(value.physical_consumers, tuple[tuple[str, str, str], ...], encode=True),
        'population': _typed(value.population, tuple[tuple[str, str, str], ...], encode=True),
        'previous': _powercalc_typed(value.previous, PowercalcPreviousOriginal, encode=True),
        'energy_original_physical': _powercalc_typed(value.energy_original_physical,
            tuple[str, int, int, int, int, int] | None, encode=True)}
    if value.dependent_config_program is not None:
        import json
        from .dependent_config_program import encode_dependent_config_program
        result['dependent_program'] = json.loads(encode_dependent_config_program(value.dependent_config_program))
        result['dependent_program_sha256'] = value.dependent_config_program.sha256
    return result


def decode_controlled_powercalc(payload, baseline):
    keys = {'schema', 'profile', 'evidence', 'original_sources', 'candidate_sources',
            'state_config_program', 'yaml_plan', 'mapping', 'prepared_digests', 'source_history',
            'consumer_history', 'coverage_sha256', 'physical_consumers', 'previous', 'energy_original_physical', 'population'}
    extra = {'dependent_program', 'dependent_program_sha256'}
    _need(type(payload) is dict and set(payload) in (keys, keys | extra) and type(payload['schema']) is int
          and payload['schema'] == 1 and payload['profile'] == PROFILE)
    result = ControlledPowercalcComposition(
        _powercalc_typed(payload['evidence'], OriginalSourceEvidence, encode=False),
        _typed(payload['original_sources'], OriginalTemplateStateSources, encode=False),
        _typed(payload['candidate_sources'], CandidateTemplateStateSources, encode=False),
        _typed(payload['state_config_program'], StateConfigProgram, encode=False),
        _typed(payload['yaml_plan'], Plan, encode=False),
        _typed(payload['mapping'], tuple[tuple[str, str], ...], encode=False),
        _typed(payload['prepared_digests'], tuple[tuple[str, str], ...], encode=False),
        _history(payload['source_history']), _history(payload['consumer_history']), payload['coverage_sha256'],
        _typed(payload['physical_consumers'], tuple[tuple[str, str, str], ...], encode=False),
        _powercalc_typed(payload['previous'], PowercalcPreviousOriginal, encode=False),
        _powercalc_typed(payload['energy_original_physical'],
                         tuple[str, int, int, int, int, int] | None, encode=False),
        _typed(payload['population'], tuple[tuple[str, str, str], ...], encode=False))
    if 'dependent_program' in payload:
        import json
        from .dependent_config_program import decode_dependent_config_program
        program = decode_dependent_config_program(
            json.dumps(payload['dependent_program'], sort_keys=True, separators=(',', ':'), allow_nan=False).encode(),
            expected_sha256=payload['dependent_program_sha256'], receipt=baseline.receipt,
            evidence=result.evidence, plan=result.yaml_plan, mapping=dict(result.mapping),
            source_order=tuple(old for old, _ in result.mapping))
        result = replace(result, dependent_config_program=program)
    result.assert_baseline(baseline)
    return result
