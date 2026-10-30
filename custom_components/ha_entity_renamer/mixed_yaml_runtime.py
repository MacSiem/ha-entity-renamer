"""Private composite Template and dependent YAML runtime readback.

Expected programs remain in one ORIGINAL checker receipt. Native reloads may
replace only their own domain's sampled objects; other consumers remain pinned.
This module supplies no public admission or whole-family readiness.
"""
from __future__ import annotations

import threading

from .dependent_yaml_runtime import DependentYAMLRuntime
from .native_template_forward import _UnchangedConsumers
from .native_template_yaml import NativeTemplateYAMLRuntimeReadback
from .transaction import Position, Refused


def _need(value):
    if not value:
        raise Refused('mixed_yaml_runtime_unconfirmed') from None


class MixedYAMLConsumers(_UnchangedConsumers):
    async def bind_sources(self, receipt, evidence, *, guard):
        # Keep the original native source/bijection admission in full. Only the
        # later phase comparison uses the separately sealed candidate hashes.
        await super().bind_sources(receipt, evidence, guard=guard)
        self.bind_phases(receipt, evidence, self.observed, Position.BEFORE)

    async def bind_prefix_sources(self, receipt, evidence, *, guard):
        # No domain reload ticket even during private prefix preparation.
        # Keep the full ORIGINAL sampler until the actual chain is bound.
        _need(getattr(self, 'phases', None) is None)
        await super().bind_sources(receipt, evidence, guard=guard)

    def bind_phases(self, receipt, evidence, observed, position):
        phases = DependentYAMLRuntime(self.hass, evidence, observed, receipt, position=position)
        self.evidence, self.observed = evidence, observed
        self.runtime_sample = None
        self.phases = phases
        self.check()

    def bind_prefix_phases(self, root):
        from .native_script_prefix_phase import NativeScriptPrefixPhase
        self.runtime_sample = None
        self.phases = NativeScriptPrefixPhase(root)
        self.check()

    def check(self):
        super().check()
        if threading.get_ident() != self.thread:
            return
        phases = getattr(self, 'phases', None)
        if phases is not None:
            phases.check()


class MixedTemplateYAMLRuntimeReadback(NativeTemplateYAMLRuntimeReadback):
    def __init__(self, hass, chain, evidence, consumers, *, guard):
        _need(type(consumers) is MixedYAMLConsumers)
        self.dependents = consumers
        super().__init__(hass, chain, evidence, guard=guard)

    def assert_change(self, change):
        self.evidence.assert_receipt(change.receipt)
        self.assert_reload_current()
        domains = self.receipt.changed_domains
        template = [row for row in self.receipt.runtime_expected if row[0] == 'template']
        _need(change.receipt == self.receipt and self.receipt.ready_for_apply
              and 'template' in domains and len(domains) in (2, 3)
              and set(domains) <= {'automation', 'script', 'template'}
              and tuple(row[0] for row in self.receipt.runtime_expected) == domains
              and len(template) == 1 and None not in template[0][1:]
              and template[0][1] != template[0][2])
        self.evidence.template_candidate_sources.assert_original(
            self.receipt, self.evidence.template_sources)

    def _sample(self, observed):
        position, tokens = super()._sample(observed)
        phases = self.dependents.phases
        phases.check()
        _need(all(value is position for domain, value in phases.positions
                  if domain in self.receipt.changed_domains))
        return position, tuple(tokens) + (('dependent_yaml', phases.positions, phases.sample),)

    @property
    def history_consumers(self):
        ids = {value[0] for value in self.identities.values()}
        ids.update(row.entity_id for row in self.dependents.phases.sample[0])
        return tuple(sorted(ids))

    def begin_domain_reload(self, domain, direction):
        self.dependents.phases.check()
        if domain in ('automation', 'script'):
            return self.dependents.phases.begin_reload(domain, direction)
        _need(domain == 'template')
        return None

    def complete_domain_reload(self, ticket):
        if ticket is not None:
            self.dependents.phases.complete_reload(ticket)
        self.dependents.phases.check()

    def abandon_domain_reload(self, ticket):
        if ticket is not None and self.dependents.phases.active is not None:
            self.dependents.phases.abandon_reload(ticket)
