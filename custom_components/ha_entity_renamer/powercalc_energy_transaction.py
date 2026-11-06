"""Receipted use of the existing native Energy adapter in a Powercalc plan."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json

from .energy_native import NativeEnergy, PreparedEnergy
from .native_adapters import EnergyTransactionAdapter
from .native_discovery_inputs import async_observe_discovery_inputs
from .native_input_baseline import _drained
from .native_registry_receipts import RegistryRawObservation
from .powercalc_energy_receipts import observe_energy, live_inputs, _cost_registry_projection
from .powercalc_lifecycle import PowercalcLifecycle
from .transaction import PhaseContext, Refused


def _need(value, reason):
    if not value:
        raise Refused('powercalc_energy_adapter_' + reason)


def _cost_registry_preimage(registry, states):
    """No pending or foreign in-memory Core registry before native detach."""
    from homeassistant.helpers.json import _orjson_bytes_default_encoder
    from .native_registry_receipts import _exact_json_equal, _freeze_json
    store = registry._store
    actual = json.loads(_orjson_bytes_default_encoder(registry._data_to_save()))
    _need(not store._write_lock.locked() and store._load_future is None and store._data is None
          and store._delay_handle is None and store._unsub_final_write_listener is None
          and _exact_json_equal(actual, _freeze_json(states['core.entity_registry']['document']['data'])),
          'cost_registry_live_preimage_changed')


class PowercalcEnergyEffect:
    """Ephemeral receipt witness issued only after the actual native adapter."""
    def __init__(self):
        raise Refused('powercalc_energy_effect_requires_actual_call')

    def claim(self, chain, intent, context):
        issuer = self.issuer
        _need(issuer._issued is self and issuer.chain is chain and issuer.context is context
              and chain.active is intent and self.intent is intent
              and asyncio.current_task() is self.owner and not self.used, 'effect_witness_changed')
        issuer._scope()
        return self.before, self.after, self.cost_before, self.cost_after

    def completed(self, reference):
        _need(not self.used and self.issuer._issued is self, 'effect_witness_changed')
        self.used = True
        self.issuer._issued = None


class ReceiptedPowercalcEnergyAdapter(EnergyTransactionAdapter):
    """One journal phase, one actual native update, one chain receipt."""
    def __init__(self, native, lifecycle, phase_loader, verify_inventory, verify_original_inventory):
        from .controlled_powercalc_handoff import ControlledPowercalcHandoff
        controlled = type(lifecycle) is ControlledPowercalcHandoff
        _need(type(native) is NativeEnergy and type(lifecycle) in (PowercalcLifecycle, ControlledPowercalcHandoff)
              and native.hass is (lifecycle.hass if controlled else lifecycle.lease.hass) and callable(phase_loader)
              and callable(verify_inventory) and callable(verify_original_inventory), 'typed_scope_required')
        self.controlled = controlled
        self.lifecycle, self.chain, self.phase_loader = lifecycle, lifecycle.chain, phase_loader
        self.context, self._issued = None, None
        def resolver(reference):
            matches = [index for index, step in enumerate(self.chain.plan.steps)
                       if step.prepared_ref == reference and step.adapter == 'energy']
            _need(len(matches) == 1 and (self.context is None or self.context.index == matches[0]),
                  'prepared_reference_changed')
            value = self.chain._original_change(matches[0])
            _need(type(value) is PreparedEnergy, 'energy_preparation_required')
            return value
        super().__init__(native, resolver, verify_inventory, verify_original_inventory)

    def bind_phase_context(self, context):
        _need(context is None or type(context) is PhaseContext, 'phase_required')
        self.context = context

    def _scope(self):
        if self.controlled:
            self.lifecycle.require_inactive()
        else:
            self.lifecycle.check()
        _need(self.native.hass is (self.lifecycle.hass if self.controlled else self.lifecycle.lease.hass)
              and not self.native._failed,
              'native_scope_changed')

    async def _durable(self, guard, expected=None):
        self._scope(); guard()
        context = self.context if expected is None else expected
        _need(type(context) is PhaseContext and self.context == context, 'phase_changed')
        actual = await self.phase_loader(self.chain.operation_id)
        self._scope(); guard()
        _need(actual == context.record, 'durable_phase_changed')
        return context

    async def _core(self, guard):
        self._scope(); guard()
        observed = await async_observe_discovery_inputs(self.native.hass, guard=self._scope)
        self._scope(); guard()
        _need(observed.version == self.chain.version
              and observed.config_root == self.chain.original_receipt.config_root, 'core_scope_changed')
        return tuple(RegistryRawObservation(row.key, row.raw, row.identity) for row in observed.files)

    async def _effect(self, step, guard, direction):
        if not self.controlled:
            self.lifecycle.lease._task_check()
        context = await self._durable(guard)
        context.require_effect(step, direction)
        _need(self._issued is None, 'effect_already_issued')
        change = self._change(step)
        initial = (self.chain.reconstruct_inputs() if self.controlled else self.lifecycle.lease.state)
        if self.controlled:
            self.lifecycle.root.observation_state = initial
        await _drained(self.native.hass.async_add_executor_job,
                       lambda: initial.assert_current(self.chain.original_receipt))
        self._scope(); guard()
        before = await self._core(guard)
        energy_before = await observe_energy(self.native, self.lifecycle)
        old_value, new_value = ((change.before.value, change.after) if direction == 'forward'
                                else (change.after, change.before.value))
        check = lambda: (self._scope(), guard()) and None
        cost_before = await self.native.observe_generated_costs(old_value, new_value, check)
        intent = await self.chain.begin_powercalc_energy(context, before, energy_before, cost_before)
        if not self.controlled:
            self.lifecycle.lease.track_intent(intent)
        self._scope(); guard()
        await self._durable(guard, context)
        prior = await observe_energy(self.native, self.lifecycle)
        _need(prior == energy_before, 'post_intent_energy_changed')
        before = await self._core(guard)
        fence = live_inputs(self.chain, intent, context, prior)
        await _drained(self.native.hass.async_add_executor_job,
                       lambda: fence.assert_current(self.chain.original_receipt))
        self._scope(); guard()
        if direction == 'forward':
            actual_change = change
        else:
            current = await self.native.read()
            check()
            _need(current.value == change.after and current.revision == step.after_revision,
                  'energy_compensation_conflict')
            actual_change = PreparedEnergy(current, change.before.value, step.before_revision)
        async def before_cost_effect():
            from homeassistant.helpers import entity_registry as er
            await _drained(self.native.hass.async_add_executor_job,
                           lambda: fence.assert_current(self.chain.original_receipt))
            self._scope(); guard()
            registry = er.async_get(self.native.hass)
            states, _, _, _, pending = self.chain._reconstruct()
            _need(pending == intent.reference, 'cost_registry_intent_changed')
            _cost_registry_preimage(registry, states)
        await self.native.write(actual_change, check, cost_preimage=cost_before,
                                before_cost_effect=before_cost_effect)
        self._scope(); guard()
        await self._durable(guard, context)
        energy_after = await observe_energy(self.native, self.lifecycle)
        fence = live_inputs(self.chain, intent, context, energy_after)
        if cost_before:
            from homeassistant.helpers import entity_registry as er
            from homeassistant.helpers.json import _orjson_bytes_default_encoder
            registry = er.async_get(self.native.hass)
            states, _, _, _, pending = self.chain._reconstruct()
            _need(pending == intent.reference, 'cost_registry_intent_changed')
            original = states['core.entity_registry']['document']
            def projected():
                result = deepcopy(original)
                result['data'] = json.loads(_orjson_bytes_default_encoder(registry._data_to_save()))
                return result
            expected = projected()
            changes = _cost_registry_projection(original, expected, cost_before)
            if changes:
                store = registry._store
                _need(not store._write_lock.locked() and store._load_future is None,
                      'cost_registry_store_busy')
                await _drained(self.native.hass.async_add_executor_job,
                               lambda: fence.assert_current(self.chain.original_receipt))
                self._scope(); guard()
                from .native_registry_receipts import _exact_json_equal, _freeze_json
                _need(_exact_json_equal(projected(), _freeze_json(expected)) and registry._store is store
                      and not store._write_lock.locked() and store._load_future is None,
                      'cost_registry_changed')
                await store.async_save(registry._data_to_save())
                self._scope(); guard()
        after = await self._core(guard)
        if cost_before and changes:
            from .accepted_input_state import AcceptedInputState
            from .native_input_baseline import _loads
            from .native_registry_receipts import _sha
            sources, documents = fence.sources(), fence.documents()
            observation = after[0]
            _need(_exact_json_equal(_loads(observation.raw), _freeze_json(expected)),
                  'cost_registry_readback_changed')
            sources['.storage/core.entity_registry'][:4] = [_sha(observation.raw), *observation.identity]
            documents['core.entity_registry'] = expected
            fence = AcceptedInputState(fence.original_json, fence.head, sources, documents,
                                       live_check=fence._live_check)
        await _drained(self.native.hass.async_add_executor_job,
                       lambda: fence.assert_current(self.chain.original_receipt))
        self._scope(); guard()
        witness = object.__new__(PowercalcEnergyEffect)
        witness.issuer, witness.intent, witness.owner = self, intent, asyncio.current_task()
        witness.before, witness.after, witness.used = energy_before, energy_after, False
        witness.cost_before, witness.cost_after = cost_before, list(self.native.last_generated_cost_effect)
        self._issued = witness
        receipt = await self.chain.complete_powercalc_energy(intent, context, before, after, witness)
        if self.controlled:
            self.lifecycle.root.observation_state = self.chain.reconstruct_inputs()
        else:
            self.lifecycle.lease.accept_receipt(receipt)
        self._scope(); guard()

    async def apply(self, step, guard):
        await self._effect(step, guard, 'forward')

    async def compensate(self, step, guard):
        await self._effect(step, guard, 'rollback')
