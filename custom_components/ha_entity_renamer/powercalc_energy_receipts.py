"""Energy step receipts in the existing SAME-ORIGINAL Powercalc registry chain.

The unchanged three core stores remain the chain's native inventory. Energy is
an explicitly prepared fourth file source in the same plan, with an actual
native manager write and independent physical readback. No replay from a
matching postimage or migration authority follows from a decoded receipt.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from copy import deepcopy
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
import stat

from .energy_native import EnergySnapshot, PreparedEnergy, generated_cost_moves
from .energy_plan import plan_energy
from .native_input_baseline import _envelope, _loads, _open_absolute_directory, _signature
from .native_registry import PreparedRegistryChange
from .native_registry_receipts import RegistryIntent, _digest, _exact_json_equal, _freeze_json, _sha
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


def _generated_cost_evidence(change, direction, states, before, after=None):
    """Bind the native generated-cost effect to this Energy/registry prefix."""
    from datetime import datetime
    from decimal import Decimal, InvalidOperation
    from .native_template_composition import _history
    def native_value(value):
        _need(type(value) is dict and set(value) == {'value', 'last_reset'}
              and (value['value'] is None or type(value['value']) is str)
              and (value['last_reset'] is None or type(value['last_reset']) is str),
              'generated_cost_native_value')
        try:
            _need(value['value'] is None or Decimal(value['value']).is_finite(), 'generated_cost_native_value')
            reset = None if value['last_reset'] is None else datetime.fromisoformat(value['last_reset'])
            _need(reset is None or reset.tzinfo is not None, 'generated_cost_native_value')
        except (ValueError, InvalidOperation):
            _need(False, 'generated_cost_native_value')
    old_value, _, new_value, _ = _expected(change, direction)
    moves = generated_cost_moves(old_value, new_value)
    before = [] if before is None else before
    _need(type(before) is list and len(before) == len(moves), 'generated_cost_preimage')
    if after is not None:
        _need(type(after) is list and len(after) == len(moves), 'generated_cost_effect')
    rows = states['core.entity_registry']['document']['data']['entities']
    for index, ((old, new), row) in enumerate(zip(moves, before, strict=True)):
        required = {'before_source', 'after_source', 'entity_id', 'registry_id', 'unique_id',
                    'source_registry_id', 'native_before', 'metadata_before', 'history_before'}
        _need(type(row) is dict and set(row) == required
              and (row['before_source'], row['after_source']) == (old, new)
              and all(type(row[key]) is str and row[key] for key in
                      ('entity_id', 'registry_id', 'unique_id', 'source_registry_id'))
              and row['unique_id'] == row['source_registry_id'] + '_grid_cost',
              'generated_cost_identity')
        source = [item for item in rows if item.get('entity_id') == new]
        cost = [item for item in rows if item.get('entity_id') == row['entity_id']]
        _need(len(source) == len(cost) == 1 and source[0].get('id') == row['source_registry_id']
              and cost[0].get('id') == row['registry_id'] and cost[0].get('platform') == 'energy'
              and cost[0].get('unique_id') == row['unique_id'], 'generated_cost_registry')
        native_value(row['native_before'])
        metadata = row['metadata_before']
        _need(type(metadata) is dict and set(metadata) == {'states', 'statistics'}
              and all(type(metadata[k]) is dict and set(metadata[k]) <= {row['entity_id']}
                      for k in metadata)
              and all(type(value) is dict and type(value.get('metadata_id')) is int
                      and value['metadata_id'] > 0 for value in metadata['states'].values())
              and all(type(value) is dict and type(value.get('id')) is int
                      and value['id'] > 0 for value in metadata['statistics'].values()),
              'generated_cost_metadata')
        history = _history(row['history_before'])
        state_ids = sorted({item['metadata_id'] for item in metadata['states'].values()})
        stat_ids = sorted({item['id'] for item in metadata['statistics'].values()})
        _need(history['state_ids'] == state_ids and history['statistic_ids'] == stat_ids,
              'generated_cost_history_binding')
        if after is not None:
            effect = after[index]
            fields = {'before_source', 'after_source', 'entity_id', 'registry_id', 'unique_id',
                      'source_registry_id', 'native_after', 'metadata_after', 'history_verified'}
            _need(type(effect) is dict and set(effect) == fields and effect['history_verified'] is True
                  and all(effect[key] == row[key] for key in
                          ('before_source', 'after_source', 'entity_id', 'registry_id', 'unique_id',
                           'source_registry_id')), 'generated_cost_effect')
            actual = effect['metadata_after']
            _need(type(actual) is dict and set(actual) == {'states', 'statistics'}
                  and all(type(actual[k]) is dict and set(actual[k]) <= {row['entity_id']} for k in actual)
                  and all(_exact_json_equal(actual[k].get(name), _freeze_json(value)) for k in metadata
                          for name, value in metadata[k].items()), 'generated_cost_metadata')
            native_value(effect['native_after'])


def _cost_registry_projection(before, after, costs):
    """Admit only the native suggested-ID update of each bound cost UUID."""
    expected = deepcopy(before)
    old_rows = expected['data']['entities']
    new_rows = after['data']['entities']
    changes = []
    for cost in costs:
        old = [row for row in old_rows if row.get('id') == cost['registry_id']]
        new = [row for row in new_rows if row.get('id') == cost['registry_id']]
        _need(len(old) == len(new) == 1 and old[0].get('entity_id') == cost['entity_id']
              and new[0].get('entity_id') == cost['entity_id'], 'cost_registry_identity')
        old, new = old[0], new[0]
        if old != new:
            target = cost['after_source'].split('.', 1)[1] + '_cost'
            _need(new.get('suggested_object_id') == target, 'cost_registry_suggestion')
            stamp = new.get('modified_at')
            try:
                parsed = datetime.fromisoformat(stamp) if type(stamp) is str else None
            except ValueError:
                parsed = None
            _need(parsed is not None and parsed.tzinfo is not None, 'cost_registry_timestamp')
            allowed = deepcopy(old)
            allowed['suggested_object_id'] = target
            allowed['modified_at'] = stamp
            _need(_exact_json_equal(new, _freeze_json(allowed)), 'cost_registry_foreign_delta')
            changes.append({'id': cost['registry_id'], 'before': deepcopy(old), 'after': deepcopy(new)})
            old.clear(); old.update(deepcopy(new))
    _need(_exact_json_equal(after, _freeze_json(expected)), 'cost_registry_foreign_delta')
    return changes


def _cost_registry_advance(states, costs, effect):
    if effect is None:
        return
    _need(type(effect) is dict and set(effect) == {'changes', 'sha256', 'identity'}
          and type(effect['changes']) is list and bool(effect['changes']) and type(effect['sha256']) is str
          and re.fullmatch(r'[0-9a-f]{64}', effect['sha256']) is not None and type(effect['identity']) is list
          and len(effect['identity']) == 3 and all(type(v) is int and v >= 0 for v in effect['identity']),
          'cost_registry_receipt')
    old = states['core.entity_registry']
    _need(effect['identity'][0] == old['identity'][0] and effect['identity'][2] == old['identity'][2],
          'cost_registry_file_identity')
    document = deepcopy(old['document'])
    rows = document['data']['entities']
    for change in effect['changes']:
        _need(type(change) is dict and set(change) == {'id', 'before', 'after'}, 'cost_registry_receipt')
        matches = [row for row in rows if row.get('id') == change['id']]
        _need(len(matches) == 1 and _exact_json_equal(matches[0], _freeze_json(change['before'])),
              'cost_registry_preimage')
        matches[0].clear(); matches[0].update(deepcopy(change['after']))
    _need(_cost_registry_projection(old['document'], document, costs) == effect['changes'],
          'cost_registry_receipt')
    states['core.entity_registry'] = {'document': document, 'sha256': effect['sha256'],
                                      'identity': effect['identity']}


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
    _generated_cost_evidence(change, binding['direction'], states,
                             intent.get('generated_cost_before'), receipt.get('generated_cost_after', []))
    old_value, old_rev, new_value, new_rev = _expected(change, binding['direction'])
    prior, after = intent.get('energy_before'), receipt.get('energy_after')
    _need(type(prior) is dict and set(prior) == {'revision', 'physical'}
          and type(after) is dict and set(after) == {'revision', 'physical'}
          and prior['revision'] == old_rev and after['revision'] == new_rev
          and _physical(prior['physical']) == sources['.storage/energy'], 'effect_binding')
    old_physical, new_physical = _physical(prior['physical']), _physical(after['physical'])
    _need((old_physical[1], old_physical[3], old_physical[4], old_physical[5]) ==
          (new_physical[1], new_physical[3], new_physical[4], new_physical[5]),
          'foreign_file_identity')
    result = dict(sources)
    result['.storage/energy'] = new_physical
    _cost_registry_advance(states, intent.get('generated_cost_before') or [], receipt.get('cost_registry_after'))
    native = states['core.entity_registry']
    result['.storage/core.entity_registry'] = list(result['.storage/core.entity_registry'])
    result['.storage/core.entity_registry'][:4] = [native['sha256'], *native['identity']]
    _need(receipt.get('result') == chain._summary(states), 'effect_binding')
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


async def begin(chain, context, observations, energy, cost_before=None):
    _need(chain.writable and type(energy) is EnergyObservation, 'live_energy_observation_required')
    binding = chain._context(context)
    _need(context.step.adapter == 'energy', 'energy_phase_required')
    states, head, sequence, completed, pending = chain._reconstruct()
    _need(pending is None and chain.active is None, 'unknown_unresolved_intent')
    chain._phase_state(context, completed)
    chain._observe(observations, states)
    sources = chain._input_sources(states)
    change = chain._original_change(context.index)
    _generated_cost_evidence(change, binding['direction'], states, cost_before)
    old_value, old_rev, _, _ = _expected(change, binding['direction'])
    _need(energy.snapshot.value == old_value and energy.snapshot.revision == old_rev
          and _physical(energy.physical) == sources.get('.storage/energy')
          and tuple(energy.snapshot.file_identity) ==
              (energy.physical[1], energy.physical[2], energy.physical[0]), 'energy_source_changed')
    payload = {'kind': 'powercalc_energy_intent', 'chain': chain.root_id, 'binding': binding,
               'source_chain': head, 'sequence': sequence, 'source': chain._summary(states),
               'energy_before': {'revision': old_rev, 'physical': list(energy.physical)}}
    if cost_before:
        payload['generated_cost_before'] = cost_before
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
    energy_before, energy_after, cost_before, cost_after = witness.claim(chain, intent, context)
    binding = chain._context(context)
    states, head, sequence, completed, pending = chain._reconstruct()
    row = chain.store.read(intent.reference[0])
    _need(pending == intent.reference and row is not None and row['digest'] == intent.reference[1]
          and row['payload']['binding'] == binding, 'intent_changed')
    _need((row['payload'].get('generated_cost_before') or []) == cost_before, 'generated_cost_intent_changed')
    chain._observe(before, states)
    result_states = deepcopy(states)
    registry = after[0]
    _need(registry.key == 'core.entity_registry', 'cost_registry_observation')
    document = _loads(registry.raw)
    changes = _cost_registry_projection(states[registry.key]['document'], document, cost_before)
    registry_effect = None
    if changes:
        registry_effect = {'changes': changes, 'sha256': _sha(registry.raw), 'identity': list(registry.identity)}
        _cost_registry_advance(result_states, cost_before, registry_effect)
    chain._observe(after, result_states)
    old_value, old_rev, new_value, new_rev = _expected(chain._original_change(context.index), binding['direction'])
    _need(energy_before.snapshot.value == old_value and energy_before.snapshot.revision == old_rev
          and energy_after.snapshot.value == new_value and energy_after.snapshot.revision == new_rev
          and list(energy_before.physical) == row['payload']['energy_before']['physical'],
          'actual_native_energy_result')
    sources = chain._input_sources(states)
    result_sources = dict(sources); result_sources['.storage/energy'] = list(energy_after.physical)
    if registry_effect:
        result_sources['.storage/core.entity_registry'] = list(result_sources['.storage/core.entity_registry'])
        result_sources['.storage/core.entity_registry'][:4] = [registry_effect['sha256'], *registry_effect['identity']]
    receipt = {'kind': 'powercalc_energy_receipt', 'chain': chain.root_id, 'binding': binding,
               'source_chain': head, 'sequence': sequence, 'intent': list(intent.reference),
               'energy_after': {'revision': new_rev, 'physical': list(energy_after.physical)},
               'input_result': result_sources, 'result': chain._summary(result_states)}
    if cost_after:
        receipt['generated_cost_after'] = cost_after
    if registry_effect:
        receipt['cost_registry_after'] = registry_effect
    fold_energy_receipt(chain, deepcopy(states), sources, row['payload'], receipt)
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
