"""Typed clock composition in the existing helper lifecycle and setup ledger.

The already live clock scope is an owned retained engine, never an inactive
engine. Historical descriptions do not create a live clock or authorize replay.
Own-ID keeps its immutable escrow/parser proof alongside current clock transfer.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import math

from .prepared import canonical


def _need(value, reason):
    if not value:
        raise ValueError('integration_clock_lifecycle_' + reason) from None


def snapshot(value):
    keys = {'full','last_valid','time','trigger','deadline','endpoint','endpoint_reported'}
    _need(type(value) is dict and set(value) == keys, 'snapshot_layout')
    for key in ('full','last_valid','endpoint'):
        _need(type(value[key]) is str and Decimal(value[key]).is_finite(), 'finite_actual')
    for key in ('time','endpoint_reported'):
        _need(type(value[key]) is str and datetime.fromisoformat(value[key]).tzinfo is not None,
              'actual_clock')
    _need(value['trigger'] in ('TimeElapsed','StateEvent')
          and type(value['deadline']) in (int,float) and math.isfinite(value['deadline']),
          'timer_shape')
    return value


def describe(scope):
    from .native_integration_continuity import NativeIntegrationContinuity
    from .native_integration_source_clock import IntegrationSourceClockBinding
    from .native_integration_own_clock import IntegrationOwnClockBinding
    _need(type(scope) is NativeIntegrationContinuity
          and type(scope.source_binding) in (IntegrationSourceClockBinding, IntegrationOwnClockBinding),
          'live_scope_required')
    scope._check()
    return {'kind':('integration_own_clock_lifecycle_v1'
                    if type(scope.source_binding) is IntegrationOwnClockBinding
                    else 'integration_source_clock_lifecycle_v1'),
            'intent':list(scope.intent), 'binding':scope.source_binding.description()}


def validate_roots(life):
    values = life.root.get('integration_clocks', {})
    _need(type(values) is dict, 'root_layout')
    for key,value in values.items():
        own = value.get('kind') == 'integration_own_clock_lifecycle_v1' if type(value) is dict else False
        _need(key in life.ids and (key in life.own_registry_changes) == own
              and life.helper_snapshots[key][0]['domain'] == 'integration'
              and any(row['entry_id'] == key and row['state'] == 'loaded'
                      for row in life.root['original'])
              and type(value) is dict and set(value) == {'kind','intent','binding'}
              and value['kind'] in ('integration_source_clock_lifecycle_v1',
                                    'integration_own_clock_lifecycle_v1')
              and type(value['intent']) is list and len(value['intent']) == 2
              and type(value['binding']) is dict
              and value['binding'].get('entry_id') == key, 'root_binding')
    return values


def live(life, key):
    scope = life._integration_clocks.get(key)
    _need(scope is not None and key in validate_roots(life)
          and describe(scope) == life.root['integration_clocks'][key]
          and scope.lease is life.activation_lease
          and scope.entry is life.manager.async_get_entry(key)
          and scope.source_binding.reverse == (life.source_authorization is not None)
          and scope.source_binding.chain.plan == life.plan
          and scope.source_binding.chain.operation_id == life.operation_id
          and scope.source_binding.chain.original_receipt == life.receipt
          and scope.store.preparations is life.store.preparations, 'live_lifecycle_binding')
    return scope


def retained(life, key):
    from homeassistant.helpers.entity import EntityPlatformState
    from homeassistant.helpers.entity_platform import async_get_platforms
    from .native_helper_transaction import _require_inactive
    scope = live(life,key)
    _require_inactive(scope.entry,scope.lease)
    _need(scope._retained and scope._phase == 'retained'
          and scope.old._platform_state is EntityPlatformState.REMOVED
          and not scope.old._on_remove and scope._new is None
          and not any(p.entities for p in async_get_platforms(scope.hass,'integration')
                      if p.config_entry is scope.entry), 'owned_removed_engine')
    scope._timer(scope.old)
    for kind,tracker_key in (('changed',scope.event._TRACK_STATE_CHANGE_DATA),
                             ('reported',scope.event._TRACK_STATE_REPORT_DATA)):
        tracker = scope.hass.data.get(tracker_key)
        _need(tracker is not None, 'tracker_missing')
        for name in scope.source_names:
            targets = [job.target for job in tracker.callbacks.get(name,())]
            _need(targets.count(scope._bridges[kind]) == 1
                  and not any(getattr(t,'__self__',None) is scope.old for t in targets),
                  'retained_listener_ownership')
    return scope


def baseline(chain,life,key):
    """Pure binding of the existing clock intent to ORIGINAL preparations."""
    descriptor = validate_roots(life)[key]
    if descriptor['kind'] == 'integration_own_clock_lifecycle_v1':
        from .native_integration_own_clock import own_baseline
        return own_baseline(chain,life,key,descriptor)
    ref = descriptor['intent']
    row = chain.store.read(ref[0])
    _need(row is not None and row['digest'] == ref[1]
          and canonical(row['payload'])[1] == ref[1], 'clock_intent_reference')
    doc = row['payload']
    before,after = life.helper_snapshots[key]
    reverse = life.source_authorization is not None
    source,target = (after,before) if reverse else (before,after)
    physical = [r for r in life.original_entities
                if before['options']['source'] in (r['id'],r['entity_id'])]
    helpers = [r for r in life.original_entities if r.get('config_entry_id') == key]
    _need(len(physical) == len(helpers) == 1 and key not in life.own_registry_changes,
          'source_and_same_helper')
    changes = [chain._original_change(i) for i,s in enumerate(chain.plan.steps)
               if s.adapter == 'registry' and s.identity == physical[0]['id']]
    _need(len(changes) == 1, 'one_prepared_source')
    change, = changes
    from .native_registry import PreparedRegistryChange
    _need(type(change) is PreparedRegistryChange and change.kind == 'entity'
          and change.before == physical[0]
          and change.identity == change.before['id'] == change.after['id']
          and change.before['entity_id'] != change.after['entity_id']
          and {k:v for k,v in change.before.items() if k not in ('entity_id','modified_at')}
           == {k:v for k,v in change.after.items() if k not in ('entity_id','modified_at')},
          'source_only_preparation')
    expected = canonical(before)[0]
    if expected['options']['source'] == change.before['entity_id']:
        expected['options']['source'] = change.after['entity_id']
    _need(after == expected, 'source_only_configuration')
    binding = {'kind':'integration_source_clock_v1',
        'root':[chain.root_id,canonical(chain.root)[1]],'entry_id':key,
        'source_uuid':physical[0]['id'],
        'registry_preparation':canonical({'before':change.before,'after':change.after})[1],
        'source_snapshot':canonical(source)[1],'target_snapshot':canonical(target)[1],
        'direction':'rollback' if reverse else 'forward'}
    _need(descriptor['binding'] == binding
          and set(doc) == {'kind','schema','entry_id','helper_uuid','source_uuid',
                          'core','config','actual','source_binding'}
          and doc['kind'] == 'integration_clock_intent' and doc['schema'] == 1
          and doc['entry_id'] == key and doc['helper_uuid'] == helpers[0]['id']
          and doc['source_uuid'] == physical[0]['id'] and doc['config'] == source
          and doc['core'] == chain.version and doc['source_binding'] == binding,
          'original_clock_intent')
    snapshot(doc['actual'])
    setup = life.store.read(life._slot('setup',key,'intent'))
    _need(setup is not None, 'lifecycle_setup_intent')
    return {'kind':'integration_source_clock_restore_v1','intent':list(ref),
            'source_binding':binding,'helper_uuid':helpers[0]['id'],
            'authorization':setup['payload']['authorization']}


def validate_unload(life,key,data):
    _need(type(data) is dict and set(data) == {'intent','actual'}
          and data['intent'] == validate_roots(life)[key]['intent'], 'unload_binding')
    snapshot(data['actual'])


def validate_audit(baseline_value,source_rows,doc,*,chain,core,entry_id):
    own = baseline_value.get('kind') == 'integration_own_clock_restore_v1'
    _need(chain is not None and set(doc) == {'kind','schema','core','entry_id',
        'baseline','source_rows','result_rows','clock_receipt','source_pins'}
        and doc['kind'] == ('integration_own_clock_transfer' if own
                            else 'integration_source_clock_setup') and doc['schema'] == 1
        and doc['core'] == core and doc['entry_id'] == entry_id
        and doc['baseline'] == baseline_value and doc['source_rows'] == source_rows
        and doc['result_rows'] == source_rows and len(source_rows) == 1
        and source_rows[0]['id'] == baseline_value['helper_uuid'], 'setup_binding')
    from .native_utility_setup_audit import _SOURCES
    from .native_integration_unload_audit import _INTEGRATION,_INIT
    from .native_integration_continuity import _EVENT_PINS
    pins = dict(_SOURCES[core])
    pins.update({'components/integration/sensor.py':_INTEGRATION[core],
                 'components/integration/__init__.py':_INIT[core],
                 'helpers/event.py':_EVENT_PINS[core]})
    _need(doc['source_pins'] == pins, 'source_pins')
    ref = doc['clock_receipt']
    _need(type(ref) is list and len(ref) == 2, 'receipt_reference')
    row = chain.store.read(ref[0])
    _need(row is not None and row['digest'] == ref[1]
          and canonical(row['payload'])[1] == ref[1], 'actual_clock_receipt')
    receipt = row['payload']
    _need(set(receipt) == {'kind','schema','intent','entry_id','actual_transfer',
        'transfer_readback','native_old_calls','bridge_calls','new_timer_rearms',
        'old_timer_cancelled','source_binding'}
        and receipt['kind'] == 'integration_clock_receipt' and receipt['schema'] == 1
        and receipt['intent'] == baseline_value['intent'] and receipt['entry_id'] == entry_id
        and type(receipt['new_timer_rearms']) is int and receipt['new_timer_rearms'] == 1
        and receipt['old_timer_cancelled'] is True,
        'actual_setup_transfer')
    actual = snapshot(receipt['actual_transfer'])
    _need(receipt['transfer_readback'] == {k:actual[k] for k in
          ('full','last_valid','time','trigger')}, 'transfer_readback')
    binding = dict(receipt['source_binding'])
    auth = binding.pop('authorization',None)
    transport = binding.pop('transport_calls',None)
    _need(type(transport) is int and transport == (0 if own else 1), 'source_transport_count')
    frozen = binding.pop('frozen_calls',None)
    _need(type(frozen) is int and frozen >= 0, 'frozen_call_shape')
    _need(all(type(receipt[k]) is int and receipt[k] >= 0
              for k in ('native_old_calls','bridge_calls')),
          'native_call_shape')
    _need(binding == baseline_value['source_binding'] and type(auth) is dict,
          'source_authorization')
    _need(auth == baseline_value['authorization'], 'setup_authorization_binding')
    return source_rows


class NativeIntegrationClockSetupAudit:
    def __init__(self,hass,life,chain,key,rows,*,guard,setup_intent):
        self.hass,self.life,self.chain,self.key = hass,life,chain,key
        self.scope,self.guard,self.intent = live(life,key),guard,setup_intent
        self.baseline = baseline(chain,life,key)
        self.rows = canonical(rows)[0]

    def __enter__(self):
        self.scope.source_binding.bind_helper_setup(self.scope,self.life,self.intent)
        return self

    def finish(self):
        from .native_integration_setup_audit import IntegrationSetupAuditData
        from . import native_restore_state_payload as codec
        _need(self.guard() is not False and live(self.life,self.key) is self.scope,
              'live_setup_scope')
        self.scope._check(owner=True)
        _need(self.scope._phase == 'loaded' and self.scope._new is not None
              and self.scope._new_timer_bound and self.scope._old_cancelled.cancelled(),
              'one_native_transfer')
        self.scope._timer(self.scope._new)
        own = self.baseline.get('kind') == 'integration_own_clock_restore_v1'
        doc = {'kind':('integration_own_clock_transfer' if own else 'integration_source_clock_setup'),
            'schema':1,
            'core':self.scope.version,'entry_id':self.key,'baseline':self.baseline,
            'source_rows':self.rows,'result_rows':self.rows,
            'clock_receipt':list(self.scope.receipt),'source_pins':self.scope.pins}
        validate_audit(self.baseline,self.rows,doc,chain=self.chain,
                       core=self.scope.version,entry_id=self.key)
        if self.chain.active is self.intent:
            # Already transferred native clock + validated immutable audit;
            # pending flush readback is not accepted H0 physical authority.
            self.scope.source_binding._require_setup_observation(self.scope)
        else:
            # After ACK only reconstructed accepted H1 can fence the graph.
            self.scope.source_binding.require_accepted(self.scope,seam=True)
        return IntegrationSetupAuditData(codec._canonical(doc))

    def __exit__(self,*_args):
        if getattr(self.scope.source_binding,'_helper_setup_intent',None) is self.intent:
            self.scope.source_binding._helper_setup_intent = None
