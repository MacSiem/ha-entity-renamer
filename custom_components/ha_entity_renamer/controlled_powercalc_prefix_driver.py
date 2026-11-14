"""One-shot inverse of a receipted, inactive Powercalc registry prefix."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal

from .native_registry_receipts import _digest, _event_id
from .transaction import Coordinator, Position, Refused


def _need(value):
    if not value:
        raise Refused('controlled_powercalc_prefix_inverse_unverified')


async def _activate(root, handoff, record):
    """Reload original YAML once under its own intent, then prove native graph."""
    from homeassistant.core import Context
    from .controlled_powercalc_handoff import _settle_yaml_discovery, _watch_restored_counters
    from .controlled_powercalc_setup_receipts import validate_handoff
    from .native_powercalc_accepted_consumers import async_observe_accepted_powercalc_consumers
    from .native_template_powercalc_ingress import PowercalcTemplateIngress
    from .powercalc_runtime_observer import capture_powercalc_runtime

    root._scope()
    handoff.require_inactive()
    _need(await root.journal.load(root.operation_id) == record)
    state = root.chain.reconstruct_inputs()
    forward = validate_handoff(root.chain, list(handoff.receipt), 'forward')
    key = _event_id(['controlled_pc_prefix_setup', root.chain.root_id, 'intent'])
    receipt_key = _event_id(['controlled_pc_prefix_setup', root.chain.root_id, 'receipt'])
    _need(root.chain.store.read(key) is None and root.chain.store.read(receipt_key) is None)
    intent = {'kind': 'controlled_powercalc_prefix_setup_intent', 'schema': 1,
              'root': root.chain.root_id, 'operation_id': root.operation_id,
              'fingerprint': root.plan.fingerprint, 'source_head': state.head,
              'store_inverse': list(root.prefix_store_receipt), 'handoff': list(handoff.receipt),
              'phases': list(record.phases)}
    reference = await root.chain.store.create(key, intent)
    root._scope()
    _need(await root.journal.load(root.operation_id) == record
          and root.chain.reconstruct_inputs().same_evidence(state))
    await root.refresh()
    root._scope()
    handoff.require_inactive()
    _need(await root.journal.load(root.operation_id) == record
          and root.chain.store.read(key) == {'digest': reference[1], 'payload': intent}
          and root.hass.services.has_service('powercalc', 'reload'))
    # The forward receipt describes transformed destination IDs. The physical
    # store inverse restored each counter to the receipt's original ID.
    counters = [dict(row, after_id=row['before_id']) for row in forward['population']]
    restored, stop = _watch_restored_counters(root.hass, counters)
    ingress = PowercalcTemplateIngress.install(handoff)
    try:
        await root.hass.services.async_call('powercalc', 'reload', {}, blocking=True, context=Context())
        await _settle_yaml_discovery(root.hass)
        await ingress.settle()
    finally:
        try:
            ingress.close()
        finally:
            stop()
    root._scope()
    _need(await root.journal.load(root.operation_id) == record
          and root.chain.reconstruct_inputs().same_evidence(state)
          and set(restored) == {row['before_id'] for row in forward['population']})
    for row in forward['population']:
        seen = restored[row['before_id']]
        expected = (row['payload']['full_total'] if row['kind'] == 'group'
                    else row['payload']['capture']['full_state_text'])
        _need(Decimal(seen['value']).as_tuple() == Decimal(expected).as_tuple())
        if row['kind'] != 'group':
            _need(Decimal(seen['last_valid']).as_tuple() ==
                  Decimal(row['payload']['capture']['last_valid_state_text']).as_tuple())
    captured = capture_powercalc_runtime(root.hass, guard=root._scope)
    accepted = await async_observe_accepted_powercalc_consumers(
        root.hass, root.chain, root.evidence, captured, guard=root._scope)
    expected = root.composition.population
    _need(accepted.source_position == 'original'
          and tuple(sorted((row.entity_id, row.registry_id, row.relation_kind)
                           for row in accepted.rows)) == expected
          and all(row.source_verified for row in accepted.rows))
    await root._common(True)
    root._scope()
    _need(await root.journal.load(root.operation_id) == record
          and root.chain.reconstruct_inputs().same_evidence(state))
    result = {'kind': 'controlled_powercalc_prefix_setup_receipt', 'schema': 1,
              'root': root.chain.root_id, 'intent': list(reference), 'source_head': state.head,
              'population': [list(row) for row in expected],
              'restored': [restored[row['before_id']] for row in forward['population']]}
    saved = await root.chain.store.create(receipt_key, result)
    root._scope()
    _need(root.chain.store.read(receipt_key) == {'digest': _digest(result), 'payload': result}
          and await root.journal.load(root.operation_id) == record)
    return saved


class _PrefixInverse(Coordinator):
    def __init__(self, base, root, handoff):
        super().__init__(base.adapters, root.journal, root._scope,
                         required_scopes=root.plan.required_scopes)
        self.root, self.handoff = root, handoff

    async def rollback(self, operation_id):
        async with self.lock:
            record = await self._load(operation_id)
            _need(record == self.root.record and record.restore_attempted is True)
            from .native_template_prefix_recovery import acknowledged_powercalc_registry_prefix
            _need(acknowledged_powercalc_registry_prefix(record))
            count = record.phases.count('verified')
            record = await self._save(replace(record, direction='rollback', status='rolling_back'))
            for index in reversed(range(count)):
                step = record.plan.steps[index]
                adapter = self._adapter(step, record, index)
                if await adapter.inspect(step) is not Position.AFTER:
                    return await self._unknown(record, 'prefix_inverse_revision_conflict')
                record = await self._phase(record, index, 'undo_intent', 'rolling_back')
                try:
                    adapter = self._adapter(step, record, index)
                    await adapter.compensate(step, self.guard)
                    if await adapter.inspect(step) is not Position.BEFORE:
                        return await self._unknown(record, 'prefix_inverse_unverified')
                except asyncio.CancelledError:
                    raise
                except Exception:
                    return await self._unknown(record, 'prefix_inverse_uncertain')
                record = await self._phase(record, index, 'undone', 'rolling_back')
            try:
                await _activate(self.root, self.handoff, record)
            except asyncio.CancelledError:
                raise
            except Exception:
                return await self._unknown(record, 'prefix_setup_uncertain')
            return await self._save(replace(record, status='rolled_back', reason=''))


def prefix_inverse(base, root, handoff):
    _need(type(base) is Coordinator)
    return _PrefixInverse(base, root, handoff)
