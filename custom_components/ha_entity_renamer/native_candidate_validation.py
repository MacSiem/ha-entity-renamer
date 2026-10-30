"""Read-only native check of an accepted whole candidate; never authorization.

Caller retains coordinator/editor locks and lease across every await. This does
not replace the original receipt, baseline, preparation or runtime expectations.
"""
from __future__ import annotations

import asyncio
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
    if reverse and not complete and set(record.phases) <= {'pending', 'undone'} and 'undone' in record.phases:
        # Unstarted steps are left pending by Coordinator.rollback. Only this
        # exact selective profile has native prefix admission; the full actual
        # original input/runtime/candidate checks below are still mandatory.
        from .native_template_state_yaml import PreparedTemplateStateYAML
        complete = (chain.yaml_index is not None
                    and type(chain._original_change(chain.yaml_index)) is PreparedTemplateStateYAML)
    if (not complete
            or record.status not in (('rolling_back', 'rolled_back', 'ready') if reverse
                                      else ('applying', 'applied', 'ready'))):
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
                   state_mapping=None):
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
    # Shield spawning too: cancellation must not orphan a checker with secrets.
    spawning = asyncio.create_task(asyncio.create_subprocess_exec(
        sys.executable, '-I', '-B', worker, str(before), str(after),
        *(('--source-evidence',) if with_sources else ()), *state_args,
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


async def async_revalidate_accepted_candidate(hass, chain, phase_loader,
                                             scratch_parent, guard, *, timeout=120, _state_program=None):
    """Actual isolated check_config on the complete accepted native+YAML image.

    Returned private proof is evidence only. It has no ready/receipt/permission
    API, cannot authorize mutation, and does not close runtime verification.
    """
    if type(chain) is not RegistryReceiptChain or chain.yaml_index != len(chain.plan.steps) - 1:
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
        change = chain._original_change(chain.yaml_index).checked()
        if change.receipt != original:
            _fail('original_preparation')
        if _state_program is not None:
            from .native_template_state_yaml import (PreparedTemplateStateYAML, PreparedMixedTemplateStateYAML,
                                                     assert_state_yaml_composition)
            if type(change) not in (PreparedTemplateStateYAML, PreparedMixedTemplateStateYAML):
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
            if _state_program is None:
                result = await _checker(before, after, timeout, checkpoint,
                    with_sources='template' in original.changed_domains)
            else:
                mapping = dict(_state_program.mapping)
                if reverse:
                    mapping = {new: old for old, new in mapping.items()}
                result = await _checker(before, after, timeout, checkpoint,
                    with_sources=True, state_mapping=mapping)
            await checkpoint()
            await observe()
            runtime_hash = (_compare(result, original, reverse) if _state_program is None
                            else (_compare_mixed_state(result, original, reverse, _state_program)
                                  if type(change) is PreparedMixedTemplateStateYAML
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


def _compare_state(result, original, reverse, program):
    if original.changed_domains != ('template',):
        _fail('state_native_result_mismatch')
    return _compare_state_full(result, original, reverse, program, ('template',))


def _compare_mixed_state(result, original, reverse, program):
    from .yaml_transaction import _assert_mixed_state_scope
    _assert_mixed_state_scope(original)
    return _compare_state_full(result, original, reverse, program, original.changed_domains)


def _compare_state_full(result, original, reverse, program, domains):
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
    from .native_template_state_yaml import PreparedTemplateStateYAML, PreparedMixedTemplateStateYAML, assert_state_yaml_composition
    if type(chain) is not RegistryReceiptChain or chain.yaml_index != len(chain.plan.steps) - 1:
        _fail('chain_binding')
    change = chain._original_change(chain.yaml_index)
    if type(change) not in (PreparedTemplateStateYAML, PreparedMixedTemplateStateYAML):
        _fail('state_preparation_required')
    composition = assert_state_yaml_composition(chain, change)
    return await async_revalidate_accepted_candidate(hass, chain, phase_loader,
        scratch_parent, guard, timeout=timeout, _state_program=composition.state_config_program)
