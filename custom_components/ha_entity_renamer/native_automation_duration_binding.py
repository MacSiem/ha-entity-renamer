"""Private literal-duration evidence and transparent OWN pair bridge.

Native pending timers stay native. Only the newly matched genuine pair uses OWN
closures around the ORIGINAL async_track_same_state and Automation action job.
Default observers still refuse durations. No public family/readiness admission.
"""
from __future__ import annotations
import asyncio
from datetime import timedelta
from functools import partial
from types import FunctionType, CodeType
from .native_automation_state_binding import NativeAutomationStateBinding, _Native, _need, _value, _job
from .native_unload_code_proof import prove_methods, unchanged
from .yaml_runtime_observer import _closure, _registered_bus_remove, _trigger_listener


def _cells(f):
    return dict(zip(f.__code__.co_freevars, (c.cell_contents for c in f.__closure__ or ()), strict=True))


def _cell(f, name):
    return dict(zip(f.__code__.co_freevars, f.__closure__ or (), strict=True))[name]


def _codes(code):
    yield code
    for child in code.co_consts:
        if type(child) is CodeType:
            yield from _codes(child)


def _nested(f, parent, name):
    _need(type(f) is FunctionType and f.__globals__ is parent.__globals__
          and f.__qualname__ == parent.__qualname__ + '.<locals>.' + name
          and any(f.__code__ is c for c in _codes(parent.__code__)), 'duration_nested_changed')
    return _cells(f)


def _shape(f):
    return id(f), id(f.__code__), id(f.__globals__), tuple((id(c),id(c.cell_contents)) for c in f.__closure__ or ())



class _PreEffectLog:
    """Exact shallow slots, with a read-only rollback plan before any undo."""
    ABSENT = object()

    def __init__(self):
        self.slots, self.records = {}, []

    @staticmethod
    def read(kind, owner):
        if kind == 'list':
            return tuple(owner)
        if kind == 'dict':
            return tuple(owner.items())
        if kind == 'entry':
            table, key = owner
            return table.get(key, _PreEffectLog.ABSENT)
        return getattr(owner, kind)

    @staticmethod
    def token(kind, value):
        if kind == 'dict':
            return tuple((id(k), id(v)) for k, v in value)
        if kind == 'list':
            return tuple(map(id, value))
        return id(value)

    def retain(self, kind, owner):
        key = (kind, id(owner[0]), id(owner[1])) if kind == 'entry' else (kind, id(owner))
        if key not in self.slots:
            self.slots[key] = kind, owner, self.read(kind, owner)
        return key

    def write(self, kind, owner, after, apply):
        key = self.retain(kind, owner)
        record = [key, self.read(kind, owner), after, False]
        self.records.append(record)
        apply()
        record[3] = True

    def plan(self,shadow=None):
        if shadow is None:
            shadow={}
        def current(key,kind,owner):
            return shadow[key] if key in shadow else self.read(kind,owner)
        expected = {key: before for key, (_, _, before) in self.slots.items()}
        completed = []
        for index, (key, before, after, done) in enumerate(self.records):
            kind, owner, _ = self.slots[key]
            same = lambda a, b: self.token(kind, a) == self.token(kind, b)
            _need(same(expected[key], before), 'duration_write_log_changed')
            if not done:
                # An interrupted built-in write has precisely two admissible
                # outcomes. Partial dict update/foreign values remain UNKNOWN.
                _need(index == len(self.records)-1, 'duration_write_log_uncertain')
                actual = current(key,kind,owner)
                _need(same(actual, before) or same(actual, after), 'duration_partial_write_uncertain')
                done = same(actual, after) and not same(actual, before)
            if done:
                expected[key] = after
                completed.append((kind, owner, before))
        for key, (kind, owner, _) in self.slots.items():
            _need(self.token(kind, current(key,kind,owner)) == self.token(kind, expected[key]),
                  'duration_rollback_slot_changed')
        for key, (_, _, before) in self.slots.items():
            shadow[key]=before
        return tuple(reversed(completed))

    @staticmethod
    def undo(plan):
        for kind, owner, before in plan:
            if kind == 'list':
                owner[:] = before
            elif kind == 'dict':
                owner.clear()
                owner.update(before)
            elif kind == 'entry':
                table, key = owner
                if before is _PreEffectLog.ABSENT:
                    table.pop(key)
                else:
                    table[key] = before
            else:
                setattr(owner, kind, before)


