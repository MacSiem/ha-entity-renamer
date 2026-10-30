"""Pinned current-only ownership of actual vendor registry-rename listeners.

No G3 activation, source-ID transition, counter migration or full-family claim.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from functools import partial

from .powercalc_source_binding import _json, _sha, PowercalcSourceBindings
from .native_unload_code_proof import unchanged


def _need(value, reason):
    if not value:
        raise ValueError('powercalc_reload_global_' + reason) from None


def _refs():
    from homeassistant.core import EventBus, HomeAssistant
    from homeassistant.config_entries import ConfigEntry
    return ((EventBus,'_async_remove_listener'),(EventBus,'_async_dispatch'),
            (EventBus,'async_fire_internal'),(HomeAssistant,'async_run_hass_job'),
            (ConfigEntry,'async_on_unload'),(ConfigEntry,'_async_process_on_unload'))


def _present(bus,event_type,pair):
    return sum(item is pair for item in bus._listeners.get(event_type,()))


@dataclass(frozen=True,eq=False)
class _NativeUnsubscribe:
    """Native ConfigEntry cleanup owns the ACTUAL captured unsubscribe.

    Membership makes this idempotent when a later managed reload has already
    detached this exact pair. No retained reload scope/entities/monitor/tool.
    """
    bus: object
    event_type: str
    pair: tuple
    unsubscribe: object
    method: object
    code: object

    def __call__(self):
        current=self.bus._async_remove_listener
        _need(current==self.method and current.__func__.__code__ is self.code,
              'unload_native_binding_changed')
        count=_present(self.bus,self.event_type,self.pair)
        _need(count in (0,1),'unload_duplicate_membership')
        if count:
            self.unsubscribe()  # Actual EventBus return, never callback/filter.
            _need(_present(self.bus,self.event_type,self.pair)==0,'unload_remove_failed')


@dataclass(frozen=True)
class _OriginalPhysicalSource:
    entity_id: str
    registry_id: str
    unique_id: str
    platform: str
    config_entry_id: str | None
    config_subentry_id: str | None
    device_id: str | None

    @classmethod
    def from_row(cls,row):
        return cls(row.entity_id,row.id,row.unique_id,row.platform,
                   row.config_entry_id,row.config_subentry_id,row.device_id)


@dataclass(frozen=True)
class PowercalcGlobalRenameReceipt:
    original_evidence_sha256: str
    original_runtime_token: bytes
    original_pairs_json: bytes
    original_source_identities_json: bytes
    native_operations_json: bytes
    actual_new_pairs_json: bytes
    native_unload_owners_json: bytes
    global_callback_ownership_verified: bool = field(default=True,init=False)
    continuity_verified: bool = field(default=False,init=False)
    migration_ready: bool = field(default=False,init=False)
    family_complete: bool = field(default=False,init=False)
    status: str = field(default='UNKNOWN',init=False)


@dataclass(frozen=True)
class PowercalcOwnedReloadWitness:
    lifecycle: object
    global_rename: PowercalcGlobalRenameReceipt
    reload_ownership_verified: bool = field(default=False,init=False)
    continuity_verified: bool = field(default=False,init=False)
    migration_ready: bool = field(default=False,init=False)
    family_complete: bool = field(default=False,init=False)
    status: str = field(default='UNKNOWN',init=False)


class _GlobalRenameOwner:
    def __init__(self,scope,before,bound):
        from homeassistant.helpers.entity_registry import EVENT_ENTITY_REGISTRY_UPDATED
        self.scope=scope
        self.bus=scope.hass.bus
        self.event_type=EVENT_ENTITY_REGISTRY_UPDATED
        self.before=before
        self.pairs={}
        self.new={}
        self.restored={}
        self.operations=[]
        self.expected=None
        self.inflight={}
        self.detached=set()
        self.committed=False
        self.metadata={entry.entry_id:_json([dict(entry.data),dict(entry.options),entry.unique_id])
                       for entry in scope.entries}
        for key,refs in scope.rename_original_refs.items():
            found=[pair for pair in self.bus._listeners.get(self.event_type,())
                   if pair[0] is refs[0] and pair[1] is refs[2]]
            _need(len(found)==1,'original_actual_pair')
            self.pairs[key]=found[0]
        # The successful SAME-original binder has already checked every native
        # source row. Freeze only those physical identities, bound to its UUIDs;
        # this is not a replacement discovery/config/runtime baseline.
        _need(type(bound) is PowercalcSourceBindings
              and bound.evidence_sha256==scope.evidence.sha256
              and bound.runtime_sha256==_sha(scope.original.payload_json)
              and all(row.source_verified for row in bound.rows),'original_bound_sources_required')
        expected={}
        for row in bound.rows:
            for entity_id,registry_id in ((row.entity_id,row.registry_id),
                                          (row.source,row.source_registry_id)):
                if entity_id is not None and registry_id is not None:
                    _need(entity_id not in expected or expected[entity_id]==registry_id,
                          'ambiguous_original_source_uuid')
                    expected[entity_id]=registry_id
        from homeassistant.helpers import entity_registry as er
        registry=scope.hass.data[er.DATA_REGISTRY]
        self.source_registry=registry
        self.source_entry_manager=scope.hass.config_entries
        self.sources={}
        self.source_classes={}
        self.source_entries={}
        active=self.active_sources()
        for _,entity_id in scope.rename_sources:
            row=registry.async_get(entity_id)
            _need(row is not None and row.id==expected.get(entity_id)
                  and entity_id in active,'original_source_uuid_not_bound')
            self.sources[entity_id]=_OriginalPhysicalSource.from_row(row)
            self.source_classes[entity_id]=type(active[entity_id])
            self.source_entries[entity_id]=(None if row.config_entry_id is None else
                scope.hass.config_entries.async_get_entry(row.config_entry_id))
        self.require_sources_current('preflight_source_identity_changed')

    def active_sources(self):
        from homeassistant.helpers import entity_platform as ep
        active={}
        relevant={entity_id for _,entity_id in self.scope.rename_sources}
        for bucket in self.scope.hass.data[ep.DATA_ENTITY_PLATFORM].values():
            for platform in bucket:
                for entity_id,entity in platform.entities.items():
                    if entity_id in relevant:
                        _need(entity.platform is platform and platform.hass is self.scope.hass,
                              'actual_source_platform_changed')
                        _need(entity_id not in active,'duplicate_active_source')
                        active[entity_id]=entity
        return active

    def require_sources_current(self,reason):
        from homeassistant.helpers import entity_registry as er
        registry=self.scope.hass.data[er.DATA_REGISTRY]
        _need(registry is self.source_registry
              and self.scope.hass.config_entries is self.source_entry_manager,reason)
        active=self.active_sources()
        for entity_id,original in self.sources.items():
            row=registry.async_get(entity_id)
            entity=active.get(entity_id)
            _need(row is not None and _OriginalPhysicalSource.from_row(row)==original
                  and type(entity) is self.source_classes[entity_id]
                  and entity.hass is self.scope.hass and entity.entity_id==entity_id
                  and entity.unique_id==original.unique_id and entity.registry_entry is row
                  and entity.platform.entities.get(entity_id) is entity
                  and entity.platform.config_entry is self.source_entries[entity_id]
                  and (original.config_entry_id is None or
                       self.scope.hass.config_entries.async_get_entry(original.config_entry_id)
                           is self.source_entries[entity_id]),reason)

    def idle(self):
        _need(not self.bus._dispatching and not self.bus._event_queue,'dispatch_in_progress')

    def bindings(self):
        scope=self.scope
        unchanged(scope.ha_refs,scope.ha_methods,scope.ha_codes)
        unchanged(scope.vendor_refs,scope.vendor_methods,scope.vendor_codes)
        _need(asyncio.get_running_loop() is scope.loop,'loop_changed')

    def metadata_current(self):
        return all(self.scope.hass.config_entries.async_get_entry(entry.entry_id) is entry
                   and entry.state.value=='loaded'
                   and _json([dict(entry.data),dict(entry.options),entry.unique_id])
                       ==self.metadata[entry.entry_id] for entry in self.scope.entries)

    def observe(self,tag,frame,phase,result):
        scope=self.scope
        if tag=='global_effect':
            if frame.f_locals.get('hass') is scope.hass and any(
                    frame.f_locals.get('entry') is row for row in scope.entries):
                _need(False,'rename_effect_during_unchanged_reload')
            return
        if frame.f_locals.get('self') is not self.bus or frame.f_locals.get('event_type')!=self.event_type:
            return
        pair=frame.f_locals.get('filterable_job')
        if type(pair) is not tuple or len(pair)!=2:
            return
        job,event_filter=pair
        if (getattr(getattr(job,'target',None),'__code__',None) is not scope.rename_target
                and getattr(event_filter,'__code__',None) is not scope.rename_filter):
            return
        key=str(id(job))
        expected=self.expected
        if tag=='global_remove':
            _need(expected is not None and expected[0]=='remove' and expected[1] is pair,
                  'foreign_remove')
        elif expected is not None and expected[0]=='restore':
            _need(expected[1] is pair,'restore_actual_pair')
        else:
            _need(scope.started==1 and scope.causal.get(None) is scope.marker
                  and key not in self.before,'foreign_registration')
            listen_frame=frame.f_back
            register_frame=None if listen_frame is None else listen_frame.f_back
            _need(listen_frame is not None and listen_frame.f_code is scope.ha_codes[4]
                  and register_frame is not None
                  and register_frame.f_code is scope.vendor_codes[6]
                  and register_frame.f_locals.get('hass') is scope.hass
                  and any(register_frame.f_locals.get('entry') is entry for entry in scope.entries)
                  and (register_frame.f_locals['entry'].entry_id,
                       register_frame.f_locals.get('source_entity_id')) in scope.rename_sources
                  and job.target is register_frame.f_locals.get('_entity_rename_listener')
                  and event_filter is register_frame.f_locals.get('_filter_entity_id'),
                  'actual_vendor_registration_stack')
        task=asyncio.current_task()
        _need(type(task) is asyncio.Task,'actual_task')
        frame_key=(id(frame),tag)
        if phase=='start':
            _need(frame_key not in self.inflight,'duplicate_start')
            self.inflight[frame_key]=(pair,task)
            self.operations.append([tag,phase,key])
            return
        started=self.inflight.pop(frame_key,None)
        _need(started is not None and started[0] is pair and started[1] is task,'native_return')
        if tag=='global_add':
            _need(type(result) is partial and result.func==self.bus._async_remove_listener
                  and not result.keywords and len(result.args)==2
                  and result.args[0]==self.event_type and result.args[1] is pair,
                  'actual_native_unsubscribe')
            rows,refs=scope.rename_listeners()
            _need(key in rows and _present(self.bus,self.event_type,pair)==1,
                  'registered_actual_pair')
            row=rows[key]
            _need((row['entry_id'],row['source_entity_id']) in scope.rename_sources,
                  'registration_original_binding')
            record=(pair,result,refs[key][3])
            if expected is not None and expected[0]=='restore':
                self.restored[key]=record
            else:
                _need(key not in self.new,'duplicate_registration')
                self.new[key]=record
        else:
            _need(_present(self.bus,self.event_type,pair)==0,'native_remove_failed')
        self.operations.append([tag,phase,key])

    def remove(self,pair,unsubscribe=None):
        self.bindings()
        _need(_present(self.bus,self.event_type,pair)==1,'remove_actual_membership')
        self.expected=('remove',pair)
        try:
            if unsubscribe is None:
                # Earlier unsubscribe was discarded by vendor. Invoke its exact
                # native target with the retained ACTUAL pair; do not invent one.
                self.bus._async_remove_listener(self.event_type,pair)
            else:
                unsubscribe()
        finally:
            self.expected=None
        _need(_present(self.bus,self.event_type,pair)==0,'remove_postcondition')

    def detach(self):
        self.scope.check()
        self.idle()
        _need(self.metadata_current(),'original_metadata_changed')
        self.require_sources_current('detach_source_identity_changed')
        for key,pair in self.pairs.items():
            self.remove(pair)
            self.detached.add(key)
        self.scope.check()
        self.idle()

    def attach(self,entry,pair,unsubscribe):
        callbacks=entry._on_unload
        _need(callbacks is None or type(callbacks) is list,'native_unload_list_shape')
        existing=[] if callbacks is None else list(callbacks)
        owned=[item for item in existing if type(item) is _NativeUnsubscribe
               and item.bus is self.bus and item.pair is pair]
        _need(len(owned)<=1,'duplicate_unload_owner')
        if owned:
            return owned[0]
        native=self.bus._async_remove_listener
        owner=_NativeUnsubscribe(self.bus,self.event_type,pair,unsubscribe,native,native.__func__.__code__)
        entry.async_on_unload(owner)
        actual=entry._on_unload
        _need(type(actual) is list and len(actual)==len(existing)+1
              and all(a is b for a,b in zip(actual[:-1],existing,strict=True))
              and actual[-1] is owner,'native_unload_owner_not_attached')
        return owner

    def finish(self,after):
        scope=self.scope
        scope.check()
        self.bindings()
        self.idle()
        _need(self.metadata_current() and not self.inflight,'final_metadata_or_native_calls')
        self.require_sources_current('final_source_identity_changed')
        _need(self.detached==set(self.pairs) and not set(self.before)&set(after),
              'old_membership_left')
        _need(set(after)==set(self.new),'new_membership_unowned')
        actual=[(row['entry_id'],row['source_entity_id']) for row in after.values()]
        _need(sorted(actual)==sorted(scope.rename_sources),'exactly_one_new_per_original_source')
        owners={}
        for key,(pair,unsubscribe,entry) in self.new.items():
            _need(_present(self.bus,self.event_type,pair)==1,'new_membership_lost')
            owner=self.attach(entry,pair,unsubscribe)
            owners[key]={'entry_id':entry.entry_id,'owner_process_id':id(owner),
                         'unsubscribe_process_id':id(unsubscribe)}
        scope.check()
        receipt=PowercalcGlobalRenameReceipt(scope.evidence.sha256,scope.original.structural_token,
            _json(self.before),_json({entity_id:vars(identity)
                for entity_id,identity in self.sources.items()}),
            _json(self.operations),_json(after),_json(owners))
        self.committed=True
        return receipt

    def abort(self):
        """Sync rollback of this seam, not numeric/entity rollback.

        Never reactivate stale original callbacks after foreign metadata drift.
        Removal/restoration continue despite a poisoned caller guard, using only
        saved exact native bindings/pairs. No foreign current pair is removed.
        """
        if self.committed or not self.detached:
            return
        self.bindings()
        self.idle()
        for pair,unsubscribe,_ in self.new.values():
            if _present(self.bus,self.event_type,pair):
                self.remove(pair,unsubscribe)
        _need(self.metadata_current(),'rollback_metadata_changed_original_not_reactivated')
        self.require_sources_current('rollback_source_identity_changed_original_not_reactivated')
        current,_=self.scope.rename_listeners()
        _need(set(current)==set(self.pairs)-self.detached,
              'rollback_foreign_membership_original_not_reactivated')
        for key in self.detached:
            pair=self.pairs[key]
            _need(_present(self.bus,self.event_type,pair)==0,'rollback_original_already_present')
            self.expected=('restore',pair)
            try:
                unsubscribe=self.bus._async_listen_filterable_job(self.event_type,pair)
            finally:
                self.expected=None
            _need(key in self.restored and self.restored[key][1] is unsubscribe,
                  'rollback_native_restore_unobserved')
            self.attach(self.restored[key][2],pair,unsubscribe)
        rows,refs=self.scope.rename_listeners()
        _need(rows==self.before and all(all(a is b for a,b in zip(
              refs[key],self.scope.rename_original_refs[key],strict=True)) for key in rows),
              'rollback_actual_identity')


async def async_reload_with_powercalc_global_listener_ownership(hass,receipt,evidence,original,*,guard):
    """Own one unchanged-ORIGINAL native reload, plus the concrete global seam.

    Reuses actual source/config/profile proof; still NO G3/G5/G6 authority.
    """
    from .powercalc_reload_witness import async_witness_unchanged_powercalc_reload
    return await async_witness_unchanged_powercalc_reload(hass,receipt,evidence,original,
        guard=guard,_manage_global_rename=True)
