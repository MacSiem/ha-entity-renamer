"""Exact private selective Template + dependent YAML runtime composition.

Template prefix transport stays in the established native state adapter. This
suffix adds independently sealed dependent phases; no Template reload occurs.
"""
from __future__ import annotations

from dataclasses import dataclass
import inspect
from pathlib import Path

from .transaction import Position, Refused
from .native_template_state_yaml import (NativeTemplateStateYAMLRuntimeReadback,
    PreparedMixedTemplateStateYAML, PreparedMixedPowercalcYAML, ReceiptedTemplateStateYAMLTransactionAdapter,
    _TemplateStateYAMLHooks, _TemplateStateRuntimeCheck, assert_state_yaml_composition)
from .mixed_yaml_runtime import MixedYAMLConsumers


def _need(value):
    if not value:
        raise Refused('mixed_template_state_runtime_unconfirmed') from None


def _owner_in_domain(fence, domain):
    return fence is not None and fence.domain == domain


def _automation_owner_replaced(fence, replaced):
    return (_owner_in_domain(fence, 'automation')
            and fence.original_row.native_key in replaced)


@dataclass(frozen=True)
class _MixedStateRuntimeCheck:
    base: _TemplateStateRuntimeCheck
    phases: object
    positions: tuple
    dependent_sample: tuple

    @property
    def owner(self):
        return self.base.owner

    @property
    def state(self):
        return self.base.state

    @property
    def sample(self):
        return self.base.sample

    def check(self):
        _need(type(self.base) is _TemplateStateRuntimeCheck)
        self.base.check()
        self.phases.check()
        _need(self.phases.positions == self.positions and self.phases.sample == self.dependent_sample)


class NativeMixedTemplateStateYAMLRuntimeReadback(NativeTemplateStateYAMLRuntimeReadback):
    def __init__(self, hass, chain, composition, consumers, *, guard, entry_witness=None):
        _need(type(consumers) is MixedYAMLConsumers)
        self.dependents, self._composite = consumers, None
        super().__init__(hass, chain, composition, guard=guard, entry_witness=entry_witness)

    def assert_change(self, change):
        from .native_external_yaml import PreparedExternalStateYAML
        _need(type(change) in (PreparedMixedTemplateStateYAML, PreparedMixedPowercalcYAML, PreparedExternalStateYAML))
        change.checked()
        composition = assert_state_yaml_composition(self.chain, change)
        _need(composition.state_config_program == self.program and change.receipt == self.receipt
              and str(Path(self.hass.config.config_dir).resolve()) == self.receipt.config_root)

    def _check(self, state, live):
        super()._check(state, live)
        self.dependents.phases.check()

    async def position_at_inputs(self, change, state, **live):
        self._composite = None
        position = await super().position_at_inputs(change, state, **live)
        if position not in (Position.BEFORE, Position.AFTER):
            return Position.UNKNOWN
        phases = self.dependents.phases
        phases.check()
        witness = _MixedStateRuntimeCheck(self._last_runtime, phases, phases.positions, phases.sample)
        witness.check()
        self._composite = witness
        return position

    def runtime_check(self, expected):
        witness = self._composite
        _need(type(witness) is _MixedStateRuntimeCheck and witness.owner is self
              and witness.sample[0] is expected)
        witness.check()
        return witness

    def assert_dependents_position(self, expected):
        phases = self.dependents.phases
        phases.check()
        _need(all(value is expected for domain, value in phases.positions
                  if domain in self.receipt.changed_domains))

    @property
    def history_consumers(self):
        ids = {value[0] for value in self.identities.values()}
        ids.update(row.entity_id for row in self.dependents.phases.sample[0])
        if self.entry_witness is not None:
            rows = [row for row in self.chain.accepted_documents()['core.entity_registry']['data']['entities']
                    if row.get('config_entry_id') == self.entry_witness.entry_id]
            _need(len(rows) == 1)
            ids.add(rows[0]['entity_id'])
        return tuple(sorted(ids))


class _MixedStateCompletionGuard:
    def __init__(self, base):
        self.base, self.witness = base, None

    def __call__(self):
        value = self.base()
        if inspect.isawaitable(value):
            if inspect.iscoroutine(value):
                value.close()
            _need(False)
        _need(value is None or value is True)
        if self.witness is not None:
            self.witness.check()

    def arm(self, witness):
        _need(type(witness) is _MixedStateRuntimeCheck and witness.state._live_check is None)
        witness.check()
        self.witness = witness


