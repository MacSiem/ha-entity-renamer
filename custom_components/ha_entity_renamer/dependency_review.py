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
    for patch in composition.yaml_plan.patches:
        counts = {}
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
                # Never advertise a full review while silently omitting an edit.
                raise Refused("review_dependency_change_unavailable")
        if not counts:
            raise Refused("review_dependency_change_unavailable")
        files.append({"path": patch.path, "edit_count": len(patch.edits),
            "references": [{"before": old, "after": mapping[old], "occurrences": count}
                           for old, count in sorted(counts.items())]})
    return files
