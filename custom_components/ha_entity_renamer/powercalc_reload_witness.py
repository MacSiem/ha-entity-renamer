"""Actual unchanged-ORIGINAL Powercalc reload witness; default capture only.

Current 2026.10.2 + official v1.26.1 only. No YAML activation integration,
replacement baseline, new root/journal, fake native input or Integration widening.
Optional private exact-fields policy owns only its finite vendor setup seam.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, fields, field
from decimal import Decimal
from datetime import datetime
from importlib import import_module
import json
import math
import sys

from .native_unload_code_proof import prove_methods, unchanged
from .native_utility_setup_audit import _SOURCES, _code_objects
from .powercalc_runtime_observer import capture_powercalc_runtime, _guard
from .powercalc_source_binding import (PowercalcVendor, async_bind_powercalc_sources,
                                      _pins, _json)
from .powercalc_profile_binding import _energy_listeners, pending


def _need(value, reason):
    if not value:
        raise ValueError('powercalc_reload_' + reason) from None


def _decimal(value):
    _need(type(value) is Decimal and value.is_finite(), 'actual_decimal_required')
    return str(value)


@dataclass(frozen=True)
class PowercalcReloadWitness:
    original_evidence_sha256: str
    original_runtime_token: bytes
    actual_before_json: bytes
    actual_departures_json: bytes
    actual_after_json: bytes
    native_calls_json: bytes
    instance_pairs: tuple[tuple[str, int, int], ...]
    actual_runtime_token: bytes
    global_rename_before_json: bytes
    global_rename_after_json: bytes
    reload_blockers: tuple[str, ...]
    entity_lifecycle_verified: bool = field(default=True,init=False)
    reload_ownership_verified: bool = field(default=False,init=False)
    counter_ownership_verified: bool = field(default=False,init=False)
    continuity_verified: bool = field(default=False,init=False)
    migration_ready: bool = field(default=False,init=False)
    family_complete: bool = field(default=False,init=False)
    status: str = field(default='UNKNOWN',init=False)


class _Reload:
    def __init__(self,hass,receipt,evidence,original,guard):
        self.hass,self.receipt,self.evidence,self.original,self.guard=hass,receipt,evidence,original,guard
        self.owner,self.loop=asyncio.current_task(),asyncio.get_running_loop()
        self.causal=ContextVar('powercalc_reload_call')  # Per-call provenance, never a global gate.
        self.marker=object()  # Descendant contexts retain no scope/native objects.
        self.error=None
        self.first_failure=None
        self.tool=None
        self.calls=[]
        self.departures={}
        self.cancelled={}
        self.last_active={}
        self.removed=set()
        self.added={}
        self.started=self.returned=0
        self.root_task=None
        self.live=False
        self.inflight={}
        self.listener_owner=None
        self.exact_owner=None
        self.producer_owner=None

    def check(self):
        _guard(self.guard)
        if self.error is not None:
            error=ValueError('powercalc_reload_live_owner_or_callback_refused')
            self.annotate(error)
            raise error from None
        _need(self.live and self.owner is not None and not self.owner.done(),
              'live_owner_or_callback_refused')
        _need(asyncio.get_running_loop() is self.loop and self.hass.loop is self.loop,'event_loop')
        self.vendor.bindings()
        unchanged(self.vendor_refs,self.vendor_methods,self.vendor_codes)
        unchanged(self.ha_refs,self.ha_methods,self.ha_codes)
        if self.exact_owner is not None:
            self.exact_owner.check()
        if self.producer_owner is not None:
            self.producer_owner.check()
        entries=tuple(self.hass.config_entries.async_entries('powercalc'))
        _need(len(entries)==len(self.entries)
              and all(a is b for a,b in zip(entries,self.entries,strict=True)),
              'entry_membership_changed')
        if self.tool is not None:
            monitor=sys.monitoring
            mask=monitor.events.PY_START|monitor.events.PY_RETURN
            _need(monitor.get_tool(self.tool)=='renamer.powercalc_reload'
                  and monitor.get_events(self.tool)==0
                  and all(monitor.get_local_events(self.tool,c)==mask for c in self.codes),'monitor_lost')

    def annotate(self,error):
        if self.first_failure is not None:
            error.add_note('powercalc_reload_first_failure='+repr(self.first_failure))

    def entities(self):
        from homeassistant.helpers.entity_platform import async_get_platforms
        platforms=tuple(async_get_platforms(self.hass,'powercalc'))
        result={}
        for platform in platforms:
            for entity in platform.entities.values():
                _need(entity.entity_id not in result,'duplicate_entity')
                result[entity.entity_id]=entity
        return platforms,result

    def snapshot(self,entity):
        from homeassistant.core import State
        cls=self.vendor.classes
        identity=entity.registry_entry
        _need(identity is not None and identity.entity_id==entity.entity_id
              and identity.platform=='powercalc','registry_owner')
        result={'entity_id':entity.entity_id,'uuid':identity.id,'unique_id':entity.unique_id,
                'process_id':id(entity),'class':type(entity).__name__}
        if type(entity) is cls['VirtualEnergySensor']:
            clock=entity._last_integration_time
            _need(type(clock) is datetime and clock.tzinfo is not None
                  and type(entity._last_integration_trigger) is self.vendor.integration._IntegrationTrigger,
                  'actual_clock_shape')
            cancel=entity._max_sub_interval_exceeded_callback
            handle=getattr(cancel,'__self__',None)
            _need(type(handle) is asyncio.TimerHandle and handle._loop is self.loop
                  and cancel==handle.cancel and math.isfinite(handle.when()),'actual_timer_shape')
            timer={'deadline':handle.when(),'cancelled':handle.cancelled()}
            if not handle.cancelled():
                _energy_listeners(self.hass,entity,entity._sensor_source_id,self.vendor)
                job=handle._args[1]
                action=job.target
                cells=dict(zip(action.__code__.co_freevars,
                    (cell.cell_contents for cell in action.__closure__),strict=True))
                endpoint=cells['source_state']
                timer.update(source_state_id=id(endpoint),source=endpoint.entity_id,
                             source_value=endpoint.state,source_reported=endpoint.last_reported.isoformat())
            result.update(full=_decimal(entity._state),last_valid=_decimal(entity._last_valid_state),
                time=clock.isoformat(),trigger=entity._last_integration_trigger.name,timer=timer)
        elif type(entity) is cls['GroupedEnergySensor']:
            store=entity._prev_state_store
            _need(type(store) is cls['PreviousStateStore']
                  and self.hass.data.get('powercalc_group_storage') is store,
                  'previous_store_owner')
            _need(type(store.states) is dict,'previous_states_shape')
            present=entity.entity_id in store.states
            states=store.states[entity.entity_id] if present else {}
            _need(type(states) is dict and set(states)<=entity._entities,'previous_membership')
            previous=[]
            for key,state in sorted(states.items()):
                _need(state is None or type(state) is State,'previous_native_state')
                previous.append([key,None if state is None else {
                    'process_id':id(state),'entity_id':state.entity_id,'state':state.state,
                    'changed':state.last_changed.isoformat(),'updated':state.last_updated.isoformat(),
                    'reported':state.last_reported.isoformat(),'attributes':dict(state.attributes)}])
            result.update(full=_decimal(entity._native_value_exact),previous=previous,
                          previous_present=present,
                          group_states_process_id=id(states) if present else None,
                          store_process_id=id(store),states_process_id=id(store.states),
                          start_at_zero=entity._sensor_config.get('group_energy_start_at_zero',True))
        return result

    def rename_listeners(self):
        """Read actual EventBus membership; never invoke callback/filter.

        These listeners are NOT sensor callbacks. Official sensor setup leaves
        their unsubscribe unowned; retained identities must remain visible.
        """
        from homeassistant.core import EventBus, HassJob, HassJobType
        from homeassistant.helpers.entity_registry import EVENT_ENTITY_REGISTRY_UPDATED
        _need(type(self.hass.bus) is EventBus and self.hass.bus._hass is self.hass,
              'actual_event_bus')
        rows={}
        refs={}
        for pair in self.hass.bus._listeners.get(EVENT_ENTITY_REGISTRY_UPDATED,()):
            _need(type(pair) is tuple and len(pair)==2,'global_bus_pair_shape')
            job,event_filter=pair
            target=getattr(job,'target',None)
            target_code=getattr(target,'__code__',None)
            filter_code=getattr(event_filter,'__code__',None)
            if target_code is not self.rename_target and filter_code is not self.rename_filter:
                continue
            _need(type(job) is HassJob and job.job_type is HassJobType.Callback
                  and target_code is self.rename_target and filter_code is self.rename_filter,
                  'global_rename_pair_code')
            def closure(function):
                cells=function.__closure__ or ()
                _need(len(cells)==len(function.__code__.co_freevars),'global_closure_shape')
                return dict(zip(function.__code__.co_freevars,
                                (cell.cell_contents for cell in cells),strict=True))
            callback_cells=closure(target)
            filter_cells=closure(event_filter)
            _need(set(callback_cells)=={'entry','hass'}
                  and set(filter_cells)=={'source_entity_id'}
                  and callback_cells['hass'] is self.hass,'global_rename_closure')
            entry=callback_cells['entry']
            source=filter_cells['source_entity_id']
            _need(any(entry is item for item in self.entries) and type(source) is str,
                  'global_rename_entry_source')
            key=str(id(job))
            _need(key not in rows,'global_rename_duplicate_job')
            rows[key]={'job_process_id':id(job),'callback_process_id':id(target),
                       'filter_process_id':id(event_filter),'entry_id':entry.entry_id,
                       'entry_process_id':id(entry),'source_entity_id':source}
            refs[key]=(job,target,event_filter,entry)  # Hold identities through reload.
        return rows,refs

    def observe(self,frame,phase,result=None):
        try:
            code=frame.f_code
            entity=frame.f_locals.get('self')
            tag=self.tags.get(code)
            if tag.startswith('producer_'):
                _need(self.producer_owner is not None,'producer_owner_missing')
                self.producer_owner.observe(tag,frame,phase,result)
                return
            if tag in ('exact_vendor_schedule','exact_base_schedule'):
                _need(self.exact_owner is not None,'exact_owner_missing')
                self.exact_owner.observe(tag,frame,phase)
                return
            if tag in ('global_add','global_remove','global_effect'):
                _need(self.listener_owner is not None,'global_owner_missing')
                self.listener_owner.observe(tag,frame,phase,result)
                return
            if tag=='root':
                call=frame.f_locals.get('_')
                if getattr(call,'hass',None) is not self.hass:
                    return
                _need(type(call) is self.call_class and self.causal.get(None) is self.marker
                      and call.context is self.context
                      and call.domain=='powercalc' and call.service=='reload','foreign_reload')
                if phase=='start':
                    _need(self.started==0,'one_reload')
                    self.started=1
                    self.root_task=asyncio.current_task()
                    _need(type(self.root_task) is asyncio.Task and self.root_task.get_loop() is self.loop,
                          'actual_service_task')
                else:
                    _need(asyncio.current_task() is self.root_task,'same_service_task')
                    self.returned+=1
                self.calls.append([tag,phase])
                return
            # Unrelated native entity/platform work is outside this finite graph.
            if tag=='reset':
                relevant=getattr(entity,'platform_name',None)=='powercalc' and entity.hass is self.hass
            elif tag in ('setup','unload','yaml','rename_register'):
                relevant=frame.f_locals.get('hass') is self.hass
            elif tag.startswith('store_'):
                relevant=entity is self.previous_store
            else:
                relevant=(getattr(entity,'entity_id',None) in self.old
                          and (entity is self.old.get(entity.entity_id)
                               or getattr(getattr(entity,'registry_entry',None),'platform',None)=='powercalc'))
            if not relevant:
                return
            _need(self.started==1 and self.causal.get(None) is self.marker
                  and asyncio.current_task() is not None,'foreign_lifecycle')
            task=asyncio.current_task()
            _need(type(task) is asyncio.Task,'real_task')
            key=(id(frame),tag)
            if phase=='start':
                _need(key not in self.inflight,'duplicate_start')
                self.inflight[key]=task
            else:
                _need(self.inflight.pop(key,None) is task,'matching_return')
            if tag in ('setup','unload'):
                entry=frame.f_locals.get('entry',frame.f_locals.get('config_entry'))
                _need(any(entry is row for row in self.entries),'entry_scope')
                if phase=='return':
                    _need(result is True,'native_entry_result')
                self.calls.append([tag,phase,entry.entry_id])
            elif tag=='rename_register':
                entry=frame.f_locals.get('entry')
                source=frame.f_locals.get('source_entity_id')
                _need(any(entry is row for row in self.entries)
                      and (entry.entry_id,source) in self.rename_sources,
                      'global_rename_registration_binding')
                self.calls.append([tag,phase,entry.entry_id,source])
            elif tag=='reset':
                self.calls.append([tag,phase,id(entity)])
            elif tag=='yaml':
                self.calls.append([tag,phase])
            elif tag.startswith('store_'):
                if tag!='store_persist':
                    _need(frame.f_locals.get('group')==self.group_id,'previous_store_group')
                self.calls.append([tag,phase,self.group_id])
            elif tag=='cancel' and entity is self.old.get(entity.entity_id):
                if phase=='start':
                    snapshot=self.snapshot(entity)
                    self.cancelled[entity.entity_id]=snapshot
                    if not snapshot['timer']['cancelled']:
                        self.last_active[entity.entity_id]=snapshot
                else:
                    _need(entity._max_sub_interval_exceeded_callback.__self__.cancelled(),'native_timer_not_cancelled')
            elif tag=='remove' and entity is self.old.get(entity.entity_id):
                if phase=='start':
                    _need(entity.entity_id not in self.departures,'one_departure')
                    self.departures[entity.entity_id]=self.snapshot(entity)
                else:
                    _need(not entity._on_remove,'old_callbacks_left')
                    self.removed.add(entity.entity_id)
                    if self.producer_owner is not None:
                        self.producer_owner.removed(entity)
            elif tag=='add' and phase=='return':
                _need(entity is not self.old.get(entity.entity_id)
                      and entity.entity_id not in self.added,'one_replacement')
                self.added[entity.entity_id]=entity
        except BaseException as error:
            if self.error is None:
                self.error='native_callback_refused'
                # Fixed machine reason and code location only. Never retain
                # the exception/frame or expose configuration and native data.
                try:
                    detail=(error.args[0] if type(error) is ValueError
                        and len(error.args)==1 and type(error.args[0]) is str
                        and error.args[0].startswith(('powercalc_reload_','powercalc_exact_fields_','powercalc_retained_producer_')) else None)
                    self.first_failure=(tag,phase,frame.f_code.co_qualname,
                        frame.f_lineno,type(error).__module__+'.'+type(error).__qualname__,detail)
                except BaseException:
                    self.first_failure=None

    async def settle_descendants(self):
        # Service ACK is not entity completion. Await only actual inherited
        # descendants/ready callbacks of THIS call, never global HA task drain.
        current=asyncio.current_task()
        failed=False
        while True:
            children=[task for task in asyncio.all_tasks(self.loop)
                if task not in (self.owner,current) and not task.done()
                and task.get_context().get(self.causal,None) is self.marker]
            if self.producer_owner is not None:
                # Finite real Tasks observed at exact native old-W frames,
                # including refused pre-boundary input; never a global HA drain.
                children.extend(task for task in self.producer_owner.pending_tasks()
                    if task not in (self.owner,current) and task not in children)
            ready=[handle for handle in self.loop._ready
                if not handle.cancelled()
                and handle._context.get(self.causal,None) is self.marker
                and getattr(handle._callback,'__self__',None) not in (self.owner,current)]
            if not children and not ready:
                _need(not failed,'native_descendant_failed')
                return
            if children:
                results=await asyncio.gather(*children,return_exceptions=True)
                failed=failed or any(isinstance(result,BaseException) for result in results)
            else:
                await asyncio.sleep(0)  # Let actual scheduled callbacks run themselves.

    def install(self):
        monitor=sys.monitoring
        free=[key for key in range(6) if monitor.get_tool(key) is None]
        _need(bool(free),'monitor_unavailable')
        self.tool=free[0]
        monitor.use_tool_id(self.tool,'renamer.powercalc_reload')
        def started(code,offset):
            self.observe(sys._getframe(1),'start')
        def returned(code,offset,result):
            self.observe(sys._getframe(1),'return',result)
        monitor.register_callback(self.tool,monitor.events.PY_START,started)
        monitor.register_callback(self.tool,monitor.events.PY_RETURN,returned)
        for code in self.codes:
            monitor.set_local_events(self.tool,code,monitor.events.PY_START|monitor.events.PY_RETURN)

    def close(self):
        first=None
        def attempt(function):
            nonlocal first
            try:
                function()
            except BaseException as error:
                if first is None:
                    first=error
        if self.tool is not None and sys.monitoring.get_tool(self.tool)=='renamer.powercalc_reload':
            for code in self.codes:
                attempt(lambda code=code:sys.monitoring.set_local_events(self.tool,code,0))
            attempt(lambda:sys.monitoring.register_callback(self.tool,sys.monitoring.events.PY_START,None))
            attempt(lambda:sys.monitoring.register_callback(self.tool,sys.monitoring.events.PY_RETURN,None))
            attempt(lambda:sys.monitoring.free_tool_id(self.tool))
        self.tool=None
        self.live=False
        if first is not None:
            raise first


async def async_witness_unchanged_powercalc_reload(hass,receipt,evidence,original,*,guard,
                                                  _manage_global_rename=False, _exact_fields=None,
                                                  _retain_producers=False):
    """Own ONE real service call under unchanged ORIGINAL. Caller owns admin/IO lease.

    PERFORMS a real vendor reload, which may reset numeric state. This function
    has no route from YAMLTransaction or WS. A private typed chain policy may
    transfer only exact live fields; all continuity/readiness flags stay false.
    Its result records observed lifecycle only, never authorization/readiness.
    """
    from homeassistant.const import __version__
    from homeassistant.core import Context, EventBus, HomeAssistant, ServiceCall
    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import EntityPlatform
    from .yaml_validation import _settled_executor
    _need(type(_manage_global_rename) is bool,'global_policy_shape')
    _need(type(_retain_producers) is bool
          and (not _retain_producers or _exact_fields is not None),'typed_producer_policy')
    _need(__version__=='2026.10.2' and type(hass) is HomeAssistant,'supported_pair')
    if _exact_fields is not None:
        from .powercalc_exact_fields import _ExactFieldsPolicy
        _need(type(_exact_fields) is _ExactFieldsPolicy and _manage_global_rename,
              'typed_exact_fields_requires_global_owner')
    scope=_Reload(hass,receipt,evidence,original,guard)
    scope.vendor=PowercalcVendor(hass,with_profiles=True)
    bound=await async_bind_powercalc_sources(hass,receipt,evidence,original,guard=guard)
    _need(all(row.source_verified for row in bound.rows),'complete_bound_graph_required')
    _need(evidence.powercalc_sources.closed_graph_json is not None,'closed_profiles_required')
    scope.entries=tuple(hass.config_entries.async_entries('powercalc'))
    _need(bool(scope.entries) and all(row.state.value=='loaded' for row in scope.entries),'loaded_entries')
    platforms,scope.old=scope.entities()
    _need(set(scope.old)=={row.entity_id for row in bound.rows},'full_reload_graph')
    kinds={row.entity_id:row.relation_kind for row in bound.rows}
    _need(sum(k=='energy_integral' for k in kinds.values())==3
          and sum(k=='energy_positive_delta' for k in kinds.values())==1,'profile_energy_slice')
    grouped=[entity for entity in scope.old.values()
             if type(entity) is scope.vendor.classes['GroupedEnergySensor']]
    _need(len(grouped)==1,'one_previous_state_owner')
    scope.group_id=grouped[0].entity_id
    scope.previous_store=grouped[0]._prev_state_store
    before={key:scope.snapshot(entity) for key,entity in sorted(scope.old.items())}
    _need(all(not row['timer']['cancelled'] for row in before.values() if 'timer' in row),
          'initial_active_clocks')
    if _exact_fields is not None:
        from .powercalc_exact_fields import _ExactFieldsOwner
        scope.exact_owner=_ExactFieldsOwner(scope,_exact_fields)
    if _retain_producers:
        from .powercalc_retained_producer import _RetainedProducerOwner
        scope.producer_owner=_RetainedProducerOwner(scope)
    native=import_module('custom_components.powercalc')
    scope.vendor_refs=tuple((native,name) for name in ('register_services','async_setup_entry',
                                                       'async_unload_entry','_async_load_yaml_sensor'))
    scope.vendor_refs+=tuple((scope.vendor.classes['PreviousStateStore'],name) for name in
        ('cleanup_entity_states','persist_states'))
    sensor=import_module('custom_components.powercalc.sensor')
    scope.vendor_refs+=((sensor,'_register_entity_id_change_listener'),)
    scope.ha_refs=((EntityPlatform,'async_reset'),(Entity,'_call_on_remove_callbacks'),
                   (Entity,'add_to_platform_finish'),
                   (scope.vendor.integration.IntegrationSensor,'_cancel_max_sub_interval_exceeded_callback'),
                   (EventBus,'async_listen'),(EventBus,'_async_listen_filterable_job'))
    if _manage_global_rename:
        from .powercalc_global_listener_ownership import _refs
        scope.ha_refs+=_refs()
    if scope.exact_owner is not None:
        scope.vendor_refs+=scope.exact_owner.vrefs
        scope.ha_refs+=scope.exact_owner.hrefs
    if scope.producer_owner is not None:
        scope.vendor_refs+=scope.producer_owner.vrefs
        scope.ha_refs+=scope.producer_owner.hrefs
    def prove():
        scope.vendor.sources()
        if scope.exact_owner is not None:
            scope.exact_owner.policy.prove(scope.vendor)
        vm,vc=prove_methods(scope.vendor.root,_pins(),scope.vendor_refs)
        pins=dict(_SOURCES['2026.10.2'])
        pins.update(scope.vendor.ha_pins)
        hm,hc=prove_methods(scope.vendor.ha_root,pins,scope.ha_refs)
        return vm,vc,hm,hc
    scope.vendor_methods,scope.vendor_codes,scope.ha_methods,scope.ha_codes=await _settled_executor(hass,prove)
    roots=[code for code in _code_objects(scope.vendor_codes[0])
           if code.co_qualname=='register_services.<locals>._reload_config']
    _need(len(roots)==1,'official_reload_closure')
    scope.tags={roots[0]:'root',scope.vendor_codes[1]:'setup',scope.vendor_codes[2]:'unload',
                scope.vendor_codes[3]:'yaml',scope.vendor_codes[4]:'store_clean',
                scope.vendor_codes[5]:'store_persist',scope.vendor_codes[6]:'rename_register',
                scope.ha_codes[0]:'reset',scope.ha_codes[1]:'remove',
                scope.ha_codes[2]:'add',scope.ha_codes[3]:'cancel'}
    nested=tuple(_code_objects(scope.vendor_codes[6]))
    targets=[code for code in nested if code.co_qualname.endswith('.<locals>._entity_rename_listener')]
    filters=[code for code in nested if code.co_qualname.endswith('.<locals>._filter_entity_id')]
    _need(len(targets)==len(filters)==1,'global_rename_native_closures')
    scope.rename_target,scope.rename_filter=targets[0],filters[0]
    rename_before,scope.rename_original_refs=scope.rename_listeners()
    scope.rename_sources={(row['entry_id'],row['source_entity_id']) for row in rename_before.values()}
    _need(bool(rename_before) and len(scope.rename_sources)==len(rename_before),
          'initial_single_global_rename_listener_per_source')
    if _manage_global_rename:
        scope.tags.update({scope.ha_codes[5]:'global_add',scope.ha_codes[6]:'global_remove',
                           scope.rename_target:'global_effect'})
    if scope.exact_owner is not None:
        scope.tags.update(scope.exact_owner.bind_codes())
    if scope.producer_owner is not None:
        scope.tags.update(scope.producer_owner.bind_codes())
    scope.codes=tuple(scope.tags)
    scope.context=Context()
    scope.call_class=ServiceCall
    scope.live=True
    scope.check()
    # The source/instance proof was sampled before executor IO; recheck ORIGINAL
    # again immediately before installing observation and the native effect.
    second=await async_bind_powercalc_sources(hass,receipt,evidence,original,guard=guard)
    _need(second==bound and not pending(hass),'preflight_changed')
    _need(scope.rename_listeners()[0]==rename_before,'preflight_global_listener_changed')
    if _manage_global_rename:
        from .powercalc_global_listener_ownership import _GlobalRenameOwner
        scope.listener_owner=_GlobalRenameOwner(scope,rename_before,second)
    if scope.exact_owner is not None:
        scope.exact_owner.bind_original(second)
    if scope.producer_owner is not None:
        scope.producer_owner.bind_original(second)
    token=scope.causal.set(scope.marker)
    task=None
    try:
        scope.install()
        scope.check()
        if scope.listener_owner is not None:
            scope.listener_owner.detach()
        async def reload_and_finish():
            try:
                await hass.services.async_call('powercalc','reload',{},
                    blocking=True,context=scope.context)
            finally:
                await scope.settle_descendants()
        task=asyncio.create_task(reload_and_finish())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            if task.done() and not task.cancelled():
                task.exception()
            raise
        scope.check()
        _need(scope.started==scope.returned==1 and not scope.inflight,'native_completion')
        _need(['store_clean','return',scope.group_id] in scope.calls,'previous_store_setup_unobserved')
        _need(scope.removed==set(scope.old) and set(scope.added)==set(scope.old),'full_entity_replacement')
        _need(all(entry.state.value=='loaded' for entry in scope.entries),'final_entries_not_loaded')
        for entity in scope.old.values():
            if type(entity) is scope.vendor.classes['VirtualEnergySensor']:
                cancel=entity._max_sub_interval_exceeded_callback
                handle=getattr(cancel,'__self__',None)
                _need(type(handle) is asyncio.TimerHandle and handle._loop is scope.loop
                      and cancel==handle.cancel and handle.cancelled(),'departed_timer_left')
                _need(entity.entity_id in scope.cancelled and entity.entity_id in scope.last_active,
                      'native_active_cancel_not_observed')
        resets={row[2] for row in scope.calls if row[:2]==['reset','return']}
        _need({id(platform) for platform in platforms}<=resets,'all_platforms_reset')
        for entry in scope.entries:
            for tag in ('unload','setup'):
                _need(scope.calls.count([tag,'return',entry.entry_id])==1,'all_gui_reloaded')
        _,current=scope.entities()
        _need(set(current)==set(scope.old) and all(current[k] is scope.added[k]
                  and current[k] is not scope.old[k] for k in current),'actual_replacement_inventory')
        for key,entity in current.items():
            _need(entity.registry_entry.id==before[key]['uuid']
                  and entity.unique_id==before[key]['unique_id']
                  and type(entity) is type(scope.old[key]),'replacement_identity')
        from homeassistant.helpers import event
        for tracker_key in (event._TRACK_STATE_CHANGE_DATA,event._TRACK_STATE_REPORT_DATA):
            tracker=hass.data.get(tracker_key)
            _need(tracker is not None and all(getattr(job.target,'__self__',None) is not old
                for jobs in tracker.callbacks.values() for job in jobs for old in scope.old.values()),
                'departed_listener_left')
        after_capture=capture_powercalc_runtime(hass,guard=guard)
        after_bound=await async_bind_powercalc_sources(hass,receipt,evidence,after_capture,guard=guard)
        def stable(rows):
            return tuple(tuple(getattr(row,f.name) for f in fields(row) if f.name!='runtime_identities')
                         for row in rows)
        _need(stable(after_bound.rows)==stable(bound.rows),'original_semantics_changed')
        scope.check()
        _need(not pending(hass),'unfinished_vendor_work')
        if scope.exact_owner is not None:
            # Final byte/code + accepted durable source fence. No refreshed baseline.
            final_proof=await _settled_executor(hass,prove)
            _need(final_proof==(scope.vendor_methods,scope.vendor_codes,
                                scope.ha_methods,scope.ha_codes),'exact_final_code_proof_changed')
            scope.check()
        after={key:scope.snapshot(entity) for key,entity in sorted(current.items())}
        rename_after,rename_refs=scope.rename_listeners()
        retained=set(rename_before)&set(rename_after)
        _need(all(rename_after[key]==rename_before[key]
                  and all(a is b for a,b in zip(rename_refs[key],scope.rename_original_refs[key],strict=True))
                  for key in retained),'global_original_identity_changed')
        actual_new=[(row['entry_id'],row['source_entity_id']) for key,row in rename_after.items()
                    if key not in rename_before]
        registered=[tuple(row[2:]) for row in scope.calls if row[:2]==['rename_register','return']]
        _need(sorted(actual_new)==sorted(registered)==sorted(scope.rename_sources),
              'actual_global_registration_completion')
        # Capture-only API: entity cleanup is not full reload ownership. In this
        # official version old global source-rename callbacks remain registered.
        blockers=('capture_only_no_full_reload_ownership',)
        if retained:
            blockers+=('vendor_global_rename_listeners_retained',)
        lifecycle=PowercalcReloadWitness(evidence.sha256,original.structural_token,_json(before),
            _json({'removed':scope.departures,'before_cancel':scope.cancelled,
                   'last_active':scope.last_active}),_json(after),
            _json(scope.calls),tuple((key,id(scope.old[key]),id(current[key])) for key in sorted(current)),
            after_capture.structural_token,_json(rename_before),_json(rename_after),blockers)
        if scope.listener_owner is not None:
            from .powercalc_global_listener_ownership import PowercalcOwnedReloadWitness
            # Complete exact-fields validation before committing global ownership.
            exact_receipt=(None if scope.exact_owner is None else scope.exact_owner.finish(current))
            producer_receipt=(None if scope.producer_owner is None else scope.producer_owner.finish(current))
            native_receipt=scope.listener_owner.finish(rename_after)
            if producer_receipt is not None:
                from .powercalc_retained_producer import PowercalcRetainedProducerReloadWitness
                return PowercalcRetainedProducerReloadWitness(
                    lifecycle,native_receipt,exact_receipt,producer_receipt)
            if exact_receipt is not None:
                from .powercalc_exact_fields import PowercalcExactFieldsReloadWitness
                return PowercalcExactFieldsReloadWitness(lifecycle,native_receipt,exact_receipt)
            return PowercalcOwnedReloadWitness(lifecycle,native_receipt)
        return lifecycle
    except BaseException as error:
        scope.annotate(error)
        if scope.exact_owner is not None:
            scope.exact_owner.annotate(error)
        if scope.producer_owner is not None:
            scope.producer_owner.annotate(error)
            try:
                scope.producer_owner.close()
            except BaseException as cleanup_error:
                error.add_note('powercalc_retained_producer_cleanup_refused')
                # Keep trying the independent existing global-owner cleanup.
                error.add_note(type(cleanup_error).__name__)
        if scope.listener_owner is not None:
            try:
                scope.listener_owner.abort()
            except BaseException as cleanup_error:
                error.add_note('powercalc_reload_global_rollback_refused')
                raise cleanup_error from error
        raise
    finally:
        scope.causal.reset(token)
        try:
            if scope.producer_owner is not None:
                scope.producer_owner.close()
        finally:
            scope.close()
