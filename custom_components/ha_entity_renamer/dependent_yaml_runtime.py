"""Task-local dependent-runtime phase checks, never mutation authorization.

A native effect adapter calls begin/complete around its actual awaited reload.
Expected bodies always come from the ORIGINAL checker receipt. Physical input,
durable intent, auth and native service ownership remain the adapter's work.
"""
from __future__ import annotations

import asyncio

from .transaction import Position, Refused
from .yaml_source_binding import _RuntimeSourceSampler


def _need(value):
    if not value:
        raise Refused('dependent_yaml_runtime_unconfirmed') from None


class DependentYAMLRuntime:
    def __init__(self, hass, evidence, observed, receipt, *, position):
        _need(type(position) is Position and position in (Position.BEFORE, Position.AFTER))
        self.hass, self.evidence, self.observed, self.receipt = hass, evidence, observed, receipt
        self.sampler = _RuntimeSourceSampler.bind(evidence, observed)
        self.positions = tuple((domain, position) for domain in ('automation', 'script'))
        self.active, self.unresolved = None, False
        self._assert_reload_idle()
        self.sample = self._sample(self.positions)
        _need(not self.sample[1])

    def _sample(self, positions):
        return self.sampler.sample_at_positions(self.hass, self.evidence, self.observed,
                                                self.receipt, positions)

    def _assert_reload_idle(self):
        # Readable inventory is not permission to reload every Script.
        if 'script' in self.receipt.changed_domains:
            from homeassistant.helpers.entity_component import DATA_INSTANCES
            component = self.hass.data.get(DATA_INSTANCES, {}).get('script')
            _need(component is None or all(not entity.script.is_running
                                          for entity in component.entities))

    def check(self):
        _need(not self.unresolved and self.active is None)
        self._assert_reload_idle()
        _need(self._sample(self.positions) == self.sample)

    def begin_reload(self, domain, direction):
        self.check()
        _need(asyncio.current_task() is not None
              and domain in ('automation', 'script') and domain in self.receipt.changed_domains
              and direction in ('forward', 'rollback'))
        expected = Position.BEFORE if direction == 'forward' else Position.AFTER
        _need(dict(self.positions)[domain] is expected)
        # Opaque task-local ticket, not an identifier accepted over any API.
        ticket = object()
        self.active = ticket, domain, direction, asyncio.current_task()
        return ticket

    def complete_reload(self, ticket):
        _need(not self.unresolved and self.active is not None
              and asyncio.current_task() is self.active[3] and self.active[0] is ticket)
        _, domain, direction, _ = self.active
        target = Position.AFTER if direction == 'forward' else Position.BEFORE
        positions = tuple((key, target if key == domain else value)
                          for key, value in self.positions)
        try:
            sample = self._sample(positions)
            _need(not sample[1])
            old_tokens = dict((row[0], row[1:]) for row in self.sample[2])
            new_tokens = dict((row[0], row[1:]) for row in sample[2])
            _need(all(new_tokens[key] == old_tokens[key] for key in old_tokens if key != domain))
            # Logical keys and registry UUIDs must survive this native reload.
            _need(sample[0] == self.sample[0])
        except BaseException:
            self.unresolved = True
            raise
        else:
            self.positions, self.sample = positions, sample
            self.active = None
            self.check()

    def abandon_reload(self, ticket):
        _need(self.active is not None and self.active[0] is ticket)
        self.unresolved = True
        self.active = None

    def begin_automation_transition(self):
        self.check()
        ticket = object()
        self.active = ticket, 'automation_activation', None, asyncio.current_task()
        return ticket

    def complete_automation_transition(self, ticket):
        _need(not self.unresolved and self.active is not None
              and asyncio.current_task() is self.active[3] and self.active[0] is ticket
              and self.active[1] == 'automation_activation')
        try:
            sample = self._sample(self.positions)
            _need(not sample[1] and sample[0] == self.sample[0])
            old = {row[0]: row[1:] for row in self.sample[2]}
            new = {row[0]: row[1:] for row in sample[2]}
            _need(all(new[key] == old[key] for key in old if key != 'automation'))
        except BaseException:
            self.unresolved = True
            raise
        self.sample, self.active = sample, None
        self.check()
