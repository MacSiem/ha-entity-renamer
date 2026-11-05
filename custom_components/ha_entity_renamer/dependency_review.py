"""Public dependency review derived from the retained server YAML plan.

Expose the exact changed files and reference substitutions, never source text,
expanded values, native keys or preparation capabilities. This projection does
not admit a plan or claim coverage of any additional consumer family.
"""
import re

from .transaction import Refused


def dependency_files(composition):
    mapping = dict(composition.mapping)
    if not mapping:
        raise Refused("review_dependency_change_unavailable")
    token = re.compile(rb"(?<![a-zA-Z0-9_.])(?:" + b"|".join(
        re.escape(old.encode("ascii")) for old in sorted(mapping, key=len, reverse=True))
        + rb")(?![a-zA-Z0-9_.])")
    files = []
    key_edits = None
    accounted_keys = set()
    for patch in composition.yaml_plan.patches:
        counts, definitions = {}, []
        for edit in patch.edits:
            if edit.before == edit.after:
                raise Refused("review_dependency_change_unavailable")
            changed = False
            for value in set(token.findall(edit.before)):
                old = value.decode("ascii")
                exact = re.compile(rb"(?<![a-zA-Z0-9_.])" + re.escape(value)
                                   + rb"(?![a-zA-Z0-9_.])")
                count = len(exact.findall(edit.before)) - len(exact.findall(edit.after))
                if count > 0:
                    counts[old] = counts.get(old, 0) + count
                    changed = True
            if not changed:
                # Only the exact source-ledger Script definition key may be
                # reported without a full entity-ID token. Another equal scalar
                # elsewhere remains an unaccounted edit and refuses review.
                if key_edits is None:
                    from .native_script_program_composition import ScriptTemplateStateComposition
                    from .native_automation_program_composition import AutomationTemplateStateComposition
                    from .dependent_config_program import verified_script_key_edits
                    if type(composition) not in (ScriptTemplateStateComposition,
                                                 AutomationTemplateStateComposition):
                        raise Refused("review_dependency_change_unavailable")
                    try:
                        key_edits = verified_script_key_edits(composition.evidence,
                                                              composition.yaml_plan, mapping)
                    except (ValueError, UnicodeError, AttributeError, KeyError, TypeError):
                        raise Refused("review_dependency_change_unavailable") from None
                identity = (patch.path, edit.start, edit.end, edit.before, edit.after)
                matches = [old for old, coordinate in key_edits.items() if coordinate == identity]
                if len(matches) != 1 or matches[0] in accounted_keys:
                    raise Refused("review_dependency_change_unavailable")
                old = matches[0]
                accounted_keys.add(old)
                definitions.append({"kind": "script_key", "before": old.split('.', 1)[1],
                                    "after": mapping[old].split('.', 1)[1], "occurrences": 1})
        references = [{"before": old, "after": mapping[old], "occurrences": count}
                      for old, count in sorted(counts.items())] + definitions
        if not references:
            raise Refused("review_dependency_change_unavailable")
        files.append({"path": patch.path, "edit_count": len(patch.edits),
                      "references": references})
    return files


def dependency_dashboards(changes, mapping):
    """Expose only exact reviewed route and ID substitutions, not card bodies."""
    from .dashboard_plan import plan_dashboard
    rows = []
    for change in changes:
        plan = plan_dashboard(change.before.config, mapping)
        if plan.markers or plan.value != change.after or not plan.changes:
            raise Refused('review_dashboard_change_unavailable')
        counts = {}
        for edit in plan.changes:
            accounted = False
            for old, new in mapping.items():
                token = re.compile(r'(?<![A-Za-z0-9_.])' + re.escape(old) + r'(?![A-Za-z0-9_.])')
                count = len(token.findall(edit.before)) - len(token.findall(edit.after))
                if count > 0:
                    counts[old] = counts.get(old, 0) + count
                    accounted = True
            if not accounted:
                raise Refused('review_dashboard_change_unavailable')
        route = change.before.dashboard.route
        if route is not None and (type(route) is not str or not route):
            raise Refused('review_dashboard_change_unavailable')
        rows.append({'route': route, 'references': [
            {'before': old, 'after': mapping[old], 'occurrences': count}
            for old, count in sorted(counts.items())]})
    return rows
