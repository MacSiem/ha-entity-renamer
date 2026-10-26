"""Own-ID policy/composite evidence in the existing Integration clock ledger.

No source transport, arithmetic, runtime rebaseline or durable root. Shared
native event provenance/frozen consumption stays in IntegrationSourceClockBinding.
"""
from __future__ import annotations

from collections import deque
import json

from .helper_native import read_entry
from .native_config_transition import _projection
from .native_registry import PreparedRegistryChange
from .native_registry_receipts import RegistryReceiptChain, RegistryIntent
from .native_integration_source_clock import IntegrationSourceClockBinding
from .native_unload_code_proof import unchanged
from .native_utility_unload_audit import _row
from .prepared import canonical
from .native_restore_state_payload import _canonical


def _need(value, reason):
    if not value:
        raise ValueError('integration_own_clock_' + reason) from None


def original_binding(chain, entry_id, reverse):
    _need(type(chain) is RegistryReceiptChain and type(reverse) is bool, 'original_chain')
    docs = {f.key: json.loads(f.manager_json) for f in chain.baseline.files}
    entries = [r for r in docs['core.config_entries']['entries'] if r['entry_id'] == entry_id]
    _need(len(entries) == 1 and entries[0]['domain'] == 'integration', 'original_entry')
    config = _projection(entries[0])
    rows = docs['core.entity_registry']['entities']
    helpers = [r for r in rows if r.get('config_entry_id') == entry_id]
    sources = [r for r in rows if config['options']['source'] in (r['id'],r['entity_id'])]
    _need(len(helpers) == len(sources) == 1 and sources[0] != helpers[0], 'original_rows')
    helper, source = helpers[0], sources[0]
    _need(config['options'].get('method') == 'left'
          and config['options'].get('unit_time') == 's'
          and config['options'].get('unit_prefix') is None
          and config['options']['source'] == source['entity_id'], 'bounded_left_source_configuration')
    indices = [i for i,s in enumerate(chain.plan.steps)
               if s.adapter == 'registry' and s.identity == helper['id']]
    _need(len(indices) == 1 and not any(s.identity == source['id'] for s in chain.plan.steps),
          'own_only_registry_preparation')
    index, = indices
    change = chain._original_change(index)
    _need(type(change) is PreparedRegistryChange and change.kind == 'entity'
          and change.identity == helper['id'] == change.after['id']
          and change.before == helper and change.before['entity_id'] != change.after['entity_id']
          and helper['platform'] == 'integration' and helper['unique_id'] == entry_id
          and helper.get('config_subentry_id') is None and helper.get('disabled_by') is None
          and {k:v for k,v in change.before.items() if k not in ('entity_id','modified_at')}
           == {k:v for k,v in change.after.items() if k not in ('entity_id','modified_at')},
          'own_id_only_preparation')
    # Own-ID mode never also changes its helper configuration.
    from .helper_transaction import PreparedHelper
    _need(not any(type(chain._original_change(i)) is PreparedHelper
                  and chain._original_change(i).before['identity']['entry_id'] == entry_id
                  for i in range(len(chain.plan.steps))), 'unchanged_configuration')
    root = chain.store.read(chain.root_id)
    _need(root == {'digest':canonical(chain.root)[1],'payload':chain.root}, 'original_root')
    description = {'kind':'integration_own_clock_v1', 'root':[chain.root_id,root['digest']],
        'entry_id':entry_id, 'helper_uuid':helper['id'], 'source_uuid':source['id'],
        'registry_preparation':canonical({'before':change.before,'after':change.after})[1],
        'source_snapshot':canonical(config)[1], 'target_snapshot':canonical(config)[1],
        'direction':'rollback' if reverse else 'forward'}
    return index, change, config, source, description


