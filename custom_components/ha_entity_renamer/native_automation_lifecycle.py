"""Private idle-automation lifecycle on the existing immutable receipt store.

Intent precedes native effects. Fresh readers never replay an uncertain effect.
This idle profile rejects a real source change during its detached interval; it
is not a general action/timer/event continuation or crash recovery mechanism.
"""
from __future__ import annotations

from copy import deepcopy

from .native_automation_transition import NativeAutomationTransition
from .prepared import canonical
from .transaction import Refused


def _need(value):
    if not value:
        raise Refused('automation_lifecycle_unconfirmed') from None


def _key(operation, fingerprint, direction, stage):
    return 'rr-' + canonical(['automation_lifecycle', operation, fingerprint, direction, stage])[1]


def assert_forward_completed(session):
    store, operation, plan = session.receipt_store, session.operation_id, session.plan
    key = _key(operation, plan.fingerprint, 'forward', 'completed')
    row = store.read(key)
    _need(row is not None and row['digest'] == canonical(row['payload'])[1])
    payload = row['payload']
    _need(payload == {'kind': 'automation_lifecycle_completed', 'schema': 1,
        'operation_id': operation, 'fingerprint': plan.fingerprint,
        'direction': 'forward', 'status': 'applied',
        'native_inputs': list(plan.native_inputs),
        'head': session.chain.reconstruct_inputs().head})
    # Completion is accepted only with the exact original root and all native
    # effect intent/receipt pairs. A completion-shaped row alone proves nothing.
    root = store.read(_key(operation, plan.fingerprint, 'forward', 'root'))
    _need(root is not None and root['digest'] == canonical(root['payload'])[1])
    binding = root['payload']
    _need(binding['kind'] == 'automation_lifecycle_root' and binding['schema'] == 1
          and binding['operation_id'] == operation and binding['fingerprint'] == plan.fingerprint
          and binding['direction'] == 'forward' and binding['native_inputs'] == list(plan.native_inputs)
          and binding['preparations'] == session.digests)
    for stage in ('quiesce', 'stage', 'restore'):
        intent = store.read(_key(operation, plan.fingerprint, 'forward', stage + '_intent'))
        receipt = store.read(_key(operation, plan.fingerprint, 'forward', stage + '_receipt'))
        _need(intent is not None and receipt is not None
              and intent['digest'] == canonical(intent['payload'])[1]
              and receipt['digest'] == canonical(receipt['payload'])[1]
              and intent['payload'] == {'kind': 'automation_lifecycle_intent',
                  'root': root['digest'], 'stage': stage}
              and receipt['payload'] == {'kind': 'automation_lifecycle_receipt',
                  'root': root['digest'], 'stage': stage, 'intent': intent['digest']})


class NativeAutomationLifecycle:
    def __init__(self, session, direction):
        _need(direction in ('forward', 'rollback'))
        self.session, self.direction = session, direction
        self.native = NativeAutomationTransition(session.hass)
        self.phases = session.consumers.phases
        self.root, self.remove, self.changed = None, None, False
        self.stage_done = False
        self.expectations = {}
        for old, new in session.mapping.items():
            current = session.hass.states.get(old if direction == 'forward' else new)
            _need(current is not None)
            expected = (current.state, deepcopy(dict(current.attributes)))
            self.expectations[old] = self.expectations[new] = expected

    def check(self):
        self.session._scope()
        _need(not self.changed)
        self.native.check_native()

    def _id(self, stage):
        s = self.session
        return _key(s.operation_id, s.plan.fingerprint, self.direction, stage)

    async def start(self):
        self.check()
        s = self.session
        if self.direction == 'rollback':
            assert_forward_completed(s)
        binding = {'kind': 'automation_lifecycle_root', 'schema': 1,
            'operation_id': s.operation_id, 'fingerprint': s.plan.fingerprint,
            'direction': self.direction, 'native_inputs': list(s.plan.native_inputs),
            'preparations': s.digests, 'enabled': [list(row) for row in self.native.enabled],
            'head': s.chain.reconstruct_inputs().head}
        _, self.root = await s.receipt_store.create(self._id('root'), binding)
        self.check()
        def event(observed):
            key = observed.data.get('entity_id')
            state = observed.data.get('new_state')
            if key in self.expectations and state is not None:
                if (state.state, dict(state.attributes)) != self.expectations[key]:
                    self.changed = True
        self.remove = s.hass.bus.async_listen('state_changed', event)
        await self._effect('quiesce', self.native.quiesce)

    async def _effect(self, stage, effect):
        self.check()
        _need(self.root is not None)
        s = self.session
        _, intent = await s.receipt_store.create(self._id(stage + '_intent'),
            {'kind': 'automation_lifecycle_intent', 'root': self.root, 'stage': stage})
        self.check()
        reload = stage == 'stage'
        ticket = (self.phases.begin_reload('automation', self.direction) if reload else
                  self.phases.begin_automation_transition())
        try:
            await effect()
            self.native.check_native()
            _need(not self.changed)
            if stage != 'restore':
                self.native.assert_disabled()
            if reload:
                self.phases.complete_reload(ticket)
            else:
                self.phases.complete_automation_transition(ticket)
        except BaseException:
            self.phases.abandon_reload(ticket)
            raise
        self.check()
        await s.receipt_store.create(self._id(stage + '_receipt'),
            {'kind': 'automation_lifecycle_receipt', 'root': self.root,
             'stage': stage, 'intent': intent})
        self.check()

    async def stage(self):
        _need(not self.stage_done)
        await self._effect('stage', self.native.stage)
        self.stage_done = True

    async def restore(self):
        _need(self.stage_done)
        await self._effect('restore', self.native.restore)

    async def complete(self, record):
        self.check()
        s = self.session
        expected = 'applied' if self.direction == 'forward' else 'rolled_back'
        _need(record.status == expected and record.plan == s.plan)
        await s.receipt_store.create(self._id('completed'),
            {'kind': 'automation_lifecycle_completed', 'schema': 1,
             'operation_id': s.operation_id, 'fingerprint': s.plan.fingerprint,
             'direction': self.direction, 'status': expected,
             'native_inputs': list(s.plan.native_inputs),
             'head': s.chain.reconstruct_inputs().head})
        self.check()
        self.close()

    def close(self):
        if self.remove is not None:
            self.remove()
            self.remove = None
