"""Private closed scalar Template-for and direct source profile.

All values come from native attach closures or authentic classified occurrences.
This module never renders a pending period and never installs a monitoring tool.
"""
from __future__ import annotations

from datetime import timedelta
from functools import _lru_cache_wrapper
import hashlib
from importlib import import_module
from pathlib import Path
import sys
from types import FunctionType, MethodType, ModuleType

from .native_automation_state_binding import _need, _value

# Actual read-only Docker source readback, current/minimum, 2026-10-30.
_PINS = {
    '2025.6.0': {
        'homeassistant.helpers.template': '2e4afdb3e16a7e6339bd72966a5d9a6d1bff5e7c602797e20118a1a191944bf7',
        'homeassistant.helpers.config_validation': '74ae0ebe29be4d28bc9ab31c5352599e2eccdc31bd534cb40d1b07f3765ce8ea',
        'voluptuous.validators': 'c29ddf98aafe282eec6b0f1a7945b0d422cea31c2b723f188ad79724df5e5084',
        'voluptuous.schema_builder': '403b79a3566d2dda93b4e779215cca7333413dfb4b28693bec2cf85052540f48',
        'voluptuous.error': 'aa2a6669d2612f2717e33223bba8fc4fcae32478a210b543bf7092a010839f03',
    },
    '2026.10.4': {
        'homeassistant.helpers.template': 'cb26b93a52a25160072ac7a987f79cc8c26534abf5522461850e8ca30dfe4bd4',
        'homeassistant.helpers.template.states': '482640b98dc1aa8b168e2bd5d3bd5adf7e1c151c3511d1fe6022258c28205803',
        'homeassistant.helpers.template.extensions.state': 'b62d4dcf0d9749c2365a47568ec41d692ad3b66cbefdaa28e2cd994e41310245',
        'homeassistant.helpers.template.extensions.type_cast': 'a5e37bd0d7239ca1919dd473d4253e5139e5c273d2595a28dc8f769064a51f5e',
        'homeassistant.helpers.template.extensions.base': 'd959f98907e3e3752f33096aa8ea185d471e8d81bc693c5dc2fa323ac3226c9b',
        'homeassistant.helpers.template.helpers': '2f4796b9d35516ab9c39498365d4fcf843ebd3334ac9c1b07000d7811d706c67',
        'homeassistant.helpers.template.context': '63e1cabb7b08027db08b0b4e513f0f7f1ad15995476829ae0c7655b9e3266f1f',
        'homeassistant.helpers.config_validation': '78776d1584e83ec05c97dc138cbaf343233f0293597dda4eb1872106ecb97d0c',
        'probatio.validators.combinators': '504c2a20c01c24fc121b017e98cf138a80c914f618e1a680beadf42eaeaca124',
        'probatio._compile': 'b6952a4932183ce2b51d10729de9910d276d80f41e96c89df754849a1828297a',
        'probatio.schema': '779540034c1a70504a39e79b6633f251ffbb6dc1d371ce4ae9375a6828eab426',
        'probatio._engine': 'a9bcc5a1b05f944a8f638d8d1410a78acfe8121c61ed05c5e74d40f50e1feeb9',
        'probatio.error': 'dc6b83e7077e627ec19569021294bc52cf0a6cf484c92498462e7e2bc3e1aae7',
    },
}
# Installed Core 2026.10.2: all thirteen actual Template/CV/native validator
# module bytes match .4; the six new source/Jinja files were compared separately
# (scalar-core102-source-readback.json and scalar-source-core102-extra-readback.json).
# The same per-runtime file/code/reference/protocol checks still apply.
_PINS['2026.10.2'] = dict(_PINS['2026.10.4'])


