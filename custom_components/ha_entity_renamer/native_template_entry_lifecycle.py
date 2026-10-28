"""Narrow numeric Template ConfigEntry observers and exact no-op setup audit.

The existing original receipt, immutable preparations, lifecycle and receipt
chain remain the only durable graph. No native writes, new baseline or replay
are performed by an observer. A failed setup audit leaves UNKNOWN effects.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys

from .helper_native import read_entry
from .helper_transaction import PreparedHelper
from .native_config_transition import prepared_helper_digest, _projection
from .native_template_consumers import _Native, _live_sensor, _registration, _source_semantics
from .native_utility_unload_audit import _row
from .native_unload_code_proof import prove_methods, unchanged
from .prepared import canonical, decode_change
from .yaml_runtime_observer import _pending_native_work
from .yaml_validation_worker import template_entry_semantic, template_entry_seal


def _need(value, reason):
    if not value:
        raise ValueError('template_entry_' + reason) from None


def validate_preparations(store, plan, receipt, digests):
    """Pure ORIGINAL/typed preparation binding, also usable by readonly reopen."""
    requests = []
    changes = {}
    for step in plan.steps:
        if step.adapter != 'helpers':
            continue
        payload = store.resolve(step.prepared_ref, digests[step.prepared_ref])
        change = decode_change(payload)
        if type(change) is not PreparedHelper or change.before['domain'] != 'template':
            continue
        change.plan()
        _need(not step.caused_by and len(change.mapping) == 1
              and [c.path for c in change.plan().changes] == [('options', 'state')], 'prepared_subset')
        requests.append((step.prepared_ref, digests[step.prepared_ref], payload))
        changes[change.before['identity']['entry_id']] = change
    if requests:
        expected = template_entry_seal(requests, receipt.version)
        _need([row for row in receipt.runtime_expected if row[0] == expected[0]] == [expected],
              'validation_seal_required')
        original = store.resolve_native_inputs(plan.native_inputs, receipt)
        docs = {f.key: json.loads(f.manager_json) for f in original.files}
        for entry_id, change in changes.items():
            rows = [row for row in docs['core.config_entries']['entries'] if row['entry_id'] == entry_id]
            _need(len(rows) == 1 and _projection(rows[0]) == change.before, 'original_entry')
            original_rows = [row for row in docs['core.entity_registry']['entities']
                             if row.get('config_entry_id') == entry_id]
            _need(len(original_rows) == 1 and original_rows[0]['platform'] == 'template'
                  and original_rows[0]['unique_id'] == entry_id
                  and original_rows[0]['config_subentry_id'] is None
                  and original_rows[0]['disabled_by'] is None
                  and original_rows[0]['device_id'] is None, 'original_registry_scope')
            no_devices(docs['core.device_registry'], entry_id)
            registry_changes = [decode_change(store.resolve(s.prepared_ref, digests[s.prepared_ref]))
                                for s in plan.steps if s.adapter == 'registry']
            old, new = next(iter(change.mapping.items()))
            source = [r for r in docs['core.entity_registry']['entities'] if r['entity_id'] == old]
            _need(len(source) == 1 and source[0]['id'] != original_rows[0]['id']
                  and len([c for c in registry_changes if c.kind == 'entity'
                           and c.identity == source[0]['id'] and c.before == source[0]
                           and c.after['entity_id'] == new]) == 1, 'original_source_uuid')
    return changes


def no_devices(document, entry_id):
    for name in ('devices', 'child_devices', 'deleted_devices'):
        for row in document.get(name, []):
            _need(entry_id not in row.get('config_entries', ())
                  and entry_id not in row.get('config_entries_subentries', {})
                  and row.get('primary_config_entry') != entry_id, 'device_housekeeping_unsupported')


def activation_baseline(chain, lifecycle, entry_id):
    """Derive from immutable ORIGINAL, not from the post-setup current manager."""
    changes = validate_preparations(chain.preparations, chain.plan, chain.original_receipt,
                                    lifecycle.binding['preparations'])
    _need(entry_id in changes and entry_id not in lifecycle.own_registry_changes, 'activation_subset')
    change = changes[entry_id]
    rows = [deepcopy(row) for row in lifecycle.original_entities
            if row.get('config_entry_id') == entry_id]
    old, new = next(iter(change.mapping.items()))
    source = [row for row in lifecycle.original_entities if row['entity_id'] == old]
    _need(len(rows) == len(source) == 1, 'original_scope')
    return {'kind': 'template_numeric_entry_baseline', 'schema': 1, 'core': chain.version,
            'entry_id': entry_id, 'prepared': prepared_helper_digest(change),
            'before': change.before, 'after': change.after, 'rows': rows,
            'source_uuid': source[0]['id'], 'source_before': old, 'source_after': new,
            'seal': list(next(r for r in chain.original_receipt.runtime_expected
                              if r[0] == 'template.config_entries'))}


class NativeTemplateEntryObserver:
    """Installed callbacks for the one narrow immutable numeric entry set."""
    def __init__(self, hass, chain, evidence, *, guard):
        self.hass, self.chain, self.evidence, self.guard = hass, chain, evidence, guard
        self.lease = None
        digests = {b['ref']: b['digest'] for b in chain.root['bindings']}
        self.changes = validate_preparations(chain.preparations, chain.plan,
                                             chain.original_receipt, digests)
        _need(len(self.changes) == 1, 'one_numeric_entry_required')
        self.entry_id, self.change = next(iter(self.changes.items()))
        self.documents = {f.key: json.loads(f.manager_json) for f in chain.baseline.files}
        self.rows = [r for r in self.documents['core.entity_registry']['entities']
                     if r.get('config_entry_id') == self.entry_id]
        self.old_entities = []
        self.native = _Native(hass)
        self.entries = tuple(row['entry_id'] for row in self.documents['core.config_entries']['entries'])

    def check(self):
        _need(self.guard() in (None, True), 'guard')
        if self.lease is not None:
            self.lease.check()
        self.native.bindings()
        from homeassistant.helpers import issue_registry as ir
        manager = self.hass.data.get(ir.DATA_REGISTRY)
        _need(type(manager) is ir.IssueRegistry
              and ('template', 'composite_device_id_' + self.entry_id) not in manager.issues,
              'repair_housekeeping_unsupported')
        from homeassistant.helpers import device_registry as dr
        devices = self.hass.data.get(dr.DATA_REGISTRY)
        _need(type(devices) is dr.DeviceRegistry, 'device_manager')
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        no_devices(json.loads(_orjson_bytes_default_encoder(devices._data_to_save())), self.entry_id)
        _need(tuple(e.entry_id for e in self.hass.config_entries.async_entries()) == self.entries,
              'entry_inventory_changed')
        from homeassistant.helpers import entity_registry as er
        registry = self.hass.data.get(er.DATA_REGISTRY)
        _need(type(registry) is er.EntityRegistry and [_row(r) for r in
              registry.entities.values() if r.config_entry_id == self.entry_id] == self.rows,
              'consumer_registry_delta')

    async def settle(self):
        async with asyncio.timeout(15):
            while _pending_native_work(self.hass, ('template',)):
                self.check()
                await asyncio.sleep(0)
        self.check()
        return True

    def _entity(self):
        from homeassistant.helpers.entity_platform import async_get_platforms
        return [e for p in async_get_platforms(self.hass,'template')
                if p.config_entry is self.hass.config_entries.async_get_entry(self.entry_id)
                for e in p.entities.values()]

    def detached(self):
        from homeassistant.helpers import event
        data = self.hass.data.get(event._KEYED_TRACK_STATE_CHANGE.key)
        for entity, info, filtered in self.old_entities:
            _need(entity not in self._entity() and not filtered._listeners
                  and not info._time_listeners, 'old_template_graph_active')
            callbacks = () if data is None else (job for jobs in data.callbacks.values() for job in jobs)
            _need(not any(getattr(job.target, '__self__', None) is info for job in callbacks),
                  'old_keyed_registration_active')

    async def validate_snapshot(self, snapshot):
        self.check()
        _need(snapshot in (self.change.before,self.change.after), 'unsealed_snapshot')
        template_entry_semantic(snapshot)
        return True

    async def verify_inactive(self, entry_id):
        from .native_helper_transaction import _require_inactive
        self.check()
        _need(entry_id == self.entry_id, 'inactive_scope')
        entry = self.hass.config_entries.async_get_entry(entry_id)
        _require_inactive(entry, self.lease)
        _need(not self._entity(), 'inactive_platform_membership')
        self.detached()
        return True

    async def verify_activation(self, snapshot):
        await self.validate_snapshot(snapshot)
        _need(read_entry(self.hass,self.entry_id) == snapshot, 'config_revision')
        return await self.verify_inactive(self.entry_id)

    async def verify_inventory_snapshot(self, snapshot):
        await self.verify_activation(snapshot)
        for row in self.documents['core.config_entries']['entries']:
            if row['entry_id'] != self.entry_id:
                _need(read_entry(self.hass,row['entry_id']) == _projection(row), 'foreign_entry_changed')
        return True

    async def verify_inventory(self, plan):
        _need(plan == self.chain.plan, 'inventory_plan')
        return await self.verify_inventory_snapshot(self.change.after)

    async def verify_original_inventory(self, plan):
        _need(plan == self.chain.plan, 'inventory_plan')
        return await self.verify_inventory_snapshot(self.change.before)

    def loaded_sample(self, snapshot):
        self.check()
        self.detached()
        _need(read_entry(self.hass,self.entry_id) == snapshot, 'loaded_config')
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        _need(entry.state.value == 'loaded' and entry.disabled_by is None, 'loaded_entry')
        entities = self._entity()
        _need(len(entities) == 1, 'loaded_entity_count')
        entity = entities[0]
        semantic, template, _ = _live_sensor(self.native,entity)
        dependencies, token = _registration(self.native,entity,template)
        expected = next(iter(self.change.mapping if snapshot == self.change.before
                             else {v:k for k,v in self.change.mapping.items()}))
        _need(dependencies == (expected,) and semantic == template_entry_semantic(snapshot)
              and entity.unique_id == self.entry_id and entity.entity_id == self.rows[0]['entity_id'],
              'loaded_semantic_or_dependency')
        source = self.hass.states.get(expected)
        result = self.hass.states.get(entity.entity_id)
        import math
        _need(source is not None and result is not None and math.isfinite(float(source.state))
              and math.isfinite(float(result.state)), 'numeric_state_required')
        from homeassistant.helpers import entity_registry as er
        registry = self.hass.data[er.DATA_REGISTRY]
        old = next(iter(self.change.mapping))
        original = next(r for r in self.documents['core.entity_registry']['entities'] if r['entity_id']==old)
        _need(registry.async_get(expected) is not None
              and registry.async_get(expected).id == original['id'], 'loaded_source_uuid')
        return entity, semantic, dependencies, token

    async def verify_loaded(self, snapshot):
        from .native_template_accepted_consumers import async_observe_accepted_template_consumers
        self.check()
        await self.hass.async_add_executor_job(self.native.sources)
        self.check()
        proof = await async_observe_accepted_template_consumers(
            self.hass,self.chain,self.evidence,guard=self.check)
        rows = [r for r in proof.rows if r.kind == 'entity' and r.entry_id == self.entry_id]
        _need(len(rows)==1 and rows[0].consumer_verified and rows[0].source_verified, 'accepted_consumer')
        self.loaded_sample(snapshot)
        return True

    async def capture_loaded(self):
        snapshot = read_entry(self.hass,self.entry_id)
        await self.verify_loaded(snapshot)
        entity, _, _, _ = self.loaded_sample(snapshot)
        info = entity._template_result_info
        # This witness is transient. It is never persisted as a new baseline.
        self.old_entities.append((entity,info,info._track_state_changes))


@dataclass(frozen=True)
class TemplateSetupAuditData:
    payload_json: bytes


def encode_setup_audit(audit):
    _need(type(audit) is TemplateSetupAuditData, 'typed_setup_audit')
    doc = json.loads(audit.payload_json)
    _need(canonical(doc)[0] == doc and canonical(doc)[1] == hashlib.sha256(audit.payload_json).hexdigest(),
          'audit_codec')
    return doc


def decode_setup_audit(doc):
    frozen,_ = canonical(doc)
    return TemplateSetupAuditData(json.dumps(frozen,sort_keys=True,separators=(',',':'),allow_nan=False).encode())


def validate_setup_audit(baseline, source_rows, audit, *, core, entry_id):
    doc = encode_setup_audit(audit)
    _need(set(doc) == {'kind','schema','core','entry_id','baseline','source_rows','result_rows',
                       'semantic','dependency','source_pins','calls'}
          and doc['kind']=='template_numeric_entry_setup' and type(doc['schema']) is int
          and doc['schema']==1 and doc['core']==core and doc['entry_id']==entry_id
          and doc['baseline']==baseline and source_rows==baseline['rows']
          and doc['source_rows']==doc['result_rows']==source_rows
          and doc['semantic'] in (template_entry_semantic(baseline['before']),
                                  template_entry_semantic(baseline['after']))
          and doc['dependency'] in ([baseline['source_before']],[baseline['source_after']])
          and doc['source_pins']==_SETUP_PINS[core]
          and doc['calls']=={'constructor':1,'native_setup_true':True}, 'setup_audit_binding')
    _need(type(doc['calls']['constructor']) is int and type(doc['calls']['native_setup_true']) is bool,
          'setup_call_types')
    # Dependency and native semantic direction must agree, not two free choices.
    before = doc['dependency'] == [baseline['source_before']]
    _need(doc['semantic']==template_entry_semantic(baseline['before' if before else 'after']),
          'setup_direction')
    return source_rows


# Exact public source pins filled from the already qualified owned Core inputs.
_SETUP_PINS = {'2025.6.0': {'components/template/__init__.py': 'ecbdfe7f1f184c80aa6957f587ec620eebc20b4dea4b5b4273ecc0086df4c266',
              'components/template/config_flow.py': '63c664ef422d3dfc0813d6c3aa57d26b0458b5163c796b8454857e50e48cb1c8',
              'components/template/helpers.py': '6e4a4f4974f2b22aca91872827e9510e8b09071e0f3ff7824c4acfa7d64827a1',
              'components/template/sensor.py': '6b71e5a605291cf925a435ebcfa70827a3dc9c624839b40156f0ee71a740e0ab',
              'components/template/template_entity.py': '7863a9a3e480fbe84f76326289d6d97a5eef4caf1b92c5caf21ada23134034f6',
              'config_entries.py': '306340f94f1387bd6b73162cbc2c37ae1c095025d34b318fcf10b8b6c40dcffb',
              'helpers/device.py': 'd74325a0ae07ba29f851d7f8e388e9b3506430b8bf0339d5f161b6c75ff3e841',
              'helpers/entity_platform.py': 'e1c976af7fb673fe3b5cf1c62be032c0d0e1740253bff65a528a9503ffca0ed6',
              'helpers/entity_registry.py': 'f37dbf51693b7295f27a2d8356142be5aedc6bca8b9d4427405e3d49e25e4d76',
              'helpers/helper_integration.py': '16549a97b848ce3f0e48a87a14dc2c01bb0d9a16289c182aadcc4c13e6186cd1',
              'helpers/issue_registry.py': '467ee810975e7825a3d6033a659bdea33754c54666bfabf834c4c88c593ee579'},
 '2026.10.2': {'components/template/__init__.py': '33e9338f4bbc17c4e3be3f2383027ed63e6669a0679deec7a2e0b2f016a649ad',
               'components/template/config_flow.py': '5d4c31026dc05738423da96199836d5134f1b57d274561cff51d6e0bc4d29020',
               'components/template/helpers.py': 'a214dd203972f939e1460e82521758819e9482ecf0d66d84ce9792ad7f8c0bff',
               'components/template/sensor.py': '171f6df16a272d352d0afe62cc35b9590f160c3e37180ab36dfb9afcdd49fb25',
               'components/template/template_entity.py': '904c82d89b83b0cc097dbb482b50407a7d3dfa9109af1f468ca48420cf496d15',
               'config_entries.py': '221e2672e9a8f0ec12fa62edbfdc2ef6d986331855797615c81ecbe99f8840db',
               'helpers/device.py': '47cc4afc5d13def0f63fb854a74ae388cb0a877b25c6b717001abae6ca19d4a5',
               'helpers/entity_platform.py': '776e21f2ef16715b17e34fe27da33a3d30042b27988d75aaf5461ddcf736b09e',
               'helpers/entity_registry.py': 'f77b7ae946a0902136faadb3cfe45d17aca24fa639c43189295e101f5e47f425',
               'helpers/helper_integration.py': 'eedfca79e0ac213fcf45cd4bcab6fa82ddd0a511e3723485388ab55657fb8972',
               'helpers/issue_registry.py': '7bda388e1aba697df84f0456c1fac165b776d2d03c31cef0c464c3f1fc5c6106'}}


class NativeTemplateSetupAudit:
    def __init__(self,hass,lifecycle,chain,entry_id,source_rows,*,guard):
        self.hass,self.life,self.chain,self.entry_id,self.guard = hass,lifecycle,chain,entry_id,guard
        self.baseline=activation_baseline(chain,lifecycle,entry_id)
        self.snapshot=self.baseline['after' if lifecycle.source_authorization is None else 'before']
        self.source_rows=deepcopy(source_rows)
        self.entry=hass.config_entries.async_get_entry(entry_id)
        self.native=_Native(hass)
        self.error=None
        self.created=None
        self.constructed=0
        self.setup_true=False
        self.task=asyncio.current_task()
        self.callback=self._profile

    def _check(self):
        _need(self.error is None and asyncio.current_task() is self.task, 'setup_scope')
        _need(self.guard() in (None,True), 'setup_guard')
        self.life.activation_lease.check()
        _need(self.life.activation_lease.owns_entry(self.entry), 'setup_lease')
        self.native.bindings()
        unchanged(self.refs,self.methods,self.codes)
        for name,digest in _SETUP_PINS[self.chain.version].items():
            _need(hashlib.sha256((self.native.root/name).read_bytes()).hexdigest()==digest,
                  'setup_source_pin')
        from homeassistant.helpers import entity_registry as er
        rows=[_row(r) for r in self.hass.data[er.DATA_REGISTRY].entities.values()
              if r.config_entry_id==self.entry_id]
        _need(rows==self.source_rows==self.baseline['rows'], 'setup_registry_delta')
        _need(read_entry(self.hass,self.entry_id)==self.snapshot, 'setup_config_changed')

    def __enter__(self):
        from homeassistant.components.template import sensor, helpers
        from homeassistant.config_entries import ConfigEntries
        refs=((ConfigEntries,'async_setup'),(self.native.sensor,'__init__'),
              (sensor,'async_setup_entry'),(helpers,'async_setup_template_entry')) if self.chain.version=='2026.10.2' else (
              (ConfigEntries,'async_setup'),(self.native.sensor,'__init__'),(sensor,'async_setup_entry'))
        # The synchronous public update path is observed only to poison a live
        # audit after a real changed call, never to rewrite or serialize Core.
        self.refs=refs+((ConfigEntries,'_async_update_entry'),)
        self.methods,self.codes=prove_methods(self.native.root,_SETUP_PINS[self.chain.version],self.refs)
        _need(sys.getprofile() is None,'existing_profiler')
        self._check()
        sys.setprofile(self.callback)
        return self

    def _profile(self,frame,event,arg):
        if self.error is not None or event!='return':
            return
        try:
            if frame.f_code is self.codes[1]:
                entity=frame.f_locals.get('self')
                if getattr(entity,'unique_id',None)==self.entry_id:
                    parent=frame.f_back
                    expected=self.codes[3] if self.chain.version=='2026.10.2' else self.codes[2]
                    _need(parent is not None and parent.f_code is expected
                          and type(entity) is self.native.sensor and entity.hass is self.hass,
                          'native_constructor_cause')
                    self.created=entity
                    self.constructed+=1
                    _need(self.constructed==1,'one_constructor')
            if (frame.f_code is self.codes[0] and frame.f_locals.get('entry_id')==self.entry_id
                    and arg is True):
                _need(frame.f_locals.get('self') is self.hass.config_entries
                      and frame.f_locals.get('_lock') is False and not self.setup_true,
                      'actual_setup_call')
                self.setup_true=True
            if (frame.f_code is self.codes[-1] and frame.f_locals.get('entry') is self.entry
                    and arg is True):
                _need(False,'foreign_update_during_setup')
        except BaseException:
            self.error='actual_setup_observation_refused'

    def finish(self):
        self._check()
        _need(self.constructed==1 and self.setup_true and self.entry.state.value=='loaded',
              'actual_setup_required')
        from homeassistant.helpers.entity_platform import async_get_platforms
        entities=[e for p in async_get_platforms(self.hass,'template') if p.config_entry is self.entry
                  for e in p.entities.values()]
        _need(entities==[self.created], 'actual_new_entity')
        semantic,template,_=_live_sensor(self.native,self.created)
        dependencies,_=_registration(self.native,self.created,template)
        _need(semantic==template_entry_semantic(self.snapshot),'setup_semantics')
        doc={'kind':'template_numeric_entry_setup','schema':1,'core':self.chain.version,
             'entry_id':self.entry_id,'baseline':self.baseline,'source_rows':self.source_rows,
             'result_rows':self.source_rows,'semantic':semantic,'dependency':list(dependencies),
             'source_pins':_SETUP_PINS[self.chain.version],
             'calls':{'constructor':self.constructed,'native_setup_true':self.setup_true}}
        audit=decode_setup_audit(doc)
        validate_setup_audit(self.baseline,self.source_rows,audit,core=self.chain.version,entry_id=self.entry_id)
        return audit

    def __exit__(self,exc_type,exc,traceback):
        if sys.getprofile() is self.callback:
            sys.setprofile(None)
        else:
            self.error='profiler_changed'
            _need(False,'profiler_changed')
