"""Private idle-automation lifecycle on the existing immutable receipt store.

Intent precedes native effects. Fresh readers never replay an uncertain effect.
This idle profile rejects a real source change during its detached interval; it
is not a general action/timer/event continuation or crash recovery mechanism.
"""
from __future__ import annotations

from copy import deepcopy
import threading

from .native_automation_transition import NativeAutomationTransition
from .prepared import canonical
from .transaction import Refused


def _need(value):
    if not value:
        raise Refused('automation_lifecycle_unconfirmed') from None


def _key(operation, fingerprint, direction, stage):
    return 'rr-' + canonical(['automation_lifecycle', operation, fingerprint, direction, stage])[1]


def _completed(store, operation, plan, digests, direction, status, head):
    row = store.read(_key(operation, plan.fingerprint, direction, 'completed'))
    _need(row is not None and row['digest'] == canonical(row['payload'])[1])
    _need(row['payload'] == {'kind': 'automation_lifecycle_completed', 'schema': 1,
        'operation_id': operation, 'fingerprint': plan.fingerprint,
        'direction': direction, 'status': status,
        'native_inputs': list(plan.native_inputs), 'head': head})
    root = store.read(_key(operation, plan.fingerprint, direction, 'root'))
    _need(root is not None and root['digest'] == canonical(root['payload'])[1])
    binding = root['payload']
    _need(type(binding) is dict and set(binding) == {'kind', 'schema', 'operation_id',
        'fingerprint', 'direction', 'native_inputs', 'preparations', 'enabled', 'head'}
        and binding['kind'] == 'automation_lifecycle_root' and type(binding['schema']) is int
        and binding['schema'] == 1 and binding['operation_id'] == operation
        and binding['fingerprint'] == plan.fingerprint and binding['direction'] == direction
        and binding['native_inputs'] == list(plan.native_inputs) and binding['preparations'] == digests
        and type(binding['head']) is str and len(binding['head']) == 64
        and type(binding['enabled']) is list and all(
            type(row) is list and len(row) == 2 and type(row[0]) is str and bool(row[0])
            and type(row[1]) is bool for row in binding['enabled'])
        and len({row[0] for row in binding['enabled']}) == len(binding['enabled']))
    for stage in ('quiesce', 'stage', 'restore'):
        intent = store.read(_key(operation, plan.fingerprint, direction, stage + '_intent'))
        receipt = store.read(_key(operation, plan.fingerprint, direction, stage + '_receipt'))
        _need(intent is not None and receipt is not None
              and intent['digest'] == canonical(intent['payload'])[1]
              and receipt['digest'] == canonical(receipt['payload'])[1]
              and intent['payload'] == {'kind': 'automation_lifecycle_intent',
                  'root': root['digest'], 'stage': stage}
              and receipt['payload'] == {'kind': 'automation_lifecycle_receipt',
                  'root': root['digest'], 'stage': stage, 'intent': intent['digest']})
    return binding


def assert_forward_completed(session):
    root = _completed(session.receipt_store, session.operation_id, session.plan,
        session.digests, 'forward', 'applied', session.chain.reconstruct_inputs().head)
    _need(root['head'] == session.chain.root_digest)


async def async_assert_no_pending_automation_lifecycle(hass, preparations, journal):
    """Read-only admission, including terminal inner records with pending outer IO.

    The already authenticated preparation row selects this profile without
    decoding/copying old Template-only full-cardinality baselines again. Actual
    mixed records still get their full original composition and chain checked.
    """
    from .native_template_composition import MixedTemplateComposition
    from .native_registry_receipts import RegistryReceiptChain, native_registry_receipt_store
    candidates = []
    for record in await journal.all():
        if record.plan.required_scopes != ('registry', 'yaml'):
            continue
        reference = record.plan.native_inputs
        _need(type(reference) is tuple and len(reference) == 3)
        row = preparations.plans.get(reference[0])
        _need(row is not None and row['digest'] == reference[1])
        composition = row['payload'].get('composition')
        if type(composition) is dict and composition.get('profile') == 'template_dependent_yaml_v1':
            candidates.append(record)
    if not candidates:
        return
    store = native_registry_receipt_store(hass, preparations)
    await store.initialize()
    for record in candidates:
        baseline, composition = preparations.resolve_composition(record.plan.native_inputs)
        _need(type(composition) is MixedTemplateComposition)
        digests = dict(composition.prepared_digests)
        chain = RegistryReceiptChain.reopen(store, record.operation_id, record.plan, baseline.receipt, digests)
        _need(not chain.writable and chain.active is None)
        head = chain.reconstruct_inputs().head
        if record.status == 'applied' and record.direction == 'forward':
            _need(record.phases == ('verified',) * len(record.plan.steps))
            root = _completed(store, record.operation_id, record.plan, digests, 'forward', 'applied', head)
            _need(root['head'] == chain.root_digest)
        elif record.status == 'rolled_back' and record.direction == 'rollback':
            _need(record.phases == ('undone',) * len(record.plan.steps))
            reverse = _completed(store, record.operation_id, record.plan, digests, 'rollback', 'rolled_back', head)
            root = _completed(store, record.operation_id, record.plan, digests, 'forward', 'applied', reverse['head'])
            _need(root['head'] == chain.root_digest)
        else:
            _need(False)


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

    def check_live(self):
        # Physical workers retain their own file guards; HA runtime is sampled
        # only on its owning loop, including all actual mutation boundaries.
        if threading.get_ident() != self.native.thread:
            return
        _need(not self.changed)
        self.native.check_native()

    def check(self):
        self.session._scope()
        self.check_live()

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
            if self.phases.active is not None:
                self.phases.abandon_reload(ticket)
            raise
        self.check()
        await s.receipt_store.create(self._id(stage + '_receipt'),
            {'kind': 'automation_lifecycle_receipt', 'root': self.root,
             'stage': stage, 'intent': intent})
        self.check()

    async def stage(self):
        _need(not self.stage_done)
        # YAML activation is drained in a native child task. Give that exact
        # task its own staging capability; the parent retains enabled baselines
        # and its separate restore capability. No shared task pin is reassigned.
        activation = NativeAutomationTransition(self.session.hass)
        activation.assert_disabled()
        await self._effect('stage', activation.stage)
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
