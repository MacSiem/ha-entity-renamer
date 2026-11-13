"""Durable no-replay sidecar around a native Utility YAML source handoff.

The existing Coordinator owns the registry intent and original source fences.
This adds one immutable intent before detaching YAML consumers and a second
immutable receipt only after native factory/add/readback completes.
"""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from .native_utility_yaml_handoff import UtilityYAMLHandoff, _continued, _static
from .transaction import PhaseContext, Position, Refused
from .utility_meter_observers import _plain


def _need(value, reason):
    if not value:
        raise Refused('utility_yaml_receipt_' + reason)


def _id(operation, fingerprint, step_key, direction, kind):
    raw = json.dumps([operation, fingerprint, step_key, direction, kind],
                     separators=(',', ':'), ensure_ascii=True).encode()
    return 'utility-yaml-' + hashlib.sha256(raw).hexdigest()


def _info(value):
    # Core's validated YAML schema supplies offset as timedelta; preserve its
    # exact integer components instead of stringifying an arbitrary object.
    _need(type(value) is dict, 'info')
    # Native YAML parser preserves source positions in string subclasses.
    # Normalize those keys without admitting non-string or colliding keys.
    _need(all(isinstance(key, str) for key in value), 'info_key')
    result = {str(key): item for key, item in value.items()}
    _need(len(result) == len(value), 'info_key_collision')
    offset = result.get('offset')
    if isinstance(offset, timedelta):
        result['offset'] = {'days': offset.days, 'seconds': offset.seconds,
                            'microseconds': offset.microseconds}
    return _plain(result)


def _rows(rows):
    return [{'meter':row.meter, 'source':row.old, 'target':row.new,
             'source_uuid':row.source_uuid, 'entity_id':row.entity_id,
             'before':_plain(row.before), 'info':_info(row.info)} for row in rows]


