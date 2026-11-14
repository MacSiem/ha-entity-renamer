"""Narrow server-owned selection of one registered numeric Template entry."""
from .helper_transaction import prepare_helper
from .transaction import Refused
from .yaml_validation_worker import template_entry_semantic, _qualified_state_expression
from .helper_plan import snapshot_equal


def prepare_controlled_template_entry(plans, mapping, source_id):
    if (type(plans) is not tuple or type(mapping) is not dict or type(source_id) is not str
            or source_id not in mapping):
        raise Refused('controlled_template_entry_inventory_required')
    entries = [plan for plan in plans if plan.before.get('domain') == 'template']
    if len(entries) != 1:
        raise Refused('controlled_template_entry_one_numeric_required')
    plan = entries[0]
    if (plan.blocked or [change.path for change in plan.changes] != [('options', 'state')]):
        raise Refused('controlled_template_entry_unqualified')
    change = prepare_helper(plan.before, {source_id: mapping[source_id]})
    if not snapshot_equal(change.after, plan.after):
        raise Refused('controlled_template_entry_mapping_conflict')
    template_entry_semantic(change.before)
    template_entry_semantic(change.after)
    _qualified_state_expression(change.before['options']['state'])
    _qualified_state_expression(change.after['options']['state'])
    return change
