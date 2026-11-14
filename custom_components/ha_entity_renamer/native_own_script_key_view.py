"""Read-only sealed Script definition-key view across one native registry seam."""
from __future__ import annotations

from .transaction import Refused


def _need(value):
    if not value:
        raise Refused('own_script_key_view_unqualified') from None


def key_view(program, mask, fence, active):
    """Return the exact installed row image and ORIGINAL-ledger key redirects."""
    expected = program.rows(mask)
    if fence is None:
        return expected, ()
    from .native_registry_transition import _native_script_unique_move
    change = fence.change
    if not _native_script_unique_move(change.before, change.after):
        return expected, ()
    old, new = change.before['unique_id'], change.after['unique_id']
    entity = fence.entity
    live = entity.unique_id
    if entity.entity_id != 'script.' + live:
        # The synchronous native registry callback has bound the new row, but
        # its audited service handoff has not yet moved the Script key.
        import asyncio
        node = fence.node
        move = None if node is None else node.script_service_move
        handoff = None if move is None else move.handoff
        _need(move is not None and move.phase == 'captured' and asyncio.current_task() is move.task
              and node.root.chain.active is node.intent and node.phase == 'registry_attempted'
              and handoff.in_native and handoff.attempted and handoff.registry_calls == handoff.api_calls == 1
              and handoff.new_row is not None and handoff.source is entity
              and handoff.registry.async_get(handoff.new) is handoff.new_row
              and handoff.registry.async_get(handoff.old) is None
              and entity.registry_entry is handoff.new_row and entity.entity_id == handoff.new
              and live == handoff.row.unique_id)
    _need(live in (old, new))
    desired = new if next(iter(change.mapping)) in mask else old
    rows = dict(expected)
    names = [row[0] for row in rows['script']]
    _need(names.count(desired) == 1 and (new if desired == old else old) not in names)
    if live != desired:
        _need(type(active) is tuple and len(active) == 3)
        node, transfer, target = active
        _need(node.change == change and node.root.chain.active is node.intent
              and node.phase in ('captured', 'registry_attempted')
              and node.script_service_move is not None and transfer is not None
              and transfer.phase in ('installed', 'attempted') and mask == target
              and (node.source, node.target) in ((change.before['entity_id'], change.after['entity_id']),
                                                  (change.after['entity_id'], change.before['entity_id'])))
        rows['script'] = sorted(([live, *row[1:]] if row[0] == desired else row
                                 for row in rows['script']), key=lambda row: row[0])
        expected = tuple((domain, rows[domain]) for domain, _ in expected)
    moves = (('script', old, new),) if live == new else ()
    return expected, moves