def _retained_value(value, omitted, seen=()):
    """Pin closure topology while separately journaled slots change values."""
    if id(value) in seen or id(value) in omitted:
        return type(value), id(value)
    seen = (*seen, id(value))
    if type(value) is FunctionType:
        return (id(value), id(value.__code__), id(value.__globals__),
            tuple((id(c), None if id(c) in omitted else _retained_value(c.cell_contents, omitted, seen))
                  for c in value.__closure__ or ()),
            tuple((name, id(value.__globals__[name])) for name in value.__code__.co_names
                  if name in value.__globals__), _value(value.__dict__))
    if type(value) is partial:
        return (id(value), _retained_value(value.func, omitted, seen),
                _retained_value(value.args, omitted, seen), _retained_value(value.keywords, omitted, seen))
    if type(value) in (list, tuple):
        return id(value), tuple(_retained_value(v, omitted, seen) for v in value)
    if type(value) is dict:
        return id(value), tuple((_retained_value(k, omitted, seen), _retained_value(v, omitted, seen))
                               for k, v in value.items())
    return _value(value)


def literal_duration(cfg):
    value = cfg.get('for')
    _need(type(value) is timedelta and value > timedelta(0), 'literal_duration_required')
    return value


class NativeAutomationDurationBinding(NativeAutomationStateBinding):
    def _read(self):
        return self._duration.original(self)


