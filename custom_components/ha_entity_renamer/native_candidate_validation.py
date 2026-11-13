"""Read-only native check of an accepted whole candidate; never authorization.

Caller retains coordinator/editor locks and lease across every await. This does
not replace the original receipt, baseline, preparation or runtime expectations.
"""
from __future__ import annotations

import asyncio
import base64
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from .native_input_baseline import _drained, _receipt_bytes
from .native_input_observation import NativeInputObservation, async_observe_native_inputs
from .native_registry_receipts import KEYS, RegistryRawObservation, RegistryReceiptChain
from .transaction import PhaseContext, Record
from .yaml_plan import Limits, _Reader, _relative
from .yaml_validation import YAMLValidationError, _MAX_BYTES, _MAX_FILES


def _fail(reason):
    raise YAMLValidationError('accepted_candidate_' + reason) from None


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _hash(value):
    return _sha(json.dumps(value, sort_keys=True, separators=(',', ':'),
                           allow_nan=False).encode())


@dataclass(frozen=True)
class _AcceptedCandidateProof:
    operation_id: str
    fingerprint: str
    head: str
    direction: str
    original_receipt_sha256: str
    accepted_inputs_sha256: str
    runtime_expectations_sha256: str
    version: str


def _record(chain, record):
    if (type(record) is not Record or record.operation_id != chain.operation_id
            or record.plan != chain.plan or not record.plan.steps
            or record.direction not in ('forward', 'rollback')):
        _fail('phase_binding')
    reverse = record.direction == 'rollback'
    complete = record.phases == (('undone' if reverse else 'verified'),) * len(chain.plan.steps)
    if reverse and not complete:
        from .native_template_prefix_recovery import unstarted_rollback_complete
        complete = unstarted_rollback_complete(chain, record)
    from .native_template_prefix_recovery import complete_forward_unknown_scope
    uncertain_complete = not reverse and complete_forward_unknown_scope(chain, record)
    allowed = (('rolling_back', 'rolled_back', 'ready') if reverse
               else ('applying', 'applied', 'ready'))
    if not complete or not (record.status in allowed or uncertain_complete):
        _fail('phase_incomplete')
    return PhaseContext(record, len(chain.plan.steps) - 1)


def _stage(state, original, change, reverse, before, after):
    """Copy accepted bytes through anchored nofollow reads; vary YAML only."""
    state.assert_current(original)
    sources = state.sources()
    if (len(sources) > _MAX_FILES
            or not {'.storage/' + key for key in KEYS} <= set(sources)):
        _fail('input_graph')
    files = {row.path: row for row in change.files}
    if len(files) != len(change.files) or not set(files) <= set(sources):
        _fail('yaml_graph')
    reader = _Reader(original.config_root, Limits(max_file_bytes=_MAX_BYTES))
    total, captured = 0, []
    try:
        for directory, _, _ in state.source_directories:
            if directory != '.':
                for root in (before, after):
                    (root / _relative(directory)).mkdir(parents=True, exist_ok=True, mode=0o700)
        for path, expected in sorted(sources.items()):
            if (path == '.storage/repairs.issue_registry'
                    and original.checker_repairs_snapshot is not None):
                continue
            raw, info = reader.read(path)
            observed = [_sha(raw), info.st_dev, info.st_ino, info.st_mode,
                        info.st_uid, info.st_gid]
            if (observed[:4] != expected[:4]
                    or expected[4] is not None and observed[4:] != expected[4:]):
                _fail('source_changed')
            total += len(raw)
            if total > _MAX_BYTES:
                _fail('input_limit')
            old = raw
            if path in files:
                row = files[path]
                old, desired = (row.after, row.before) if reverse else (row.before, row.after)
                if raw != desired:
                    _fail('yaml_current_bytes')
            for root, data in ((before, old), (after, raw)):
                target = root / _relative(path)
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                target.write_bytes(data)
                target.chmod(0o600)
            captured.append((path, observed))
        snapshot = original.checker_repairs_snapshot
        if snapshot is not None and snapshot[0] == 'present':
            raw = base64.b64decode(snapshot[1], validate=True)
            for root in (before, after):
                target = root / '.storage/repairs.issue_registry'
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                target.write_bytes(raw)
                target.chmod(0o600)
        # Also pin uid/gid for original ancillary files whose old receipts do
        # not record ownership. This is only an IO fence, never learned authority.
        for path, expected in captured:
            raw, info = reader.read(path)
            if [_sha(raw), info.st_dev, info.st_ino, info.st_mode,
                    info.st_uid, info.st_gid] != expected:
                _fail('source_changed')
        state.assert_current(original)
        return _hash((state.head, sources, state.documents(), state.source_directories))
    except OSError:
        _fail('staging_failed')
    finally:
        os.close(reader.root)


