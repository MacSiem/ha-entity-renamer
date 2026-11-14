"""Explicit selective YAML-last path on the SAME ORIGINAL receipt chain.

No reload, setup, synthetic service, runtime projection replacement or journal.
Runtime registry prefix and physical YAML image are independent coordinates.
Only the genuine receipted adapter owns writes and captures file CAS returns.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, fields
import inspect
import logging
import json
import os
from pathlib import Path

from .transaction import Position, Refused
from .yaml_transaction import PreparedYAML, YAMLFile, _decode_yaml, _digest, _sha, _hash
from .yaml_plan import Limits, _Reader
from .yaml_validation import assert_proposal_matches, assert_validation_current


def _identity_payload(value):
    payload = asdict(value)
    if value.receipt.checker_repairs_snapshot is None:
        payload['receipt'].pop('checker_repairs_snapshot', None)
    if value.receipt.source_links is None:
        payload['receipt'].pop('source_links', None)
    return payload


@dataclass(frozen=True)
class PreparedTemplateStateYAML(PreparedYAML):
    source_program_sha256: str

    @property
    def identity(self):
        return 'template_state_yaml:' + _digest(_identity_payload(self))

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
        return 'template_mixed_state_yaml:' + _digest(_identity_payload(self))

    def checked(self):
        if decode_mixed_template_state_yaml(asdict(self)) != self:
            raise ValueError('noncanonical_prepared_template_mixed_state_yaml')
        return self


def decode_mixed_template_state_yaml(data):
    """Exact mixed kind; program digest is bound to full sources by composition."""
    if type(data) is not dict or set(data) != {'receipt', 'files', 'source_program_sha256'}:
        raise ValueError('invalid_prepared_template_mixed_state_yaml')
    program_sha = _sha(data['source_program_sha256'])
    base = _decode_yaml({key: data[key] for key in ('receipt', 'files')}, mixed_state_mode=True,
                        dependent_owner_mode=True)
    return PreparedMixedTemplateStateYAML(base.receipt, base.files, program_sha)


@dataclass(frozen=True)
class PreparedMixedPowercalcYAML(PreparedYAML):
    """One YAML effect bound to both source proofs under one receipt."""
    source_program_sha256: str
    powercalc_original_sha256: str
    powercalc_candidate_sha256: str

    @property
    def original_sources_sha256(self):
        return self.powercalc_original_sha256

    @property
    def candidate_sources_sha256(self):
        return self.powercalc_candidate_sha256

    @property
    def identity(self):
        return 'template_powercalc_yaml:' + _digest(_identity_payload(self))

    def checked(self):
        if decode_mixed_powercalc_yaml(asdict(self)) != self:
            raise ValueError('noncanonical_prepared_mixed_powercalc_yaml')
        return self


def decode_mixed_powercalc_yaml(data):
    if type(data) is not dict or set(data) != {
            'receipt', 'files', 'source_program_sha256',
            'powercalc_original_sha256', 'powercalc_candidate_sha256'}:
        raise ValueError('invalid_prepared_mixed_powercalc_yaml')
    values = tuple(_sha(data[key]) for key in ('source_program_sha256',
                   'powercalc_original_sha256', 'powercalc_candidate_sha256'))
    external = bool(set(data['receipt']['changed_domains']) & {'homekit', 'utility_meter'})
    base = _decode_yaml({key: data[key] for key in ('receipt', 'files')},
                        mixed_powercalc_mode=True, external_state_mode=external)
    return PreparedMixedPowercalcYAML(base.receipt, base.files, *values)


def capture_mixed_powercalc_yaml(bundle, plan, mapping):
    from .powercalc_source_binding import OriginalPowercalcSources
    from .powercalc_candidate_sources import CandidatePowercalcSources
    original, candidate = bundle.evidence.powercalc_sources, bundle.evidence.powercalc_candidate_sources
    if type(original) is not OriginalPowercalcSources or type(candidate) is not CandidatePowercalcSources:
        raise ValueError('mixed_powercalc_original_required')
    candidate.assert_original(bundle.receipt, original)
    return _capture_template_state_yaml(bundle, plan, mapping, PreparedMixedPowercalcYAML,
                                        original.sha256, candidate.sha256)


def capture_template_state_yaml(bundle, plan, mapping):
    return _capture_template_state_yaml(bundle, plan, mapping, PreparedTemplateStateYAML)


def capture_mixed_template_state_yaml(bundle, plan, mapping):
    return _capture_template_state_yaml(bundle, plan, mapping, PreparedMixedTemplateStateYAML)


def _capture_template_state_yaml(bundle, plan, mapping, prepared_type, *additional_seals):
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
        result = prepared_type(receipt, tuple(files), program.sha256, *additional_seals).checked()
        assert_validation_current(receipt)
        return result
    finally:
        os.close(reader.root)


def _receipt_values(receipt):
    """Expose every field of the exact typed receipt for immutable comparison."""
    from .yaml_validation import ValidationReceipt
    if type(receipt) is not ValidationReceipt:
        raise Refused('template_state_yaml_original_composition_mismatch')
    return tuple(getattr(receipt, field.name) for field in fields(ValidationReceipt))


def retained_powercalc_original(chain):
    """Reuse authenticated immutable ORIGINAL, never live state or receipts."""
    if not chain._powercalc_preparations_unchanged():
        return None
    binding = getattr(chain, '_template_state_original_binding', None)
    if binding is None:
        return None
    from .native_template_yaml_server import _baseline_stamp
    from .native_registry_receipts import _exact_json_equal
    from .controlled_powercalc_composition import ControlledPowercalcComposition
    if (type(binding) is not tuple or len(binding) != 8
            or binding[0] != chain.plan.native_inputs
            or binding[1] != _baseline_stamp(chain.baseline)
            or type(binding[2]) is not ControlledPowercalcComposition
            or not _exact_json_equal(binding[3], binding[7])
            or not _exact_json_equal(_receipt_values(chain.original_receipt), binding[6])):
        raise Refused('powercalc_retained_original_changed')
    binding[4](binding[2])
    return binding[2]


def assert_state_yaml_composition(chain, change):
    """Every root/reopen binds the exact new preparation to retained sources."""
    from .native_template_state_composition import (TemplateStateComposition,
        MixedTemplateStateComposition, ControlledReloadComposition, ControlledReloadEntryComposition, retain_state_composition)
    from .native_script_program_composition import ScriptTemplateStateComposition
    from .native_automation_program_composition import AutomationTemplateStateComposition
    from .controlled_script_composition import ControlledScriptComposition
    from .controlled_powercalc_composition import ControlledPowercalcComposition
    from .native_external_yaml import PreparedExternalStateYAML
    from .native_template_state_composition import ExternalStateComposition
    expected = {PreparedExternalStateYAML: (ExternalStateComposition,),
                PreparedTemplateStateYAML: (TemplateStateComposition,),
                PreparedMixedTemplateStateYAML: (MixedTemplateStateComposition, ControlledReloadComposition, ControlledReloadEntryComposition, ControlledScriptComposition, ScriptTemplateStateComposition, AutomationTemplateStateComposition),
                PreparedMixedPowercalcYAML: (ControlledPowercalcComposition,)}
    if type(change) not in expected:
        raise Refused('template_state_yaml_preparation_required')
    from .native_template_composition import decode_composition
    from .native_template_yaml_server import _baseline_stamp
    from .prepared import canonical_digest
    from .native_registry_receipts import _freeze_json, _exact_json_equal
    reference = chain.plan.native_inputs
    payload = chain.preparations.resolve(*reference[:2])
    binding = getattr(chain, '_template_state_original_binding', None)
    baseline = chain.baseline
    if binding is None:
        # Authenticate the entire payload once, then retain a detached tree of
        # immutable typed values. Each later fence compares EVERY node against
        # this authenticated tree; no mutable store object is trusted or cached.
        if canonical_digest(payload) != reference[1]:
            raise Refused('template_state_yaml_preparation_changed')
        if baseline.digest != reference[2]:
            raise Refused('template_state_yaml_original_baseline_changed')
        composition = decode_composition(payload.get('composition'), baseline)
        wire, check = retain_state_composition(composition, baseline)
        # Parse the authenticated receipt once; its detached recursively frozen
        # fields are checked against both live receipt objects at every fence.
        original_receipt = _freeze_json(_receipt_values(baseline.receipt))
        binding = (reference, _baseline_stamp(baseline), composition, wire, check,
                   _freeze_json(payload), original_receipt, _freeze_json(wire))
        chain._template_state_original_binding = binding
    else:
        if (type(binding) is not tuple or len(binding) != 8 or binding[0] != reference
                or binding[1] != _baseline_stamp(baseline)):
            raise Refused('template_state_yaml_original_baseline_changed')
        if not _exact_json_equal(payload, binding[5]):
            raise Refused('template_state_yaml_preparation_changed')
        composition = binding[2]
    binding[4](composition)
    if (not _exact_json_equal(binding[3], binding[7])
            or not _exact_json_equal(payload.get('composition'), binding[7])):
        raise Refused('template_state_yaml_original_composition_changed')
    if (type(composition) not in expected[type(change)] or baseline != chain.baseline
            or not _exact_json_equal(_receipt_values(chain.original_receipt), binding[6])
            or not _exact_json_equal(_receipt_values(change.receipt), binding[6])
            or change.source_program_sha256 != composition.state_config_program.sha256
            or (type(change) is PreparedMixedPowercalcYAML and
                (change.powercalc_original_sha256 != composition.evidence.powercalc_sources.sha256
                 or change.powercalc_candidate_sha256 != composition.evidence.powercalc_candidate_sources.sha256))):
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
    def __init__(self, hass, chain, composition, *, guard, entry_witness=None):
        from .native_registry_receipts import RegistryReceiptChain
        from .native_template_state_composition import TemplateStateComposition, MixedTemplateStateComposition, ExternalStateComposition, ControlledReloadComposition, ControlledReloadEntryComposition
        from .native_template_mixed_state_yaml import NativeMixedTemplateStateYAMLRuntimeReadback
        from .native_template_state_binding import _StateRuntime
        from .native_script_program_composition import ScriptTemplateStateComposition
        from .native_automation_program_composition import AutomationTemplateStateComposition
        from .controlled_script_composition import ControlledScriptComposition
        from .controlled_powercalc_composition import ControlledPowercalcComposition
        expected = {NativeTemplateStateYAMLRuntimeReadback: (TemplateStateComposition,),
                    NativeMixedTemplateStateYAMLRuntimeReadback: (MixedTemplateStateComposition, ExternalStateComposition, ControlledReloadComposition, ControlledReloadEntryComposition, ControlledScriptComposition, ScriptTemplateStateComposition, AutomationTemplateStateComposition, ControlledPowercalcComposition)}
        _need(type(chain) is RegistryReceiptChain and type(self) in expected
              and type(composition) in expected[type(self)], 'template_state_original_chain_required')
        composition.assert_baseline(chain.baseline)
        self.composition = composition
        self.hass, self.chain, self.program, self.guard = hass, chain, composition.state_config_program, guard
        self.receipt, self._last_runtime = chain.original_receipt, None
        self.entry_witness = entry_witness
        self.runtime_original = False
        self.native = _StateRuntime(hass)
        _need(self.native.native.version == self.receipt.version,
              'template_state_native_version_changed')
        _need(chain.yaml_index is not None, 'template_state_yaml_step_required')
        self.assert_change(chain._original_change(chain.yaml_index))
        documents = {row.key: json.loads(row.raw) for row in chain.baseline.files}
        rows = [row for row in documents['core.entity_registry']['data']['entities']
                if row['platform'] == 'template' and (entry_witness is None
                    or row.get('config_entry_id') != entry_witness.entry_id)]
        self.identities = {(row['entity_id'].split('.')[0], row['unique_id']):
                           (row['entity_id'], row['id']) for row in rows}
        self.physical_consumers = composition.physical_consumers
        _need(len(self.identities) == len(rows), 'template_state_original_identity_ambiguous')
        for domain, key, entity_id in self.physical_consumers:
            _need((domain, key) not in self.identities, 'template_state_original_identity_ambiguous')
            self.identities[domain, key] = (entity_id, None)

    def identities_at(self, active):
        _need(type(active) is frozenset and active <= set(dict(self.composition.mapping)),
              'template_state_identity_prefix_changed')
        mapping = dict(self.composition.mapping)
        return {(mapping[entity_id] if entity_id in active else entity_id, identity)
                for entity_id, identity in self.identities.values()}

    def assert_change(self, change):
        _need(type(change) is PreparedTemplateStateYAML,
              'template_state_yaml_preparation_required')
        change.checked()
        composition = assert_state_yaml_composition(self.chain, change)
        _need(composition.state_config_program == self.program
              and change.receipt == self.receipt
              and str(Path(self.hass.config.config_dir).resolve()) == self.receipt.config_root,
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
        if self.runtime_original:
            from dataclasses import replace
            _need(image == 'original', 'template_state_original_projection_requires_original_yaml')
            prefix = replace(prefix, active=frozenset())
        snapshot = sample_state_bindings(runtime, self.program, prefix, image,
                                         self.chain.accepted_documents(), entry_witness=self.entry_witness)
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
                self.program, guard=lambda: self._check(state, live), input_state=state,
                runtime_original=self.runtime_original, entry_witness=self.entry_witness, **live)
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
        except Exception as error:
            from .yaml_validation_worker import _checker_diagnostic
            logging.getLogger(__name__).warning('Template runtime observation unavailable: %s',
                                               _checker_diagnostic(error))
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


def compose_template_state_yaml_coordinator(adapters, journal, guard, *, required_scopes, lock=None,
                                            record_admission=None):
    from .transaction import Coordinator
    adapter = adapters.get('yaml')
    _need(type(adapter) is ReceiptedTemplateStateYAMLTransactionAdapter
          and type(adapter.observer) is NativeTemplateStateYAMLRuntimeReadback,
          'template_state_yaml_composition_required')
    completion = _TemplateStateCompletionGuard(guard)
    adapter._template_completion = completion
    return Coordinator(adapters, journal, completion, required_scopes=required_scopes, lock=lock,
                       record_admission=record_admission)


# Short root composition API; each alias names the SAME exact class/function.
StateYAMLRuntimeReadback = NativeTemplateStateYAMLRuntimeReadback
StateReceiptedYAMLTransactionAdapter = ReceiptedTemplateStateYAMLTransactionAdapter
compose_state_yaml_coordinator = compose_template_state_yaml_coordinator