class UtilityYAMLRegistryAdapter:
    """Wrap exactly one registry step; all other Coordinator adapters unchanged."""
    def __init__(self, inner, handoff, preparations, resolver, operation_id, fingerprint, guard):
        _need(type(handoff) is UtilityYAMLHandoff and callable(resolver) and callable(guard), 'binding')
        self.inner, self.handoff, self.preparations, self.resolver = inner, handoff, preparations, resolver
        self.operation_id, self.fingerprint, self.guard = operation_id, fingerprint, guard
        self.context = None

    def bind_phase_context(self, context):
        _need(context is None or type(context) is PhaseContext, 'context')
        self.context = context
        fn = getattr(self.inner, 'bind_phase_context', None)
        if fn is not None: fn(context)

    def _refs(self, step, direction):
        return tuple(_id(self.operation_id, self.fingerprint, step.key, direction, kind)
                     for kind in ('intent','receipt'))

    def _mapping(self, step, reverse):
        from .native_registry import PreparedRegistryChange
        change = self.resolver(step.prepared_ref)
        _need(type(change) is PreparedRegistryChange and change.identity == step.identity, 'step')
        mapping = change.mapping
        return {new:old for old,new in mapping.items()} if reverse else dict(mapping)

    def _receipt_valid(self, step, direction, intent_ref, receipt_ref, *, ignore_sources=frozenset()):
        """Reopen both immutable records and independently sample native actors."""
        from homeassistant.components.utility_meter.const import DATA_TARIFF_SENSORS
        from homeassistant.helpers import entity_registry as er
        records = self.preparations.plans
        intent_row, receipt_row = records[intent_ref], records[receipt_ref]
        intent = self.preparations.resolve(intent_ref, intent_row['digest'])
        receipt = self.preparations.resolve(receipt_ref, receipt_row['digest'])
        expected = {'operation': self.operation_id, 'fingerprint': self.fingerprint,
                    'step': step.key, 'direction': direction}
        _need(intent.get('kind') == 'utility_yaml_intent'
              and receipt.get('kind') == 'utility_yaml_receipt'
              and all(intent.get(k) == v and receipt.get(k) == v for k, v in expected.items())
              and receipt.get('intent') == [intent_ref, intent_row['digest']]
              and receipt.get('rows') == intent.get('rows')
              and type(receipt.get('rows')) is list and bool(receipt['rows'])
              and len(receipt.get('after', ())) == len(receipt['rows']), 'receipt_binding')
        registry = er.async_get(self.handoff.hass)
        actors = {row.meter: row for row in self.handoff.rows}
        self.handoff.check_population(ignore_sources=ignore_sources)
        for saved, after in zip(receipt['rows'], receipt['after'], strict=True):
            row = actors.get(saved['meter'])
            _need(row is not None, 'receipt_population')
            expected_source, expected_target = ((row.old, row.new) if direction == 'forward'
                                                else (row.new, row.old))
            _need(saved['source'] == expected_source and saved['target'] == expected_target
                  and saved['source_uuid'] == row.source_uuid and saved['entity_id'] == row.entity_id
                  and saved['info'] == {**_info(row.info), 'source': expected_source}
                  and (direction != 'forward' or saved == _rows((row,))[0]), 'receipt_original_binding')
            entity = row.platform.entities.get(saved['entity_id'])
            info = self.handoff.data.get(saved['meter'])
            native = registry.async_get(saved['target'])
            _need(entity is not None and native is not None and native.id == saved['source_uuid']
                  and registry.async_get(saved['source']) is None
                  and entity._sensor_source_id == saved['target'] and entity._collecting is not None
                  and type(info) is dict and info.get('source') == saved['target']
                  and info.get(DATA_TARIFF_SENSORS) == [entity]
                  and _info({k:v for k,v in info.items() if k != DATA_TARIFF_SENSORS})
                      == {**saved['info'], 'source': saved['target']}
                  and after == saved['before']
                  and (_continued(row.before, _static(entity), entity,
                                  self.handoff.hass.states.get(saved['target']))
                       if direction == 'forward' else _plain(_static(entity)) == after), 'receipt_readback')
        return True

    async def inspect(self, step):
        try:
            completed = []
            for direction in ('forward', 'rollback'):
                intent, receipt = self._refs(step, direction)
                if intent in self.preparations.plans and receipt not in self.preparations.plans:
                    return Position.UNKNOWN
                if receipt in self.preparations.plans:
                    if intent not in self.preparations.plans:
                        return Position.UNKNOWN
                    completed.append((direction, intent, receipt))
            if completed:
                self._receipt_valid(step, *completed[-1])
            return await self.inner.inspect(step)
        except Exception:
            return Position.UNKNOWN

    def _completed_guard(self, current_step):
        """Keep every previously completed Utility actor fenced across awaits."""
        _need(self.context is not None and self.preparations.plans is not None, 'context')
        ignore_sources = frozenset(self._mapping(current_step, False))
        for candidate in self.context.record.plan.steps:
            if candidate.adapter != 'registry' or candidate == current_step:
                continue
            for direction in ('rollback', 'forward'):
                intent, receipt = self._refs(candidate, direction)
                if receipt in self.preparations.plans:
                    _need(intent in self.preparations.plans, 'receipt_binding')
                    self._receipt_valid(candidate, direction, intent, receipt, ignore_sources=ignore_sources)
                    break
                _need(intent not in self.preparations.plans, 'unresolved_intent')

    async def _effect(self, step, guard, *, reverse):
        direction = 'rollback' if reverse else 'forward'
        _need(self.context is not None, 'context')
        self.context.require_effect(step, direction)
        original_guard = guard
        def guarded():
            original_guard(); self.guard(); self._completed_guard(step)
        guard = guarded
        guard()
        mapping = self._mapping(step, reverse)
        if not mapping:
            return await (self.inner.compensate(step, guard) if reverse else self.inner.apply(step, guard))
        _need(len(mapping) == 1, 'mapping')
        old, new = next(iter(mapping.items()))
        if reverse:
            # A fresh inverse must bind current live postimage. Existing forward
            # sidecars are checked by the normal completed Coordinator record.
            handoff = UtilityYAMLHandoff.capture(self.handoff.hass, {old:new})
        else:
            handoff = self.handoff
        rows = tuple(row for row in handoff.rows if row.old == old and row.new == new)
        if not rows:
            return await (self.inner.compensate(step, guard) if reverse else self.inner.apply(step, guard))
        intent_ref, receipt_ref = self._refs(step, direction)
        _need(intent_ref not in self.preparations.plans and receipt_ref not in self.preparations.plans, 'no_replay')
        intent_digest = None
        async def record_intent(items):
            nonlocal intent_digest, rows
            _need(len(items) == len(rows) and all(
                (current.meter, current.old, current.new, current.entity_id, current.source_uuid,
                 current.platform, current.entity, current.info) ==
                (previous.meter, previous.old, previous.new, previous.entity_id, previous.source_uuid,
                 previous.platform, previous.entity, previous.info)
                for current, previous in zip(items, rows, strict=True)), 'population')
            rows = tuple(items)
            guard(); self.guard()
            payload = {'kind':'utility_yaml_intent', 'operation':self.operation_id,
                       'fingerprint':self.fingerprint, 'step':step.key, 'direction':direction,
                       'rows':_rows(items)}
            intent_digest = await self.preparations.create(intent_ref, payload)
            guard(); self.guard()
        await handoff.detach_group(old, guard=guard, record_intent=record_intent)
        result = await (self.inner.compensate(step, guard) if reverse else self.inner.apply(step, guard))
        restored = await handoff.attach_group(old, guard=guard)
        _need(len(restored) == len(rows) and all(_static(entity) == row.before
              for entity,row in zip(restored, rows, strict=True)), 'readback')
        guard(); self.guard()
        payload = {'kind':'utility_yaml_receipt', 'intent':[intent_ref,intent_digest],
                   'operation':self.operation_id, 'fingerprint':self.fingerprint,
                   'step':step.key, 'direction':direction,
                   'rows':_rows(rows), 'after':[_plain(_static(entity)) for entity in restored]}
        await self.preparations.create(receipt_ref, payload)
        guard(); self.guard()
        return result

    async def apply(self, step, guard):
        return await self._effect(step, guard, reverse=False)

    async def compensate(self, step, guard):
        return await self._effect(step, guard, reverse=True)

    async def _verify(self, plan, direction):
        try:
            if await (self.inner.verify(plan) if direction == 'forward'
                      else self.inner.verify_rollback(plan)) is not True:
                return False
            required = {row.old for row in self.handoff.rows}
            observed = set()
            for step in plan.steps:
                if step.adapter != 'registry':
                    continue
                mapping = self._mapping(step, False)
                for source, target in mapping.items():
                    if source not in required:
                        continue
                    _need(source not in observed and self.handoff.mapping.get(source) == target,
                          'terminal_population')
                    intent, receipt = self._refs(step, direction)
                    self._receipt_valid(step, direction, intent, receipt)
                    observed.add(source)
            _need(observed == required, 'terminal_population')
            self.guard()
            return True
        except Exception:
            return False

    async def verify(self, plan):
        return await self._verify(plan, 'forward')

    async def verify_rollback(self, plan):
        return await self._verify(plan, 'rollback')