class NativeAutomationDurationCapability:
    def __init__(self, hass, native):
        _need(type(native) is _Native and native.hass is hass, 'duration_source_owner')
        self.hass, self.native, self.wrappers = hass, native, {}
        self.refs = [(native.event,n) for n in ('async_track_same_state','async_call_later',
            '_run_async_call_action','async_track_state_change_event')]
        self.methods,self.codes = prove_methods(native.root,native.pins,self.refs)
        self.functions = tuple(_value(f) for f in self.methods)
        self.check_sources()

    def check_sources(self):
        self.native.check()
        unchanged(self.refs,self.methods,self.codes)
        _need(tuple(_value(getattr(o,n)) for o,n in self.refs) == self.functions
              and self.native.state.async_track_same_state is self.native.event.async_track_same_state,
              'duration_native_import_changed')

    def capture(self, entity, index=0):
        from collections import defaultdict
        from homeassistant.util.yaml.objects import NodeDictClass, NodeListClass, NodeStrClass
        self.check_sources()
        n,h = self.native,self.hass
        _need(type(entity) is n.AutomationEntity and entity.hass is h and entity._is_enabled is True
              and len(entity._trigger_config) == 1 and index == 0, 'duration_single_trigger_required')
        cfg = entity._trigger_config[0]
        _need(type(cfg) in (dict,NodeDictClass) and cfg.get('platform') == 'state'
              and cfg.get('enabled',True) is True and 'variables' not in cfg
              and entity._trigger_variables is None, 'duration_static_config_required')
        literal_duration(cfg)
        detach = entity._async_detach_triggers
        removes = _closure(detach,n.trigger.__name__,'async_initialize_triggers.<locals>.remove_triggers')['removes']
        _need(type(removes) is list and len(removes) == 1,'duration_remove_inventory')
        remove = removes[0]
        rc = _closure(remove,n.state.__name__,'async_attach_trigger.<locals>.async_remove')
        unsub = rc['unsub']
        _need(type(unsub) is partial and unsub.func is n.event._remove_listener
              and not unsub.keywords and len(unsub.args)==5,'duration_main_cleanup')
        owner,tracker,keys,job,callbacks = unsub.args
        ids = cfg['entity_id']
        _need(owner is h and tracker is n.event._KEYED_TRACK_STATE_CHANGE
              and type(keys) is list and type(ids) in (list,NodeListClass) and keys is not ids and keys==ids
              and keys and len(set(keys))==len(keys)
              and all(type(k) in (str,NodeStrClass) and k==k.lower() and '.' in k for k in keys)
              and type(job) is n.HassJob,'duration_main_route')
        listener = job.target
        cells,_ = _trigger_listener(h,entity,listener,n.state.__name__,'state_automation_listener',cfg,0)
        _need(cells['config'] is cfg and cells['time_delta'] is cfg['for']
              and type(cells['period']) is dict and type(cells['unsub_track_same']) is dict
              and cells['unsub_track_same'] is rc['unsub_track_same']
              and cells['attribute']==cfg.get('attribute')
              and cells['match_all']==all(k not in cfg for k in ('from','not_from','to','not_to')),
              'duration_config_cells')
        for side in ('from','to'):
            parameter,invert=cfg.get(side),False
            if parameter is None:
                parameter=cfg.get('not_'+side);invert=parameter is not None
            expected=n.event.process_state_match('*' if parameter is None else parameter,invert=invert)
            actual=cells['match_'+side+'_state']
            _need(type(actual) is FunctionType and actual.__code__ is expected.__code__
                  and actual.__globals__ is expected.__globals__ and _cells(actual)==_cells(expected),
                  'duration_matcher_changed')
        data=h.data[tracker.key]
        _need(type(data) is n.event._KeyedEventData and data.callbacks is callbacks
              and type(callbacks) is defaultdict and callbacks.default_factory is list,'duration_tracker')
        entry,_=_registered_bus_remove(h,data.listener,tracker.event_type)
        for f,expected in ((entry[0].target,tracker.dispatcher_callable),(entry[1],tracker.filter_callable)):
            _need(type(f) is partial and f.func is expected and not f.keywords and len(f.args)==2
                  and f.args[0] is h and f.args[1] is callbacks,'duration_dispatch')
        values=(h,entity,0,cfg,ids,keys,listener,job,cells['job'],cells['match_from_state'],
            cells['match_to_state'],cells['trigger_data'],cells['platform_type'],cells['attribute'],
            cells['match_all'],detach,removes,remove,unsub,tracker,data,callbacks,data.listener,entry,cells,n)
        b=NativeAutomationDurationBinding(*values,())
        object.__setattr__(b,'_duration',self)
        object.__setattr__(b,'_original',b._read())
        b.assert_original()
        return b

    def stable(self,b):
        c=_closure(b.listener,b._native.state.__name__,'async_attach_trigger.<locals>.state_automation_listener')
        _need(set(c)==set(b.listener_cells) and c['config'] is b.config
              and c['period'] is b.listener_cells['period'] and c['unsub_track_same'] is b.listener_cells['unsub_track_same'],
              'duration_map_identity_changed')
        return tuple((k,_value(v)) for k,v in c.items() if k not in ('config','period','unsub_track_same'))+(
            ('period',id(c['period'])),('unsub_track_same',id(c['unsub_track_same'])))

    def pending(self,b):
        """Full native U/F/A/H and cancel-job census; orphan or ready => refusal."""
        from homeassistant.core import Event,State,Context,HassJobType
        self.check_sources()
        from contextvars import Context as TimerContext
        n,e=self.native,self.native.event
        period,unsubs=b.listener_cells['period'],b.listener_cells['unsub_track_same']
        _need(set(unsubs)==set(period) and set(period)<=set(b.original_keys),'duration_map_keys')
        _need(all(k in b.callbacks and sum(j is b.keyed_job for j in b.callbacks[k])==1
                  for k in b.original_keys),'duration_main_membership')
        pending,rows=[],[]
        for key,clear in unsubs.items():
            u=_nested(clear,e.async_track_same_state,'clear_listener')
            _need(set(u)=={'async_remove_state_for_cancel','async_remove_state_for_listener'},'duration_clear_cells')
            cancel,timer=u['async_remove_state_for_cancel'],u['async_remove_state_for_listener']
            _need(type(period[key]) is timedelta and period[key]==literal_duration(b.config),'duration_period_changed')
            if cancel is None and timer is None:
                rows.append((key,id(clear),None));continue
            _need(type(cancel) is partial and cancel.func is e._remove_listener
                  and not cancel.keywords and len(cancel.args)==5,'duration_cancel_cleanup')
            owner,tracker,keys,job,callbacks=cancel.args
            _need(owner is b.hass and tracker is b.tracker and callbacks is b.callbacks
                  and type(keys) is list and keys==[key] and keys is not b.config_ids
                  and keys is not b.original_keys and type(job) is n.HassJob
                  and job.job_type is HassJobType.Callback,'duration_cancel_route')
            target=job.target
            if job in self.wrappers:
                wrapper,original,shape=self.wrappers[job]
                _need(target is wrapper and _shape(wrapper)==shape,'duration_cancel_wrapper_changed')
                target=original
            x=_nested(target,e.async_track_same_state,'state_for_cancel_listener')
            _need(set(x)=={'async_check_same_func','clear_listener'} and x['clear_listener'] is clear,'duration_cancel_cells')
            handle=getattr(timer,'__self__',None)
            _need(type(handle) is asyncio.TimerHandle and handle._loop is b.hass.loop
                  and getattr(timer,'__func__',None) is asyncio.TimerHandle.cancel and not handle.cancelled()
                  and handle._callback is e._run_async_call_action and len(handle._args)==2
                  and handle._args[0] is b.hass and type(handle._context) is TimerContext
                  and sum(h is handle for h in b.hass.loop._scheduled)==1
                  and not any(h is handle for h in b.hass.loop._ready),'duration_timer_not_scheduled')
            tj=handle._args[1]
            _need(type(tj) is n.HassJob and tj.job_type is HassJobType.Callback,'duration_timer_job')
            f=_nested(tj.target,e.async_track_same_state,'state_for_listener')
            _need(set(f)=={'async_remove_state_for_listener','clear_listener','hass','job'}
                  and f['hass'] is b.hass and f['clear_listener'] is clear
                  and _cell(tj.target,'async_remove_state_for_listener') is _cell(clear,'async_remove_state_for_listener'),
                  'duration_expiry_cells')
            sj=f['job'];_need(type(sj) is n.HassJob and sj.job_type is HassJobType.Callback,'duration_action_job')
            action,predicate=sj.target,x['async_check_same_func']
            ac=_cells(action)
            own=ac.get('self')
            if type(own) is _OwnDuration:
                own.check()
                _need(own.action is action and own.predicate is predicate and own.clear is clear
                      and own.binding.config is b.config and own.binding.action_job is b.action_job,'duration_own_owner')
                a=own.history();entity_cell=own.entity_cell
            else:
                a=_nested(action,b.listener,'call_action');p=_nested(predicate,b.listener,'_check_same_state')
                _need(set(a)=={'attribute','entity','event','from_s','hass','job','period','platform_type','time_delta','to_s','trigger_data'}
                      and a['entity']==key and a['hass'] is b.hass and a['job'] is b.action_job
                      and a['period'] is period and a['time_delta'] is b.config['for']
                      and a['trigger_data'] is b.trigger_data and a['attribute']==b.attribute
                      and a['platform_type']==b.platform_type
                      and set(p)=={'attribute','config','new_value','old_value'}
                      and p['config'] is b.config and p['attribute']==b.attribute,'duration_native_action_cells')
                for name in ('period','time_delta','job','trigger_data','attribute','platform_type','hass'):
                    _need(_cell(action,name) is _cell(b.listener,name),'duration_shared_action_cell')
                for name in ('config','attribute'):
                    _need(_cell(predicate,name) is _cell(b.listener,name),'duration_shared_predicate_cell')
                entity_cell=_cell(action,'entity')
                old = (None if a['from_s'] is None else a['from_s'].state if b.attribute is None
                       else a['from_s'].attributes.get(b.attribute))
                new = (None if a['to_s'] is None else a['to_s'].state if b.attribute is None
                       else a['to_s'].attributes.get(b.attribute))
                _need(_value(p['old_value'])==_value(old) and _value(p['new_value'])==_value(new),
                      'duration_predicate_history')
            _need(entity_cell.cell_contents==key and type(a['event']) is Event
                  and type(a['event'].context) is Context and a['event'].event_type=='state_changed'
                  and (a['from_s'] is None or type(a['from_s']) is State)
                  and (a['to_s'] is None or type(a['to_s']) is State)
                  and a['event'].data['old_state'] is (None if type(own) is _OwnDuration else a['from_s'])
                  and a['event'].data['new_state'] is a['to_s'],'duration_history')
            pending.append(dict(key=key,clear=clear,cancel=cancel,keys=keys,cancel_job=job,
                cancel_listener=target,predicate=predicate,handle=handle,timer_job=tj,same_job=sj,
                action=action,entity_cell=entity_cell,own=own if type(own) is _OwnDuration else None))
            rows.append((key,id(clear),id(handle),handle.when(),id(handle._context),_job(tj),_job(sj),
                _value(action),_value(predicate),id(a['event'].context),id(a['from_s']),id(a['to_s']),
                id(cancel),id(keys),tuple(keys),id(job),_shape(target)))
        expected_jobs={id(p['cancel_job']):p for p in pending};found=set()
        # Cancel-route census detects native match_all overwrites/orphans.
        for key,members in b.callbacks.items():
            _need(type(members) is list and len({id(j) for j in members})==len(members),'duration_duplicate_route')
            _need(sum(j is b.keyed_job for j in members)==(1 if key in b.original_keys else 0),'duration_main_membership')
            for job in members:
                _need(type(job) is n.HassJob,'duration_foreign_job_type')
                target=self.wrappers[job][1] if job in self.wrappers else job.target
                if type(target) is not FunctionType or target.__code__ not in tuple(_codes(e.async_track_same_state.__code__)) or target.__code__.co_name!='state_for_cancel_listener':
                    continue
                x=_nested(target,e.async_track_same_state,'state_for_cancel_listener');p=_cells(x['async_check_same_func'])
                own=p.get('self')
                if p.get('config') is b.config or type(own) is _OwnDuration and own.binding.config is b.config:
                    _need(id(job) in expected_jobs and key==expected_jobs[id(job)]['key'],'duration_orphan_cancel_job')
                    found.add(id(job))
        _need(found==set(expected_jobs),'duration_cancel_membership')
        expected_handles={id(p['handle']) for p in pending}
        for h in (*b.hass.loop._scheduled,*b.hass.loop._ready):
            if h.cancelled() or h._callback is not e._run_async_call_action:
                continue
            _need(len(h._args)==2,'duration_timer_args')
            job=h._args[1]
            if type(job) is not n.HassJob or type(job.target) is not FunctionType:
                continue
            target=job.target
            if target.__code__ not in tuple(_codes(e.async_track_same_state.__code__)) or target.__code__.co_name!='state_for_listener':
                continue
            f=_nested(target,e.async_track_same_state,'state_for_listener');a=_cells(f['job'].target);own=a.get('self')
            if a.get('period') is period or a.get('job') is b.action_job or type(own) is _OwnDuration and own.binding.config is b.config:
                _need(id(h) in expected_handles,'duration_orphan_timer')
        return tuple(pending),tuple(rows)

    def original(self,b):
        self.check_sources()
        target=b.action_job.target
        _need(getattr(target,'__self__',None) is b.entity
              and getattr(target,'__func__',None) is self.native.AutomationEntity._async_trigger_if_enabled
              and b.hass.data.get(b.tracker.key) is b.data and b.data.callbacks is b.callbacks
              and b.data.listener is b.bus_remove,'duration_action_or_tracker_owner')
        entry,_=_registered_bus_remove(b.hass,b.bus_remove,b.tracker.event_type)
        _need(entry is b.bus_entry,'duration_bus_owner')
        _need(b.entity._async_detach_triggers is b.detach and b.removes[0] is b.remove
              and b.entity._trigger_config[0] is b.config and b.config['entity_id'] is b.config_ids
              and b.keyed_job.target is b.listener and b.config_ids==b.original_keys,'duration_original_owner')
        return (_value(b.entity.raw_config),_value(b.entity._trigger_config),self.stable(b),
            _value(b.unsubscribe),_job(b.action_job),self.pending(b)[1])

    def tokens(self,entity):
        b=self.capture(entity)
        return ('literal_for',id(b.detach),id(b.removes),id(b.remove),id(b.unsubscribe),
            id(b.config),id(b.config_ids),id(b.original_keys),id(b.data),id(b.callbacks),
            id(b.keyed_job),id(b.listener),id(b.action_job),_shape(b.detach),_shape(b.remove),
            _shape(b.listener),tuple((k,id(b.callbacks[k])) for k in b.original_keys),self.stable(b))

    def lock(self,route):
        route.duration_snapshot=self.pending(route.binding)[1]

    def check_route(self,route):
        _need(self.pending(route.binding)[1]==route.duration_snapshot,'duration_pending_changed_during_root')

    def transport(self,route,target):
        b=route.binding;mapping=dict(zip(route.before,target,strict=True))
        pending,_=self.pending(b)
        maps=(b.listener_cells['period'],b.listener_cells['unsub_track_same'])
        for values in maps:
            _need(len({mapping.get(k,k) for k in values})==len(values),'duration_map_collision')
        for p in pending:
            old,new=p['key'],mapping[p['key']]
            _need(old==new or not any(j is p['cancel_job'] for j in b.callbacks.get(new,())), 'duration_cancel_collision')
        _need(route.duration_operations is None, 'duration_transport_repeated')
        log = route.duration_operations = _PreEffectLog()
        route.duration_pending = pending
        # Retain every related mutable slot, including untouched ones, before
        # the first duration write. Membership lists retain foreign job order.
        log.retain('dict', b.callbacks)
        for members in b.callbacks.values():
            log.retain('list', members)
        for values in maps:
            log.retain('dict', values)
        for p in pending:
            log.retain('list', p['keys'])
            log.retain('cell_contents', p['entity_cell'])
            log.retain('target', p['cancel_job'])
            log.retain('entry', (self.wrappers, p['cancel_job']))
            if p.get('own') is not None:
                log.retain('approved_entity', p['own'])
        route.duration_retained = self._retained(route)
        def write(kind,owner,before,after,apply):
            slot = {'append':'list','remove':'list','keys':'list',
                    'cell':'cell_contents','map_clear':'dict','map_update':'dict'}.get(kind,kind)
            _need(log.token(slot,log.read(slot,owner)) == log.token(slot,before),
                  'duration_write_before_changed')
            log.write(slot,owner,after,apply)
        for p in pending:
            old,new=p['key'],mapping[p['key']]
            if old!=new:
                _need(new in b.callbacks, 'duration_cancel_target_missing')
                write('append',b.callbacks[new],tuple(b.callbacks[new]),(*b.callbacks[new],p['cancel_job']),
                      lambda: b.callbacks[new].append(p['cancel_job']))
                members=b.callbacks[old]
                write('remove',members,tuple(members),tuple(j for j in members if j is not p['cancel_job']),
                      lambda: members.remove(p['cancel_job']))
                if not members:
                    write('dict',b.callbacks,tuple(b.callbacks.items()),
                          tuple((k,v) for k,v in b.callbacks.items() if k!=old),lambda: b.callbacks.pop(old))
                write('keys',p['keys'],tuple(p['keys']),(new,),lambda: p['keys'].__setitem__(slice(None),[new]))
                write('cell',p['entity_cell'],p['entity_cell'].cell_contents,new,
                      lambda: setattr(p['entity_cell'],'cell_contents',new))
                if p['own'] is not None:
                    own=p['own']
                    write('approved_entity',own,own.approved_entity,new,
                          lambda: setattr(own,'approved_entity',new))
        for values in maps:
            projected={mapping.get(k,k):v for k,v in values.items()}
            write('map_clear',values,tuple(values.items()),(),values.clear)
            write('map_update',values,(),tuple(projected.items()),lambda: values.update(projected))

    def wrap(self,graph,route):
        from homeassistant.core import callback
        from types import MappingProxyType
        for p in self.pending(route.binding)[0]:
            job=p['cancel_job']
            if job in self.wrappers:
                continue
            def factory(pending):
                @callback
                def receive(event):
                    graph._receive_duration(route,event,pending)
                return receive
            wrapper=factory(MappingProxyType(dict(p)))
            entry=(wrapper,p['cancel_listener'],_shape(wrapper))
            if route.duration_operations is not None:
                log=route.duration_operations
                log.write('entry',(self.wrappers,job),entry,
                          lambda: self.wrappers.__setitem__(job,entry))
                log.write('target',job,wrapper,lambda: setattr(job,'target',wrapper))
            else:
                self.wrappers[job]=entry
                job.target=wrapper
        self.lock(route)

    def _retained(self,route):
        log=route.duration_operations
        omitted={id(owner) for kind,owner,_ in log.slots.values() if kind!='entry'}
        rows=[]
        for values in (route.binding.listener_cells['period'],route.binding.listener_cells['unsub_track_same']):
            _,_,before=log.slots[('dict',id(values))]
            rows.append(tuple(_retained_value(v,omitted) for _,v in before))
        for p in route.duration_pending:
            handle=p['handle']
            _need(not handle.cancelled() and sum(h is handle for h in self.hass.loop._scheduled)==1
                  and not any(h is handle for h in self.hass.loop._ready), 'duration_rollback_timer_changed')
            rows.append((id(handle),handle.when(),id(handle._loop),id(handle._context),
                id(handle._callback),tuple(map(id,handle._args)),
                tuple(_retained_value(p[n],omitted) for n in
                      ('clear','cancel','cancel_listener','predicate','action')),
                tuple((id(p[n]),id(p[n].target),p[n].job_type,p[n].name,
                       p[n]._cancel_on_shutdown,_value(p[n]._cache)) for n in ('timer_job','same_job')),
                (id(p['cancel_job']),p['cancel_job'].job_type,p['cancel_job'].name,
                 p['cancel_job']._cancel_on_shutdown,_value(p['cancel_job']._cache)),
                None if p.get('own') is None else
                (id(p['own'].binding),id(p['own'].change),id(p['own'].entity_cell),
                 id(p['own'].action),id(p['own'].predicate),id(p['own'].clear),_value(p['own'].edges))))
        return tuple(rows)

    def validate_retire(self,route,before_attempt=False,shadow=None):
        if before_attempt and route.duration_operations is not None:
            self.check_sources()
            for p in route.duration_pending:
                if p['cancel_job'] in self.wrappers:
                    wrapper,original,shape=self.wrappers[p['cancel_job']]
                    _need(_shape(wrapper)==shape and _cells(wrapper)['route'] is route
                          and original is p['cancel_listener'], 'duration_rollback_wrapper_changed')
            _need(self._retained(route)==route.duration_retained, 'duration_rollback_retained_changed')
            return route.duration_operations.plan(shadow)
        self.check_route(route)
        for job,(wrapper,original,shape) in self.wrappers.items():
            if _cells(wrapper)['route'] is route:
                _need(job.target is wrapper and _shape(wrapper)==shape,'duration_retire_wrapper')
                _nested(original,self.native.event.async_track_same_state,'state_for_cancel_listener')
        return None

    def retire(self,route,before_attempt,plan=None):
        if before_attempt and route.duration_operations is not None:
            _need(plan is not None,'duration_rollback_plan_missing')
            _PreEffectLog.undo(plan)
            return
        for job,(wrapper,original,_) in tuple(self.wrappers.items()):
            if _cells(wrapper)['route'] is route:
                job.target=original
                self.wrappers.pop(job)

    def start_pair(self,graph,route,change):
        b=route.binding;key=change.event.data['entity_id']
        _need(key not in {p['key'] for p in self.pending(b)[0]},'duration_match_would_orphan_pending')
        own=_OwnDuration(b,change)
        b.listener_cells['period'][key]=literal_duration(b.config)
        own.clear=self.native.event.async_track_same_state(self.hass,b.listener_cells['period'][key],own.action,own.predicate,entity_ids=key)
        b.listener_cells['unsub_track_same'][key]=own.clear
        self.wrap(graph,route)


