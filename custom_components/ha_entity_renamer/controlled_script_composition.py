"""Sealed checked Script program for the controlled-reload transaction."""
from dataclasses import dataclass, fields
import json

from .dependent_config_program import (DependentConfigProgram,
    encode_dependent_config_program, decode_dependent_config_program, verified_script_key_moves)
from .native_template_composition import _TYPES, _need
from .native_template_state_composition import (MixedTemplateStateComposition,
    encode_mixed_state_composition, decode_mixed_state_composition,
    _encode_state_composition, _decode_state_composition)

PROFILE = 'template_controlled_script_reload_v1'
ENTRY_BASE_PROFILE = 'template_controlled_script_entry_base_v1'


def _entry_seal(receipt):
    return any(row[0] == 'template.config_entries' for row in receipt.runtime_expected)


@dataclass(frozen=True)
class _ScriptEntryBase(MixedTemplateStateComposition):
    def _assert_changed_scope(self, receipt):
        from .yaml_transaction import _assert_mixed_state_scope
        _assert_mixed_state_scope(receipt, allow_dependent_only=True, allow_template_entry=True)
        _need(bool(receipt.proposed_files))


def _base(value, baseline):
    cls = _ScriptEntryBase if _entry_seal(baseline.receipt) else MixedTemplateStateComposition
    return cls(**{
        field.name: getattr(value, field.name) for field in fields(MixedTemplateStateComposition)})


@dataclass(frozen=True)
class ControlledScriptComposition(MixedTemplateStateComposition):
    dependent_config_program: DependentConfigProgram | None = None

    def _assert_changed_scope(self, receipt):
        from .yaml_transaction import _assert_mixed_state_scope
        _assert_mixed_state_scope(receipt, allow_dependent_only=True, allow_template_entry=_entry_seal(receipt))
        _need(bool(receipt.proposed_files))

    def assert_baseline(self, baseline):
        super().assert_baseline(baseline)
        program = self.dependent_config_program
        _need(type(program) is DependentConfigProgram)
        program.assert_sources(baseline.receipt, self.evidence, plan=self.yaml_plan,
            mapping=dict(self.mapping), source_order=tuple(old for old, _ in self.mapping))
        rows = json.loads(next(row.raw for row in baseline.files if row.key == 'core.entity_registry'))['data']['entities']
        selected = [row for row in rows if row.get('entity_id') in dict(self.mapping)
                    and row.get('platform') in ('template', 'automation', 'script')]
        _need(len(selected) == 1 and selected[0].get('platform') == 'script'
              and selected[0]['entity_id'] in verified_script_key_moves(self.evidence, self.yaml_plan, dict(self.mapping)))


_TYPES.update({kind.__name__: kind for kind in (ControlledScriptComposition, DependentConfigProgram)})


def encode_controlled_script_composition(value, baseline):
    _need(type(value) is ControlledScriptComposition)
    value.assert_baseline(baseline)
    base = _base(value, baseline)
    encoded = (_encode_state_composition(base, baseline, _ScriptEntryBase, ENTRY_BASE_PROFILE)
               if type(base) is _ScriptEntryBase else encode_mixed_state_composition(base, baseline))
    return {'schema': 1, 'profile': PROFILE,
        'base': encoded,
        'dependent_program_sha256': value.dependent_config_program.sha256,
        'dependent_program': json.loads(encode_dependent_config_program(value.dependent_config_program))}


def decode_controlled_script_composition(payload, baseline):
    _need(type(payload) is dict and set(payload) == {
        'schema', 'profile', 'base', 'dependent_program_sha256', 'dependent_program'}
        and type(payload['schema']) is int and payload['schema'] == 1 and payload['profile'] == PROFILE)
    base = (_decode_state_composition(payload['base'], baseline, _ScriptEntryBase, ENTRY_BASE_PROFILE)
            if _entry_seal(baseline.receipt) else decode_mixed_state_composition(payload['base'], baseline))
    program = decode_dependent_config_program(
        json.dumps(payload['dependent_program'], sort_keys=True, separators=(',', ':'), allow_nan=False).encode(),
        expected_sha256=payload['dependent_program_sha256'], receipt=baseline.receipt,
        evidence=base.evidence, plan=base.yaml_plan, mapping=dict(base.mapping),
        source_order=tuple(old for old, _ in base.mapping))
    value = ControlledScriptComposition(**{
        field.name: getattr(base, field.name) for field in fields(MixedTemplateStateComposition)},
        dependent_config_program=program)
    value.assert_baseline(baseline)
    return value


def is_controlled_script(value):
    """Only the sealed combined Powercalc profile may carry this program."""
    from .controlled_powercalc_composition import ControlledPowercalcComposition
    return (type(value) is ControlledScriptComposition or
            type(value) is ControlledPowercalcComposition and
            type(value.dependent_config_program) is DependentConfigProgram)
