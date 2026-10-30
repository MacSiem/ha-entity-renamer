"""Read-only five-unit native duration Schema and result-wrapper proof.

The native positive_time_period remains the only validator. No render, CV call,
result copy, generator execution or counter/policy write occurs in this module.
"""
from __future__ import annotations

import __future__
from collections import _tuplegetter
from contextvars import ContextVar
import _thread
import re
import builtins
from datetime import timedelta
from functools import _lru_cache_wrapper
import hashlib
from importlib import import_module
from pathlib import Path
import sys
from types import CodeType, FunctionType, MethodType, ModuleType

from .native_automation_state_binding import _need, _value
from .native_template_yaml import _EmptyReloadSchemaProof

_KEYS = ('days', 'hours', 'minutes', 'seconds', 'milliseconds')
_EXTRA = {
    'probatio.validators.coerce': '88098ea872872d8028e727c851b56ff975d75e8841c70bf8f412e7ad1580c3e1',
    'probatio.validators.conditional': '8e20efd43db0ba26c4209517af4a2078abf9b574e06f2bde9b5d518bfac5fe45',
    'probatio.validators._base': '8a3276348099cc080e1e8e2f1b0c70677bb171dd09bb44b6851eeb92b458ea42',
    'probatio.markers': '89c5f8cb025ca3ff5d4053ffeed9055e4e04e2729501e7cd6f24d76105c71fee',
    'probatio._codegen': '1c065f7e75742ddcda47abb247a9e7d4116ad44d509524b9dacb95e67f42802d',
    'probatio._compile_policy': '258d77594266ea278b20af948dc6f69b12b3a5269a2ffc4a9001e25c8f46b5b4',
    'probatio._build_policy': '1a07080fbbbef4bdf1cf61392cba9e1a630c5d47ad024c3b2d92c1ac5d2f8fa4',
    'probatio.validators.comparison': '204ffe9f59882b9cb22353a2466e751e187412e71afa233e7faf488c8dd3c5bb',
    'probatio.validators.strings': '2d0c4894c88ff896805aac1e81e790776d73c80e1badcab36da71f8b5559e788',
    'probatio._messages': '10a4d63a82dec6f2e0f9a77122d29d7534df343dc3be80aa6f2384dcbde1c05e',
}


def _codes(code):
    yield code
    for value in code.co_consts:
        if type(value) is CodeType:
            yield from _codes(value)


def _same(actual, expected):
    """Typed structural comparison with identity at executable/object leaves."""
    if type(expected) in (str, int, bool, float, bytes, type(None)):
        return type(actual) is type(expected) and actual == expected
    if type(expected) in (tuple, list):
        return (type(actual) is type(expected) and len(actual) == len(expected)
                and all(_same(a, e) for a, e in zip(actual, expected, strict=True)))
    if type(expected) is dict:
        return (type(actual) is dict and all(type(key) is str for key in actual)
                and set(actual) == set(expected)
                and all(_same(actual[key], value) for key, value in expected.items()))
    if type(expected) in (set, frozenset):
        return (type(actual) is type(expected)
                and all(type(item) in (str, int, bool, bytes, type(None)) for item in actual)
                and actual == expected)
    return actual is expected


def _shape(actual, expected, label):
    _need(_same(actual, expected), 'duration_dict_' + label)


def _cells(function):
    return dict(zip(function.__code__.co_freevars,
                    (cell.cell_contents for cell in function.__closure__ or ()), strict=True))


def _canonical_hot_text():
    """Independent specialization of pinned _codegen.py for the owned descriptor.

    No live generator call and no observed generated function/code baseline.
    PREVENT_EXTRA, five optional literal keys, distinct Coerce(float), no groups,
    aliases, defaults, construction, recursion or validators are proved below.
    Indentation and line layout match the pinned emitter, including blank lines.
    """
    lines = ['def _validate(data):', '    if type(data) is not dict:',
             '        return _interpreted(data)', '    out = dict(data)', '    try:']
    for key in _KEYS:
        target = f'out[{key!r}]'
        # _emit_field chooses the optional Undefined branch and _inline_coerce(float).
        field = [f'    _v = data.get({key!r}, _MISSING)', '    if _v is _MISSING:',
                 '        pass', '    else:', '        if type(_v) is float:',
                 '            pass', '        elif type(_v) is int:',
                 '            _v = float(_v)', '        elif type(_v) is str:',
                 '            try:', '                _v = float(_v)',
                 '            except ValueError:', '                raise _Bail',
                 '        else:', '            raise _Bail', f'        {target} = _v']
        lines.extend('    ' + line for line in field)
    lines.extend(('        if not _issuperset(data.keys()):', '            raise _Bail',
                  '    except _Bail:', '        return _interpreted(data)', '    return out'))
    return '\n'.join(lines)


