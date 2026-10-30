"""Typed combined producer/action fence for one Root-owned registry edge."""
from __future__ import annotations
import asyncio
from .native_template_rename_ingress import NativeTemplateProducerDelivery
from .native_automation_owned_graph import OwnedAutomationGraph, _need


class NativeAutomationProducerDelivery:
    def __init__(self, runtime, entities, witness, graph):
        _need(type(graph) is OwnedAutomationGraph and witness.automation_graph is graph
              and witness.preflight is None and not witness.attempted)
        # Existing Template fence owns no separate monitor; compose its routes.
        self.template = NativeTemplateProducerDelivery(runtime, entities, witness)
        self.witness, self.graph, self.live = witness, graph, True
        self.action_frames, self.action_tasks = set(), set()
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        self.owners, self.roots, self.jobs = [], [], []
        for domain in ('automation', 'script'):
            component = witness.hass.data.get(DATA_INSTANCES, {}).get(domain)
            for entity in component.entities if component is not None else ():
                self.owners.append(entity)
                self.roots.append(entity.action_script if domain == 'automation' else entity.script)
        # Capture actual keyed jobs before cutover; bindings are recaptured at
        # the final fence, and no await can separate that capture from install.
        self.jobs.extend(b.keyed_job for b in graph.prepared)
        self.template.jobs.extend(self.jobs)
        witness.preflight = self

    def _ours(self, frame):
        actor = frame.f_locals.get('self')
        code = frame.f_code
        return ((code in (self.witness.codes['automation_action'], self.witness.codes['automation_run'])
                 and any(actor is e for e in self.owners))
                or code is self.witness.codes['script_run'] and any(actor is root for root in self.roots))

    def started(self, frame):
        self.template.started(frame)
        if self._ours(frame):
            self.action_frames.add(id(frame))
            if frame.f_code is self.witness.codes['automation_action']:
                return  # native action job is a callback, not a coroutine
            task = asyncio.current_task()
            _need(task is not None and task is not self.witness.owner,
                  'automation_actual_action_task_required')
            self.action_frames.add(id(frame))
            if task not in self.action_tasks:
                self.action_tasks.add(task)
                task.add_done_callback(self._done)

    def _done(self, task):
        if task.cancelled() or task.exception() is not None:
            self.witness._poison('automation_native_action_failed')
        self.witness._signal()

    def ended(self, frame, failed):
        self.template.ended(frame, failed)
        if id(frame) in self.action_frames:
            self.action_frames.remove(id(frame))
            if failed:
                self.witness._poison('automation_native_action_unwound')
            self.witness._signal()

    def pending_actions(self):
        for task in self.action_tasks:
            if task.done() and (task.cancelled() or task.exception() is not None):
                self.witness._poison('automation_native_action_failed')
                _need(False, 'automation_native_action_failed')
        if self.action_frames or any(not task.done() for task in self.action_tasks):
            return True
        # A Task's exact initial coroutine frame proves a queued native action
        # even before PY_START. No all_tasks/sleep guess and no Handle execution.
        for handle in self.witness.loop._ready:
            if handle.cancelled():
                continue
            task = getattr(handle._callback, '__self__', None)
            if type(task) is asyncio.Task:
                coro = task.get_coro()
                frame = getattr(coro, 'cr_frame', None)
                if frame is not None and self._ours(frame):
                    if task not in self.action_tasks:
                        self.action_tasks.add(task)
                        task.add_done_callback(self._done)
                    return True
        return False

    def pending(self):
        return self.template.pending() or self.pending_actions()

    async def drain(self):
        while True:
            self.witness._owner_guard()
            self.witness._check()
            self.witness.wake = self.witness.loop.create_future()
            if not self.pending():
                return
            await self.witness.wake

    def retire_at_final_fence(self):
        _need(self.live and not self.pending(), 'automation_new_pending_producer')
        self.template.retire_at_final_fence()
        # Keep action observation active through OWN delivery and native action
        # completion; the witness closes the shared monitor after finish.
        self.live = False