class IntegrationOwnClockBinding(IntegrationSourceClockBinding):
    """Exact own registry policy, shared authentic native source event engine."""
    def __init__(self, chain, entry_id, *, reverse=False):
        _need(chain.writable and chain.active is None, 'live_chain')
        self.index,self.change,self.source_snapshot,self.source_row,self._description = original_binding(
            chain,entry_id,reverse)
        self.target_snapshot = self.source_snapshot
        self.chain,self.entry_id,self.reverse = chain,entry_id,reverse
        self.direction = 'rollback' if reverse else 'forward'
        self.root_ref = tuple(self._description['root'])
        self.source_uuid = self.source_row['id']
        self.names = (self.source_row['entity_id'],)
        self.from_name = self.to_name = self.names[0]
        self.helper_from,self.helper_to = ((self.change.after['entity_id'],self.change.before['entity_id'])
            if reverse else (self.change.before['entity_id'],self.change.after['entity_id']))
        self.scope = self.authorization = None
        self.flights = deque()
        self._native_events = {}
        self._emission_endpoint = self._endpoint_time = None
        self._report_updates = {}
        self._creation_emitted = False
        self.transport_calls = self.frozen_calls = 0
        self.last_consumption = None

    def physical_uuid(self):
        return self.source_uuid

    def description(self, *, accepted=False):
        value = canonical(self._description)[0]
        if accepted:
            _need(self.authorization is not None and self.transport_calls == 0,
                  'accepted_no_source_transport')
            value['authorization'] = self.authorization
            value['transport_calls'],value['frozen_calls'] = 0,self.frozen_calls
        return value

    def check(self, scope):
        _need(scope is self.scope and self.chain.writable and not self.chain.store.poisoned
              and self.chain.store.read(self.root_ref[0]) == {
                  'digest':self.root_ref[1],'payload':self.chain.root}, 'live_original_chain')
        _need(read_entry(scope.hass,self.entry_id) == self.source_snapshot
              and _row(scope.registry.async_get(self.from_name)) == self.source_row,
              'unchanged_actual_source_and_config')
        rows = [r for r in scope.registry.entities.values() if r.id == self.change.identity]
        _need(len(rows) == 1 and rows[0].entity_id in (self.helper_from,self.helper_to), 'one_own_uuid')
        actual = _row(rows[0])
        prepared = self.change.before if rows[0].entity_id == self.change.before['entity_id'] else self.change.after
        _need({k:v for k,v in actual.items() if k != 'modified_at'}
              == {k:v for k,v in prepared.items() if k != 'modified_at'}, 'own_prepared_row')
        accepted = [r for r in self.chain.accepted_documents()['core.entity_registry']['data']['entities']
                    if r['id'] == self.change.identity]
        if accepted != [actual]:
            # A clock may keep computing during our single lawful native own-ID
            # effect. This permits only its prepared row under the SAME live
            # persisted intent; it does not confer accepted physical authority.
            intent = self.chain.active
            _need(type(intent) is RegistryIntent, 'own_pending_intent_required')
            row = self.chain.store.read(intent.reference[0])
            _need(row is not None and row['digest'] == intent.reference[1]
                  and canonical(row['payload'])[1] == intent.reference[1]
                  and row['payload']['kind'] == 'registry_intent'
                  and row['payload']['chain'] == self.chain.root_id
                  and row['payload']['binding']['index'] == self.index
                  and row['payload']['binding']['identity'] == self.change.identity
                  and row['payload']['binding']['direction'] == self.direction
                  and self.chain._reconstruct()[-1] == intent.reference, 'own_pending_binding')
        if hasattr(self,'refs'):
            unchanged(self.refs,self.methods,self.codes)
        if self.authorization is not None:
            _need(rows[0].entity_id == self.helper_to and accepted == [actual], 'accepted_own_row_lost')

    def emission(self, scope, frame):
        data = frame.f_locals.get('event_data')
        if (frame.f_locals.get('event_type') == 'state_changed' and type(data) is dict
                and data.get('entity_id') in self.names):
            _need(data.get('old_state') is not None and data.get('new_state') is not None,
                  'own_mode_source_transport_refused')
        return super().emission(scope,frame)

    def require_accepted(self, scope, *, seam=False):
        self._require_accepted_head(scope,seam=seam)
        _need(self.transport_calls == 0, 'no_source_transport')
        self.transfer_row(scope)

    def transfer_row(self, scope):
        self.check(scope)
        row = scope.registry.async_get(self.helper_to)
        accepted = [r for r in self.chain.accepted_documents()['core.entity_registry']['data']['entities']
                    if r['id'] == self.change.identity]
        _need(row is not None and row.id == self.change.identity and accepted == [_row(row)]
              and self.authorization is not None, 'accepted_transfer_target')
        return row


