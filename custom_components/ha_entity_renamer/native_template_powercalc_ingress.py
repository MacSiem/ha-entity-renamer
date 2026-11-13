"""Defer the initial unknown publication of a restarting Powercalc leaf.

Only the original native Event is retained, for the bounded vendor reload.
Other events continue through the original tracker immediately. A finite first
value retires the transient event; without one it is delivered before exit.
"""
from decimal import Decimal, InvalidOperation

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
        obj.pending, obj.ready = {}, set()
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
                            if name in obj.ids and after is not None:
                                entry = obj.registry.async_get(name)
                                owners = [platform.entities[name] for platform in async_get_platforms(obj.hass, 'powercalc')
                                          if name in platform.entities]
                                _need(entry is not None and entry.id == obj.ids[name]
                                      and entry.platform == 'powercalc' and len(owners) == 1
                                      and type(owners[0]) in obj.classes and owners[0].registry_entry is entry,
                                      'powercalc_template_ingress_registry_changed')
                                key = (id(original.__self__), name)
                                if before is None and after.state in ('unknown', 'unavailable') and key not in obj.ready:
                                    obj.pending[key] = (original, event)
                                    return
                                if key in obj.pending:
                                    try:
                                        finite = Decimal(after.state).is_finite()
                                    except (InvalidOperation, ValueError):
                                        finite = False
                                    if finite:
                                        obj.pending.pop(key)
                                        obj.ready.add(key)
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

    def close(self):
        for filtered, original, wrapper, original_jobs in reversed(self.rows):
            _need(filtered._action is wrapper, 'powercalc_template_ingress_replaced')
            jobs = NativeTemplateRenameIngress.native_jobs(self.hass, self.event, filtered, wrapper)
            filtered._action = original
            _need(all(job.target is wrapper for job in original_jobs),
                  'powercalc_template_ingress_original_job_changed')
            for job in (*original_jobs, *jobs):
                job.target = original
        self.rows.clear()
        pending, self.pending = self.pending, {}
        for original, event in pending.values():
            original(event)