def configured_duration(config, hass):
    """Separate configured shape from the occurrence's already computed value."""
    from homeassistant.helpers.template import Template
    value = config.get('for')
    if type(value) is timedelta:
        _need(value > timedelta(0), 'literal_duration_required')
        return value
    _need(type(value) is Template and value.hass is hass and not value.is_static
          and type(value.template) is str, 'duration_scalar_template_required')
    # Closed expressions over native trigger/this and literal states('id')|int.
    # Calls/filters are admitted only as this exact pair, without aliases,
    # defaults, keyword/dynamic args or other globals. Parsing never renders.
    from jinja2 import Environment, nodes
    tree = Environment().parse(value.template)
    allowed = (nodes.Template, nodes.Output, nodes.Const, nodes.Name,
               nodes.Getattr, nodes.Getitem, nodes.CondExpr, nodes.Compare,
               nodes.Operand, nodes.And, nodes.Or, nodes.Not, nodes.Add,
               nodes.Sub, nodes.Mul, nodes.Div, nodes.FloorDiv, nodes.Mod,
               nodes.Pow, nodes.Neg, nodes.Pos, nodes.Tuple, nodes.List, nodes.Call, nodes.Filter)
    _need(all(type(node) in allowed for node in (tree, *tree.find_all(nodes.Node)))
          and all(node.name in ('trigger', 'this', 'states') and node.ctx == 'load'
                  for node in tree.find_all(nodes.Name))
          and all(not node.attr.startswith('_') for node in tree.find_all(nodes.Getattr)),
          'duration_scalar_expression_unsupported')
    calls = tuple(tree.find_all(nodes.Call))
    filters = tuple(tree.find_all(nodes.Filter))
    import re
    _need(all(type(call.node) is nodes.Name and call.node.name == 'states'
              and len(call.args) == 1 and type(call.args[0]) is nodes.Const
              and type(call.args[0].value) is str
              and re.fullmatch(r'[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*', call.args[0].value)
              and not call.kwargs and call.dyn_args is None and call.dyn_kwargs is None
              for call in calls)
          and all(f.name == 'int' and type(f.node) is nodes.Call
                  and any(f.node is call for call in calls) and not f.args and not f.kwargs
                  and f.dyn_args is None and f.dyn_kwargs is None for f in filters)
          and len(calls) == len(filters)
          and all(sum(f.node is call for f in filters) == 1 for call in calls)
          and all(any(name is call.node for call in calls)
                  for name in tree.find_all(nodes.Name) if name.name == 'states'),
          'duration_scalar_source_expression_unsupported')
    return value


