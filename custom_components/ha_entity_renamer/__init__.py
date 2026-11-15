"""Native lifecycle for Entity Renamer's durable migration backend.

Imports remain lazy so pure planners can run without Home Assistant installed.
"""
from dataclasses import dataclass, field
import asyncio

from .const import DATA_RUNTIME, DATA_WEBSOCKETS, DOMAIN
from .installed_cost_trace import trace_request, trace_owned


class _ReviewRetry(Exception):
    """A pre-effect attempt ended; the next attempt must start from scratch."""


def _review_selected_sources(hass, mapping, entity_names, device_names):
    """Pin only selected native rows and selected devices' member rows."""
    from homeassistant.helpers import entity_registry as er
    from .native_registry import NativeRegistryIO
    from .native_registry_receipts import _freeze_json
    from .transaction import Refused
    if (type(mapping) is not dict or entity_names is not None and type(entity_names) is not dict
            or device_names is not None and type(device_names) is not dict):
        raise Refused('review_selection_invalid')
    io = NativeRegistryIO(hass)
    entities = io.registries['entity']
    rows = {}
    for entity_id in sorted(set(mapping) | set(entity_names or {})):
        row = entities.async_get(entity_id)
        if row is None or row.entity_id != entity_id or not row.id or not row.unique_id:
            raise Refused('source_uuid_required')
        rows[row.id] = io.row('entity', row.id)
    devices = {}
    for identity in sorted(device_names or {}):
        try:
            devices[identity] = io.row('device', identity)
        except ValueError:
            raise Refused('selected_device_source_required') from None
        members = er.async_entries_for_device(entities, identity, include_disabled_entities=True)
        for row in members:
            if not row.id or not row.unique_id or row.device_id != identity:
                raise Refused('selected_device_source_required')
            rows[row.id] = io.row('entity', row.id)
    return _freeze_json({'entities': dict(sorted(rows.items())),
                         'devices': dict(sorted(devices.items()))})


def _retry_difference(old, new):
    """Closed, value-free diagnostics; never grant authority to retry."""
    from .native_input_baseline import _json, _loads

    def equal_json(left, right, *, data=False):
        try:
            left, right = _loads(left), _loads(right)
            if data:
                left, right = left['data'], right['data']
            return _json(left) == _json(right)
        except (ValueError, TypeError, KeyError):
            return None

    return {
        'key_changed': old.key != new.key,
        'raw_changed': old.raw != new.raw,
        'manager_changed': old.manager_json != new.manager_json,
        'mode_changed': (old.identity is None or new.identity is None
                         or old.identity[2] != new.identity[2]),
        'manager_semantic_equal': equal_json(old.manager_json, new.manager_json),
        'envelope_semantic_equal': equal_json(old.raw, new.raw),
        'disk_data_semantic_equal': equal_json(old.raw, new.raw, data=True),
        'old_disk_manager_equal': _equal_json_data_manager(old.raw, old.manager_json),
        'new_disk_manager_equal': _equal_json_data_manager(new.raw, new.manager_json),
    }


def _equal_json_data_manager(raw, manager):
    """Compare disk data and manager without emitting either image."""
    from .native_input_baseline import _json, _loads
    try:
        return _json(_loads(raw)['data']) == _json(_loads(manager))
    except (ValueError, TypeError, KeyError):
        return None


def _retry_receipt(baseline, observed):
    """Bind a retry to the same bytes and all unchanged validation sources.

    This temporary receipt authorizes no effects. The next complete prepare
    creates its own original receipt and keeps all ordinary fences.
    """
    from dataclasses import replace
    from .transaction import Refused
    from .native_input_baseline import _KEYS
    if (observed.version != baseline.receipt.version
            or observed.config_root != baseline.receipt.config_root):
        raise Refused('review_native_settle_changed')
    updated = {}
    for old, new in zip(baseline.files, observed.files, strict=True):
        if (old.key != new.key or old.raw is None or old.identity is None
                or old.raw != new.raw or old.manager_json != new.manager_json
                or old.identity[2] != new.identity[2]):
            import logging
            try:
                diagnostic = _retry_difference(old, new)
            except Exception:
                diagnostic = {'unavailable': True}
            logging.getLogger(__name__).warning('Native retry comparison: %s', diagnostic)
            raise Refused('review_native_settle_changed')
        updated['.storage/' + old.key] = (old.sha256, *new.identity[:2])
    if tuple(row.key for row in observed.files) != _KEYS:
        raise Refused('review_native_settle_changed')
    receipt = baseline.receipt
    sources = tuple((path, *(updated[path] if path in updated else (digest, dev, ino)))
                    for path, digest, dev, ino in receipt.source_files)
    if not set(updated) <= {row[0] for row in sources}:
        raise Refused('review_native_settle_changed')
    directories = []
    for path, entries, names in receipt.source_directories:
        if path == '.storage':
            entries = tuple((name, mode, *(updated['.storage/' + name][1:]
                if '.storage/' + name in updated else (dev, ino)))
                for name, mode, dev, ino in entries)
        directories.append((path, entries, names))
    return replace(receipt, source_files=sources, source_directories=tuple(directories))