def _compare(result, original, reverse):
    expected = tuple((domain, after, before) if reverse else (domain, before, after)
                     for domain, before, after in original.runtime_expected)
    if (type(result) is not dict or result.get('status') != 'valid'
            or result.get('version') != original.version
            or result.get('changed_domains') != list(original.changed_domains)
            or result.get('unsupported_domains') != list(original.unsupported_domains)
            or result.get('runtime_expected') != [list(row) for row in expected]
            or original.unsupported_domains
            or {row[0] for row in expected} != set(original.changed_domains)
            or any(before is None or after is None for _, before, after in expected)):
        _fail('native_result_mismatch')
    return _hash(expected)


async def _checker(before, after, timeout, checkpoint, *, with_sources=False,
                   state_mapping=None, registry_only_template=False, stable_script_services=(),
                   owned_script_services=(), native_helpers=(), mixed_powercalc=False):
    """Execute the existing native worker by path, without copying its source."""
    worker = str(Path(__file__).with_name('yaml_validation_worker.py'))
    state_args = ()
    if state_mapping is not None:
        from .yaml_plan import _mapping
        if not with_sources:
            _fail('state_source_evidence_required')
        mapping = _mapping(state_mapping)
        request = Path(before).parent / 'template-reference-mapping.json'
        # Same private staged input owner; this contains entity IDs, no secrets.
        with request.open('x', encoding='utf-8') as stream:
            json.dump(mapping, stream, sort_keys=True, separators=(',', ':'), allow_nan=False)
        request.chmod(0o600)
        state_args = ('--template-state-source-v3', '--template-reference-mapping',
                      str(request), '--template-state-config-program')
        if registry_only_template:
            state_args += ('--template-state-registry-only',)
        if stable_script_services:
            if (type(stable_script_services) is not tuple or len(stable_script_services) != 1
                    or type(stable_script_services[0]) is not str):
                _fail('stable_script_service_scope')
            request = Path(before).parent / 'stable-script-services.json'
            with request.open('x', encoding='utf-8') as stream:
                json.dump(list(stable_script_services), stream, separators=(',', ':'))
            request.chmod(0o600)
            state_args += ('--template-stable-script-services', str(request))
        if owned_script_services or mixed_powercalc:
            if (type(owned_script_services) is not tuple or len(owned_script_services) > 1
                    or not owned_script_services and not mixed_powercalc
                    or owned_script_services and owned_script_services[0] not in mapping
                    or registry_only_template):
                _fail('owned_script_service_scope')
            dependent_request = Path(before).parent / 'dependent-reference-mapping.json'
            with dependent_request.open('x', encoding='utf-8') as stream:
                json.dump(mapping, stream, sort_keys=True, separators=(',', ':'), allow_nan=False)
            dependent_request.chmod(0o600)
            state_args += ('--dependent-config-program', '--dependent-reference-mapping', str(dependent_request))
            if owned_script_services:
                owned_request = Path(before).parent / 'owned-script-services.json'
                with owned_request.open('x', encoding='utf-8') as stream:
                    json.dump(list(owned_script_services), stream, separators=(',', ':'))
                owned_request.chmod(0o600)
                state_args += ('--template-owned-script-services', str(owned_request))
    if native_helpers:
        if state_mapping is None or type(native_helpers) is not tuple or len(native_helpers) != 1:
            _fail('controlled_entry_checker_scope')
        request = Path(before).parent / 'native-helpers.json'
        with request.open('x', encoding='utf-8') as stream:
            json.dump(native_helpers, stream, sort_keys=True, separators=(',', ':'), allow_nan=False)
        request.chmod(0o600)
        state_args += ('--native-helpers', str(request))
    # Shield spawning too: cancellation must not orphan a checker with secrets.
    spawning = asyncio.create_task(asyncio.create_subprocess_exec(
        sys.executable, '-I', '-B', worker, str(before), str(after),
        *(('--source-evidence',) if with_sources else ()), *state_args,
        *(('--qualified-mixed-powercalc', '--powercalc-profile-inputs', '--powercalc-candidate-inputs')
          if mixed_powercalc else ()),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True))
    process = None
    cancelled = False
    while not spawning.done():
        try:
            await asyncio.shield(spawning)
        except asyncio.CancelledError:
            cancelled = True
    process = spawning.result()
    if cancelled:
        if process.returncode is None:
            process.kill()
        draining = asyncio.create_task(process.communicate())
        while not draining.done():
            try:
                await asyncio.shield(draining)
            except asyncio.CancelledError:
                pass
        draining.result()
        raise asyncio.CancelledError
    communication = asyncio.create_task(process.communicate())
    try:
        await checkpoint()
        async with asyncio.timeout(timeout):
            stdout, _ = await asyncio.shield(communication)
        await checkpoint()
    except BaseException:
        if process.returncode is None:
            process.kill()
        while not communication.done():
            try:
                await asyncio.shield(communication)
            except asyncio.CancelledError:
                pass
        communication.result()
        raise
    if process.returncode or len(stdout) > 1_000_000:
        _fail('native_checker_failed')
    try:
        return json.loads(stdout)
    except (ValueError, UnicodeError):
        _fail('native_checker_failed')


