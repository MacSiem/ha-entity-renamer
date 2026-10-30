"""Explicit selective YAML-last path on the SAME ORIGINAL receipt chain.

No reload, setup, synthetic service, runtime projection replacement or journal.
Runtime registry prefix and physical YAML image are independent coordinates.
Only the genuine receipted adapter owns writes and captures file CAS returns.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import inspect
import json
import os
from pathlib import Path

from .transaction import Position, Refused
from .yaml_transaction import PreparedYAML, YAMLFile, _decode_yaml, _digest, _sha, _hash
from .yaml_plan import Limits, _Reader
from .yaml_validation import assert_proposal_matches, assert_validation_current


@dataclass(frozen=True)
class PreparedTemplateStateYAML(PreparedYAML):
    source_program_sha256: str

    @property
    def identity(self):
        return 'template_state_yaml:' + _digest(asdict(self))

    def checked(self):
        if decode_template_state_yaml(asdict(self)) != self:
            raise ValueError('noncanonical_prepared_template_state_yaml')
        return self


def decode_template_state_yaml(data):
    """Exact new kind, sharing only the legacy bounded byte/graph codec."""
    if type(data) is not dict or set(data) != {'receipt', 'files', 'source_program_sha256'}:
        raise ValueError('invalid_prepared_template_state_yaml')
    program_sha = _sha(data['source_program_sha256'])
    base = _decode_yaml({key: data[key] for key in ('receipt', 'files')}, state_mode=True)
    return PreparedTemplateStateYAML(base.receipt, base.files, program_sha)


@dataclass(frozen=True)
class PreparedMixedTemplateStateYAML(PreparedYAML):
    source_program_sha256: str

    @property
    def identity(self):
        return 'template_mixed_state_yaml:' + _digest(asdict(self))

    def checked(self):
        if decode_mixed_template_state_yaml(asdict(self)) != self:
            raise ValueError('noncanonical_prepared_template_mixed_state_yaml')
        return self


def decode_mixed_template_state_yaml(data):
    """Exact mixed kind; program digest is bound to full sources by composition."""
    if type(data) is not dict or set(data) != {'receipt', 'files', 'source_program_sha256'}:
        raise ValueError('invalid_prepared_template_mixed_state_yaml')
    program_sha = _sha(data['source_program_sha256'])
    base = _decode_yaml({key: data[key] for key in ('receipt', 'files')}, mixed_state_mode=True)
    return PreparedMixedTemplateStateYAML(base.receipt, base.files, program_sha)


def capture_template_state_yaml(bundle, plan, mapping):
    return _capture_template_state_yaml(bundle, plan, mapping, PreparedTemplateStateYAML)


def capture_mixed_template_state_yaml(bundle, plan, mapping):
    return _capture_template_state_yaml(bundle, plan, mapping, PreparedMixedTemplateStateYAML)


def _capture_template_state_yaml(bundle, plan, mapping, prepared_type):
    """Capture actual ORIGINAL bytes under caller's locks, after actual check.

    This consumes the same native validation bundle as StateComposition. It
    neither recaptures a receipt nor calls the legacy activation readiness gate.
    Caller persists encode_change(result) BEFORE constructing the saga Plan.
    """
    from .yaml_validation import TemplateStateSourceValidation
    from .template_state_program import StateConfigProgram
    if type(bundle) is not TemplateStateSourceValidation or type(bundle.state_config_program) is not StateConfigProgram:
        raise ValueError('template_state_validation_bundle_required')
    receipt, program = bundle.receipt, bundle.state_config_program
    program.assert_sources(bundle.original_sources, bundle.candidate_sources,
                           plan=plan, mapping=mapping)
    assert_proposal_matches(receipt, plan)
    assert_validation_current(receipt)
    reader = _Reader(receipt.config_root, Limits())
    try:
        files = []
        for patch in plan.patches:
            before, info = reader.read(patch.path)
            if _hash(before) != patch.expected_sha256:
                raise Refused('yaml_source_changed')
            files.append(YAMLFile(patch.path, before.hex(), patch.proposed_bytes.hex(),
                                  info.st_dev, info.st_ino))
        result = prepared_type(receipt, tuple(files), program.sha256).checked()
        assert_validation_current(receipt)
        return result
    finally:
        os.close(reader.root)


def assert_state_yaml_composition(chain, change):
    """Every root/reopen binds the exact new preparation to retained sources."""
    from .native_template_state_composition import (TemplateStateComposition,
        MixedTemplateStateComposition, retain_state_composition)
    from .native_script_program_composition import ScriptTemplateStateComposition
    from .native_automation_program_composition import AutomationTemplateStateComposition
    expected = {PreparedTemplateStateYAML: (TemplateStateComposition,),
                PreparedMixedTemplateStateYAML: (MixedTemplateStateComposition, ScriptTemplateStateComposition, AutomationTemplateStateComposition)}
    if type(change) not in expected:
        raise Refused('template_state_yaml_preparation_required')
    from .native_template_composition import decode_composition
    from .native_template_yaml_server import _baseline_stamp
    from .prepared import canonical
    reference = chain.plan.native_inputs
    payload = chain.preparations.resolve(*reference[:2])
    if canonical(payload)[1] != reference[1]:
        raise Refused('template_state_yaml_preparation_changed')
    binding = getattr(chain, '_template_state_original_binding', None)
    baseline = chain.baseline
    if binding is None:
        if baseline.digest != reference[2]:
            raise Refused('template_state_yaml_original_baseline_changed')
        composition = decode_composition(payload.get('composition'), baseline)
        wire, check = retain_state_composition(composition, baseline)
        binding = (reference, _baseline_stamp(baseline), composition, wire, check)
        chain._template_state_original_binding = binding
    else:
        if (type(binding) is not tuple or len(binding) != 5 or binding[0] != reference
                or binding[1] != _baseline_stamp(baseline)):
            raise Refused('template_state_yaml_original_baseline_changed')
        composition = binding[2]
    binding[4]()
    if binding[3] != payload.get('composition'):
        raise Refused('template_state_yaml_original_composition_changed')
    if (type(composition) not in expected[type(change)] or baseline != chain.baseline
            or change.receipt != chain.original_receipt or baseline.receipt != change.receipt
            or change.source_program_sha256 != composition.state_config_program.sha256):
        raise Refused('template_state_yaml_original_composition_mismatch')
    patches = composition.yaml_plan.patches
    if (tuple((row.path, _hash(row.before), row.after) for row in change.files)
            != tuple((row.path, row.expected_sha256, row.proposed_bytes) for row in patches)):
        raise Refused('template_state_yaml_original_plan_mismatch')
    return composition


def _need(value, reason):
    if not value:
        raise Refused(reason)


class NativeTemplateStateYAMLRuntimeReadback:
    """Full source-to-live prefix proof, followed by physical file position.

    BEFORE/AFTER describe disk only AFTER the runtime proof succeeds. Reverse
    YAML restores ORIGINAL while registry/runtime may still be at FINAL.
    """
    def __init__(self, hass, chain, composition, *, guard):
        from .native_registry_receipts import RegistryReceiptChain
        from .native_template_state_composition import TemplateStateComposition, MixedTemplateStateComposition
        from .native_template_mixed_state_yaml import NativeMixedTemplateStateYAMLRuntimeReadback
        from .native_template_state_binding import _StateRuntime
        from .native_script_program_composition import ScriptTemplateStateComposition
        from .native_automation_program_composition import AutomationTemplateStateComposition
        expected = {NativeTemplateStateYAMLRuntimeReadback: (TemplateStateComposition,),
                    NativeMixedTemplateStateYAMLRuntimeReadback: (MixedTemplateStateComposition, ScriptTemplateStateComposition, AutomationTemplateStateComposition)}
        _need(type(chain) is RegistryReceiptChain and type(self) in expected
              and type(composition) in expected[type(self)], 'template_state_original_chain_required')
        composition.assert_baseline(chain.baseline)
        self.composition = composition
        self.hass, self.chain, self.program, self.guard = hass, chain, composition.state_config_program, guard
        self.receipt, self._last_runtime = chain.original_receipt, None
        self.native = _StateRuntime(hass)
        _need(self.native.native.version == self.receipt.version,
              'template_state_native_version_changed')
        _need(chain.yaml_index is not None, 'template_state_yaml_step_required')
        self.assert_change(chain._original_change(chain.yaml_index))
        documents = {row.key: json.loads(row.raw) for row in chain.baseline.files}
        rows = [row for row in documents['core.entity_registry']['data']['entities']
                if row['platform'] == 'template']
        self.identities = {(row['entity_id'].split('.')[0], row['unique_id']):
                           (row['entity_id'], row['id']) for row in rows}
        self.physical_consumers = composition.physical_consumers
        _need(len(self.identities) == len(rows), 'template_state_original_identity_ambiguous')
        for domain, key, entity_id in self.physical_consumers:
            _need((domain, key) not in self.identities, 'template_state_original_identity_ambiguous')
            self.identities[domain, key] = (entity_id, None)

    def assert_change(self, change):
        _need(type(change) is PreparedTemplateStateYAML,
              'template_state_yaml_preparation_required')
        change.checked()
        composition = assert_state_yaml_composition(self.chain, change)
        _need(composition.state_config_program == self.program
              and change.receipt == self.receipt
              and self.hass.config.config_dir == self.receipt.config_root,
              'template_state_yaml_runtime_binding_changed')

    def _check(self, state, live):
        from .native_template_state_binding import _state_inputs
        value = self.guard()
        if inspect.isawaitable(value):
            if inspect.iscoroutine(value):
                value.close()
            raise Refused('template_state_synchronous_guard_required')
        _need(value is None or value is True, 'template_state_guard_rejected')
        self.assert_change(self.chain._original_change(self.chain.yaml_index))
        _state_inputs(self.chain, state, **live)

    def _sample(self, runtime, state, live):
        from .native_template_state_binding import accepted_state_prefix, sample_state_bindings
        prefix = accepted_state_prefix(self.chain, self.program, input_state=state, **live)
        current = {p: r[0] for p, r in state.sources().items() if not p.startswith('.storage/')}
        original = {p: d for p, d, *_ in self.receipt.source_files if not p.startswith('.storage/')}
        proposed = original | dict(self.receipt.proposed_files)
        _need(current in (original, proposed), 'template_state_physical_yaml_image_changed')
        image = 'original' if current == original else 'proposed'
        snapshot = sample_state_bindings(runtime, self.program, prefix, image,
                                         self.chain.accepted_documents())
        return (Position.BEFORE if image == 'original' else Position.AFTER), snapshot

    async def position(self, change):
        return Position.UNKNOWN  # No implicit accepted/live input adoption.

    async def position_at_inputs(self, change, state, *, live_intent=None,
                                 live_context=None, live_calls=()):
        from .native_template_state_binding import (async_observe_accepted_state_bindings,
                                                    _StateRuntime)
        from .native_discovery_inputs import async_observe_discovery_inputs
        from .yaml_validation import _settled_executor
        self._last_runtime = None
        live = dict(live_intent=live_intent, live_context=live_context, live_calls=live_calls)
        try:
            self.assert_change(change)
            self._check(state, live)
            snapshot = await async_observe_accepted_state_bindings(self.hass, self.chain,
                self.program, guard=lambda: self._check(state, live), input_state=state, **live)
            self._check(state, live)
            runtime = self.native
            await _settled_executor(self.hass, runtime.sources)
            self._check(state, live)
            observed = await async_observe_discovery_inputs(self.hass,
                                                guard=lambda: self._check(state, live))
            self._check(state, live)
            sample = self._sample(runtime, state, live)
            _need(sample[1].same_bindings(snapshot), 'template_state_runtime_changed')
            witness = _TemplateStateRuntimeCheck(self, state, live, runtime, observed, sample)
            witness.check()
            self._last_runtime = witness
            return sample[0]
        except asyncio.CancelledError:
            raise
        except Exception:
            return Position.UNKNOWN

    def runtime_check(self, expected):
        witness = self._last_runtime
        _need(type(witness) is _TemplateStateRuntimeCheck and witness.owner is self
              and witness.sample[0] is expected, 'template_state_runtime_check_required')
        witness.check()
        return witness

    async def verify_inventory(self, plan, *, rollback):
        # Actual whole-scope callback remains required by the receipted adapter.
        return False


@dataclass(frozen=True)
class _TemplateStateRuntimeCheck:
    owner: NativeTemplateStateYAMLRuntimeReadback
    state: object
    live: dict
    runtime: object
    observed: object
    sample: tuple

    def check(self):
        from .native_discovery_inputs import assert_discovery_managers_current
        from .native_template_accepted_consumers import _documents
        owner = self.owner
        owner._check(self.state, self.live)
        self.runtime.sources()
        self.runtime.bindings()
        self.state.assert_current(owner.receipt)
        assert_discovery_managers_current(owner.hass, self.observed,
                                          guard=lambda: owner._check(self.state, self.live))
        _documents(self.state, self.observed)
        current = owner._sample(self.runtime, self.state, self.live)
        _need(current[0] is self.sample[0] and current[1].same_bindings(self.sample[1]),
              'template_state_runtime_check_changed')


class _TemplateStateCompletionGuard:
    """Exact explicit suffix, armed only by the final independent getter."""
    def __init__(self, base):
        self.base, self.witness = base, None

    def __call__(self):
        value = self.base()
        if inspect.isawaitable(value):
            if inspect.iscoroutine(value):
                value.close()
            raise Refused('template_state_synchronous_guard_required')
        _need(value is None or value is True, 'template_state_guard_rejected')
        if self.witness is not None:
            self.witness.check()

    def arm(self, witness):
        _need(type(witness) is _TemplateStateRuntimeCheck
              and witness.state._live_check is None,
              'template_state_accepted_runtime_check_required')
        witness.check()
        self.witness = witness


# Imports follow codec declarations: RegistryReceiptChain imports the exact
# prepared type above, and must not be pulled into its own initialization cycle.
from .native_yaml_transaction import ReceiptedYAMLTransactionAdapter, _YAMLHooks


class _TemplateStateYAMLHooks(_YAMLHooks):
    async def runtime_position(self):
        return await self.adapter.observer.position_at_inputs(self.change, self.state(),
            live_intent=self.intent, live_context=self.context, live_calls=self.calls)

    async def activate(self):
        # No service seam at all. Source-to-live FULL prefix proof is the sole
        # activation: registry transport has already installed the final runtime.
        await self.checkpoint()
        position = await self.runtime_position()
        _need(position in (Position.BEFORE, Position.AFTER),
              'template_state_runtime_activation_unconfirmed')
        self.adapter.observer.runtime_check(position).check()
        await self.checkpoint()

    async def settle_reload(self):
        # No reload was initiated. Native prefix getter requires settled graphs.
        return None


class ReceiptedTemplateStateYAMLTransactionAdapter(ReceiptedYAMLTransactionAdapter):
    def _change(self, step):
        change = self.resolver(step.prepared_ref)
        _need(type(change) is PreparedTemplateStateYAML,
              'template_state_yaml_preparation_required')
        change.checked()
        _need(bool(self.chain.plan.steps) and step == self.chain.plan.steps[-1]
              and step.adapter == 'yaml' and change == self.chain._original_change(self.chain.yaml_index)
              and change.receipt == self.chain.original_receipt
              and change.identity == step.identity
              and change.before_revision == step.before_revision
              and change.after_revision == step.after_revision
              and change.receipt.config_root == str(Path(self.hass.config.config_dir).resolve())
              and change.receipt.version == self.runtime_version,
              'template_state_yaml_preparation_mismatch')
        _need(type(self.observer) is NativeTemplateStateYAMLRuntimeReadback
              and self.observer.chain is self.chain,
              'template_state_yaml_observer_required')
        _need(type(self._template_completion) is _TemplateStateCompletionGuard,
              'template_state_completion_guard_required')
        self.observer.assert_change(change)
        return change

    def _hooks(self, change, context, intent, guard):
        return _TemplateStateYAMLHooks(self, change, context, intent, guard)

    def _runtime_check(self, change, position):
        return self.observer.runtime_check(position)

    async def _runtime_position(self, change, state):
        return await self.observer.position_at_inputs(change, state)


def compose_template_state_yaml_coordinator(adapters, journal, guard, *, required_scopes, lock=None):
    from .transaction import Coordinator
    adapter = adapters.get('yaml')
    _need(type(adapter) is ReceiptedTemplateStateYAMLTransactionAdapter
          and type(adapter.observer) is NativeTemplateStateYAMLRuntimeReadback,
          'template_state_yaml_composition_required')
    completion = _TemplateStateCompletionGuard(guard)
    adapter._template_completion = completion
    return Coordinator(adapters, journal, completion, required_scopes=required_scopes, lock=lock)


# Short root composition API; each alias names the SAME exact class/function.
StateYAMLRuntimeReadback = NativeTemplateStateYAMLRuntimeReadback
StateReceiptedYAMLTransactionAdapter = ReceiptedTemplateStateYAMLTransactionAdapter
compose_state_yaml_coordinator = compose_template_state_yaml_coordinator
