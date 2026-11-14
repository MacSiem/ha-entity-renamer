"""Connect one population handoff to the existing Coordinator adapters."""
from __future__ import annotations

from .transaction import PhaseContext, Refused


class _Adapter:
    def __init__(self, inner, handoff):
        self.inner, self.handoff, self.context = inner, handoff, None

    def bind_phase_context(self, context):
        if context is not None and type(context) is not PhaseContext:
            raise Refused('controlled_powercalc_phase_required')
        self.context = context
        binder = getattr(self.inner, 'bind_phase_context', None)
        if binder is not None:
            binder(context)

    async def inspect(self, step):
        return await self.inner.inspect(step)

    async def _start(self, step, direction):
        if self.context is None:
            raise Refused('controlled_powercalc_durable_phase_required')
        self.context.require_effect(step, direction)
        if self.handoff.receipt is None:
            await self.handoff.start(self.context)
        if self.handoff.direction != direction:
            raise Refused('controlled_powercalc_direction_changed')
        if self.handoff.setup_reference is None:
            self.handoff.require_inactive()

    async def apply(self, step, guard):
        await self._start(step, 'forward')
        return await self.inner.apply(step, guard)

    async def compensate(self, step, guard):
        await self._start(step, 'rollback')
        return await self.inner.compensate(step, guard)

    async def verify(self, plan):
        return await self.inner.verify(plan)

    async def verify_rollback(self, plan):
        return await self.inner.verify_rollback(plan)


def attach(coordinator, handoff):
    from .controlled_powercalc_handoff import ControlledPowercalcHandoff
    from .transaction import Coordinator
    if type(coordinator) is not Coordinator or type(handoff) is not ControlledPowercalcHandoff:
        raise Refused('controlled_powercalc_coordinator_binding_required')
    coordinator.adapters = {key: _Adapter(value, handoff)
                            for key, value in coordinator.adapters.items()}
    return coordinator


class _InverseOwner:
    """Admit the existing Powercalc inverse order without a live-phase engine."""
    def __init__(self, root, handoff, guard):
        from types import SimpleNamespace
        self.root, self.handoff, self._scope = root, handoff, guard
        self.chain, self.journal, self.plan = root.chain, root.journal, root.plan
        self.admission = SimpleNamespace(record=root.record)

    async def start_inverse(self):
        from .native_registry_receipts import _digest, _event_id
        self._scope()
        state = self.chain.reconstruct_inputs()
        record = await self.journal.load(self.chain.operation_id)
        self._scope()
        if (record is None or record.direction != 'rollback' or record.status != 'rolling_back'
                or record.phases != ('verified',) * len(self.plan.steps)
                or self.chain.reconstruct_inputs().head != state.head):
            raise Refused('controlled_powercalc_reverse_admission_changed')
        key = _event_id(['controlled_pc_inverse', self.chain.root_id, state.head])
        value = {'kind': 'controlled_powercalc_inverse_admission', 'schema': 1,
                 'root': self.chain.root_id, 'operation_id': self.chain.operation_id,
                 'fingerprint': self.plan.fingerprint, 'forward_head': state.head,
                 'population': [list(item) for item in self.root.composition.population]}
        if self.chain._effective_cold is not None:
            from .native_input_baseline import _drained
            await _drained(self.handoff.hass.async_add_executor_job,
                           lambda: state.assert_current(self.chain.original_receipt))
            self._scope()
            value['cold_registry'] = self.chain._effective_cold
        if self.chain.store.read(key) is not None:
            raise Refused('controlled_powercalc_reverse_no_replay')
        await self.chain.store.create(key, value)
        self._scope()
        if (self.chain.store.read(key) != {'digest': _digest(value), 'payload': value}
                or await self.journal.load(self.chain.operation_id) != record):
            raise Refused('controlled_powercalc_reverse_admission_uncertain')
        self._scope()

    async def verify_restored(self, plan):
        if plan != self.plan or self.handoff.setup_reference is None:
            return False
        self.handoff.require_active()
        return await self.root._full(True)


def inverse(coordinator, root, handoff):
    from .powercalc_reverse_driver import _PowercalcInverseCoordinator
    owner = _InverseOwner(root, handoff, coordinator.guard)
    return _PowercalcInverseCoordinator(owner, coordinator.adapters)
