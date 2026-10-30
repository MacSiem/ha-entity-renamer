"""Private durable Script-prefix adjunct; legacy composition bytes stay exact.

The entire envelope is authenticated by the owning native-input preparation.
Neither this codec nor its expected program digest grants write authority.
"""
from dataclasses import dataclass, fields
import json

from .dependent_config_program import (DependentConfigProgram,
    encode_dependent_config_program, decode_dependent_config_program)
from .native_template_state_composition import (MixedTemplateStateComposition,
    encode_mixed_state_composition, decode_mixed_state_composition)
from .native_template_composition import _TYPES, _need

PROFILE = 'template_script_prefix_binding_v1'


def _base(value):
    return MixedTemplateStateComposition(**{
        field.name: getattr(value, field.name) for field in fields(MixedTemplateStateComposition)})


@dataclass(frozen=True)
class ScriptTemplateStateComposition(MixedTemplateStateComposition):
    dependent_config_program: DependentConfigProgram | None = None

    def assert_baseline(self, baseline):
        super().assert_baseline(baseline)
        _need(baseline.receipt.changed_domains == ('script', 'template'))
        program = self.dependent_config_program
        _need(type(program) is DependentConfigProgram)
        program.assert_sources(baseline.receipt, self.evidence, plan=self.yaml_plan,
            mapping=dict(self.mapping), source_order=tuple(old for old, _ in self.mapping))
        # This profile transports Script configuration only. Automation rows
        # must be identical across EVERY sealed source prefix.
        rows = [dict(program.rows(frozenset(program.source_order[:count])))
                for count in range(len(program.source_order) + 1)]
        _need(all(row['automation'] == rows[0]['automation'] for row in rows))


_TYPES.update({kind.__name__: kind for kind in (ScriptTemplateStateComposition, DependentConfigProgram)})


def encode_script_state_composition(value, baseline):
    _need(type(value) is ScriptTemplateStateComposition)
    value.assert_baseline(baseline)
    return {'schema': 1, 'profile': PROFILE,
        'base': encode_mixed_state_composition(_base(value), baseline),
        'dependent_program_sha256': value.dependent_config_program.sha256,
        'dependent_program': json.loads(encode_dependent_config_program(value.dependent_config_program))}


def decode_script_state_composition(payload, baseline):
    _need(type(payload) is dict and set(payload) == {
        'schema', 'profile', 'base', 'dependent_program_sha256', 'dependent_program'}
        and type(payload['schema']) is int and payload['schema'] == 1
        and payload['profile'] == PROFILE)
    base = decode_mixed_state_composition(payload['base'], baseline)
    # The caller has already authenticated the WHOLE native-input envelope;
    # this pin comes from that preparation, never from a public request.
    program = decode_dependent_config_program(
        json.dumps(payload['dependent_program'], sort_keys=True, separators=(',', ':'), allow_nan=False).encode(),
        expected_sha256=payload['dependent_program_sha256'], receipt=baseline.receipt,
        evidence=base.evidence, plan=base.yaml_plan, mapping=dict(base.mapping),
        source_order=tuple(old for old, _ in base.mapping))
    value = ScriptTemplateStateComposition(**{
        field.name: getattr(base, field.name) for field in fields(MixedTemplateStateComposition)},
        dependent_config_program=program)
    value.assert_baseline(baseline)
    return value
