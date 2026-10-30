"""Private idle-automation lifecycle on the existing immutable receipt store.

Intent precedes native effects. Fresh readers never replay an uncertain effect.
This idle profile rejects a real source change during its detached interval; it
is not a general action/timer/event continuation or crash recovery mechanism.
"""
from __future__ import annotations

from copy import deepcopy
import threading
import hashlib
import json

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


def _affected_automations(session):
    """Select from proved native programs; never pause an unrelated consumer.

    Cross-output and broad native-event transitions are not qualified by this
    idle slice. Refuse those before effects rather than muting a whole domain.
    """
    from .reference_planner import Context, Consumer, Scope, Syntax, plan_references
    component = session.hass.data.get('automation')
    _need(component is not None)
    primary = set(session.mapping) | set(session.mapping.values())
    original = session.evidence.template_sources.rows
    candidate = session.evidence.template_candidate_sources.sources.rows
    old, new = ({(domain, key): digest for domain, key, digest in rows}
                for rows in (original, candidate))
    _need(set(old) == set(new))
    changed_template = {session.observer.identities[(domain.removeprefix('template.'), key)][0]
        for domain, key in old if domain in ('template.sensor', 'template.binary_sensor')
        and old[domain, key] != new[domain, key]}
    if any(domain == 'template.state' and old[domain, key] != new[domain, key] for domain, key in old):
        changed_template.update(value[0] for value in session.observer.identities.values())
    probe = {key: key.split('.')[0] + '.renamer_dependency_probe_' + hashlib.sha256(key.encode()).hexdigest()[:24]
             for key in primary}
    own_events = {'state_changed', 'entity_registry_updated', 'device_registry_updated',
        'call_service', 'service_registered', 'service_removed', 'automation_reloaded',
        'script_reloaded', 'event_template_reloaded', 'template_reloaded'}
    targets, watched, events, triggers = [], set(), set(), {}
    def scopes(value, path=()):
        if type(value) is dict:
            return tuple(scope for key, item in value.items() for scope in scopes(item, path + (key,)))
        if type(value) is list:
            return tuple(scope for i, item in enumerate(value) for scope in scopes(item, path + (i,)))
        if type(value) is str:
            return (Scope(path, Syntax.JINJA if '{{' in value or '{%' in value else Syntax.ENTITY_TREE),)
        return ()
    for entity in component.entities:
        raw = json.loads(json.dumps(entity.raw_config, allow_nan=False))
        report = plan_references(raw, probe, Context(Consumer.AUTOMATION, scopes(raw)))
        _need(not report.markers)
        inputs, kinds = set(), set()
        for config in entity._trigger_config:
            if config['platform'] == 'state':
                _need('for' not in config)
                inputs.update(config['entity_id'])
            elif config['platform'] == 'event':
                _need(all(value.is_static for value in config['event_type']))
                kinds.update(value.template for value in config['event_type'])
            else:
                _need(False)
        _need(not inputs & changed_template and not kinds & own_events
              and not ('script' in session.receipt.changed_domains
                       and any(key.startswith('script.') for key in inputs)))
        triggers[entity.unique_id] = inputs
        if report.changes or inputs & primary:
            targets.append(entity.unique_id)
            watched.update(inputs - primary)
            events.update(kinds)
    target_entities = {entity.entity_id for entity in component.entities if entity.unique_id in targets}
    _need(not any(inputs & target_entities for inputs in triggers.values()))
    return tuple(sorted(targets)), tuple(sorted(watched)), tuple(sorted(events))


class NativeAutomationLifecycle:
    def __init__(self, session, direction):
        _need(direction in ('forward', 'rollback'))
        self.session, self.direction = session, direction
        targets, watched, events = _affected_automations(session)
        self.native = NativeAutomationTransition(session.hass, targets=targets)
        self.watched_events = events
        self.phases = session.consumers.phases
        self.root, self.removes, self.changed = None, [], False
        self.stage_done = False
        self.expectations = {}
        self.primary = set(session.mapping) | set(session.mapping.values())
        for old, new in session.mapping.items():
            current = session.hass.states.get(old if direction == 'forward' else new)
            _need(current is not None)
            expected = (current.state, deepcopy(dict(current.attributes)))
            self.expectations[old] = self.expectations[new] = expected
        for key in watched:
            current = session.hass.states.get(key)
            self.expectations[key] = None if current is None else (current.state, deepcopy(dict(current.attributes)))

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
            if key not in self.expectations or (key in self.primary and state is None):
                return
            actual = None if state is None else (state.state, dict(state.attributes))
            if actual != self.expectations[key]:
                self.changed = True
        self.removes.append(s.hass.bus.async_listen('state_changed', event))
        def external_event(observed):
            self.changed = True
        for kind in self.watched_events:
            self.removes.append(s.hass.bus.async_listen(kind, external_event))
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
        activation = NativeAutomationTransition(self.session.hass, targets=self.native.targets)
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
        for remove in self.removes:
            remove()
        self.removes.clear()