class _MixedStateYAMLHooks(_TemplateStateYAMLHooks):
    async def activate(self):
        # Existing full-prefix proof replaces Template activation completely.
        await super().activate()
        observer = self.adapter.observer
        from .native_template_yaml_server import _script_prefix_composition
        if _script_prefix_composition(observer.composition):
            observer.dependents.phases.check()
            return
        from .native_template_yaml_server import _controlled_reload
        for domain in ('script', 'automation'):
            if domain not in self.change.receipt.changed_domains:
                continue
            await self.checkpoint()
            if domain == 'automation' and not _controlled_reload(observer.composition):
                await observer.automation_lifecycle.stage()
            else:
                phases = observer.dependents.phases
                ticket = phases.begin_reload(domain, self.context.record.direction)
                try:
                    if domain == 'automation':
                        replaced = await observer.controlled_automation.stage_preserving_enabled()
                        fence = getattr(phases, 'own_fence', None)
                        if _automation_owner_replaced(fence, replaced):
                            fence.rebind_after_controlled_reload()
                    else:
                        await self.adapter.hass.services.async_call(domain, 'reload', {},
                            blocking=True, context=self.adapter.context)
                        from .controlled_script_composition import ControlledScriptComposition, is_controlled_script
                        if is_controlled_script(observer.composition):
                            _need(_owner_in_domain(phases.own_fence, 'script'))
                            phases.own_fence.rebind_after_controlled_script_reload()
                    phases.complete_reload(ticket)
                except BaseException as error:
                    import logging
                    code = str(error) if str(error) in ('automation_external_state_changed',
                        'automation_enabled_population_changed', 'native_automation_transition_unconfirmed') else type(error).__name__
                    logging.getLogger(__name__).warning('Controlled activation failed: %s', code)
                    if phases.active is not None:
                        phases.abandon_reload(ticket)
                    raise
            await self.checkpoint()
        position = await self.runtime_position()
        _need(position in (Position.BEFORE, Position.AFTER))
        observer.assert_dependents_position(position)
        observer.runtime_check(position).check()
        await self.checkpoint()


class ReceiptedMixedTemplateStateYAMLTransactionAdapter(ReceiptedTemplateStateYAMLTransactionAdapter):
    def _change(self, step):
        change = self.resolver(step.prepared_ref)
        from .native_external_yaml import PreparedExternalStateYAML
        _need(type(change) in (PreparedMixedTemplateStateYAML, PreparedMixedPowercalcYAML, PreparedExternalStateYAML))
        change.checked()
        _need(bool(self.chain.plan.steps) and step == self.chain.plan.steps[-1]
              and step.adapter == 'yaml' and change == self.chain._original_change(self.chain.yaml_index)
              and change.receipt == self.chain.original_receipt
              and change.identity == step.identity and change.before_revision == step.before_revision
              and change.after_revision == step.after_revision
              and change.receipt.config_root == str(Path(self.hass.config.config_dir).resolve())
              and change.receipt.version == self.runtime_version)
        _need(type(self.observer) is NativeMixedTemplateStateYAMLRuntimeReadback
              and self.observer.chain is self.chain
              and type(self._template_completion) is _MixedStateCompletionGuard)
        self.observer.assert_change(change)
        return change

    def _hooks(self, change, context, intent, guard):
        return _MixedStateYAMLHooks(self, change, context, intent, guard)


class _ControlledPowercalcYAMLHooks(_MixedStateYAMLHooks):
    async def activate(self):
        await self.checkpoint()
        await self.adapter.handoff.activate(self.context, self.intent, self.state,
                                             service_context=self.adapter.context)
        await self.checkpoint()
        await super().activate()


class ReceiptedControlledPowercalcYAMLAdapter(ReceiptedMixedTemplateStateYAMLTransactionAdapter):
    def __init__(self, *args, handoff, **kwargs):
        from .controlled_powercalc_handoff import ControlledPowercalcHandoff
        _need(type(handoff) is ControlledPowercalcHandoff)
        self.handoff = handoff
        super().__init__(*args, **kwargs)

    def _change(self, step):
        change = super()._change(step)
        _need(type(change) is PreparedMixedPowercalcYAML)
        return change

    def _hooks(self, change, context, intent, guard):
        return _ControlledPowercalcYAMLHooks(self, change, context, intent, guard)

    def _completion_extra(self, hooks, context):
        _need(type(hooks) is _ControlledPowercalcYAMLHooks and hooks.context == context
              and self.handoff.setup_reference is not None)
        return {'powercalc_setup': self.handoff}


def compose_mixed_template_state_yaml_coordinator(adapters, journal, guard, *, required_scopes, lock=None,
                                                 record_admission=None, rollback_order=None):
    from .transaction import Coordinator
    adapter = adapters.get('yaml')
    _need(type(adapter) in (ReceiptedMixedTemplateStateYAMLTransactionAdapter,
                            ReceiptedControlledPowercalcYAMLAdapter)
          and type(adapter.observer) is NativeMixedTemplateStateYAMLRuntimeReadback)
    completion = _MixedStateCompletionGuard(guard)
    adapter._template_completion = completion
    return Coordinator(adapters, journal, completion, required_scopes=required_scopes, lock=lock,
                       record_admission=record_admission, rollback_order=rollback_order)