def own_baseline(chain, life, key, descriptor):
    from .native_integration_clock_lifecycle import snapshot
    from .native_integration_setup_audit import exact_restore_baseline
    reverse = life.source_authorization is not None
    _,change,config,source,binding = original_binding(chain,key,reverse)
    _need(life.own_registry_changes.get(key) == change
          and life.helper_snapshots[key] == (config,config)
          and descriptor['binding'] == binding, 'original_lifecycle')
    ref = descriptor['intent']
    row = chain.store.read(ref[0])
    _need(row is not None and row['digest'] == ref[1]
          and canonical(row['payload'])[1] == ref[1], 'clock_intent_reference')
    doc = row['payload']
    _need(set(doc) == {'kind','schema','entry_id','helper_uuid','source_uuid',
                      'core','config','actual','source_binding'}
          and doc['kind'] == 'integration_clock_intent' and doc['schema'] == 1
          and doc['entry_id'] == key and doc['helper_uuid'] == change.identity
          and doc['source_uuid'] == source['id'] and doc['config'] == config
          and doc['core'] == chain.version and doc['source_binding'] == binding,
          'original_clock_intent')
    snapshot(doc['actual'])
    setup = life.store.read(life._slot('setup',key,'intent'))
    _need(setup is not None, 'setup_intent')
    return {'kind':'integration_own_clock_restore_v1','intent':list(ref),
        'source_binding':binding,'helper_uuid':change.identity,
        'authorization':setup['payload']['authorization'],
        'escrow':exact_restore_baseline(chain,life,key)}


def validate_composite(baseline, rows, doc, *, chain, core, entry_id):
    from .native_integration_clock_lifecycle import validate_audit
    from .native_integration_setup_audit import validate_exact_audit
    _need(chain is not None and type(doc) is dict
          and set(doc) == {'kind','schema','core','entry_id','baseline','source_rows',
                          'result_rows','parser','clock'}
          and doc['kind'] == 'integration_own_clock_setup' and doc['schema'] == 1
          and doc['core'] == core and doc['entry_id'] == entry_id
          and doc['baseline'] == baseline and doc['source_rows'] == doc['result_rows'] == rows,
          'composite_layout')
    validate_exact_audit(baseline['escrow'],rows,doc['parser'],core=core,
                         entry_id=entry_id,clock_parser=True)
    return validate_audit(baseline,rows,doc['clock'],chain=chain,core=core,entry_id=entry_id)


class NativeIntegrationOwnClockSetupAudit:
    def __init__(self,hass,life,chain,key,rows,*,guard,setup_intent):
        from .native_integration_clock_lifecycle import NativeIntegrationClockSetupAudit
        from .native_integration_setup_audit import NativeIntegrationSetupAudit
        self.clock = NativeIntegrationClockSetupAudit(hass,life,chain,key,rows,
            guard=guard,setup_intent=setup_intent)
        self.parser = NativeIntegrationSetupAudit(hass,life,chain,key,rows,
            guard=guard,clock_scope=self.clock.scope)
        self.rows,self.chain,self.key = canonical(rows)[0],chain,key

    def __enter__(self):
        self.clock.__enter__()
        try:
            self.parser.__enter__()
        except BaseException:
            self.clock.__exit__(None,None,None)
            raise
        return self

    def finish(self):
        from .native_integration_setup_audit import IntegrationSetupAuditData, encode_setup_audit
        parser = encode_setup_audit(self.parser.finish())
        clock = encode_setup_audit(self.clock.finish())
        doc = {'kind':'integration_own_clock_setup','schema':1,'core':self.clock.scope.version,
            'entry_id':self.key,'baseline':self.clock.baseline,
            'source_rows':self.rows,'result_rows':self.rows,'parser':parser,'clock':clock}
        validate_composite(self.clock.baseline,self.rows,doc,chain=self.chain,
                           core=self.clock.scope.version,entry_id=self.key)
        return IntegrationSetupAuditData(_canonical(doc))

    def __exit__(self,*args):
        try:
            return self.parser.__exit__(*args)
        finally:
            self.clock.__exit__(*args)
