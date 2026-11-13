"""Defer the initial unknown publication of a restarting Powercalc leaf.

Only the original native Event is retained, for the bounded vendor reload.
Other events continue through the original tracker immediately. A finite first
value retires the transient event; without one it is delivered before exit.
"""
from decimal import Decimal, InvalidOperation
import asyncio
import sys

from .native_template_consumers import _bound, _need
from .native_template_rename_ingress import NativeTemplateRenameIngress


class PowercalcTemplateIngress:
    @classmethod
    def install(cls, handoff):
        from homeassistant.core import callback
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.helpers import entity_registry as er
        from .controlled_powercalc_population import POWER_KINDS
        from .native_template_binding_transport import _Runtime
        from .powercalc_source_binding import PowercalcVendor
        obj = cls()
        obj.handoff, obj.hass, obj.rows = handoff, handoff.hass, []
        obj.pending, obj.ready, obj.error, obj.witnessed = {}, set(), None, {}
        obj.monitor_slot = None
        obj.signal = asyncio.Event()
        mapping = dict(handoff.root.mapping)
        if handoff.direction == 'rollback':
            mapping = {new: old for old, new in mapping.items()}
        obj.ids = {mapping.get(name, name): uid for name, uid, kind in handoff.population.descriptors
                   if kind in POWER_KINDS}
        obj.registry = er.async_get(obj.hass)
        vendor = PowercalcVendor(obj.hass, with_profiles=True)
        vendor.bindings()
        obj.classes = (vendor.classes['VirtualPowerSensor'], vendor.classes['GroupedSensor'])
        runtime = _Runtime(obj.hass)
        obj.event = runtime.event
        # Bind the exact State object to a native Powercalc Entity write. A
        # matching ID/UUID alone cannot authenticate a foreign states.async_set.
        from homeassistant.core import EventBus, StateMachine
        from homeassistant.helpers.entity import Entity
        from .native_unload_code_proof import prove_methods, unchanged
        obj.refs = [(EventBus, 'async_fire_internal'), (StateMachine, 'async_set_internal'),
                    (Entity, '_async_write_ha_state')]
        obj.methods, obj.codes = prove_methods(runtime.native.root, runtime.native.pins, obj.refs)
        obj.monitor = sys.monitoring
        slot = next((i for i in range(6) if obj.monitor.get_tool(i) is None), None)
        _need(slot is not None, 'powercalc_template_ingress_monitor_unavailable')
        obj.monitor_slot = slot
        obj.monitor.use_tool_id(slot, 'native Powercalc Template ingress provenance')
        def observe(code, offset):
            frame = sys._getframe(1)
            state_frame, entity_frame = frame.f_back, frame.f_back.f_back if frame.f_back else None
            if (frame.f_locals.get('self') is not obj.hass.bus
                    or frame.f_locals.get('event_type') != 'state_changed'
                    or state_frame is None or state_frame.f_code is not StateMachine.async_set_internal.__code__
                    or state_frame.f_locals.get('self') is not obj.hass.states
                    or entity_frame is None or entity_frame.f_code is not Entity._async_write_ha_state.__code__):
                return
            data = frame.f_locals.get('event_data')
            owner = entity_frame.f_locals.get('self')
            if type(data) is not dict or type(owner) not in obj.classes:
                return
            name, state = data.get('entity_id'), data.get('new_state')
            entry = obj.registry.async_get(name) if name in obj.ids else None
            if (entry is not None and entry.id == obj.ids[name] and entry.platform == 'powercalc'
                    and owner.entity_id == name and owner.registry_entry is entry and state is not None):
                obj.witnessed[id(state)] = (state, owner)
        obj.monitor.register_callback(slot, obj.monitor.events.PY_START, observe)
        obj.monitor.set_local_events(slot, EventBus.async_fire_internal.__code__, obj.monitor.events.PY_START)
        try:
            for platform in async_get_platforms(obj.hass, 'template'):
                for entity in platform.entities.values():
                    if type(entity) not in (runtime.native.sensor, runtime.native.binary, runtime.native.switch):
                        continue
                    tracker = runtime.graph(entity)
                    infos = tracker._info.values()
                    if not any(any(name in info.entities or info.all_states or
                                   name.split('.')[0] in info.domains for name in obj.ids)
                               for info in infos):
                        continue
                    filtered = tracker._track_state_changes
                    original = filtered._action
                    _need(_bound(original, tracker, runtime.event.TrackTemplateResultInfo._refresh),
                          'powercalc_template_ingress_action_changed')
                    jobs = NativeTemplateRenameIngress.native_jobs(obj.hass, obj.event, filtered, original)
                    def make_action(original):
                        @callback
                        def action(event):
                            data = event.data
                            name, before, after = data.get('entity_id'), data.get('old_state'), data.get('new_state')
                            try:
                                if name in obj.ids and after is not None:
                                    key = (id(original.__self__), name)
                                    proof = obj.witnessed.get(id(after))
                                    owner = proof[1] if proof is not None and proof[0] is after else None
                                    if owner is None:
                                        prior = obj.pending.pop(key, None)
                                        if prior is not None:
                                            prior[0](prior[1])
                                        original(event)
                                        return
                                    entry = obj.registry.async_get(name)
                                    owners = [platform.entities[name] for platform in async_get_platforms(obj.hass, 'powercalc')
                                              if name in platform.entities]
                                    _need(entry is not None and entry.id == obj.ids[name]
                                          and entry.platform == 'powercalc' and len(owners) == 1
                                          and owners[0] is owner and owner.registry_entry is entry,
                                          'powercalc_template_ingress_registry_changed')
                                    restored = (before is not None and before.state == 'unavailable'
                                                and before.attributes.get('restored') is True)
                                    if (before is None or restored) and after.state in ('unknown', 'unavailable') and key not in obj.ready:
                                        obj.pending[key] = (original, event, owner)
                                        obj.signal.clear()
                                        return
                                    if key in obj.pending:
                                        try:
                                            finite = Decimal(after.state).is_finite()
                                        except (InvalidOperation, ValueError):
                                            finite = False
                                        prior = obj.pending.pop(key)
                                        if finite and prior[2] is owner:
                                            obj.ready.add(key)
                                            if not obj.pending:
                                                obj.signal.set()
                                        else:
                                            prior[0](prior[1])
                            except Exception as error:
                                # HA dispatch logs callback errors; retain the error for
                                # the owning transaction and deliver this real event.
                                obj.error = error
                            original(event)
                        return action
                    wrapper = make_action(original)
                    obj.rows.append((filtered, original, wrapper, jobs))
                    filtered._action = wrapper
                    for job in jobs:
                        job.target = wrapper
        except BaseException:
            obj.close()
            raise
        return obj

    async def settle(self):
        # Vendor reload can return before its first calculated power value.
        # Keep the original pending Event until that native write arrives.
        while self.pending:
            self.handoff._scope()
            try:
                await asyncio.wait_for(self.signal.wait(), timeout=30)
            except TimeoutError:
                _need(False, 'powercalc_template_first_value_timeout')
            self.handoff._scope()
            self.signal.clear()
        _need(self.error is None, 'powercalc_template_ingress_callback_failed')

    def close(self):
        try:
            for filtered, original, wrapper, original_jobs in reversed(self.rows):
                _need(filtered._action is wrapper, 'powercalc_template_ingress_replaced')
                jobs = NativeTemplateRenameIngress.native_jobs(self.hass, self.event, filtered, wrapper)
                _need(all(job.target is wrapper for job in original_jobs),
                      'powercalc_template_ingress_original_job_changed')
                filtered._action = original
                for job in (*original_jobs, *jobs):
                    job.target = original
            self.rows.clear()
            pending, self.pending = self.pending, {}
            for original, event, _ in pending.values():
                original(event)
            from .native_unload_code_proof import unchanged
            unchanged(self.refs, self.methods, self.codes)
            _need(self.error is None, 'powercalc_template_ingress_callback_failed')
        finally:
            if self.monitor_slot is not None:
                from homeassistant.core import EventBus
                self.monitor.set_local_events(self.monitor_slot, EventBus.async_fire_internal.__code__, 0)
                self.monitor.register_callback(self.monitor_slot, self.monitor.events.PY_START, None)
                self.monitor.free_tool_id(self.monitor_slot)
                self.monitor_slot = None
