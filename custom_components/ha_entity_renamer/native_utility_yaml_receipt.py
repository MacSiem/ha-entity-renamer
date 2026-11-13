"""Durable no-replay sidecar around a native Utility YAML source handoff.

The existing Coordinator owns the registry intent and original source fences.
This adds one immutable intent before detaching YAML consumers and a second
immutable receipt only after native factory/add/readback completes.
"""
from __future__ import annotations

import hashlib
import json

from .native_utility_yaml_handoff import UtilityYAMLHandoff, _static
from .transaction import PhaseContext, Position, Refused
from .utility_meter_observers import _plain


def _need(value, reason):
    if not value:
        raise Refused('utility_yaml_receipt_' + reason)


def _id(operation, fingerprint, step_key, direction, kind):
    raw = json.dumps([operation, fingerprint, step_key, direction, kind],
                     separators=(',', ':'), ensure_ascii=True).encode()
    return 'utility-yaml-' + hashlib.sha256(raw).hexdigest()


def _rows(rows):
    return [{'meter':row.meter, 'source':row.old, 'target':row.new,
             'source_uuid':row.source_uuid, 'entity_id':row.entity_id,
             'before':_plain(row.before), 'info':_plain(row.info)} for row in rows]


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

    def _receipt_valid(self, step, direction, intent_ref, receipt_ref):
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
        self.handoff.check_foreign()
        for saved, after in zip(receipt['rows'], receipt['after'], strict=True):
            row = actors.get(saved['meter'])
            _need(row is not None, 'receipt_population')
            entity = row.platform.entities.get(saved['entity_id'])
            info = self.handoff.data.get(saved['meter'])
            native = registry.async_get(saved['target'])
            _need(entity is not None and native is not None and native.id == saved['source_uuid']
                  and registry.async_get(saved['source']) is None
                  and entity._sensor_source_id == saved['target'] and entity._collecting is not None
                  and type(info) is dict and info.get('source') == saved['target']
                  and info.get(DATA_TARIFF_SENSORS) == [entity]
                  and _plain({k:v for k,v in info.items() if k != DATA_TARIFF_SENSORS})
                      == {**saved['info'], 'source': saved['target']}
                  and _plain(_static(entity)) == after == saved['before'], 'receipt_readback')
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

    async def _effect(self, step, guard, *, reverse):
        direction = 'rollback' if reverse else 'forward'
        _need(self.context is not None, 'context')
        self.context.require_effect(step, direction)
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
            nonlocal intent_digest
            _need(tuple(items) == rows, 'population')
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

    async def verify(self, plan): return await self.inner.verify(plan)
    async def verify_rollback(self, plan): return await self.inner.verify_rollback(plan)
