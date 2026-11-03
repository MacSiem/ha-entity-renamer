"""Registry-only own-Template readback over the SAME accepted input chain.

There is no YAML effect or synthetic file position: the observed registry UUID
prefix selects BEFORE/AFTER, and the original file graph is always checked.
"""
from __future__ import annotations

import inspect
import json

from .native_template_state_yaml import NativeTemplateStateYAMLRuntimeReadback
from .transaction import Position, Refused


def _need(value, reason):
    if not value:
        raise Refused(reason) from None


class NativeOwnIDRegistryReadback(NativeTemplateStateYAMLRuntimeReadback):
    def __init__(self, hass, chain, composition, *, guard):
        from .native_registry_receipts import RegistryReceiptChain
        from .native_template_state_binding import _StateRuntime
        from .native_template_state_composition import TemplateStateComposition, retain_state_composition
        _need(type(chain) is RegistryReceiptChain and chain.yaml_index is None
              and type(composition) is TemplateStateComposition
              and not chain.original_receipt.proposed_files
              and not chain.original_receipt.changed_domains
              and chain.plan.required_scopes == chain.plan.covered_scopes
                  == chain.plan.verification_adapters == ('registry',)
              and bool(chain.plan.steps)
              and all(step.adapter == 'registry' for step in chain.plan.steps),
              'own_registry_readback_scope_unqualified')
        composition.assert_baseline(chain.baseline)
        self.composition, self.hass, self.chain, self.guard = composition, hass, chain, guard
        self.program, self.receipt = composition.state_config_program, chain.original_receipt
        self._last_runtime = None
        self.native = _StateRuntime(hass)
        _need(self.native.native.version == self.receipt.version, 'own_registry_core_changed')
        self.change = chain._original_change(len(chain.plan.steps) - 1)
        self.wire, self.composition_check = retain_state_composition(composition, chain.baseline)
        documents = {row.key: json.loads(row.raw) for row in chain.baseline.files}
        rows = [row for row in documents['core.entity_registry']['data']['entities']
                if row['platform'] == 'template']
        self.identities = {(row['entity_id'].split('.')[0], row['unique_id']):
                           (row['entity_id'], row['id']) for row in rows}
        _need(len(self.identities) == len(rows), 'own_registry_template_identity_ambiguous')
        self.physical_consumers = composition.physical_consumers
        for domain, key, entity_id in self.physical_consumers:
            _need((domain, key) not in self.identities, 'own_registry_template_identity_ambiguous')
            self.identities[domain, key] = (entity_id, None)

    def assert_change(self, change):
        _need(change == self.change and self.chain.yaml_index is None
              and self.chain._original_change(len(self.chain.plan.steps) - 1) == self.change,
              'own_registry_preparation_changed')
        self.composition_check()
        _need(self.chain.original_receipt == self.receipt
              and self.hass.config.config_dir == self.receipt.config_root,
              'own_registry_original_changed')

    def _check(self, state, live):
        from .native_template_state_binding import _state_inputs
        value = self.guard()
        if inspect.isawaitable(value):
            if inspect.iscoroutine(value):
                value.close()
            raise Refused('own_registry_synchronous_guard_required')
        _need(value is None or value is True, 'own_registry_guard_rejected')
        self.assert_change(self.change)
        _state_inputs(self.chain, state, **live)

    def _sample(self, runtime, state, live):
        from .native_template_state_binding import accepted_state_prefix, sample_state_bindings
        prefix = accepted_state_prefix(self.chain, self.program, input_state=state, **live)
        original = {path: digest for path, digest, *_ in self.receipt.source_files
                    if not path.startswith('.storage/')}
        current = {path: row[0] for path, row in state.sources().items()
                   if not path.startswith('.storage/')}
        _need(current == original, 'own_registry_yaml_graph_changed')
        snapshot = sample_state_bindings(runtime, self.program, prefix, 'original',
                                         self.chain.accepted_documents())
        expected = frozenset(dict(self.composition.mapping))
        position = Position.AFTER if prefix.active == expected else Position.BEFORE
        return position, snapshot