def inspect_accepted(handoff, preparations, operation_id, plan, registry_changes):
    """Validate cutover receipts and current actors without freezing meter values.

    The receipt proves the exact counter/deadline at cutover. Current values may
    advance after that point; current native source, registry identity, meter
    configuration, actor population and subscription are independently checked.
    """
    _need(type(handoff) is UtilityYAMLHandoff, 'inspection_handoff')
    handoff.check_population()
    actors = {row.meter: row for row in handoff.rows}
    covered = set()
    changes = {change.identity: change for change in registry_changes}
    for step in plan.steps:
        if step.adapter != 'registry':
            continue
        change = changes.get(step.identity)
        _need(change is not None, 'inspection_registry_change')
        intent_ref = _id(operation_id, plan.fingerprint, step.key, 'forward', 'intent')
        receipt_ref = _id(operation_id, plan.fingerprint, step.key, 'forward', 'receipt')
        intent_row = preparations.plans.get(intent_ref)
        receipt_row = preparations.plans.get(receipt_ref)
        relevant = {row.meter for row in handoff.rows
                    if change.mapping.get(row.new) == row.old}
        if not relevant:
            _need(intent_row is None and receipt_row is None, 'inspection_unexpected_receipt')
            continue
        _need(intent_row is not None and receipt_row is not None, 'inspection_receipt_missing')
        intent = preparations.resolve(intent_ref, intent_row['digest'])
        receipt = preparations.resolve(receipt_ref, receipt_row['digest'])
        expected = {'operation': operation_id, 'fingerprint': plan.fingerprint,
                    'step': step.key, 'direction': 'forward'}
        _need(intent.get('kind') == 'utility_yaml_intent'
              and receipt.get('kind') == 'utility_yaml_receipt'
              and all(intent.get(k) == v and receipt.get(k) == v for k, v in expected.items())
              and receipt.get('intent') == [intent_ref, intent_row['digest']]
              and receipt.get('rows') == intent.get('rows')
              and type(receipt.get('rows')) is list
              and type(receipt.get('after')) is list
              and len(receipt['rows']) == len(receipt['after']), 'inspection_receipt_binding')
        names = set()
        for saved, cutover in zip(receipt['rows'], receipt['after'], strict=True):
            _need(type(saved) is dict and set(saved) == {'meter', 'source', 'target', 'source_uuid',
                  'entity_id', 'before', 'info'} and type(saved['before']) is list
                  and len(saved['before']) == 5 and saved.get('meter') in relevant, 'inspection_population')
            row = actors[saved['meter']]
            _need(saved['meter'] not in names and saved['meter'] not in covered
                  and saved.get('source') == row.new and saved.get('target') == row.old
                  and saved.get('source_uuid') == row.source_uuid
                  and saved.get('entity_id') == row.entity_id
                  and saved.get('info') == {**_info(row.info), 'source': row.new}
                  and saved.get('before') == cutover, 'inspection_cutover')
            names.add(saved['meter'])
        _need(names == relevant, 'inspection_population')
        covered.update(names)
    _need(covered == set(actors), 'inspection_population')
    handoff.check_population()
    return True
