"""Native lifecycle for Entity Renamer's durable migration backend.

Imports remain lazy so pure planners can run without Home Assistant installed.
"""
from dataclasses import dataclass, field
import asyncio

from .const import DATA_RUNTIME, DATA_WEBSOCKETS, DOMAIN


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
    if profile in ('template_state_binding_v3', 'template_mixed_state_binding_v1'):
        return NativeTemplateStateServer
    if profile in ('template_numeric_entry_reverse_v1', 'template_dependent_yaml_v1'):
        return NativeTemplateYAMLServer
    raise Refused('yaml_original_profile_unavailable')


@dataclass(frozen=True)
class _PreparedReview:
    """Server-retained immutable Plan; no live session, callback or HA tokens."""
    actor_id: str
    plan: object
    entity_count: int
    expires_at: float


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

    async def native_yaml_prepare(self, hass, mapping, *, actor_id, guard, refresh, entity_names=None, device_names=None):
        """Private ordinary mapping -> server-owned immutable retained review.

        No mutation endpoint or family readiness is granted by this method.
        The future authenticated transport supplies its own fresh actor identity;
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
            from .review_retention import async_reclaim_reviews, review_lease
            import uuid
            self.reviews = {op: review for op, review in self.reviews.items()
                            if review.actor_id != actor_id and review.expires_at > hass.loop.time()}
            self.preparations, self.journal = await async_reclaim_reviews(
                hass, keep=self.reviews, guard=current)
            current()
            prefix = uuid.uuid4().hex
            await self.preparations.create('review-' + prefix, review_lease(prefix, actor_id))
            current()
            try:
                session = await NativeTemplateStateServer.prepare(hass, mapping,
                    guard=current, refresh=refresh, entity_names=entity_names,
                    device_names=device_names, review_id=prefix)
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
                plan, operation = session.plan, session.operation_id
                count = sum(change.kind == 'entity' for change in session.registry_changes)
                devices = sum(change.kind == 'device' for change in session.registry_changes)
                self.reviews[operation] = _PreparedReview(actor_id, plan, count, hass.loop.time() + 900)
                changes = []
                for change in session.registry_changes:
                    before, after = change.before, change.after
                    keys = ('entity_id', 'name') if change.kind == 'entity' else ('name_by_user',)
                    fields = {key: {'before': before.get(key), 'after': after.get(key)}
                              for key in keys if before.get(key) != after.get(key)}
                    target = ({'entity_id': before['entity_id']} if change.kind == 'entity'
                              else {'device_id': change.identity})
                    changes.append({**target, 'fields': fields})
                return {'operation_id': operation, 'fingerprint': plan.fingerprint,
                    'entity_count': count, 'device_count': devices, 'changes': changes,
                    'migration_ready': False, 'family_complete': False}
            finally:
                session.close()

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
            async def operation():
                factory = _yaml_factory_for_plan(self.preparations, review.plan)
                session = await factory.bind_prepared(hass, operation_id,
                    review.plan, guard=current, refresh=refresh)
                try:
                    return await session.apply()
                finally:
                    session.close()
            record = await drain_effect(operation())
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
                factory = _yaml_factory_for_plan(self.preparations, record.plan)
                session = await factory.bind_reverse(hass, operation_id,
                    fingerprint, guard=current, refresh=refresh)
                try:
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
    # Private preparations/journal survive reload/removal for explicit recovery.
    return True
