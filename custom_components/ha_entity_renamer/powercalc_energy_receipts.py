"""Energy step receipts in the existing SAME-ORIGINAL Powercalc registry chain.

The unchanged three core stores remain the chain's native inventory. Energy is
an explicitly prepared fourth file source in the same plan, with an actual
native manager write and independent physical readback. No replay from a
matching postimage or migration authority follows from a decoded receipt.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import Path
import stat

from .energy_native import EnergySnapshot, PreparedEnergy
from .energy_plan import plan_energy
from .native_input_baseline import _envelope, _loads, _open_absolute_directory, _signature
from .native_registry import PreparedRegistryChange
from .native_registry_receipts import RegistryIntent, _digest, _exact_json_equal, _freeze_json
from .transaction import Refused
from .yaml_plan import _Reader, Limits


def _need(value, reason):
    if not value:
        raise Refused('powercalc_energy_' + reason)


def validate_prepared_energy(chain, change):
    """Prove ORIGINAL and candidate Energy bytes are exactly the queued plan."""
    from .powercalc_composition import PowercalcComposition
    from .controlled_powercalc_composition import ControlledPowercalcComposition
    from .powercalc_profile_inputs import digest
    from .prepared import decode_change
    _need(type(change) is PreparedEnergy and change.before.value is not None
          and change.before.file_identity is not None, 'prepared_required')
    baseline, composition = chain.preparations.resolve_composition(chain.plan.native_inputs)
    _need(baseline == chain.baseline and type(composition) in (PowercalcComposition, ControlledPowercalcComposition)
          and composition.evidence.powercalc_candidate_sources.documents.energy is not None,
          'same_original_required')
    before_raw, after_raw = composition.evidence.powercalc_candidate_sources.documents.energy
    before, after = _loads(before_raw), _loads(after_raw)
    _need(_exact_json_equal(before['data'], _freeze_json(change.before.value))
          and _exact_json_equal(after['data'], _freeze_json(change.after))
          and hashlib.sha256(before_raw).hexdigest() == change.before.file_identity[2],
          'candidate_energy_bytes_changed')
    source = next((row for row in chain.original_receipt.source_files if row[0] == '.storage/energy'), None)
    _need(source is not None and source[1:] == (change.before.file_identity[2],
          change.before.file_identity[0], change.before.file_identity[1]), 'original_file_identity')
    mapping = {}
    for step in chain.plan.steps:
        if step.adapter != 'registry':
            continue
        digest_ref = dict(composition.prepared_digests).get(step.prepared_ref)
        _need(digest_ref is not None, 'registry_preparation_missing')
        candidate = decode_change(chain.preparations.resolve(step.prepared_ref, digest_ref))
        if type(candidate) is PreparedRegistryChange and candidate.kind == 'entity':
            for old, new in candidate.mapping.items():
                _need(old not in mapping and new not in mapping.values(), 'registry_mapping_ambiguous')
                mapping[old] = new
    planned = plan_energy(change.before.value, mapping)
    _need(not planned.markers and _exact_json_equal(planned.value, _freeze_json(change.after)),
          'energy_mapping_not_exact')
    old_graph = _loads(composition.evidence.powercalc_sources.closed_graph_json)
    new_graph = _loads(composition.evidence.powercalc_candidate_sources.sources.closed_graph_json)
    _need(old_graph['energy_settings_sha256'] == digest(change.before.value)
          and new_graph['energy_settings_sha256'] == digest(change.after), 'candidate_energy_graph_changed')


def _physical(value):
    _need(type(value) in (tuple, list) and len(value) == 6 and type(value[0]) is str
          and len(value[0]) == 64 and all(c in '0123456789abcdef' for c in value[0])
          and all(type(item) is int and item >= 0 for item in value[1:])
          and value[2] != 0 and stat.S_ISREG(value[3]), 'physical_shape')
    return list(value)


def _expected(change, direction):
    return ((change.before.value, change.before.revision, change.after, change.after_revision)
            if direction == 'forward' else
            (change.after, change.after_revision, change.before.value, change.before.revision))


def fold_energy_receipt(chain, states, sources, intent, receipt):
    """Reconstruct one acknowledged native Energy delta in chain sequence."""
    _need(type(sources) is dict and '.storage/energy' in sources, 'source_file_required')
    binding = intent['binding']
    from .powercalc_source_receipts import _composition
    from .controlled_powercalc_composition import ControlledPowercalcComposition
    if type(_composition(chain)) is ControlledPowercalcComposition:
        from .controlled_powercalc_setup_receipts import validate_handoff
        validate_handoff(chain, intent.get('controlled_powercalc_handoff'), binding['direction'])
    else:
        _need(intent.get('controlled_powercalc_handoff') is None, 'foreign_handoff')
    change = chain._original_change(binding['index'])
    old_value, old_rev, new_value, new_rev = _expected(change, binding['direction'])
    prior, after = intent.get('energy_before'), receipt.get('energy_after')
    _need(type(prior) is dict and set(prior) == {'revision', 'physical'}
          and type(after) is dict and set(after) == {'revision', 'physical'}
          and prior['revision'] == old_rev and after['revision'] == new_rev
          and _physical(prior['physical']) == sources['.storage/energy']
          and receipt.get('result') == chain._summary(states), 'effect_binding')
    old_physical, new_physical = _physical(prior['physical']), _physical(after['physical'])
    _need((old_physical[1], old_physical[3], old_physical[4], old_physical[5]) ==
          (new_physical[1], new_physical[3], new_physical[4], new_physical[5]),
          'foreign_file_identity')
    result = dict(sources)
    result['.storage/energy'] = new_physical
    _need(receipt.get('input_result') == result, 'source_graph_mismatch')
    return result


@dataclass(frozen=True)
class EnergyObservation:
    snapshot: EnergySnapshot
    physical: tuple[str, int, int, int, int, int]


def _read_physical(root, expected_identity, root_identity, storage_identity):
    safe = _open_absolute_directory(root)
    reader = None
    storage = None
    try:
        _need(_signature(os.fstat(safe))[:3] == root_identity, 'root_changed')
        reader = _Reader(root, Limits())
        _need(_signature(os.fstat(reader.root))[:3] == root_identity, 'root_changed')
        storage = reader.open('.storage', directory=True)
        _need(_signature(os.fstat(storage))[:3] == storage_identity, 'storage_changed')
        raw, info = reader.read('.storage/energy')
        sha = hashlib.sha256(raw).hexdigest()
        _need((info.st_dev, info.st_ino, sha) == expected_identity, 'native_disk_identity_changed')
        named_root = _open_absolute_directory(root)
        try:
            _need(_signature(os.fstat(named_root))[:3] == root_identity, 'root_changed')
        finally:
            os.close(named_root)
        named_storage = reader.open('.storage', directory=True)
        try:
            _need(_signature(os.fstat(named_storage))[:3] == storage_identity, 'storage_changed')
        finally:
            os.close(named_storage)
        return (sha, info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)
    finally:
        if storage is not None:
            os.close(storage)
        if reader is not None:
            os.close(reader.root)
        os.close(safe)


async def observe_energy(native, lifecycle):
    """Actual native cache/disk read plus symlink-safe physical identity."""
    from .native_input_baseline import _drained
    from .controlled_powercalc_handoff import ControlledPowercalcHandoff
    controlled = type(lifecycle) is ControlledPowercalcHandoff
    check = lifecycle.require_inactive if controlled else lifecycle.check
    composition = lifecycle.root.composition if controlled else lifecycle.composition
    check()
    before = await native.read()
    check()
    _need(before.file_identity is not None, 'energy_file_required')
    physical = await _drained(native.hass.async_add_executor_job, lambda: _read_physical(
        lifecycle.chain.original_receipt.config_root, before.file_identity,
        composition.previous.root_identity, composition.previous.storage_identity))
    check()
    after = await native.read()
    check()
    _need(after == before, 'energy_changed_during_read')
    return EnergyObservation(before, physical)


async def begin(chain, context, observations, energy):
    _need(chain.writable and type(energy) is EnergyObservation, 'live_energy_observation_required')
    binding = chain._context(context)
    _need(context.step.adapter == 'energy', 'energy_phase_required')
    states, head, sequence, completed, pending = chain._reconstruct()
    _need(pending is None and chain.active is None, 'unknown_unresolved_intent')
    chain._phase_state(context, completed)
    chain._observe(observations, states)
    sources = chain._input_sources(states)
    change = chain._original_change(context.index)
    old_value, old_rev, _, _ = _expected(change, binding['direction'])
    _need(energy.snapshot.value == old_value and energy.snapshot.revision == old_rev
          and _physical(energy.physical) == sources.get('.storage/energy')
          and tuple(energy.snapshot.file_identity) ==
              (energy.physical[1], energy.physical[2], energy.physical[0]), 'energy_source_changed')
    payload = {'kind': 'powercalc_energy_intent', 'chain': chain.root_id, 'binding': binding,
               'source_chain': head, 'sequence': sequence, 'source': chain._summary(states),
               'energy_before': {'revision': old_rev, 'physical': list(energy.physical)}}
    from .powercalc_source_receipts import _composition
    from .controlled_powercalc_composition import ControlledPowercalcComposition
    if type(_composition(chain)) is ControlledPowercalcComposition:
        from .native_registry_receipts import _event_id
        from .controlled_powercalc_setup_receipts import validate_handoff
        key = _event_id(['controlled_pc_population', chain.root_id, binding['direction'], 'receipt'])
        row = chain.store.read(key)
        _need(row is not None, 'controlled_handoff_required')
        reference = [key, row['digest']]
        validate_handoff(chain, reference, binding['direction'])
        payload['controlled_powercalc_handoff'] = reference
    ref = await chain.store.create(chain._slot('intent', context.index, binding['direction']), payload)
    chain.active = RegistryIntent(ref)
    chain._remember((states, head, sequence, completed, ref))
    return chain.active


async def complete(chain, intent, context, before, after, witness):
    from .powercalc_energy_transaction import PowercalcEnergyEffect
    _need(chain.writable and type(intent) is RegistryIntent and intent is chain.active
          and type(witness) is PowercalcEnergyEffect, 'live_energy_effect_required')
    energy_before, energy_after = witness.claim(chain, intent, context)
    binding = chain._context(context)
    states, head, sequence, completed, pending = chain._reconstruct()
    row = chain.store.read(intent.reference[0])
    _need(pending == intent.reference and row is not None and row['digest'] == intent.reference[1]
          and row['payload']['binding'] == binding, 'intent_changed')
    chain._observe(before, states); chain._observe(after, states)
    old_value, old_rev, new_value, new_rev = _expected(chain._original_change(context.index), binding['direction'])
    _need(energy_before.snapshot.value == old_value and energy_before.snapshot.revision == old_rev
          and energy_after.snapshot.value == new_value and energy_after.snapshot.revision == new_rev
          and list(energy_before.physical) == row['payload']['energy_before']['physical'],
          'actual_native_energy_result')
    sources = chain._input_sources(states)
    result_sources = dict(sources); result_sources['.storage/energy'] = list(energy_after.physical)
    receipt = {'kind': 'powercalc_energy_receipt', 'chain': chain.root_id, 'binding': binding,
               'source_chain': head, 'sequence': sequence, 'intent': list(intent.reference),
               'energy_after': {'revision': new_rev, 'physical': list(energy_after.physical)},
               'input_result': result_sources, 'result': chain._summary(states)}
    fold_energy_receipt(chain, states, sources, row['payload'], receipt)
    ref = await chain.store.create(chain._slot('receipt', context.index, binding['direction']), receipt)
    chain.active = None
    chain._cache = None
    chain._reconstruct()
    witness.completed(ref)
    return ref


def live_inputs(chain, intent, context, observed):
    """Ephemeral full-graph fence for this exact pending native Energy call."""
    from .accepted_input_state import AcceptedInputState
    _need(chain.writable and type(intent) is RegistryIntent and chain.active is intent
          and type(observed) is EnergyObservation, 'live_intent_required')
    binding = chain._context(context)
    states, head, _, _, pending = chain._reconstruct()
    row = chain.store.read(intent.reference[0])
    _need(pending == intent.reference and row is not None and row['digest'] == intent.reference[1]
          and row['payload']['binding'] == binding, 'intent_changed')
    old, old_revision, new, new_revision = _expected(chain._original_change(context.index), binding['direction'])
    _need((observed.snapshot.value == old and observed.snapshot.revision == old_revision
           and list(observed.physical) == row['payload']['energy_before']['physical'])
          or (observed.snapshot.value == new and observed.snapshot.revision == new_revision),
          'live_energy_observation_invalid')
    sources = chain._input_sources(states)
    prior = _physical(row['payload']['energy_before']['physical'])
    actual = _physical(observed.physical)
    _need((prior[1], prior[3], prior[4], prior[5]) == (actual[1], actual[3], actual[4], actual[5])
          and tuple(observed.snapshot.file_identity) == (actual[1], actual[2], actual[0]),
          'live_energy_physical_changed')
    sources['.storage/energy'] = actual
    def live():
        _, current_head, _, _, current_pending = chain._reconstruct()
        _need(chain.writable and chain.active is intent and current_pending == intent.reference
              and current_head == head, 'live_intent_expired')
    return AcceptedInputState(chain.baseline.receipt_json, head, sources,
        {key: value['document'] for key, value in states.items()}, live_check=live)
