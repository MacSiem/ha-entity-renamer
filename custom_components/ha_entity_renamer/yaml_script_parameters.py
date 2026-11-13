"""Source-local proof for arguments to a statically named native Script service."""

from collections import defaultdict

from yaml.nodes import MappingNode, ScalarNode, SequenceNode

_SCRIPT_CONTROLS = frozenset({'sequence', 'action', 'actions', 'choose', 'default',
                              'if', 'then', 'else', 'repeat', 'parallel'})


def _fields(node):
    if isinstance(node, MappingNode):
        keys = [key.value for key, _ in node.value if isinstance(key, ScalarNode)]
        if len(keys) == len(node.value) == len(set(keys)) and '<<' not in keys:
            return {key.value: value for key, value in node.value}
    return {}


def prove_script_parameters(documents, mapping, analyze, max_nodes):
    """Bind declared parameters and authorize only arguments with a sink proof."""
    from .yaml_plan import _ENTITY, _STR, YAMLPlanError

    definitions, actors = defaultdict(list), []
    budget = max_nodes

    def charge():
        nonlocal budget
        budget -= 1
        if budget < 0:
            raise YAMLPlanError('Script argument proof work limit')

    def discover(node, context='', named=None):
        charge()
        context = context.split(' ', 1)[0]
        values = _fields(node)
        if context == 'automation':
            if isinstance(node, SequenceNode):
                for actor in node.value:
                    charge()
                    actors.append(actor)
            elif values:
                actors.append(node)
        elif context == 'script':
            for key, body in ({named: node} if named else values).items():
                if 'sequence' in _fields(body):
                    definitions['script.' + key].append(body)
                    actors.append(body)
        else:
            for key, value in values.items():
                kind = key.split(' ', 1)[0]
                if context == 'packages':
                    discover(value)
                elif context == 'homeassistant' and key == 'packages':
                    discover(value, 'packages')
                elif not context and kind in ('automation', 'script', 'homeassistant'):
                    discover(value, kind)

    for _, (tree, context) in documents.items():
        discover(tree, context)

    def calls(node, ancestry=()):
        charge()
        if id(node) in ancestry:
            raise YAMLPlanError('Script argument YAML alias cycle')
        ancestry += (id(node),)
        if isinstance(node, SequenceNode):
            for item in node.value:
                yield from calls(item, ancestry)
        else:
            values = _fields(node)
            service = values.get('action', values.get('service'))
            if isinstance(service, ScalarNode) and service.tag == _STR:
                yield node, service.value, _fields(values.get('data'))
            for key in values.keys() & _SCRIPT_CONTROLS:
                yield from calls(values[key], ancestry)

    parameters, arguments, argument_nodes = {}, {}, set()
    contracts = {}
    for service, bodies in definitions.items():
        if len(bodies) != 1:
            continue
        body, = bodies
        fields = _fields(_fields(body).get('fields'))
        for name in fields:
            if not name.isidentifier():
                continue
            # Distinct sentinels are never template-source rewrite locations.
            origin = (-id(fields[name]), 0, 'sensor.renamer_parameter')
            safe_shapes = set()
            for shape in ('scalar', 'list'):
                charge()
                supplied = {name: (shape, (origin,))}
                _, used, unsafe, *_ = analyze(body, '', {}, lambda *_: None, max_nodes,
                                              input_bindings=supplied)
                if origin in used and origin not in unsafe:
                    safe_shapes.add(shape)
            if safe_shapes:
                contracts[service, name] = safe_shapes
                if safe_shapes == {'scalar', 'list'}:
                    # Only this fixed union can be admitted for arbitrary direct
                    # service calls. Exact shapes remain required at call sites.
                    parameters.setdefault(id(body), {})[name] = ('scalar', (origin,))
                elif safe_shapes == {'list'}:
                    parameters.setdefault(id(body), {})[name] = ('list', (origin,))
                else:
                    parameters.setdefault(id(body), {})[name] = ('scalar', (origin,))

    for actor in actors:
        for action, service, data in calls(actor):
            supported = set()
            for name, node in data.items():
                shapes = contracts.get((service, name), ())
                if not shapes:
                    continue
                shape = 'list' if isinstance(node, SequenceNode) else 'scalar'
                leaves = node.value if shape == 'list' else [node]
                if shape in shapes and leaves and all(isinstance(item, ScalarNode)
                        and item.tag == _STR and _ENTITY.fullmatch(item.value) for item in leaves):
                    supported.add(name)
                    argument_nodes.update(id(item) for item in leaves)
                elif shapes == {'scalar', 'list'}:
                    # A caller's existing bounded template environment must
                    # prove the value. This never grants a free-form expression.
                    if isinstance(node, ScalarNode) and node.tag == _STR and '{{' in node.value:
                        supported.add(name)
            if supported:
                arguments[id(action)] = supported
    return parameters, arguments, argument_nodes
