"""Read-only native dependent program position at an authenticated registry prefix.

This is separate from domain reload tickets. A client mask, physical YAML
position or current-looking body cannot select an expected prefix.
"""
from __future__ import annotations

from dataclasses import dataclass

from .dependent_config_program import DependentConfigProgram, _digest
from .transaction import Refused


def _need(value, reason='dependent_config_binding_unconfirmed'):
    if not value:
        raise Refused(reason) from None


@dataclass(frozen=True)
class DependentConfigPrefix:
    program_sha256: str
    operation_id: str
    plan_fingerprint: str
    accepted_head: str
    active: frozenset[str]
    registry_ids: tuple[tuple[str, str], ...]


def accepted_dependent_prefix(chain, program, *, input_state=None, live_intent=None,
                              live_context=None, live_calls=()):
    """Derive the mask from immutable preparations, accepted documents and UUIDs."""
    from .native_registry_receipts import RegistryReceiptChain
    from .native_registry import PreparedRegistryChange
    from .native_template_state_binding import _state_inputs
    _need(type(chain) is RegistryReceiptChain and type(program) is DependentConfigProgram)
    state = _state_inputs(chain, input_state, live_intent=live_intent,
                          live_context=live_context, live_calls=live_calls)
    _need(program.receipt_json == state.original_json,
          'dependent_config_original_receipt_changed')
    registry = chain.accepted_documents()['core.entity_registry']['data']['entities']
    mapping, order, active, identities = {}, [], set(), []
    for index, step in enumerate(chain.plan.steps):
        if step.adapter != 'registry':
            continue
        change = chain._original_change(index)
        _need(type(change) is PreparedRegistryChange)
        for old, new in change.mapping.items():
            _need(old not in mapping, 'dependent_config_registry_mapping_duplicate')
            mapping[old] = new
            order.append(old)
            matches = [row for row in registry if row['id'] == change.identity]
            _need(len(matches) == 1 and matches[0]['entity_id'] in (old, new),
                  'dependent_config_registry_identity_changed')
            if matches[0]['entity_id'] == new:
                active.add(old)
            identities.append((change.identity, matches[0]['entity_id']))
    _need(mapping == dict(program.mapping) and tuple(order) == program.source_order,
          'dependent_config_registry_order_changed')
    program.rows(frozenset(active))  # exact sealed finite prefix, not any subset
    return DependentConfigPrefix(program.sha256, chain.operation_id, chain.plan.fingerprint,
        state.head, frozenset(active), tuple(sorted(identities)))


@dataclass(frozen=True)
class DependentConfigSample:
    prefix: DependentConfigPrefix
    rows_json: bytes
    registry_rows: tuple
    native_tokens: tuple


class NativeDependentConfigReadback:
    """Full native rows + ORIGINAL source/registry/object bijection, no write API."""
    def __init__(self, hass, program, evidence, receipt, plan):
        from .dependent_config_program import _bytes
        from .native_template_state_composition import _retained_fields
        _need(type(program) is DependentConfigProgram)
        program.assert_sources(receipt, evidence, plan=plan, mapping=dict(program.mapping),
                               source_order=program.source_order)
        self.hass, self.program, self.evidence = hass, program, evidence
        self.receipt, self.plan = receipt, plan
        self._program_json, self._program_sha = _bytes(program.descriptor_json.decode()), program.sha256
        self._receipt_json, self._evidence_sha = program.receipt_json, evidence.sha256
        self._plan_fields = _retained_fields(plan)

    def sample(self, chain, **live):
        from .dependent_config_program import _bytes
        from .native_input_baseline import _receipt_bytes
        from .native_template_state_composition import _retained_fields
        from .yaml_runtime_observer import NativeYAMLRuntimeReadback
        from .yaml_source_binding import _runtime_rows_sample
        from .yaml_validation_worker import _normal
        _need(self.program.sha256 == self._program_sha
              and _bytes(self.program.descriptor_json.decode()) == self._program_json
              and self.program.original_evidence_sha256 == self._evidence_sha
              and self.evidence.sha256 == self._evidence_sha
              and _receipt_bytes(self.receipt) == self._receipt_json
              and _retained_fields(self.plan) == self._plan_fields
              and chain.original_receipt == self.receipt,
              'dependent_config_retained_original_changed')
        prefix = accepted_dependent_prefix(chain, self.program, **live)
        expected = self.program.rows(prefix.active)
        observer = NativeYAMLRuntimeReadback(self.hass)
        actual = []
        for domain, rows in expected:
            from homeassistant.helpers.entity_component import DATA_INSTANCES
            component = self.hass.data.get(DATA_INSTANCES, {}).get(domain)
            projection = observer._projection(domain)[0] if component is not None else []
            normalized = _normal(projection)
            _need(normalized == rows, 'dependent_config_native_prefix_mismatch')
            actual.append((domain, normalized))
        documents = chain.accepted_documents()
        population, blockers, tokens = _runtime_rows_sample(self.hass, self.evidence,
            documents['core.entity_registry']['data']['entities'],
            expected_projections=tuple((domain, _digest(rows)) for domain, rows in expected))
        _need(not blockers, 'dependent_config_source_bijection_changed')
        # Recheck the authenticated head after the synchronous full native read.
        _need(accepted_dependent_prefix(chain, self.program, **live) == prefix,
              'dependent_config_accepted_prefix_changed')
        return DependentConfigSample(prefix, _bytes(actual), population, tokens)