async def _retry_pending_review(hass, guard, native, *, receipt=None, baseline=None):
    """Drain the pending native save, then discard the entire attempt."""
    from .native_discovery_inputs import async_observe_discovery_inputs
    await async_observe_discovery_inputs(hass, guard=guard, settle=True)
    guard()
    raise _ReviewRetry()


def CONFIG_SCHEMA(config):
    from homeassistant.helpers.config_validation import config_entry_only_config_schema
    return config_entry_only_config_schema(DOMAIN)(config)


def _yaml_factory_for_plan(preparations, plan):
    """Advisory typed routing only; the factory freshly admits every original.

    Peek at the already authenticated immutable row instead of decoding/copying
    its potentially large baseline solely to choose an implementation.
    """
    from .transaction import Plan, Refused
    from .native_template_state_server import NativeTemplateStateServer
    from .native_template_yaml_server import NativeTemplateYAMLServer
    preparations._ready()
    if type(plan) is not Plan or type(plan.native_inputs) is not tuple or len(plan.native_inputs) != 3:
        raise Refused('yaml_original_profile_unavailable')
    row = preparations.plans.get(plan.native_inputs[0])
    if row is None or row['digest'] != plan.native_inputs[1]:
        raise Refused('yaml_original_profile_unavailable')
    composition = row['payload'].get('composition')
    profile = composition.get('profile') if type(composition) is dict else None
    if profile == 'powercalc_source_own_binding_v1':
        from .powercalc_public_server import PowercalcPublicServer
        return PowercalcPublicServer
    if profile in ('template_state_binding_v3', 'template_mixed_state_binding_v1', 'template_external_state_binding_v1', 'template_controlled_reload_v1', 'template_controlled_reload_entry_v1', 'template_controlled_script_reload_v1', 'template_script_prefix_binding_v1', 'template_automation_prefix_binding_v1', 'template_controlled_powercalc_v1'):
        return NativeTemplateStateServer
    if profile == 'template_numeric_entry_reverse_v1' and plan.required_scopes == ('registry', 'helpers'):
        from .native_template_forward import NativeTemplateForward
        return NativeTemplateForward
    if profile in ('template_numeric_entry_reverse_v1', 'template_dependent_yaml_v1'):
        return NativeTemplateYAMLServer
    raise Refused('yaml_original_profile_unavailable')


def _review_session_binding(session):
    """Revalidate the same prepared plan and changes across review awaits."""
    from dataclasses import asdict
    from .native_input_baseline import _receipt_bytes
    from .native_registry_receipts import _freeze_json, _exact_json_equal
    from .prepared import encode_change
    from .transaction import Refused
    plan, operation, receipt = session.plan, session.operation_id, session.receipt
    entries = getattr(session, 'registry_changes', None)
    is_entry = entries is None
    changes = (session.registry_change,) if is_entry else tuple(entries)
    plan_image = _freeze_json(asdict(plan))
    change_images = _freeze_json([encode_change(change) for change in changes])
    receipt_image = _receipt_bytes(receipt)
    def check():
        current = getattr(session, 'registry_changes', None)
        if is_entry:
            current = (session.registry_change,) if current is None else ()
        else:
            current = () if current is None else tuple(current)
        if (session.plan is not plan or session.operation_id != operation
                or session.receipt is not receipt or len(current) != len(changes)
                or any(a is not b for a, b in zip(current, changes, strict=True))
                or not _exact_json_equal(asdict(plan), plan_image)
                or not _exact_json_equal([encode_change(change) for change in current], change_images)
                or _receipt_bytes(receipt) != receipt_image):
            raise Refused('review_preparation_changed')
        # The INITIAL observer's executor proof must still describe the same
        # sources after discovery and every other review await.
        observer = getattr(session, 'observer', None)
        witness = getattr(observer, '_composite', None) or getattr(observer, '_last_runtime', None)
        base = getattr(witness, 'base', witness)
        if getattr(base, 'review_identity', None) is not None:
            witness.check()
    return plan, operation, changes, is_entry, check


@dataclass(frozen=True)
class _PreparedReview:
    """Server-retained Plan and optional read-only execution admission pins."""
    actor_id: str
    plan: object
    entity_count: int
    expires_at: float
    execution_admission: tuple = ()
    own_admission: object = None
    trace_admission: tuple = ()
    template_admission: object = None
    entry_admission: object = None
    foreign_admission: object = None
    foreign_loaded: tuple = ()