@contextmanager
def private_candidate_scratch(config_root):
    """Own a private sibling of config, even when its parent is public.

    TemporaryDirectory creates mode 0700 without changing the parent. Keep this
    scope around the awaited verifier: its staging/checker drains cancellation
    before returning, then both temporary layers are removed on every exit.
    The verifier still enforces its outside-config, mode and ownership checks.
    """
    with tempfile.TemporaryDirectory(prefix='renamer-candidate-scratch-',
                                     dir=Path(config_root).resolve().parent) as scratch:
        yield Path(scratch)


async def async_revalidate_accepted_candidate(hass, chain, phase_loader,
                                             scratch_parent, guard, *, timeout=120, _state_program=None):
    """Actual isolated check_config on the complete accepted native+YAML image.

    Returned private proof is evidence only. It has no ready/receipt/permission
    API, cannot authorize mutation, and does not close runtime verification.
    """
    if type(chain) is not RegistryReceiptChain:
        _fail('chain_binding')
    registry_only = chain.yaml_index is None
    if registry_only:
        from .native_template_state_composition import TemplateStateComposition
        from .native_template_yaml_server import _dashboard_plan
        baseline, composition = chain.preparations.resolve_composition(chain.plan.native_inputs)
        if (type(composition) is not TemplateStateComposition or baseline != chain.baseline
                or composition.state_config_program != _state_program
                or chain.original_receipt.proposed_files or chain.original_receipt.changed_domains
                or (any(step.adapter != 'registry' for step in chain.plan.steps)
                    and not _dashboard_plan(chain.plan))):
            _fail('registry_only_binding')
        composition.assert_baseline(baseline)
    elif chain.yaml_index != len(chain.plan.steps) - 1:
        _fail('chain_binding')
    original = chain.original_receipt
    original_json = _receipt_bytes(original)
    state = chain.reconstruct_inputs()  # Pending intent always refuses.
    root, scratch = Path(original.config_root).resolve(), Path(scratch_parent).resolve()
    # Deployment owns the physical volume: HA containers see SSD mounts as
    # /config, so a host-specific /Volumes path cannot establish that policy.
    # Require a private directory outside the live configuration here.
    if (scratch == root or root in scratch.parents
            or not scratch.is_dir() or scratch.stat().st_mode & 0o077
            or scratch.stat().st_uid != os.getuid()):
        _fail('scratch_not_private')
    record = None

    def fence():
        guard()
        if (_receipt_bytes(chain.original_receipt) != original_json
                or not state.same_evidence(chain.reconstruct_inputs())):
            _fail('input_head_changed')
        state.assert_current(original)
        guard()

    async def checkpoint():
        nonlocal record
        fence()
        actual = await phase_loader(chain.operation_id)
        fence()
        context = _record(chain, actual)
        if record is not None and actual != record:
            _fail('phase_changed')
        record = actual
        return context

    async def observe():
        context = await checkpoint()
        observed = await async_observe_native_inputs(hass, original,
                                                     guard=fence, input_state=state)
        await checkpoint()
        if (type(observed) is not NativeInputObservation
                or observed.original_receipt_json != original_json
                or observed.version != original.version):
            _fail('observation_binding')
        rows = tuple(RegistryRawObservation(row.key, row.raw, row.identity)
                     for row in observed.files)
        if chain.expected(context, rows) != state.head:
            _fail('input_head_changed')
        fence()

    try:
        await observe()
        reverse = record.direction == 'rollback'
        if registry_only:
            # This is only an input to the read-only staging helper. It is not
            # a prepared YAML change, a transaction step, or an effect grant.
            @dataclass(frozen=True)
            class NoYAMLFiles:
                files: tuple = ()
            change = NoYAMLFiles()
        else:
            change = chain._original_change(chain.yaml_index).checked()
            if change.receipt != original:
                _fail('original_preparation')
        if _state_program is not None and not registry_only:
            from .native_template_state_yaml import (PreparedTemplateStateYAML, PreparedMixedTemplateStateYAML, PreparedMixedPowercalcYAML,
                                                     assert_state_yaml_composition)
            from .native_external_yaml import PreparedExternalStateYAML
            if type(change) not in (PreparedTemplateStateYAML, PreparedMixedTemplateStateYAML, PreparedMixedPowercalcYAML, PreparedExternalStateYAML):
                _fail('state_preparation_required')
            composition = assert_state_yaml_composition(chain, change)
            if _state_program != composition.state_config_program:
                _fail('state_program_binding')
        with tempfile.TemporaryDirectory(prefix='renamer-candidate-', dir=scratch) as stage:
            before, after = Path(stage) / 'before', Path(stage) / 'after'
            before.mkdir(mode=0o700)
            after.mkdir(mode=0o700)
            fence()
            graph_hash = await _drained(hass.async_add_executor_job,
                lambda: _stage(state, original, change, reverse, before, after))
            await checkpoint()
            await observe()
            mixed_powercalc = (_state_program is not None and not registry_only
                               and type(change) is PreparedMixedPowercalcYAML)
            if mixed_powercalc:
                from .native_powercalc_accepted_consumers import _source_image
                from .powercalc_source_binding import PowercalcVendor, stage_powercalc_bundle
                _source_image(chain, composition.evidence, state)
                planned = composition.evidence.powercalc_candidate_sources.documents
                for key, old, proposed in planned.documents:
                    (before / '.storage' / key).write_bytes(proposed if reverse else old)
                if planned.energy is not None:
                    (before / '.storage' / 'energy').write_bytes(planned.energy[1] if reverse else planned.energy[0])
                vendor = PowercalcVendor(hass, with_profiles=True)
                bundle = await _drained(hass.async_add_executor_job, vendor.sources)
                await _drained(hass.async_add_executor_job, lambda: stage_powercalc_bundle(bundle, before, after))
                await checkpoint()
            if _state_program is None:
                result = await _checker(before, after, timeout, checkpoint,
                    with_sources='template' in original.changed_domains)
            else:
                mapping = dict(_state_program.mapping)
                if reverse:
                    mapping = {new: old for old, new in mapping.items()}
                stable = tuple(change.after['entity_id'] for index, step in enumerate(chain.plan.steps)
                    if step.adapter == 'registry'
                    for change in (chain._original_change(index),)
                    if (change.kind == 'entity' and change.mapping
                        and change.before.get('platform') == 'script'
                        and change.after['entity_id'] == 'script.' + change.before['unique_id']))
                if len(stable) > 1:
                    _fail('stable_script_service_scope')
                from .native_registry_transition import _native_script_unique_move
                owned = tuple((item.after if reverse else item.before)['entity_id']
                    for index, step in enumerate(chain.plan.steps) if step.adapter == 'registry'
                    for item in (chain._original_change(index),)
                    if item.kind == 'entity' and _native_script_unique_move(item.before, item.after))
                if len(owned) > 1:
                    _fail('owned_script_service_scope')
                helper_request = ()
                from .native_template_state_composition import ControlledReloadEntryComposition
                from .controlled_script_composition import ControlledScriptComposition
                from .controlled_powercalc_composition import ControlledPowercalcComposition
                from .helper_transaction import PreparedHelper
                template_helper_items = [(step, chain._original_change(index))
                    for index, step in enumerate(chain.plan.steps) if step.adapter == 'helpers'
                    and type(chain._original_change(index)) is PreparedHelper
                    and chain._original_change(index).before.get('domain') == 'template']
                if type(composition) is ControlledReloadEntryComposition or (
                        type(composition) in (ControlledPowercalcComposition, ControlledScriptComposition)
                        and template_helper_items):
                    from .helper_transaction import prepare_helper
                    from .prepared import encode_change, canonical
                    items = template_helper_items
                    if len(items) != 1 or type(items[0][1]) is not PreparedHelper:
                        _fail('controlled_entry_preparation_required')
                    step, original_change = items[0]
                    selected = (prepare_helper(original_change.after,
                                {new: old for old, new in original_change.mapping.items()})
                                if reverse else original_change)
                    after_path = after / '.storage/core.config_entries'
                    before_path = before / '.storage/core.config_entries'
                    staged = before_path.read_bytes() if mixed_powercalc else None
                    before_raw = _entry_before_document(staged, after_path.read_bytes(),
                                                        original.version, selected)
                    if not mixed_powercalc:
                        before_path.write_bytes(before_raw)
                    payload = encode_change(selected)
                    _, digest = canonical(payload)
                    helper_request = ((step.prepared_ref, digest, payload),)
                result = await _checker(before, after, timeout, checkpoint,
                    with_sources=True, state_mapping=mapping, registry_only_template=registry_only,
                    stable_script_services=stable, owned_script_services=owned, native_helpers=helper_request,
                    mixed_powercalc=mixed_powercalc)
            await checkpoint()
            await observe()
            runtime_hash = (_compare(result, original, reverse) if _state_program is None
                            else (_compare_state_full(result, original, reverse, _state_program, ())
                                  if registry_only else
                                  _compare_mixed_state(result, original, reverse, _state_program,
                                      helper_request=helper_request)
                                  if type(change) is PreparedMixedTemplateStateYAML
                                  else _compare_external_state(result, original, reverse, _state_program, composition)
                                  if type(change) is PreparedExternalStateYAML
                                  else _compare_controlled_powercalc(result, original, reverse, _state_program, composition, helper_request=helper_request)
                                  if type(change) is PreparedMixedPowercalcYAML
                                  else _compare_state(result, original, reverse, _state_program)))
            fence()
            arguments = (chain.operation_id, chain.plan.fingerprint, state.head,
                record.direction, _sha(original_json), graph_hash, runtime_hash, original.version)
            if _state_program is not None:
                return _AcceptedStateCandidateProof(*arguments, _state_program.sha256)
            return _AcceptedCandidateProof(*arguments)
    except (OSError, TimeoutError):
        _fail('native_checker_unavailable')