class ScalarDurationNative:
    """Pinned actual render methods and versioned All/Any callable protocol.

    Native Schema objects are NOT assumed to have __code__. On minimum, direct
    All/Any calls create genuine fresh Schema instances and may update their
    `_compiled`/`schema` compilation bookkeeping. Authenticate active compiled
    closures structurally instead of freezing that legal native bookkeeping.
    This first profile accepts scalar render results; a dict/list result is
    explicitly unsupported before period assignment or timer creation.
    """
    def __init__(self, native):
        from .native_automation_duration_binding import _codes
        self.native = native
        _need(native.version in _PINS, 'duration_scalar_core_unqualified')
        self.modules, self.expected, self.paths, self.refs = {}, {}, {}, []
        for name, digest in _PINS[native.version].items():
            module = import_module(name)
            path = Path(module.__file__)
            raw = path.read_bytes()
            _need(path.resolve() == path and hashlib.sha256(raw).hexdigest() == digest,
                  'duration_scalar_source_changed')
            self.modules[name] = module
            self.paths[name] = path
            self.expected[name] = tuple(_codes(compile(raw, str(path), 'exec',
                dont_inherit=True, optimize=sys.flags.optimize)))
        # Jinja's int converter is reachable from both pinned native filters.
        name = 'jinja2.filters'
        module = import_module(name)
        path = Path(module.__file__)
        raw = path.read_bytes()
        _need(path.resolve() == path and hashlib.sha256(raw).hexdigest() ==
              '3d0fc481df67f634a0b671906321782b98f69d8c21508ba584f9f28a691dafe9',
              'duration_scalar_int_source_changed')
        self.modules[name], self.paths[name] = module, path
        self.expected[name] = tuple(_codes(compile(raw, str(path), 'exec',
            dont_inherit=True, optimize=sys.flags.optimize)))
        self.source_envs = {}
        from homeassistant.core import StateMachine
        from .native_unload_code_proof import prove_methods
        self.state_machine = StateMachine
        self.state_refs = [(StateMachine, 'get')]
        self.state_methods, self.state_codes = prove_methods(native.root, native.pins, self.state_refs)
        self.state_shapes = tuple(_value(method) for method in self.state_methods)
        # Authenticate module functions and native declared class descriptors,
        # including Template render/compile/parse and CV/Schema execution paths.
        for name, module in self.modules.items():
            for key, value in tuple(vars(module).items()):
                if name == 'jinja2.filters' and key != 'do_int':
                    continue
                # Native decorated exports (minimum attach, unrelated Voluptuous
                # validators and raises) execute code owned by other modules.
                # They are outside the closed render/scalar-CV path. Apply the
                # same declared-source filter as for class descriptors below;
                # reached render/CV/compiled protocols are checked explicitly.
                if (type(value) is FunctionType and value.__module__ == name
                        and value.__code__.co_filename == str(self.paths[name])):
                    self._retain(module, key, value)
                elif isinstance(value, type) and value.__module__ == name:
                    for attr, descriptor in tuple(vars(value).items()):
                        function = self._function_descriptor(descriptor)
                        if (type(function) is FunctionType and function.__module__ == name
                                and function.__code__.co_filename == str(self.paths[name])):
                            self._retain(value, attr, function)
        self.template = self.modules['homeassistant.helpers.template']
        self.states_class = self.template.AllStates
        self.environment_class = self.template.TemplateEnvironment
        self.cv = self.modules['homeassistant.helpers.config_validation']
        self.state = native.state
        self.positive = self.cv.positive_time_period
        self.period = self.cv.time_period
        self.dict_period = self.cv.time_period_dict
        self.api = import_module('voluptuous' if native.version == '2025.6.0' else 'probatio')
        self.invalid = self.api.Invalid
        self.template_error = self.state.exceptions.TemplateError
        # lru_cache is native stateful infrastructure. Pin its original wrapper
        # and authenticate its underlying Python body, not its changing cache.
        cached = getattr(self.template, '_cached_parse_result')
        _need(type(cached) is _lru_cache_wrapper, 'duration_scalar_parse_wrapper_changed')
        self.cached, self.cached_body = cached, cached.__wrapped__
        self._authenticate(self.cached_body)
        self.cached_shape = self._function_shape(self.cached_body)
        self.check()

    @staticmethod
    def _function_descriptor(value):
        if type(value) is property:
            return value.fget
        if type(value) in (staticmethod, classmethod):
            return value.__func__
        return value

    @staticmethod
    def _function_shape(function):
        return (id(function), id(function.__code__), id(function.__globals__), id(function.__builtins__),
                tuple((id(cell), _value(cell.cell_contents)) for cell in function.__closure__ or ()),
                tuple((name, id(function.__globals__[name])) for name in function.__code__.co_names
                      if name in function.__globals__),
                _value(function.__defaults__), _value(function.__kwdefaults__),
                _value(function.__dict__))

    def _authenticate(self, function):
        module = self.modules.get(function.__module__)
        _need(type(function) is FunctionType and module is not None
              and function.__globals__ is vars(module)
              and function.__code__.co_filename == str(self.paths[function.__module__])
              and any(function.__code__ == code and function.__code__.co_qualname == code.co_qualname
                      for code in self.expected[function.__module__]),
              'duration_scalar_callable_unverified')

    def _retain(self, owner, name, function):
        self._authenticate(function)
        expected = name if type(owner) is ModuleType else owner.__qualname__ + '.' + name
        # Exact aliases from the two read-only native source inventories. Enum,
        # NamedTuple and interpreter generated descriptors are not Python source
        # methods of these modules and are excluded above, never re-authenticated
        # from a captured foreign code body.
        aliases = {
            'ResultWrapper.__annotate_func__': 'ResultWrapper.__annotate__',
            '_Hass.__annotate_func__': '_Hass.__annotate__',
            'AllStates.__setitem__': '_readonly', 'AllStates.__delitem__': '_readonly',
            'AllStates.__getitem__': 'AllStates.__getattr__',
            'DomainStates.__setitem__': '_readonly', 'DomainStates.__delitem__': '_readonly',
            'DomainStates.__getitem__': 'DomainStates.__getattr__',
            'TemplateStateBase.__setitem__': '_readonly',
            'TemplateStateBase.__delitem__': '_readonly',
            '_HAS_ENTITY_SERVICE_FIELD': 'has_at_least_one_key.<locals>.validate',
            'extra': 'Extra', '_sort_item': '_compile_itemsort.<locals>.item_priority',
        }
        annotation = expected.replace('.__annotate_func__', '.__annotate__')
        _need(function.__qualname__ == aliases.get(expected, annotation),
              'duration_scalar_method_qualifier_changed')
        self.refs.append((owner, name, function, self._function_shape(function)))

    def _compiled_scalar(self, function, expected, *, instance=False):
        self._authenticate(function)
        qualifier = ('_compile_scalar.<locals>.validate_instance' if instance else
                     '_compile_scalar.<locals>.validate_callable') if self.native.version == '2025.6.0' else '_compile_callable.<locals>.validate'
        cells = dict(zip(function.__code__.co_freevars,
                         (c.cell_contents for c in function.__closure__ or ()), strict=True))
        _need(function.__qualname__ == qualifier and set(cells) == {'schema'}
              and cells['schema'] is expected and function.__defaults__ is None
              and function.__kwdefaults__ is None and not function.__dict__,
              'duration_scalar_compiled_validator_changed')

    def _declaration(self, validator, cls, validators):
        _need(type(validator) is cls and len(validator.validators) == len(validators)
              and all(actual is expected for actual, expected in zip(validator.validators, validators, strict=True))
              and validator.msg is None and validator.required is False,
              'duration_scalar_validator_declaration_changed')
        if self.native.version == '2025.6.0':
            _need(type(validator.validators) is tuple and validator.discriminant is None,
                  'duration_scalar_validator_mode_changed')
        else:
            _need(type(validator.validators) is list and validator._extra == 0,
                  'duration_scalar_validator_mode_changed')

    def check_template(self, value):
        from jinja2 import Environment, nodes
        configured_duration({'for': value}, self.native.hass)
        if not tuple(Environment().parse(value.template).find_all(nodes.Call)):
            return
        _need(self.native.version in ('2025.6.0', '2026.10.2', '2026.10.4'),
              'duration_scalar_source_core_unqualified')
        self.check()
        t, hass = self.template, self.native.hass
        _need(value._log_fn is None and not value._limited and not value._strict,
              'duration_scalar_source_environment_mode')
        env = hass.data.get(t._ENVIRONMENT)
        _need(t.TemplateEnvironment is self.environment_class
              and type(env) is self.environment_class and type(hass.states) is self.state_machine,
              'duration_scalar_source_environment_changed')
        states = env.globals.get('states')
        _need(t.AllStates is self.states_class and type(states) is self.states_class
              and states._hass is hass,
              'duration_scalar_states_api_changed')
        if self.native.version == '2025.6.0':
            expected_int = t.forgiving_int_filter
        else:
            expected_int = self.modules['homeassistant.helpers.template.extensions.type_cast'].TypeCastExtension.forgiving_int
        _need(env.filters.get('int') is expected_int,
              'duration_scalar_int_api_changed')
        # Exact source-authenticated native mapping; no arbitrary globals/alias.
        image = (env, env.globals, env.filters, states, expected_int)
        key = id(env)
        if key in self.source_envs:
            prior = self.source_envs[key]
            _need(all(a is b for a, b in zip(image, prior, strict=True)),
                  'duration_scalar_source_environment_replaced')
        else:
            self.source_envs[key] = image

    def check(self):
        from .native_unload_code_proof import unchanged
        unchanged(self.state_refs, self.state_methods, self.state_codes)
        _need(tuple(_value(getattr(o, n)) for o, n in self.state_refs) == self.state_shapes,
              'duration_scalar_state_machine_changed')
        _need(self.template.AllStates is self.states_class
              and self.template.TemplateEnvironment is self.environment_class,
              'duration_scalar_source_class_changed')
        if 'homeassistant.helpers.template.states' in self.modules:
            _need(self.modules['homeassistant.helpers.template.states'].AllStates is self.states_class,
                  'duration_scalar_states_import_changed')
        for env, globals_, filters, states, integer in self.source_envs.values():
            _need(env.globals is globals_ and env.filters is filters
                  and globals_.get('states') is states and filters.get('int') is integer
                  and states._hass is self.native.hass,
                  'duration_scalar_source_api_changed')
        _need(all(sys.modules.get(name) is module for name, module in self.modules.items()),
              'duration_scalar_module_changed')
        for owner, name, function, shape in self.refs:
            _need(self._function_descriptor(vars(owner)[name]) is function
                  and self._function_shape(function) == shape, 'duration_scalar_method_changed')
        t, c, s, api = self.template, self.cv, self.state, self.api
        _need(s.template is t and s.cv is c and c.positive_time_period is self.positive
              and c.time_period is self.period and c.time_period_dict is self.dict_period
              and getattr(s, 'vol' if self.native.version == '2025.6.0' else 'probatio') is api
              and api.Invalid is self.invalid and s.exceptions.TemplateError is self.template_error
              and t._cached_parse_result is self.cached and self.cached.__wrapped__ is self.cached_body
              and self._function_shape(self.cached_body) == self.cached_shape,
              'duration_scalar_import_changed')
        self._declaration(self.positive, api.All, (self.period, c.positive_timedelta))
        self._declaration(self.period, api.Any,
                          (c.time_period_str, c.time_period_seconds, timedelta, self.dict_period))
        _need(type(self.dict_period) is api.All and self.dict_period.validators[0] is dict
              and self.dict_period.msg is None and self.dict_period.required is False,
              'duration_scalar_dict_type_guard_changed')
        if self.native.version == '2025.6.0':
            # Native __call__ recreates Schema; _run is what Schema compiles for
            # the nested Any/All. Only the reachable scalar closures are admitted.
            root, branches = self.positive._compiled, self.period._compiled
            _need(type(root) is list and len(root) == 2 and type(root[0]) is MethodType
                  and root[0].__self__ is self.period
                  and root[0].__func__ is api.Any._run
                  and type(branches) is list and len(branches) == 4,
                  'duration_scalar_compiled_protocol_changed')
            self._compiled_scalar(root[1], c.positive_timedelta)
            self._compiled_scalar(branches[0], c.time_period_str)
            self._compiled_scalar(branches[1], c.time_period_seconds)
            self._compiled_scalar(branches[2], timedelta, instance=True)
            _need(self.dict_period.discriminant is None and type(self.dict_period._compiled) is list
                  and len(self.dict_period._compiled) == 4, 'duration_scalar_dict_protocol_changed')
            self._compiled_scalar(self.dict_period._compiled[0], dict, instance=True)
            _need(type(branches[3]) is MethodType and branches[3].__self__ is self.dict_period
                  and branches[3].__func__ is api.All._run, 'duration_scalar_dict_branch_changed')
        else:
            root, branches = self.positive._compiled, self.period._compiled
            _need(type(root) is list and len(root) == 2 and root[0] is self.period
                  and type(self.positive._pair) is tuple and len(self.positive._pair) == 2
                  and self.positive._pair[0] is root[0] and self.positive._pair[1] is root[1]
                  and type(branches) is list and len(branches) == 4
                  and self.period._types is None and self.period._float_superset is None
                  and self.period._expected is None and branches[3] is self.dict_period,
                  'duration_scalar_compiled_protocol_changed')
            self._compiled_scalar(root[1], c.positive_timedelta)
            self._compiled_scalar(branches[0], c.time_period_str)
            self._compiled_scalar(branches[1], c.time_period_seconds)
            kind = self.modules['probatio._compile']._TypeCheck
            _need(type(branches[2]) is kind and branches[2].checked_type is timedelta
                  and branches[2]._expected == 'timedelta'
                  and self.dict_period._pair is None
                  and type(self.dict_period._compiled) is list and len(self.dict_period._compiled) == 4
                  and type(self.dict_period._compiled[0]) is kind
                  and self.dict_period._compiled[0].checked_type is dict
                  and self.dict_period._compiled[0]._expected == 'dict',
                  'duration_scalar_native_type_guard_changed')

    def render(self, binding, change):
        self.check()
        self.check_template(binding.config['for'])
        cells = binding.listener_cells
        variables = {**cells['_variables'], 'trigger': {
            'platform': 'state', 'entity_id': change.event.data['entity_id'],
            'from_state': change.prior_state, 'to_state': change.new_state}}
        try:
            rendered = self.template.render_complex(cells['time_delta'], variables)
            _need(type(rendered) in (str, int, float, bool, timedelta),
                  'duration_scalar_result_unsupported')
            return self.positive(rendered)
        except (self.template_error, self.invalid) as ex:
            # Same native failure semantics: log, no new period/U/timer. Any
            # earlier cancellation already occurred in the native-order bridge.
            self.state._LOGGER.error("Error rendering '%s' for template: %s",
                                     cells['trigger_info']['name'], ex)
            return None