@dataclass
class Runtime:
    entry_id: str
    journal: object
    preparations: object
    operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    loaded: bool = True
    unloading: bool = False
    reviews: dict = field(default_factory=dict, repr=False)

    async def _recovery(self, hass, action, operation_id=None, fingerprint=None, *, guard, refresh):
        from .recovery_controller import RecoveryController
        def current():
            guard()
            if (hass.data.get(DATA_RUNTIME) is not self or not self.loaded or self.unloading):
                raise ValueError('recovery_runtime_unavailable')
        current()
        if action == 'status':
            # Inventory has no native capability and must remain available while
            # an owned mutation drains. Never install its fresh readers in Runtime.
            await refresh()
            current()
            controller = RecoveryController(hass, self, guard=current, refresh=refresh,
                                            status_only=True)
            result = await controller.status()
            current()
            return result
        async with self.operation_lock:
            current()  # Fresh identity/role after the native FIFO lock wait.
            await refresh()
            current()
            controller = RecoveryController(hass, self, guard=current, refresh=refresh)
            if action == 'inspect':
                result = await controller.inspect(operation_id, fingerprint)
            elif action == 'restore':
                result = await controller.restore(operation_id, fingerprint)
            else:
                raise ValueError('recovery_invalid_action')
            current()
            return result

    async def recovery_status(self, hass, *, guard, refresh):
        return await self._recovery(hass, 'status', guard=guard, refresh=refresh)

    async def recovery_inspect(self, hass, operation_id, fingerprint, *, guard, refresh):
        return await self._recovery(hass, 'inspect', operation_id, fingerprint, guard=guard, refresh=refresh)

    async def recovery_restore(self, hass, operation_id, fingerprint, *, guard, refresh):
        return await self._recovery(hass, 'restore', operation_id, fingerprint, guard=guard, refresh=refresh)

    @trace_request("prepare")
    async def native_yaml_prepare(self, hass, mapping, *, actor_id, guard, refresh, entity_names=None, device_names=None):
        # Freeze the caller's requested selection across an interrupted attempt.
        if type(mapping) is dict:
            mapping = dict(mapping)
        if type(entity_names) is dict:
            entity_names = dict(entity_names)
        if type(device_names) is dict:
            device_names = dict(device_names)
        selection = []
        from .native_template_state_yaml import ReviewSourceInvalidated
        for attempt in range(2):
            try:
                return await self._native_yaml_prepare_once(hass, mapping, actor_id=actor_id,
                    guard=guard, refresh=refresh, entity_names=entity_names,
                    device_names=device_names, retry=bool(attempt), selection=selection)
            except (_ReviewRetry, ReviewSourceInvalidated) as error:
                if attempt:
                    from .transaction import Refused
                    reason = ('review_source_invalidated_repeated' if isinstance(error, ReviewSourceInvalidated)
                              else 'review_native_settle_repeated')
                    raise Refused(reason) from None
                # A new attempt begins only after the abandoned lease is reclaimed.
                from .native_template_reverse import _authorize
                from .review_retention import async_reclaim_reviews
                def current():
                    _authorize(guard)
                    if hass.data.get(DATA_RUNTIME) is not self or not self.loaded or self.unloading:
                        from .transaction import Refused
                        raise Refused('review_runtime_unavailable')
                async with self.operation_lock:
                    current()
                    self.preparations, self.journal = await async_reclaim_reviews(
                        hass, keep=self.reviews, guard=current)
                    current()

    async def _native_yaml_prepare_once(self, hass, mapping, *, actor_id, guard, refresh,
                                        entity_names=None, device_names=None, retry=False, selection=None):
        """Private ordinary mapping -> server-owned immutable retained review.

        No mutation endpoint or family readiness is granted by this method.
        The authenticated transport supplies its own fresh actor identity;
        neither a Plan nor any original/source document is accepted from clients.
        """
        from .native_template_state_server import NativeTemplateStateServer
        from .native_template_reverse import _authorize
        from .transaction import Refused
        def current():
            _authorize(guard)
            if (hass.data.get(DATA_RUNTIME) is not self or not self.loaded or self.unloading
                    or type(actor_id) is not str or not actor_id):
                raise Refused('review_runtime_unavailable')
        current()
        async with self.operation_lock:
            await refresh()
            current()
            selected = _review_selected_sources(hass, mapping, entity_names, device_names)
            if retry:
                if not selection or selection[0] != selected:
                    raise Refused('review_selected_source_changed')
            else:
                selection.append(selected)
            from .native_discovery_inputs import async_observe_discovery_inputs
            native = await async_observe_discovery_inputs(hass, guard=current, settle=True)
            from .review_retention import async_reclaim_reviews, review_lease
            import uuid
            retained = {}
            for op, review in self.reviews.items():
                if review.actor_id != actor_id and review.expires_at > hass.loop.time():
                    retained[op] = review
                elif review.foreign_admission is not None:
                    review.foreign_admission.close()
            self.reviews = retained
            self.preparations, self.journal = await async_reclaim_reviews(
                hass, keep=self.reviews, guard=current)
            current()
            prefix = uuid.uuid4().hex
            await self.preparations.create('review-' + prefix, review_lease(prefix, actor_id))
            current()
            try:
                powercalc_selected = False
                if 'powercalc' in hass.config.components and mapping:
                    from .native_registry import NativeRegistryIO
                    from .powercalc_runtime_observer import capture_powercalc_runtime, selected_powercalc_reference
                    rows = NativeRegistryIO(hass).document('entity')['entities']
                    powercalc_selected = any(row.get('platform') == 'powercalc'
                        and row.get('entity_id') in mapping for row in rows)
                    if not powercalc_selected:
                        try:
                            inventory = capture_powercalc_runtime(hass, guard=current)
                            powercalc_selected = selected_powercalc_reference(inventory, mapping, rows)
                        except ValueError:
                            # Discovery retains the unavailable/partial PC
                            # inventory as an explicit coverage limitation.
                            current()
                if powercalc_selected:
                    session = await NativeTemplateStateServer.prepare(hass, mapping,
                        guard=current, refresh=refresh, entity_names=entity_names,
                        device_names=device_names, review_id=prefix, _mixed_powercalc=True, _review_native=native, _review_retry=retry)
                elif hass.config_entries.async_entries('template') and 'powercalc' not in hass.config.components:
                    # YAML dependencies belong to the same ordinary plan. The
                    # legacy one-entry path remains only for no-YAML requests.
                    from pathlib import Path
                    from .native_input_baseline import _drained
                    from .yaml_plan import plan_yaml
                    root = Path(hass.config.config_dir).resolve()
                    proposal = await _drained(hass.async_add_executor_job, lambda: plan_yaml(root, mapping))
                    current()
                    if proposal.patches or not proposal.ready:
                        session = await NativeTemplateStateServer.prepare(hass, mapping,
                            guard=current, refresh=refresh, entity_names=entity_names,
                            device_names=device_names, review_id=prefix, _review_native=native, _review_retry=retry)
                    else:
                        from .native_template_forward import NativeTemplateForward
                        if entity_names or device_names:
                            raise Refused('template_forward_names_unsupported')
                        session = await NativeTemplateForward.prepare(hass, mapping,
                            guard=current, refresh=refresh, review_id=prefix, _review_native=native, _review_retry=retry)
                else:
                    session = await NativeTemplateStateServer.prepare(hass, mapping,
                        guard=current, refresh=refresh, entity_names=entity_names,
                        device_names=device_names, review_id=prefix, _review_native=native, _review_retry=retry)
            except BaseException:
                # Revocation/uncertain IO can prevent immediate housekeeping.
                # Its durable lease remains reclaimable at next prepare/setup.
                try:
                    self.preparations, self.journal = await async_reclaim_reviews(
                        hass, keep=self.reviews, guard=current)
                except Exception:
                    pass
                raise
            try:
                current()
                plan, operation, registry_changes, is_entry, check_session = _review_session_binding(session)
                count = sum(change.kind == 'entity' for change in registry_changes)
                devices = sum(change.kind == 'device' for change in registry_changes)
                changes = []
                for change in registry_changes:
                    before, after = change.before, change.after
                    keys = ('entity_id', 'name') if change.kind == 'entity' else ('name_by_user',)
                    fields = {key: {'before': before.get(key), 'after': after.get(key)}
                              for key in keys if before.get(key) != after.get(key)}
                    target = ({'entity_id': before['entity_id']} if change.kind == 'entity'
                              else {'device_id': change.identity})
                    changes.append({**target, 'fields': fields})
                baseline, composition = await self.preparations.async_resolve_composition(
                    plan.native_inputs, hass=hass, guard=current, receipt=session.receipt)
                check_session()
                from .dependency_review import dependency_files
                receipt = baseline.receipt
                # Aggregate only the admitted planned YAML scope. Paths, YAML,
                # native keys, receipts and private consumer configuration stay here.
                from .powercalc_composition import PowercalcComposition
                from .controlled_powercalc_composition import ControlledPowercalcComposition
                powercalc = type(composition) in (PowercalcComposition, ControlledPowercalcComposition)
                dependencies = {
                    'domains': (sorted(set(['powercalc'] + (['energy'] if 'energy' in plan.required_scopes else [])
                        + [domain for domain in receipt.changed_domains if domain in ('template', 'automation', 'script')]))) if powercalc else (['template'] if is_entry else sorted(domain for domain in receipt.changed_domains
                                      if domain in ('template', 'automation', 'script'))),
                    'yaml_file_count': len(receipt.proposed_files),
                    'files': (session.dependency_files if type(composition) is PowercalcComposition
                              else ([] if is_entry else dependency_files(composition))),
                    'template_consumer_count': 0 if type(composition) is PowercalcComposition else (1 if is_entry else sum(
                        row.domain in ('template.sensor', 'template.binary_sensor')
                        for row in session.evidence.template_sources.ledger.definitions))}
                from .native_template_state_composition import ExternalStateComposition
                if (type(composition) is ExternalStateComposition
                        or type(composition) is ControlledPowercalcComposition
                        and composition.external_source_proof):
                    dependencies['domains'] = sorted(set(dependencies['domains']) |
                        (set(receipt.changed_domains) & {'homekit', 'utility_meter'}))
                if powercalc:
                    from .dependency_review import dependency_powercalc_groups
                    from .prepared import decode_change
                    digests = dict(composition.prepared_digests)
                    helpers = [decode_change(self.preparations.resolve(step.prepared_ref, digests[step.prepared_ref]))
                               for step in plan.steps if step.adapter == 'helpers']
                    dependencies['config_entries'] = dependency_powercalc_groups(
                        helpers, controlled=type(composition) is ControlledPowercalcComposition)
                if 'dashboard' in plan.required_scopes:
                    dependencies['domains'] = sorted([*dependencies['domains'], 'dashboard'])
                    from .dependency_review import dependency_dashboards
                    dependencies['dashboards'] = dependency_dashboards(session.dashboard_changes, session.mapping)
                if is_entry:
                    old, new = next(iter(session.mapping.items()))
                    dependencies['config_entries'] = [{'entity_id': session.observer.rows[0]['entity_id'],
                                                       'before': old, 'after': new}]
                elif getattr(session, 'entry_change', None) is not None:
                    import json
                    change = session.entry_change
                    old, new = next(iter(change.mapping.items()))
                    rows = json.loads(next(f.raw for f in baseline.files if f.key == 'core.entity_registry'))['data']['entities']
                    consumers = [row for row in rows if row.get('config_entry_id') == change.before['identity']['entry_id']]
                    if len(consumers) != 1:
                        raise Refused('controlled_entry_review_consumer_unqualified')
                    dependencies.setdefault('config_entries', []).append(
                        {'entity_id': consumers[0]['entity_id'], 'before': old, 'after': new})
                # Use the existing discovery and the same prepared YAML graph.
                # Its unverified families are limitations, not proof of an
                # affected reference and not an automatic custom-integration ban.
                from .discovery import async_discover
                from .discovery_final_fence import async_finalize_discovery
                from .native_template_state_composition import ControlledReloadComposition, ControlledReloadEntryComposition
                from .controlled_script_composition import ControlledScriptComposition, is_controlled_script
                names = entity_names or {}
                registry_devices = {change.before['entity_id']: change.before.get('device_id')
                                    for change in registry_changes if change.kind == 'entity'}
                proposal = {'entities': [
                    {'entity_id': old, 'proposed_entity_id': mapping.get(old, old),
                     'device_id': registry_devices.get(old),
                     **({'proposed_name': names[old]} if old in names else {})}
                    for old in sorted(set(mapping) | set(names))],
                    'devices': [{'device_id': identity, 'proposed_name': name}
                                for identity, name in sorted((device_names or {}).items())],
                    'statistics': []}
                owned_script = ()
                if is_controlled_script(composition):
                    from .dependent_config_program import verified_script_key_edits
                    key_edits = verified_script_key_edits(composition.evidence, composition.yaml_plan, mapping)
                    if len(key_edits) != 1:
                        raise Refused('review_script_definition_unqualified')
                    owned_script = tuple(key_edits)
                expected_yaml = (composition.yaml_plan if type(composition) in
                    (ControlledReloadComposition, ControlledReloadEntryComposition, ControlledScriptComposition,
                     ControlledPowercalcComposition) else None)
                if type(composition) is ControlledPowercalcComposition:
                    # Discovery scans YAML; the separately sealed Powercalc
                    # profile inputs are verified by the existing PC fence.
                    from dataclasses import replace
                    expected_yaml = replace(expected_yaml, powercalc_inputs=None)
                settled = []
                capture = await async_discover(hass, proposal, session._scope,
                    _expected_yaml=expected_yaml,
                    _owned_script_services=owned_script, _settle_native=True,
                    _on_native_settle=settled.append)
                if settled:
                    if retry:
                        raise Refused('review_native_settle_repeated')
                    raise _ReviewRetry()
                from homeassistant import loader
                custom = await loader.async_get_custom_components(hass)
                capture = await async_finalize_discovery(hass, capture, session._scope)
                if capture.conflicts:
                    raise Refused('review_discovery_source_changed')
                limitations = [{'source': row.name, 'status': row.status,
                    'reason': ('external_consumers_not_enumerated' if row.name == 'external' else row.reason),
                    'blockers': list(row.blockers)} for row in capture.families
                    if row.status != 'COMPLETE' or row.blockers]
                current()
                session._scope()
                limitations.extend({'source': 'config_entry:' + row['domain'] + '/' + row['entry_id'],
                    'status': 'UNKNOWN', 'reason': 'dynamic_reference_unverified', 'blockers': row['reasons']}
                    for row in getattr(session, 'config_entry_limitations', ()))
                limitations.extend({'source': 'custom:' + domain, 'status': 'UNKNOWN',
                    'reason': 'integration_code_not_enumerated', 'blockers': []}
                    for domain in sorted(set(custom) & set(hass.config.components) - {'ha_entity_renamer'}))
                dependencies['coverage'] = limitations
                current()
                phases = getattr(getattr(session, 'consumers', None), 'phases', None)
                admission = tuple(getattr(phases, 'admission', ()))
                from .native_active_execution_pins import check_census
                for script, census in admission:
                    check_census(script, census)
                trace_admission = tuple(getattr(phases, 'trace_admission', ()))
                for proof in trace_admission:
                    proof.check_review()
                own = getattr(session, 'own_dependent_fence', None)
                if own is not None:
                    from .native_own_dependent_fence import NativeOwnDependentAdmission
                    own = NativeOwnDependentAdmission(own)
                    own.check()
                template_admission = getattr(session, 'own_template_admission', None)
                if template_admission is not None:
                    template_admission.check_review()
                foreign = getattr(session, 'foreign', None)
                if foreign is not None:
                    foreign.check(require_sources=True)
                    foreign.expiry = hass.loop.call_later(900, foreign.close)
                check_session()
                if retry and _review_selected_sources(hass, mapping, entity_names, device_names) != selection[0]:
                    raise Refused('review_selected_source_changed')
                self.reviews[operation] = _PreparedReview(actor_id, plan, count,
                    hass.loop.time() + 900, admission, own, trace_admission, template_admission,
                    getattr(session, 'review_admission', None), foreign,
                    tuple(sorted({'mqtt', 'rest', 'command_line'} & set(hass.config.components))))
                if foreign is not None:
                    session.foreign_transferred = True
                return {'operation_id': operation, 'fingerprint': plan.fingerprint,
                    'entity_count': count, 'device_count': devices, 'changes': changes,
                    'dependencies': dependencies,
                    'migration_ready': False, 'family_complete': False}
            except Exception as error:
                from .native_input_baseline import NativeInputWritePending
                from .native_discovery_inputs import DiscoveryNativeWritePending
                if isinstance(error, (NativeInputWritePending, DiscoveryNativeWritePending)):
                    if retry:
                        raise Refused('review_native_settle_repeated') from None
                    await _retry_pending_review(hass, session._scope, native,
                        receipt=getattr(session, 'receipt', None),
                        baseline=getattr(session, '_review_baseline', None))
                raise
            finally:
                session.close()

    @trace_request("apply")
    async def native_yaml_apply(self, hass, operation_id, fingerprint, *, actor_id, guard, refresh):
        """Consume this actor's retained plan under fresh authorization and fences.

        Review is consumed before binding effects. An uncertain/lost response is
        resolved by status/inspection, never a second invocation of this method.
        """
        from .native_template_yaml_server import NativeTemplateYAMLServer
        from .native_template_reverse import _authorize
        from .recovery_controller import _drain_restore as drain_effect
        from .transaction import Refused
        def current():
            _authorize(guard)
            if (hass.data.get(DATA_RUNTIME) is not self or not self.loaded or self.unloading):
                raise Refused('review_runtime_unavailable')
        current()
        async with self.operation_lock:
            await refresh()
            current()
            review = self.reviews.get(operation_id)
            if (type(review) is not _PreparedReview or type(actor_id) is not str
                    or review.actor_id != actor_id or review.plan.fingerprint != fingerprint
                    or review.expires_at <= hass.loop.time()):
                raise Refused('review_unavailable_or_mismatched')
            self.reviews.pop(operation_id)
            if review.foreign_admission is not None and review.foreign_admission.expiry is not None:
                review.foreign_admission.expiry.cancel()
                review.foreign_admission.expiry = None
            @trace_owned
            async def operation():
                try:
                    if review.foreign_loaded != tuple(sorted(
                            {'mqtt', 'rest', 'command_line'} & set(hass.config.components))):
                        raise Refused('review_foreign_population_changed')
                    from .native_active_execution_pins import check_census
                    for script, census in review.execution_admission:
                        check_census(script, census)
                    for proof in review.trace_admission:
                        proof.check_review()
                    if review.template_admission is not None:
                        review.template_admission.check_review()
                    if review.own_admission is not None:
                        review.own_admission.check()
                    if review.entry_admission is not None:
                        review.entry_admission.check()
                    if review.foreign_admission is not None:
                        review.foreign_admission.check(require_sources=True)
                    factory = _yaml_factory_for_plan(self.preparations, review.plan)
                    session = await factory.bind_prepared(hass, operation_id,
                        review.plan, guard=current, refresh=refresh)
                    try:
                        if review.foreign_admission is not None:
                            current_foreign = getattr(session, 'foreign', None)
                            review.foreign_admission.check(require_sources=True)
                            if current_foreign is None or current_foreign.image != review.foreign_admission.image:
                                raise Refused('review_foreign_population_changed')
                            session.review_foreign_pin = review.foreign_admission
                        if review.entry_admission is not None:
                            review.entry_admission.check()
                            session.review_record_admission = review.entry_admission
                        for script, census in review.execution_admission:
                            check_census(script, census)
                        for proof in review.trace_admission:
                            proof.check_review()
                        session.trace_record_admission = review.trace_admission
                        if review.template_admission is not None:
                            review.template_admission.check_review()
                            current_template = getattr(session, 'own_template_admission', None)
                            if (current_template is None or current_template.entity is not review.template_admission.entity
                                    or current_template.tracker is not review.template_admission.tracker):
                                raise Refused('review_own_template_population_changed')
                            session.template_record_admission = review.template_admission
                        if review.own_admission is not None:
                            review.own_admission.bind_scene_session(session)
                            review.own_admission.check()
                            if (getattr(session, 'own_dependent_fence', None) is None
                                    or session.own_dependent_fence.entity is not review.own_admission.entity):
                                raise Refused('review_own_dependent_population_changed')
                            session.own_record_admission = review.own_admission
                        phases = getattr(getattr(session, 'consumers', None), 'phases', None)
                        actual = tuple(getattr(phases, 'admission', ()))
                        if tuple(script for script, _ in actual) != tuple(
                                script for script, _ in review.execution_admission):
                            raise Refused('review_execution_population_changed')
                    except BaseException:
                        session.close()
                        raise
                except Exception:
                    # bind_prepared is read-only and session.apply has not been
                    # entered. Only a freshly authorized durable record may
                    # advertise this precise pre-effect refusal after reconnect.
                    current()
                    from .journal import native_journal
                    from .transaction import Record
                    journal = native_journal(hass)
                    await journal.initialize()
                    current()
                    await journal.create(Record(operation_id, review.plan, status='refused',
                        phases=('pending',) * len(review.plan.steps), reason='prewrite_refused',
                        restore_attempted=False))
                    current()
                    self.journal = journal
                    raise
                try:
                    return await session.apply()
                finally:
                    session.close()
            try:
                record = await drain_effect(operation())
            finally:
                if review.foreign_admission is not None:
                    review.foreign_admission.close()
            current()
            return {'operation_id': record.operation_id, 'fingerprint': record.plan.fingerprint,
                'state': record.status, 'reason': record.reason,
                'migration_ready': False, 'family_complete': False}

    async def native_forward(self, hass, mapping, *, guard, refresh):
        """Private qualified component, not a public migration admission route.

        The same Runtime lock spans fresh authorization, prepare, native effects
        and drain. The native lease belongs to the owned operation task; request
        cancellation cannot release this lock while its IO still runs.
        """
        from .native_template_forward import NativeTemplateForward
        from .native_template_reverse import _authorize
        from .recovery_controller import _drain_restore as drain_effect
        def current():
            _authorize(guard)
            if (hass.data.get(DATA_RUNTIME) is not self or not self.loaded or self.unloading):
                raise ValueError('forward_runtime_unavailable')
        current()
        async with self.operation_lock:
            current()
            await refresh()
            current()
            async def operation():
                # No Template ConfigEntry means the aggregate native YAML route.
                # Selection is server-owned; unsupported populations refuse.
                factory = NativeTemplateForward
                if not hass.config_entries.async_entries('template'):
                    from .native_template_state_server import NativeTemplateStateServer
                    factory = NativeTemplateStateServer
                session = await factory.prepare(hass, mapping,
                    guard=current, refresh=refresh)
                try:
                    return await session.apply()
                finally:
                    session.close()
            record = await drain_effect(operation())
            current()
            # No Plan, receipt, prepared refs or source documents cross this
            # boundary. Global/family readiness is still explicitly false.
            return {'operation_id': record.operation_id,
                'fingerprint': record.plan.fingerprint, 'state': record.status,
                'reason': record.reason, 'migration_ready': False, 'family_complete': False}

    async def native_yaml_reverse(self, hass, operation_id, fingerprint, *, guard, refresh):
        """Private fresh terminal reverse; no retained forward session capability."""
        from .native_template_yaml_server import NativeTemplateYAMLServer
        from .native_template_reverse import _authorize
        from .recovery_controller import _drain_restore as drain_effect
        def current():
            _authorize(guard)
            if (hass.data.get(DATA_RUNTIME) is not self or not self.loaded or self.unloading):
                raise ValueError('reverse_runtime_unavailable')
        current()
        async with self.operation_lock:
            current()
            await refresh()
            current()
            async def operation():
                from .transaction import Refused
                record = await self.journal.load(operation_id)
                current()
                if record is None or record.plan.fingerprint != fingerprint:
                    raise Refused('yaml_operation_mismatch')
                if record.restore_attempted is not False:
                    raise Refused('restore_attempt_unavailable')
                factory = _yaml_factory_for_plan(self.preparations, record.plan)
                session = await factory.bind_reverse(hass, operation_id,
                    fingerprint, guard=current, refresh=refresh)
                try:
                    from .recovery_controller import _consume_yaml_restore_intent
                    await _consume_yaml_restore_intent(session, record, current)
                    return await session.rollback()
                finally:
                    session.close()
            record = await drain_effect(operation())
            current()
            return {'operation_id': record.operation_id,
                'fingerprint': record.plan.fingerprint, 'state': record.status,
                'reason': record.reason, 'migration_ready': False, 'family_complete': False}

    async def discover(self, hass, proposal, *, guard, refresh):
        """Server-owned read under the same lease used by migration/recovery.

        Trusted callers provide fresh authorization IO, never a request field.
        This neither creates preparations nor changes an original recovery plan.
        """
        from .discovery import async_discover
        def current():
            guard()
            if (hass.data.get(DATA_RUNTIME) is not self or not self.loaded
                    or self.unloading):
                raise ValueError('discovery_runtime_unavailable')
        current()
        async with self.operation_lock:
            current()
            await refresh()
            current()
            result = await async_discover(hass, proposal, current)
            current()
            await refresh()
            current()
            from .discovery_final_fence import async_finalize_discovery
            result = await async_finalize_discovery(hass,result,current)
            current()
            return result

    async def preview(self, hass, proposal, *, guard, refresh):
        """Use the very same discovery for the server's conflict preview."""
        from .discovery import compose_preview
        capture = await self.discover(hass, proposal, guard=guard, refresh=refresh)
        result = compose_preview(capture)
        from .discovery import DiscoveryCapture
        if type(capture) is DiscoveryCapture:
            from .discovery_final_fence import _Preview
            return _Preview(result,capture)
        return result