class _OwnDuration:
    def __init__(self,binding,change):
        from homeassistant.core import callback
        self.binding,self.change,self.clear=binding,change,None
        entity=change.event.data['entity_id']
        @callback
        def action():
            self.check();b,c=self.binding,self.change
            b.hass.async_run_hass_job(b.action_job,{'trigger':{**b.trigger_data,'platform':b.platform_type,
                'entity_id':entity,'from_state':c.prior_state,'to_state':c.new_state,
                'for':b.listener_cells['period'][entity],'attribute':b.attribute,
                'description':f'state of {entity}'}},c.event.context)
        def predicate(_, _2, state):
            self.check()
            if state is None:
                return False
            b,c=self.binding,self.change;a=b.attribute
            cur=state.state if a is None else state.attributes.get(a)
            prior=c.prior_state.state if a is None else c.prior_state.attributes.get(a)
            new=c.new_state.state if a is None else c.new_state.attributes.get(a)
            return cur!=prior if 'from' in b.config and 'to' not in b.config else cur==new
        self.action,self.predicate=action,predicate
        self.entity_cell=_cell(action,'entity')
        self.approved_entity=entity
        self.edges=(binding.config,binding.action_job,binding.listener_cells['period'],change,
            change.prior_state,change.new_state,change.event,change.event.context)

    def check(self):
        b,c=self.binding,self.change
        b._native.check()
        target=b.action_job.target
        _need(getattr(target,'__self__',None) is b.entity
              and getattr(target,'__func__',None) is b._native.AutomationEntity._async_trigger_if_enabled
              and b.entity.hass is b.hass and b.entity._trigger_config[b.index] is b.config
              and self.entity_cell.cell_contents == self.approved_entity
              and self.entity_cell.cell_contents in b.original_keys
              and self.entity_cell.cell_contents in b.listener_cells['period'],
              'duration_own_current_binding_changed')
        _need(_nested(self.action,_OwnDuration.__init__,'action')['self'] is self
              and _nested(self.predicate,_OwnDuration.__init__,'predicate')['self'] is self
              and _cell(self.action,'entity') is self.entity_cell
              and all(x is y for x,y in zip(self.edges,(b.config,b.action_job,b.listener_cells['period'],c,
                  c.prior_state,c.new_state,c.event,c.event.context),strict=True)),'duration_own_edges')

    def history(self):
        return dict(event=self.change.event,from_s=self.change.prior_state,to_s=self.change.new_state)
