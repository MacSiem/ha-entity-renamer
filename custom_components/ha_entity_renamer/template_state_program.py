"""Private read-only v3 full configuration program, never an effect grant.

The adjunct is produced INSIDE the same native checked-config invocation as v3.
Legacy profiles (including v3) retain their byte representation. No native
Template is constructed, rendered or evaluated by this module.
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass, asdict
import hashlib
import json


def _need(value, reason='template_state_program_rejected'):
    if not value:
        raise ValueError(reason) from None


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _digest(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def _pairs(value):
    _need(type(value) is dict and set(value) == {'$map'} and type(value['$map']) is list)
    rows = value['$map']
    _need(all(type(row) is list and len(row) == 2 and type(row[0]) is str for row in rows)
          and len({row[0] for row in rows}) == len(rows))
    return rows


def _get(value, key, default=None):
    return next((v for k, v in _pairs(value) if k == key), default)


def native_state_descriptors(checked, root, *, source_paths):
    """Worker-only adjunct of the exact native schema result supplied to v3."""
    from .yaml_validation_worker import (_normal, _template_entity_keys,
        full_template_state_semantics, _template_source_projection)
    # Full normalized descriptors would otherwise serialize expanded !secret
    # values. The existing reference-only secret policy must survive this
    # private sidecar. Staging is already a bounded, pinned input bundle; no
    # live configuration is rediscovered here. Refuse this descriptor scope
    # before returning any expanded configuration to the parent process.
    from pathlib import Path
    import yaml
    from yaml.nodes import MappingNode, SequenceNode
    def secret(node, ancestry=()):
        if node is None or id(node) in ancestry:
            return False
        if node.tag in ('!secret', '!env_var'):
            return True
        ancestry += (id(node),)
        if isinstance(node, MappingNode):
            return any(secret(key, ancestry) or secret(value, ancestry) for key, value in node.value)
        if isinstance(node, SequenceNode):
            return any(secret(value, ancestry) for value in node.value)
        return False
    from .yaml_plan import _relative
    _need(type(source_paths) is tuple and bool(source_paths))
    for name in source_paths:
        source = Path(root) / _relative(name)
        _need(not any(secret(node) for node in yaml.compose_all(source.read_bytes())),
              'template_state_config_program_secret_scope_unverified')
    rows = []
    for block in checked.get('template', ()):
        if 'trigger' in block or 'triggers' in block:
            continue
        for platform in ('sensor', 'binary_sensor'):
            for entity, key in zip(block.get(platform, ()),
                    _template_entity_keys(block, platform, root, full_state=True), strict=True):
                rows.append(['template.' + platform, key,
                    _normal(full_template_state_semantics(block, platform, entity))])
    return {'profile': 'template_state_config_program_v1',
            'rows': sorted(rows, key=lambda r: (r[0], r[1])),
            'inventory': _normal(_template_source_projection(checked, root, full_state=True))}


@dataclass(frozen=True)
class StateConfigProgram:
    original_sha256: str
    candidate_sha256: str
    original_json: bytes
    candidate_json: bytes
    mapping: tuple[tuple[str, str], ...]
    physical_keys: tuple[tuple[str, str], ...]
    source_locations: tuple[tuple[str, str, str, int, str], ...]
    variable_locators_json: bytes

    @property
    def sha256(self):
        return _digest([self.original_sha256, self.candidate_sha256,
            self.original_json.decode(), self.candidate_json.decode(), self.mapping, self.physical_keys,
            self.source_locations, self.variable_locators_json.decode()])

    @property
    def migration_ready(self):
        return False

    def assert_sources(self, original, candidate, *, plan, mapping):
        """Revalidate a decoded adjunct; a dataclass/JSON is never a live seal."""
        _need(seal_state_config_program(original, candidate,
            json.loads(self.original_json), json.loads(self.candidate_json),
            mapping=mapping, plan=plan) == self,
            'template_state_program_original_changed')

    def rows(self, active):
        """Rebuild from SAME ORIGINAL every time, including a fresh reverse."""
        mapping = dict(self.mapping)
        _need(type(active) is frozenset and active <= set(mapping))
        return tuple((domain, key, *_project(semantic, mapping, active))
                     for domain, key, semantic in json.loads(self.original_json)['rows'])


def seal_state_config_program(original, candidate, before, after, *, mapping, plan):
    from .template_source_evidence import (OriginalTemplateStateSources,
        CandidateTemplateStateSources)
    _need(type(original) is OriginalTemplateStateSources
          and type(candidate) is CandidateTemplateStateSources
          and candidate.original_sources_sha256 == original.sha256
          and original.source_matched and candidate.sources.source_matched)
    from .yaml_plan import Plan
    _need(type(plan) is Plan and plan.ready)
    locators = tuple(getattr(plan, 'template_variable_bindings', ()))
    _need(all(row.approved and row.before in mapping and row.after == mapping[row.before]
              for row in locators), 'template_state_variable_locator_unverified')
    _need(type(mapping) is dict and list(sorted(mapping.items())) == [tuple(r) for r in
        json.loads(original.native_json)['reference_proof']['mapping']])
    for source, descriptor in ((original, before), (candidate.sources, after)):
        _need(type(descriptor) is dict and set(descriptor) == {'profile', 'rows', 'inventory'}
              and descriptor['profile'] == 'template_state_config_program_v1')
        rows = descriptor['rows']
        _need(type(rows) is list and [[r[0], r[1]] for r in rows] ==
              [r[:2] for r in json.loads(source.native_json)['states']])
        _need([[r[0], r[1], _digest(r)] for r in descriptor['inventory']] ==
              json.loads(source.native_json)['rows'])
        expected = {(r[0], r[1]): r[2] for r in json.loads(source.native_json)['states']}
        for domain, key, semantic in rows:
            _need(type(semantic) is list and len(semantic) == 4
                  and semantic[:2] == ['template_state_entity_v3', domain.removeprefix('template.')]
                  and _digest(semantic) == expected[domain, key])
            _pairs(semantic[2]); _pairs(semantic[3])
    # Every unsupported family stays visible; any proposed change refuses.
    state_keys = {(r[0], r[1]) for r in before['rows']}
    before_unsupported = [r for r in before['inventory']
        if (r[0], r[1]) not in state_keys and r[0] != 'template.state']
    after_unsupported = [r for r in after['inventory']
        if (r[0], r[1]) not in state_keys and r[0] != 'template.state']
    _need(before_unsupported == after_unsupported, 'template_state_affected_unsupported_family')
    from .template_physical_identity import is_physical_definition
    physical = tuple(sorted((d.domain, d.native_key) for d in original.ledger.definitions
                            if is_physical_definition(d)))
    locations = tuple(sorted((d.domain, d.native_key, d.origin.path,
        d.origin.start_line, d.source_ast_sha256) for d in original.ledger.definitions
        if (d.domain, d.native_key) in state_keys))
    _need(len(locations) == len(state_keys))
    program = StateConfigProgram(original.sha256, candidate.sha256,
        _bytes(before), _bytes(after), tuple(sorted(mapping.items())), physical,
        locations, _bytes([asdict(row) for row in locators]))
    projected = [[d, k, semantic] for d, k, semantic, _ in program.rows(frozenset(mapping))]
    _need(projected == after['rows'], 'template_state_program_candidate_mismatch')
    return program


def _project(semantic, mapping, active):
    """Planner dataflow owns every source edit; ordinary equal strings stay data.

    External binding origins are negative planner coordinates. Whole-output
    aliases alone carry the producer. Snapshot expressions are never aliases.
    Static producer admission needs a real sink and zero unsafe use across the
    complete entity, including context/variables/metadata.
    """
    from .reference_planner import analyze_template_alias, analyze_template_bindings
    from .yaml_plan import _ENTITY
    result = deepcopy(semantic)
    chosen = {old: new for old, new in mapping.items() if old in active}
    bindings, producers, aliases, layouts, used, unsafe = {}, {}, {}, {}, set(), set()
    counter = 0
    def origin(value):
        nonlocal counter
        if type(value) is str and _ENTITY.fullmatch(value):
            counter += 1
            token = (-counter, -counter, value)
            if value in mapping:
                producers[token] = value
            return ('scalar', frozenset({token}))
        if type(value) is list:
            children = [origin(v) for v in value]
            if children and all(child and child[0] == 'scalar' for child in children):
                return ('list', frozenset(t for child in children for t in child[1]))
        if type(value) is dict and set(value) == {'$map'}:
            children = [origin(key) for key, _ in _pairs(value)]
            if children and all(child for child in children):
                return ('keys', frozenset(t for child in children for t in child[1]))
        return None
    def layout(value):
        if type(value) is str and _ENTITY.fullmatch(value):
            return ('id', value)
        if type(value) is list:
            parts = [layout(child) for child in value]
            return ('list', parts) if all(part is not None for part in parts) else None
        if type(value) is dict and set(value) == {'$map'}:
            return ('map', [(key, deepcopy(child)) for key, child in _pairs(value)])
        return None
    def alias_layout(text):
        from jinja2 import Environment, nodes
        tree = Environment().parse(text)
        if len(tree.body) != 1 or not isinstance(tree.body[0], nodes.Output) or len(tree.body[0].nodes) != 1:
            return None
        def expression(node):
            if isinstance(node, nodes.Name):
                return layouts.get(node.name)
            if isinstance(node, (nodes.List, nodes.Tuple)):
                parts = [expression(child) for child in node.items]
                return ('list', parts) if all(part is not None for part in parts) else None
            if isinstance(node, nodes.Filter) and node.name == 'list' and not (node.args or node.kwargs):
                return expression(node.node)
            return None
        return expression(tree.body[0].nodes[0])
    # Native normalized entity variables include inherited block variables.
    # A retained context copy must be congruent; it is projected independently.
    cfg = result[3]
    entity_variables = _get(cfg, 'variables')
    context_variables = _get(result[2], 'variables')
    variables = entity_variables if entity_variables is not None else context_variables
    if variables is not None:
        raw = (variables.get('$variables', variables) if type(variables) is dict else None)
        _need(raw is not None, 'template_state_variables_descriptor_required')
        for name, value in _pairs(raw):
            bound = origin(value)
            shape = layout(value)
            if type(value) is dict and set(value) == {'$template'}:
                bound = analyze_template_alias(value['$template'], bindings)
                shape = alias_layout(value['$template']) if bound else None
            if bound:
                _need(shape is not None, 'template_state_static_alias_layout_unverified')
                bindings[name] = bound
                aliases[name] = bound
                layouts[name] = shape
    def visit(value, path):
        if type(value) is dict and set(value) == {'$template'}:
            text, markers, sinks, bad = analyze_template_bindings(
                value['$template'], chosen, bindings,
                output_reference=bool(path and path[-1] in aliases
                    and 'variables' in path))
            # The reference planner supplies exact spans; never str.replace.
            _need(not markers, 'template_state_reference_scope_unverified')
            used.update(sinks); unsafe.update(bad)
            return {'$template': text}
        if type(value) is dict and set(value) == {'$map'}:
            return {'$map': [[key, visit(child, path + (key,))] for key, child in _pairs(value)]}
        if type(value) is dict and set(value) == {'$variables'}:
            return {'$variables': visit(value['$variables'], path + ('$variables',))}
        if type(value) is list:
            return [visit(child, path + (i,)) for i, child in enumerate(value)]
        return deepcopy(value)
    result = visit(result, ())
    qualified = {token for token in used if token not in unsafe}
    _need(all(token in qualified for token in producers),
          'template_state_variable_producer_unverified')
    static_values = {}
    def materialize(shape):
        kind, value = shape
        if kind == 'id':
            return chosen.get(value, value)
        if kind == 'list':
            return [materialize(child) for child in value]
        if kind == 'map':
            keys = [chosen.get(key, key) for key, _ in value]
            _need(len(keys) == len(set(keys)), 'template_state_variable_map_collision')
            return {chosen.get(key, key): deepcopy(child) for key, child in value}
        _need(False, 'template_state_variable_layout_unverified')
    for name in aliases:
        static_values[name] = materialize(layouts[name])
    # Rewrite only proven producer variable locations. Alias Template bodies
    # remain syntax and may be identical at both prefixes.
    original_variables = (_get(semantic[3], 'variables') if entity_variables is not None
                          else _get(semantic[2], 'variables'))
    next_variables = (_get(result[3], 'variables') if entity_variables is not None
                      else _get(result[2], 'variables'))
    if original_variables is not None:
        before_rows = _pairs(original_variables.get('$variables', original_variables))
        after_rows = _pairs(next_variables.get('$variables', next_variables))
        for (name, original_value), row in zip(before_rows, after_rows, strict=True):
            if name in aliases and not (type(original_value) is dict and '$template' in original_value):
                value = static_values[name]
                row[1] = {'$map': [[k, v] for k, v in value.items()]} if type(value) is dict else value
    # Block defaults are full source context, even when the native constructor
    # retains only the effective entity copy. Keep them congruent for hashing.
    block_vars = _get(result[2], 'variables')
    if block_vars is not None and entity_variables is not None:
        _need(_get(semantic[2], 'variables') == variables,
              'template_state_variable_context_projection_unverified')
        for row in _pairs(result[2]):
            if row[0] == 'variables':
                row[1] = deepcopy(next_variables)
    return result, tuple(sorted(static_values.items()))