async def async_setup(hass, config):
    return True


async def async_setup_entry(hass, entry):
    # Config flow normally prevents duplicate entries, but setup can overlap
    # during reload/retry; serialize admission before initializing stores.
    lock = hass.data.setdefault(DOMAIN + '.setup_lock', asyncio.Lock())
    async with lock:
        return await _async_setup_entry_locked(hass, entry)


async def _async_setup_entry_locked(hass, entry):
    from homeassistant.exceptions import ConfigEntryNotReady
    from .journal import native_journal
    from .prepared import native_prepared_store
    from .websocket_api import async_register

    if hass.data.get(DATA_RUNTIME) is not None:
        raise ConfigEntryNotReady('An Entity Renamer backend is already loaded')
    journal, preparations = native_journal(hass), native_prepared_store(hass)
    # Never turn a damaged or unconfirmed journal into an empty history.
    try:
        await journal.initialize()
        await preparations.initialize()
        from .review_retention import async_reclaim_reviews
        preparations, journal = await async_reclaim_reviews(hass, guard=lambda: None)
    except Exception as error:
        raise ConfigEntryNotReady('Migration recovery data could not be verified') from error
    try:
        from .frontend import async_register_frontend
        await async_register_frontend(hass)
    except Exception as error:
        raise ConfigEntryNotReady(f'Card resource setup failed: {error}') from error
    runtime = Runtime(entry.entry_id, journal, preparations)
    entry.runtime_data = runtime
    if not hass.data.get(DATA_WEBSOCKETS):
        async_register(hass)
        hass.data[DATA_WEBSOCKETS] = True
    hass.data[DATA_RUNTIME] = runtime
    return True


async def async_unload_entry(hass, entry):
    runtime = hass.data.get(DATA_RUNTIME)
    if runtime is None or runtime.entry_id != entry.entry_id:
        return False
    # Returning False leaves native HA in FAILED_UNLOAD. Drain the current
    # operation while refusing new work, then let HA complete its lifecycle.
    runtime.unloading = True
    try:
        async with runtime.operation_lock:
            runtime.loaded = False
            del hass.data[DATA_RUNTIME]
    finally:
        runtime.unloading = False
    from .frontend import async_unregister_frontend
    await async_unregister_frontend(hass)
    # Private preparations/journal and authored resources survive for recovery.
    return True
