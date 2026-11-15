"""Construct an isolated ConfigEntry candidate without touching HA state."""
from copy import deepcopy

from .helper_plan import snapshot_equal
from .helper_transaction import PreparedHelper
from .native_config_transition import _document, _projection, _target


def candidate(raw, version, change):
    """Return the full candidate document with exactly the prepared options delta.

    This is a scratch input, not an effect, receipt, or authority to reload.
    Both original and replacement are checked as typed whole snapshots.
    """
    if type(change) is not PreparedHelper:
        raise ValueError('controlled_entry_preparation_required')
    change.plan()
    before, after = change.before, change.after
    if (before['domain'] != 'template' or after['domain'] != 'template'
            or before['identity'] != after['identity'] or before['data'] != after['data']
            or before['subentries'] != after['subentries']):
        raise ValueError('controlled_entry_scope_unsupported')
    document = _document(raw, version)
    result = deepcopy(document)
    row = _target(result, before['identity']['entry_id'])
    if not snapshot_equal(_projection(row), before):
        raise ValueError('controlled_entry_original_changed')
    row['options'] = deepcopy(after['options'])
    if not snapshot_equal(_projection(row), after):
        raise ValueError('controlled_entry_candidate_changed')
    return result