@dataclass(frozen=True)
class _AcceptedStateCandidateProof(_AcceptedCandidateProof):
    """Actual config-check evidence only; never a runtime proof or receipt."""
    source_program_sha256: str


def _entry_before_document(staged, actual, version, selected):
    """Check Template's delta while retaining the separately staged PC image."""
    from .helper_plan import snapshot_equal
    from .native_config_transition import _document, _projection, _target
    from .controlled_template_entry_candidate import candidate
    doc = _document(actual, version)
    row = _target(doc, selected.before['identity']['entry_id'])
    if not snapshot_equal(_projection(row), selected.after):
        _fail('controlled_entry_current_changed')
    row['options'] = selected.before['options']
    before_raw = json.dumps(doc, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    rebuilt = candidate(before_raw, version, selected)
    if json.dumps(rebuilt, sort_keys=True, separators=(',', ':'), allow_nan=False) != json.dumps(
            _document(actual, version), sort_keys=True, separators=(',', ':'), allow_nan=False):
        _fail('controlled_entry_candidate_changed')
    if staged is not None:
        planned = _document(staged, version)
        staged_row = _target(planned, selected.before['identity']['entry_id'])
        if not snapshot_equal(_projection(staged_row), selected.before):
            _fail('controlled_entry_original_changed')
        return staged
    return before_raw


def _compare_state(result, original, reverse, program):
    if original.changed_domains != ('template',):
        _fail('state_native_result_mismatch')
    return _compare_state_full(result, original, reverse, program, ('template',))


def _compare_mixed_state(result, original, reverse, program, *, helper_request=()):
    from .yaml_transaction import _assert_mixed_state_scope
    _assert_mixed_state_scope(original, allow_dependent_only=True, allow_template_entry=bool(helper_request))
    return _compare_state_full(result, original, reverse, program, original.changed_domains,
                               helper_request=helper_request)


def _compare_external_state(result, original, reverse, program, composition):
    from dataclasses import replace
    from .yaml_transaction import _assert_external_state_scope
    _assert_external_state_scope(original)
    if (type(result) is not dict or type(result.get('runtime_expected')) is not list
            or any(type(row) is not list or len(row) != 3 for row in result['runtime_expected'])):
        _fail('external_native_source_mismatch')
    external = {'homekit', 'utility_meter'} & set(original.changed_domains)
    proof = [[domain, True] for domain in ('homekit', 'utility_meter') if domain in external]
    expected = [[domain, new, old] if reverse else [domain, old, new]
                for domain, old, new in original.runtime_expected if domain in external]
    actual = [row for row in result.get('runtime_expected', []) if row[0] in external]
    if (not external or result.get('external_source_proof') != proof
            or tuple(tuple(row) for row in proof) != composition.external_source_proof
            or actual != expected):
        _fail('external_native_source_mismatch')
    def scoped(rows):
        return [row for row in rows if (row if type(row) is str else row[0]) not in external]
    filtered = dict(result, changed_domains=scoped(result.get('changed_domains', [])),
                    unsupported_domains=scoped(result.get('unsupported_domains', [])),
                    runtime_expected=scoped(result.get('runtime_expected', [])))
    receipt = replace(original, changed_domains=tuple(scoped(original.changed_domains)),
                      unsupported_domains=tuple(scoped(original.unsupported_domains)),
                      runtime_expected=tuple(scoped(original.runtime_expected)))
    return _hash((_compare_state_full(filtered, receipt, reverse, program, receipt.changed_domains), proof, expected))


def _compare_controlled_powercalc(result, original, reverse, program, composition, *, helper_request=()):
    from dataclasses import replace
    if type(result) is not dict:
        _fail('powercalc_native_result_mismatch')
    old, new = composition.evidence.powercalc_sources, composition.evidence.powercalc_candidate_sources.sources
    def projection(source):
        value = json.loads(source.native_json)
        value['closed_graph'] = json.loads(source.closed_graph_json)
        return value
    expected = (projection(new), projection(old)) if reverse else (projection(old), projection(new))
    if (result.get('powercalc_sources'), result.get('powercalc_candidate_sources')) != expected:
        _fail('powercalc_native_graph_mismatch')
    external = {'homekit', 'utility_meter'} & set(original.changed_domains)
    proof = [[domain, True] for domain in ('homekit', 'utility_meter') if domain in external]
    expected_external = [[domain, new, old] if reverse else [domain, old, new]
                         for domain, old, new in original.runtime_expected if domain in external]
    if external and (result.get('external_source_proof') != proof
            or tuple(tuple(row) for row in proof) != composition.external_source_proof
            or [row for row in result.get('runtime_expected', []) if row[0] in external] != expected_external):
        _fail('external_native_source_mismatch')
    if not external and composition.external_source_proof:
        _fail('external_native_source_mismatch')
    excluded = {'powercalc'} | external
    if not helper_request:
        excluded.add('template.config_entries')
    def scoped(rows):
        return [row for row in rows if (row if type(row) is str else row[0]) not in excluded]
    filtered = dict(result, changed_domains=scoped(result.get('changed_domains', [])),
                    unsupported_domains=scoped(result.get('unsupported_domains', [])),
                    runtime_expected=scoped(result.get('runtime_expected', [])))
    receipt = replace(original, changed_domains=tuple(scoped(original.changed_domains)),
                      unsupported_domains=tuple(scoped(original.unsupported_domains)),
                      runtime_expected=tuple(scoped(original.runtime_expected)))
    state = _compare_state_full(filtered, receipt, reverse, program, receipt.changed_domains, helper_request=helper_request)
    return _hash((state, expected, proof, expected_external) if external else (state, expected))


def _compare_state_full(result, original, reverse, program, domains, *, helper_request=()):
    """Explicit full state mode; the legacy unsupported/None gate is unchanged.

    Actual native worker descriptors include every semantic field and inventory
    row. check_config success alone, legacy digests or fixture projections cannot
    satisfy this comparison. Reverse requires the opposite source/candidate.
    """
    from .template_state_program import StateConfigProgram, _digest
    if type(program) is not StateConfigProgram:
        _fail('state_program_required')
    before, after = json.loads(program.original_json), json.loads(program.candidate_json)
    if reverse:
        before, after = after, before
    expected = [[domain, new, old] if reverse else [domain, old, new]
                for domain, old, new in original.runtime_expected]
    if helper_request:
        from .yaml_validation_worker import template_entry_seal
        expected = [row for row in expected if row[0] != 'template.config_entries']
        expected.append(list(template_entry_seal(helper_request, original.version)))
    if (type(result) is not dict or result.get('status') != 'valid'
            or result.get('version') != original.version
            or not set(original.unsupported_domains) <= {'template'}
            or result.get('changed_domains') != list(domains)
            or result.get('unsupported_domains') != list(original.unsupported_domains)
            or result.get('runtime_expected') != expected
            or result.get('template_state_config_program') != before
            or result.get('template_candidate_state_config_program') != after):
        _fail('state_native_result_mismatch')
    for name, descriptor in (('template_state_source_profile', before),
                             ('template_candidate_state_source_profile', after)):
        profile = result.get(name)
        if (type(profile) is not dict or profile.get('version') != original.version
                or type(profile.get('states')) is not list
                or any(type(row) is not list or len(row) != 4
                       or type(row[3]) is not dict for row in profile['states'])
                or [row[:3] for row in profile['states']] != [[domain, key, _digest(semantic)]
                    for domain, key, semantic in descriptor['rows']]
                or profile.get('rows') != [[row[0], row[1], _digest(row)]
                    for row in descriptor['inventory']]):
            _fail('state_native_profile_mismatch')
    return _hash((program.sha256, reverse, before, after, expected))


async def async_revalidate_accepted_template_state_candidate(hass, chain, phase_loader,
        scratch_parent, guard, *, timeout=120):
    """SAME accepted graph, retained StateComposition and actual isolated check.

    No ORIGINAL replacement or replay. Runtime acceptance remains the separate
    full prefix observer and its completion suffix. Every pending intent refuses.
    """
    from .native_template_state_yaml import PreparedTemplateStateYAML, PreparedMixedTemplateStateYAML, PreparedMixedPowercalcYAML, assert_state_yaml_composition
    if type(chain) is not RegistryReceiptChain:
        _fail('chain_binding')
    if chain.yaml_index is None:
        from .native_template_state_composition import TemplateStateComposition
        baseline, composition = chain.preparations.resolve_composition(chain.plan.native_inputs)
        if type(composition) is not TemplateStateComposition or baseline != chain.baseline:
            _fail('state_preparation_required')
        composition.assert_baseline(baseline)
    else:
        if chain.yaml_index != len(chain.plan.steps) - 1:
            _fail('chain_binding')
        change = chain._original_change(chain.yaml_index)
        from .native_external_yaml import PreparedExternalStateYAML
        if type(change) not in (PreparedTemplateStateYAML, PreparedMixedTemplateStateYAML, PreparedMixedPowercalcYAML, PreparedExternalStateYAML):
            _fail('state_preparation_required')
        composition = assert_state_yaml_composition(chain, change)
    return await async_revalidate_accepted_candidate(hass, chain, phase_loader,
        scratch_parent, guard, timeout=timeout, _state_program=composition.state_config_program)