class _DurationSchemaProof(_EmptyReloadSchemaProof):
    """Reuse the established lazy/bootstrap/AUTO/native-generated state machine."""
    def __init__(self, owner):
        self.owner = owner
        super().__init__(owner.scalar.native.version, owner.scalar.api)

    def _minimum(self, schema):
        _need(schema is self.owner.schema, 'duration_dict_schema_replaced')
        self.owner._minimum_mapping()

    def _mapping(self, validator):
        self.owner._interpreted_mapping(validator)

    def _generated(self, function):
        self.owner._hot(function)

    def check(self, schema):
        # Base check's declaration is deliberately empty. The inherited runtime
        # state machine is reused only after this five-unit declaration proof.
        self.owner._declaration()
        for owner, name, function, code in self.refs:
            _need(getattr(owner, name) is function and function.__code__ is code,
                  'duration_dict_schema_method_changed')
        if self.version == '2025.6.0':
            self._minimum(schema)
        else:
            self._current(schema)


class DictDurationNative:
    """Additive proof owned by ScalarDurationNative's configured-tree caller.

    Caller checks scalar first; check() never recurses into scalar.check().
    admission returns the exact rendered object, including native invalid values.
    Source/method retention follows ScalarDurationNative's accepted source proof;
    generated code and mutable native caches use independent explicit protocols.
    """
    def __init__(self, scalar_capability):
        from .native_automation_scalar_duration import _PINS
        self.scalar = scalar_capability
        self.minimum = self.scalar.native.version == '2025.6.0'
        self.modules, self.paths, self.expected, self.refs = {}, {}, {}, []
        self.classes = []
        self.pins = dict(_PINS[self.scalar.native.version])
        if not self.minimum:
            self.pins.update(_EXTRA)
        for name, digest in self.pins.items():
            module = import_module(name)
            path = Path(module.__file__)
            raw = path.read_bytes()
            _need(path.resolve() == path and hashlib.sha256(raw).hexdigest() == digest,
                  'duration_dict_source_changed')
            self.modules[name], self.paths[name] = module, path
            self.expected[name] = tuple(_codes(compile(raw, str(path), 'exec',
                dont_inherit=True, optimize=sys.flags.optimize)))
        self.builtins, self.builtin_refs = vars(builtins), dict(vars(builtins))
        self.schema = self.scalar.dict_period.validators[1]
        self.key_rule = self.scalar.dict_period.validators[2]
        self.final = self.scalar.dict_period.validators[3]
        self.coercers = tuple(self.schema.schema[key] for key in _KEYS)
        self.wrapper_map = self.scalar.template.RESULT_WRAPPERS
        self.wrappers = tuple(self.wrapper_map.get(kind) for kind in (dict, list, set, tuple))
        self.hot_code = next(code for code in _codes(compile(_canonical_hot_text(),
            '<probatio-generated>', 'exec', flags=__future__.annotations.compiler_flag,
            dont_inherit=True, optimize=sys.flags.optimize)) if code.co_name == '_validate')
        self._inventory()
        self._source_classes_complete()
        self.schema_proof = _DurationSchemaProof(self)
        self.check()

    def _authenticate(self, function):
        _need(type(function) is FunctionType and function.__module__ in self.modules,
              'duration_dict_function_owner')
        name = function.__module__
        _need(function.__globals__ is vars(self.modules[name])
              and function.__builtins__ is self.builtins
              and function.__code__.co_filename == str(self.paths[name])
              and any(function.__code__ == expected
                      and function.__code__.co_qualname == expected.co_qualname
                      for expected in self.expected[name]), 'duration_dict_function_source')

    @staticmethod
    def _shape(function):
        # _validator_types changes its one write-once cache on generation. Its
        # exact seven-class protocol is checked by _current_protocols instead.
        dynamic = ({'_VALIDATOR_TYPES'} if function.__module__ == 'probatio._codegen' else
                   {'_SCHEMA_CLS'} if function.__module__ == 'probatio._compile' else set())
        return (id(function), id(function.__code__), id(function.__globals__), id(function.__builtins__),
                tuple((id(cell), _value(cell.cell_contents)) for cell in function.__closure__ or ()),
                tuple((name, id(function.__globals__[name])) for name in function.__code__.co_names
                      if name in function.__globals__ and name not in dynamic),
                _value(function.__defaults__), _value(function.__kwdefaults__), _value(function.__dict__))

    def _inventory(self):
        # Same source-file/body inventory as the scalar proof. This authenticates
        # independent source bytes before retaining live binding/default images.
        # Foreign decorated code is outside this path; reached cache wrappers and
        # factory-created wrappers have separate explicit checks below.
        aliases = {
            'AllStates.__setitem__': '_readonly', 'AllStates.__delitem__': '_readonly',
            'AllStates.__getitem__': 'AllStates.__getattr__',
            'DomainStates.__setitem__': '_readonly', 'DomainStates.__delitem__': '_readonly',
            'DomainStates.__getitem__': 'DomainStates.__getattr__',
            'TemplateStateBase.__setitem__': '_readonly',
            'TemplateStateBase.__delitem__': '_readonly',
            '_HAS_ENTITY_SERVICE_FIELD': 'has_at_least_one_key.<locals>.validate',
            'extra': 'Extra', '_sort_item': '_compile_itemsort.<locals>.item_priority',
        }
        for name, module in self.modules.items():
            owners = [(module, tuple(vars(module).items()))]
            for key, value in tuple(vars(module).items()):
                if isinstance(value, type) and value.__module__ == name:
                    _need(value.__qualname__ == {'Or': 'Any', 'And': 'All', 'Switch': 'Union'}.get(key, key),
                          'duration_dict_class_export_changed')
                    self.classes.append((module, key, value, value.__bases__))
                    owners.append((value, tuple(vars(value).items())))
            for owner, entries in owners:
                for attr, descriptor in entries:
                    if type(descriptor) is property:
                        functions = tuple(x for x in (descriptor.fget, descriptor.fset, descriptor.fdel) if x)
                    elif type(descriptor) in (staticmethod, classmethod):
                        functions = (descriptor.__func__,)
                    else:
                        functions = (descriptor,)
                    for function in functions:
                        if (type(function) is not FunctionType or function.__module__ != name
                                or function.__code__.co_filename != str(self.paths[name])):
                            continue
                        self._authenticate(function)
                        qualifier = attr if type(owner) is ModuleType else owner.__qualname__ + '.' + attr
                        qualifier = qualifier.replace('.__annotate_func__', '.__annotate__')
                        _need(function.__qualname__ == aliases.get(qualifier, qualifier),
                              'duration_dict_source_method_alias_changed')
                        self.refs.append((owner, attr, descriptor, function, self._shape(function)))
        if not self.minimum:
            codegen = self.modules['probatio._codegen']
            cached = codegen._compile_source
            _need(type(cached) is _lru_cache_wrapper, 'duration_dict_codegen_cache_changed')
            self.codegen_cache, self.codegen_body = cached, cached.__wrapped__
            self._authenticate(self.codegen_body)
            self.codegen_shape = self._shape(self.codegen_body)

    def _function(self, function, qualifier, *, cells=None, defaults=None, kwdefaults=None, line=None):
        self._authenticate(function)
        _need(function.__qualname__ == qualifier
              and function.__code__.co_qualname == qualifier
              and (line is None or function.__code__.co_firstlineno == line),
              'duration_dict_function_qualifier_changed')
        _shape(function.__defaults__, defaults, 'function_defaults_changed')
        _shape(function.__kwdefaults__, kwdefaults, 'function_kwdefaults_changed')
        attributes = ({'__override__': True} if not self.minimum and qualifier in
                      ('gen_result_wrapper.<locals>.Wrapper.__str__', 'TupleWrapper.__str__') else {})
        _shape(function.__dict__, attributes, 'function_attributes_changed')
        if cells is not None:
            _shape(_cells(function), cells, 'function_closure_changed')
        return _cells(function)

    def _closure(self, function, qualifier, cells):
        self._function(function, qualifier, cells=cells)

    def _compiled_callable(self, function, expected, *, instance=False):
        if self.minimum:
            qual = '_compile_scalar.<locals>.validate_' + ('instance' if instance else 'callable')
        else:
            qual = '_compile_callable.<locals>.validate'
        self._closure(function, qual, {'schema': expected})

    def _coerce(self, coerce):
        api = self.scalar.api
        _need(type(coerce) is api.Coerce, 'duration_dict_coerce_class_changed')
        attrs = {'type': float, 'type_name': 'float', 'msg': None}
        if not self.minimum:
            cache = coerce._failure_cache
            # Legal lazy failure cache is source-determined for float, and can
            # transition once from None. Never freeze its current state.
            _need(cache is None or _same(cache, ('expected_type', {'expected': 'float'}, ())),
                  'duration_dict_coerce_failure_cache_changed')
            attrs['_failure_cache'] = cache
        _shape(vars(coerce), attrs, 'coerce_declaration_changed')
        self._coerce_class()

    def _minimum_mapping(self):
        schema = self.schema
        _shape(vars(schema), {'schema': schema.schema, 'required': False, 'extra': 0,
                              '_compiled': schema._compiled}, 'minimum_schema_fields')
        outer = self._function(schema._compiled, 'Schema._compile_dict.<locals>.validate_dict')
        _shape(outer, {'base_validate': outer.get('base_validate'),
                       'groups_of_exclusion': {}, 'groups_of_inclusion': {}}, 'minimum_outer_closure')
        base = outer['base_validate']
        inner = self._function(base, 'Schema._compile_mapping.<locals>.validate_mapping')
        _shape(inner, {'additional_candidates': [], 'all_default_keys': set(),
                       'all_required_keys': set(), 'candidates_by_key': inner.get('candidates_by_key'),
                       'invalid_msg': 'dictionary value', 'self': schema}, 'minimum_mapping_closure')
        by_key = inner['candidates_by_key']
        _need(type(by_key) is dict and tuple(by_key) == _KEYS, 'duration_dict_minimum_mapping_keys')
        for key, coerce in zip(_KEYS, self.coercers, strict=True):
            records = by_key[key]
            _need(type(records) is list and len(records) == 1
                  and type(records[0]) is tuple and len(records[0]) == 2
                  and type(records[0][0]) is str and records[0][0] == key
                  and type(records[0][1]) is tuple and len(records[0][1]) == 2,
                  'duration_dict_minimum_mapping_record')
            key_check, value_check = records[0][1]
            self._closure(key_check, '_compile_scalar.<locals>.validate_value', {'schema': key})
            self._compiled_callable(value_check, coerce)
        self._closure(self.key_rule, 'has_at_least_one_key.<locals>.validate',
                      {'key_set': set(_KEYS), 'keys': _KEYS})
        for function, expected in zip(self.scalar.dict_period._compiled[1:],
                                      (schema, self.key_rule, self.final), strict=True):
            self._compiled_callable(function, expected)

    def _interpreted_mapping(self, mapping):
        engine = self.modules['probatio._engine']
        markers = self.modules['probatio.markers']
        _need(type(mapping) is engine._MappingValidator, 'duration_dict_mapping_type_changed')
        candidates = mapping._candidates
        _need(type(candidates) is list and len(candidates) == 5, 'duration_dict_candidate_count')
        literal, fast = {}, {}
        for index, (key, coerce, candidate) in enumerate(zip(_KEYS, self.coercers, candidates, strict=True)):
            _need(type(candidate) is engine._Candidate and len(candidate) == 18,
                  'duration_dict_candidate_type')
            expected = (key, candidate.check_key, coerce, False, markers.UNDEFINED, False, True,
                        False, False, None, None, None, None, None, None, False, (), coerce)
            _shape(tuple(candidate), expected, 'candidate_fields_changed')
            self._closure(candidate.check_key, '_compile_literal.<locals>.validate', {'schema': key})
            literal[key] = (index, candidate)
            fast[key] = (index, None, coerce)
        _shape(vars(mapping), {'_candidates': candidates, '_extra': 0,
            '_invalid_msg': 'dictionary value', '_literal': literal, '_validators': [],
            '_literal_fast': fast, '_finalizers': [], '_key_names': _KEYS,
            '_secret_keys': frozenset(), '_exclusive_groups': [], '_inclusive_groups': [],
            '_has_groups': False, '_track_seen': False, '_alias_lookup': {}, '_alias_claimed': set()},
            'mapping_graph_changed')
        self._candidate_class()
        self._undefined_class()
        _need(type(markers.UNDEFINED) is markers.Undefined and not vars(markers.UNDEFINED),
              'duration_dict_undefined_changed')

    def _hot(self, function):
        cg = self.modules['probatio._codegen']
        _need(type(function) is FunctionType and function.__code__ == self.hot_code
              and function.__qualname__ == '_validate'
              and function.__module__ is None
              and function.__code__.co_filename == '<probatio-generated>'
              and function.__code__.co_firstlineno == 1
              and function.__builtins__ is self.builtins
              and function.__defaults__ is None and function.__kwdefaults__ is None
              and function.__closure__ is None and not function.__dict__,
              'duration_dict_generated_code_changed')
        namespace = function.__globals__
        _need(type(namespace) is dict and set(namespace) == {'_MISSING', '_Bail', '_Invalid',
              '_UNDEFINED_CLS', '_interpreted', '_Type', '_issuperset', '__builtins__', '_validate'},
              'duration_dict_generated_namespace_changed')
        _need(type(cg._MISSING) is object, 'duration_dict_generated_missing_changed')
        expected = {'_MISSING': cg._MISSING, '_Bail': cg._Bail, '_Invalid': self.scalar.invalid,
                    '_UNDEFINED_CLS': self.modules['probatio.markers'].Undefined,
                    '_interpreted': namespace['_interpreted'], '_Type': None,
                    '_issuperset': namespace['_issuperset'], '__builtins__': self.builtins,
                    '_validate': function}
        _shape(namespace, expected, 'generated_namespace_values_changed')
        bound = namespace['_issuperset']
        _need(type(bound) is type(frozenset().issuperset)
              and bound.__name__ == 'issuperset' and type(bound.__self__) is frozenset
              and bound.__self__ == frozenset(_KEYS)
              and bound == bound.__self__.issuperset, 'duration_dict_generated_allowed_keys_changed')
        _need(cg._Bail.__bases__ == (Exception,)
              and not any(name in vars(cg._Bail) for name in
                          ("__new__", "__init__", "__getattribute__", "__getattr__")),
              "duration_dict_bail_class_changed")
        self._interpreted_mapping(namespace['_interpreted'])

    def admit_result(self, actual_native_render_value):
        self.check()
        value = actual_native_render_value
        kind = type(value)
        _need(kind in (str, int, float, bool, timedelta, dict) or kind is self.wrappers[0],
              'duration_dict_native_result_unsupported')
        if kind is self.wrappers[0]:
            _need(type(value.render_result) in (str, type(None))
                  and set(vars(value)) == {'render_result'}, 'duration_dict_wrapper_instance_changed')
        return value

    def _candidate_class(self):
        cls = self.modules['probatio._engine']._Candidate
        fields = ('key_schema', 'check_key', 'check_value', 'required', 'default',
                  'remove', 'is_literal', 'forbidden', 'secret', 'exclusive_group',
                  'inclusive_group', 'msg', 'value_type', 'key_type', 'complex_keys',
                  'exclusive_required', 'alias_input_names', 'value_schema')
        _need(type(cls) is type and cls.__bases__ == (tuple,)
              and cls.__qualname__ == '_Candidate' and cls.__slots__ == ()
              and type(cls._fields) is tuple and cls._fields == fields,
              'duration_dict_candidate_class_changed')
        # Each actual reached field must be the C tuple descriptor for that
        # precise index. Do not trust _fields or a generated Python accessor.
        for index, name in enumerate(fields):
            descriptor = vars(cls).get(name)
            _need(type(descriptor) is _tuplegetter
                  and descriptor.__reduce__() == (_tuplegetter, (index, f'Alias for field number {index}')),
                  'duration_dict_candidate_getter_changed')
        for name in ('__getattribute__', '__getitem__', '__iter__', '__len__',
                     '__contains__', '__eq__', '__ne__', '__hash__', '__setattr__'):
            _need(name not in vars(cls) and getattr(cls, name) is getattr(tuple, name),
                  'duration_dict_candidate_tuple_behavior_changed')
        _need('__getattr__' not in vars(cls), 'duration_dict_candidate_lookup_changed')
        # The builder reaches __new__. This independently reconstructs the
        # stdlib namedtuple's fixed constructor expression, never calls it.
        expression = 'lambda _cls, ' + ', '.join(fields) + ': _tuple_new(_cls, (' + ', '.join(fields) + '))'
        expected = next(code for code in _codes(compile(expression, '<string>', 'eval',
                        dont_inherit=True, optimize=sys.flags.optimize)) if code.co_name == '<lambda>')
        new = vars(cls)['__new__']
        if type(new) is staticmethod:
            new = new.__func__
        defaults = (False, False, None, None, None, None, None, None, False, (), None)
        _need(type(new) is FunctionType and new.__code__ == expected
              and new.__qualname__ == '_Candidate.__new__'
              and new.__closure__ is None and new.__kwdefaults__ is None
              and not new.__dict__ and _same(new.__defaults__, defaults)
              and set(new.__globals__) == {'_tuple_new', '__builtins__', '__name__'}
              and new.__globals__['_tuple_new'] is tuple.__new__
              and new.__globals__['__name__'] == 'namedtuple__Candidate'
              and type(new.__globals__['__builtins__']) is dict
              and not new.__globals__['__builtins__']
              and new.__builtins__ is new.__globals__['__builtins__'],
              'duration_dict_candidate_constructor_changed')

    def _undefined_class(self):
        markers = self.modules['probatio.markers']
        _need(type(markers.Undefined) is type and markers.Undefined.__bases__ == (object,)
              and type(markers.UNDEFINED) is markers.Undefined and not vars(markers.UNDEFINED)
              and not any(name in vars(markers.Undefined) for name in
                          ('__new__', '__init__', '__getattribute__', '__getattr__', '__call__')),
              'duration_dict_undefined_changed')

    def _coerce_class(self):
        api = self.scalar.api
        if self.minimum:
            validators = self.modules['voluptuous.validators']
            _need(api.Coerce is validators.Coerce and api.Coerce.__bases__ == (object,),
                  'duration_dict_coerce_bases_changed')
            self._function(api.Coerce.__call__, 'Coerce.__call__', cells={})
        else:
            module = self.modules['probatio.validators.coerce']
            base = self.modules['probatio.validators._base']._SafeValidator
            _need(api.Coerce is module.Coerce and module.Coerce.__bases__[0] is base
                  and base.__bases__ == (object,) and base.__probatio_safe__ is True
                  and api.Coerce.__probatio_safe__ is True
                  and '__call__' not in vars(base), 'duration_dict_coerce_bases_changed')
            # Generic[T] metadata does not execute on the closed float call path.
            self._function(module.Coerce.__call__, 'Coerce.__call__', cells={})
            self._function(module.Coerce._default_message_parts, 'Coerce._default_message_parts', cells={})
            self._function(module.Coerce._enum_values, 'Coerce._enum_values', cells={})
        for cls in api.Coerce.__mro__[:-1]:
            _need(not any(name in vars(cls) for name in
                          ('__getattribute__', '__getattr__', '__setattr__', '__delattr__')),
                  'duration_dict_coerce_lookup_changed')

    def _wrapper(self):
        t = self.scalar.template
        _need(type(t.RESULT_WRAPPERS) is dict and t.RESULT_WRAPPERS is self.wrapper_map
              and tuple(t.RESULT_WRAPPERS) == (dict, list, set, tuple)
              and all(t.RESULT_WRAPPERS[kind] is cls for kind, cls in
                      zip((dict, list, set, tuple), self.wrappers, strict=True)),
              'duration_dict_wrapper_mapping_changed')
        self._function(t.gen_result_wrapper, 'gen_result_wrapper', cells={})
        base = t.ResultWrapper
        _need(type(base) is type and base.__module__ == t.__name__
              and base.__qualname__ == 'ResultWrapper' and base.__bases__ == (object,)
              and set(vars(base)) <= {'__module__', '__doc__', '__dict__', '__weakref__',
                  '__annotations__', '__annotate_func__', '__firstlineno__', '__static_attributes__'},
              'duration_dict_result_base_changed')
        for kind, cls in zip((dict, list, set), self.wrappers[:3], strict=True):
            _need(type(cls) is type and cls.__module__ == t.__name__
                  and cls.__qualname__ == 'gen_result_wrapper.<locals>.Wrapper'
                  and cls.__bases__ == (kind, base)
                  and {'__init__', '__str__'} <= set(vars(cls))
                  and set(vars(cls)) <= {'__module__', '__firstlineno__', '__doc__', '__init__', '__str__',
                                        '__static_attributes__', '__dict__', '__weakref__'},
                  'duration_dict_wrapper_class_changed')
            self._function(vars(cls)['__init__'], 'gen_result_wrapper.<locals>.Wrapper.__init__',
                           cells={'__class__': cls}, kwdefaults={'render_result': None})
            self._function(vars(cls)['__str__'], 'gen_result_wrapper.<locals>.Wrapper.__str__',
                           cells={'kls': kind})
        tuple_cls = self.wrappers[3]
        _need(tuple_cls is t.TupleWrapper and tuple_cls.__bases__ == (tuple, base)
              and tuple_cls.__slots__ == ()
              and set(vars(tuple_cls)) <= {'__module__', '__doc__', '__slots__', '__new__', '__init__',
                  '__str__', '__firstlineno__', '__static_attributes__', '__dict__'},
              'duration_dict_tuple_wrapper_changed')
        for name in ('__new__', '__init__', '__str__'):
            function = vars(tuple_cls)[name]
            if type(function) is staticmethod:
                function = function.__func__
            self._function(function, 'TupleWrapper.' + name,
                           cells={'__class__': tuple_cls} if name in ('__new__', '__str__') else {},
                           kwdefaults={'render_result': None} if name != '__str__' else None)

    def _declaration(self):
        branch, api = self.scalar.dict_period, self.scalar.api
        self.scalar._declaration(branch, api.All, (dict, self.schema, self.key_rule, self.final))
        allowed = {'validators', 'msg', 'required', '_compiled'} | (
            {'discriminant', 'schema'} if self.minimum else {'_extra', '_pair'})
        _need(set(vars(branch)) <= allowed, 'duration_dict_all_attributes_changed')
        _need(type(self.schema) is api.Schema and type(self.schema.schema) is dict
              and tuple(self.schema.schema) == _KEYS
              and all(type(key) is str for key in self.schema.schema)
              and all(self.schema.schema[key] is value for key, value in
                      zip(_KEYS, self.coercers, strict=True))
              and len({id(value) for value in self.coercers}) == 5
              and self.schema.required is False and type(self.schema.extra) is int
              and self.schema.extra == api.PREVENT_EXTRA == 0,
              'duration_dict_schema_declaration_changed')

    def _compiled_branch(self):
        funcs = self.scalar.dict_period._compiled
        _need(type(funcs) is list and len(funcs) == 4, 'duration_dict_compiled_branch_changed')
        if self.minimum:
            self._compiled_callable(funcs[0], dict, instance=True)
            self._closure(self.key_rule, 'has_at_least_one_key.<locals>.validate',
                          {'key_set': set(_KEYS), 'keys': _KEYS})
        else:
            module = self.modules['probatio.validators.conditional']
            _need(type(self.key_rule) is module.AtLeastOne
                  and self.key_rule.__probatio_safe__ is True
                  and module.AtLeastOne.__bases__ == (module._KeyGroup,)
                  and module._KeyGroup.__bases__ == (self.modules['probatio.validators._base']._SafeValidator,),
                  'duration_dict_atleastone_class_changed')
            _shape(vars(self.key_rule), {'keys': _KEYS, 'msg': None, 'require_mapping': True},
                   'key_rule_fields_changed')
            self._function(module.AtLeastOne.__call__, 'AtLeastOne.__call__', cells={})
            self._function(module._KeyGroup._mapping, '_KeyGroup._mapping', cells={})
            kind = self.modules['probatio._compile']._TypeCheck
            _need(type(funcs[0]) is kind and funcs[0].checked_type is dict
                  and funcs[0]._expected == 'dict' and self.scalar.dict_period._pair is None,
                  'duration_dict_native_dict_guard_changed')
            _need(funcs[2] is self.key_rule, 'duration_dict_compiled_key_rule_changed')
        self._compiled_callable(funcs[1], self.schema)
        if self.minimum:
            self._compiled_callable(funcs[2], self.key_rule)
        self._compiled_callable(funcs[3], self.final)
        self._function(self.final, '<lambda>', cells={}, line=523 if self.minimum else 487)
        _need(self.final.__globals__['timedelta'] is timedelta, 'duration_dict_timedelta_binding_changed')

    def _current_protocols(self):
        schema, engine, compile_module, codegen, markers = (self.modules[name] for name in
            ('probatio.schema', 'probatio._engine', 'probatio._compile', 'probatio._codegen', 'probatio.markers'))
        coercion, comparison, combinators, strings = (self.modules[name] for name in
            ('probatio.validators.coerce', 'probatio.validators.comparison',
             'probatio.validators.combinators', 'probatio.validators.strings'))
        classes = (coercion.Coerce, comparison.Range, comparison.In, combinators.Any,
                   combinators.All, comparison.Length, strings.Match)
        _need(codegen._VALIDATOR_TYPES is None or _same(codegen._VALIDATOR_TYPES, classes),
              'duration_dict_validator_types_cache_changed')
        _need(compile_module._SCHEMA_CLS is None or compile_module._SCHEMA_CLS is schema.Schema,
              'duration_dict_schema_class_cache_changed')
        _need(self.scalar.api.Schema is schema.Schema
              and schema._AUTO_COMPILE_THRESHOLD == 50 and type(schema._AUTO_COMPILE_THRESHOLD) is int
              and engine._NO_SECRET_KEYS == frozenset() and type(engine._NO_SECRET_KEYS) is frozenset
              and engine.UNDEFINED is compile_module.UNDEFINED is markers.UNDEFINED
              and codegen.Undefined is engine.Undefined is markers.Undefined
              and schema.Invalid is engine.Invalid is compile_module.Invalid is codegen.Invalid is self.scalar.invalid,
              'duration_dict_native_namespace_changed')
        policy = self.modules['probatio._compile_policy']
        build = self.modules['probatio._build_policy']
        _need(policy._policy is policy.CompilePolicy.AUTO
              and policy._DEFAULT_POLICY is policy.CompilePolicy.AUTO
              and build._policy is build.BuildPolicy.EAGER
              and build._DEFAULT_POLICY is build.BuildPolicy.EAGER,
              'duration_dict_native_policy_changed')
        _need(type(schema._INHERIT_CONTEXT) is schema._InheritContext
              and not vars(schema._INHERIT_CONTEXT)
              and schema.Schema.__call__.__kwdefaults__ == {'context': schema._INHERIT_CONTEXT}
              and type(schema._BOOTSTRAP_LOCK) is _thread.LockType
              and type(schema._BUILD_LOCK) is _thread.RLock,
              'duration_dict_schema_infrastructure_changed')
        # Checking the default in a fresh context does not mutate the native
        # ContextVars or reject an unrelated caller's active native context.
        from contextvars import Context
        contexts = ((schema, '_VALIDATION_CONTEXT', 'probatio_validation_context', None),
                    (schema, '_INTERNAL_BUILD', 'probatio_internal_build', False),
                    (compile_module, '_COMPILING_ROOT', 'probatio_compiling_root', None),
                    (compile_module, '_COMPILING_FOR_COMBINATOR', 'probatio_compiling_for_combinator', False),
                    (compile_module, '_ACTIVE_ROOT', 'probatio_active_root', None),
                    (compile_module, '_SELF_DEPTH', 'probatio_self_depth', 0))
        for owner, name, label, default in contexts:
            value = getattr(owner, name)
            _need(type(value) is ContextVar and value.name == label
                  and _same(Context().run(value.get), default), 'duration_dict_native_context_changed')
        _need(type(codegen._MISSING) is object and codegen._MappingValidator is engine._MappingValidator,
              'duration_dict_codegen_sentinel_changed')
        cached = codegen._compile_source
        _need(cached is self.codegen_cache and cached.__wrapped__ is self.codegen_body
              and self._shape(self.codegen_body) == self.codegen_shape
              and cached.cache_parameters() == {'maxsize': 512, 'typed': False},
              'duration_dict_codegen_cache_changed')
        self._function(self.codegen_body, '_compile_source', cells={})
        self._candidate_class()
        self._undefined_class()
        error = self.modules['probatio.error']
        _need(type(error._NO_SUGGESTION) is object
              and type(error._BARE_KEY) is re.Pattern
              and error._BARE_KEY.pattern == r'[A-Za-z_][A-Za-z0-9_-]*\Z'
              and error._BARE_KEY.flags == re.UNICODE,
              'duration_dict_error_infrastructure_changed')

    def check(self):
        # Scalar is checked by the owning tree first. Calling it here would
        # recurse once scalar's configured profile owns this additive proof.
        for name, digest in self.pins.items():
            path = self.paths[name]
            _need(sys.modules.get(name) is self.modules[name]
                  and Path(self.modules[name].__file__) == path and path.resolve() == path
                  and hashlib.sha256(path.read_bytes()).hexdigest() == digest,
                  'duration_dict_source_or_module_changed')
        _need(set(self.builtins) == set(self.builtin_refs)
              and all(self.builtins[name] is value for name, value in self.builtin_refs.items()),
              'duration_dict_builtins_changed')
        for owner, name, cls, bases in self.classes:
            _need(vars(owner).get(name) is cls and cls.__bases__ == bases,
                  'duration_dict_source_class_binding_changed')
        for owner, name, descriptor, function, shape in self.refs:
            _need(vars(owner).get(name) is descriptor and self._shape(function) == shape,
                  'duration_dict_source_method_binding_changed')
        self._lookup_classes()
        self._declaration()
        for coercer in self.coercers:
            self._coerce(coercer)
        self._compiled_branch()
        if not self.minimum:
            self._current_protocols()
        else:
            self._minimum_protocols()
        self.schema_proof.check(self.schema)
        self._wrapper()

    def _minimum_protocols(self):
        schema = self.modules['voluptuous.schema_builder']
        validators = self.modules['voluptuous.validators']
        error = self.modules['voluptuous.error']
        _need(self.scalar.api.Schema is validators.Schema is schema.Schema
              and schema.er is error and validators.Invalid is self.scalar.invalid is error.Invalid
              and validators.CoerceInvalid is error.CoerceInvalid
              and validators.MultipleInvalid is error.MultipleInvalid,
              'duration_dict_minimum_alias_changed')
        outer = self._function(schema._sort_item, '_compile_itemsort.<locals>.item_priority')
        _need(set(outer) == {'priority'} and type(outer['priority']) is list
              and len(outer['priority']) == 5, 'duration_dict_minimum_priority_changed')
        for record, number, name in zip(outer['priority'], (1, 2, 4, 3, 5),
                                      ('is_remove', 'is_marker', 'is_type', 'is_callable', 'is_extra'), strict=True):
            _need(type(record) is tuple and len(record) == 2 and type(record[0]) is int
                  and record[0] == number, 'duration_dict_minimum_priority_changed')
            self._function(record[1], '_compile_itemsort.<locals>.' + name, cells={})

    def _source_classes_complete(self):
        # Prevent a foreign replacement from evading the source inventory merely
        # because it carries a different filename. Every direct source-declared
        # method of the validator classes must be in that inventory, including
        # each property getter/setter code object. No AST namespace evaluator.
        for module, name, cls, _ in self.classes:
            if module.__name__.startswith('homeassistant.'):
                continue
            prefix = cls.__qualname__ + '.'
            for expected in self.expected[module.__name__]:
                if (not expected.co_qualname.startswith(prefix)
                        or '.' in expected.co_qualname[len(prefix):]
                        or expected.co_name.startswith('<')
                        or expected.co_name == '__annotate__'):
                    continue
                _need(any(owner is cls and function.__code__ == expected
                          for owner, _, _, function, _ in self.refs),
                      'duration_dict_declared_source_method_missing')

    def _lookup_classes(self):
        critical = {'Schema', '_MappingValidator', '_Candidate', 'CompileCtx', '_TypeCheck',
                    '_SafeValidator', 'Coerce', '_KeyGroup', 'AtLeastOne', 'All', 'Any',
                    '_WithSubValidators', 'Invalid', '_SuggestionInvalid', 'MultipleInvalid'}
        for module, name, cls, _ in self.classes:
            if module.__name__.startswith('homeassistant.'):
                continue
            if name in critical:
                _need(not any(attr in vars(cls) for attr in
                              ('__getattribute__', '__getattr__', '__setattr__', '__delattr__')),
                      'duration_dict_native_lookup_override_changed')
