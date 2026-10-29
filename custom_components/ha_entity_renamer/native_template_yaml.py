"""Scoped modern YAML numeric/binary activation readback; no native effects.

ORIGINAL/candidate seals and accepted/live YAML hook sources remain distinct.
Only ReceiptedYAMLTransactionAdapter supplies its actual partial-call state.
No new receipt, baseline, journal, callback execution or whole-family COMPLETE.
"""
from __future__ import annotations

import asyncio
import __future__
import builtins
from dataclasses import dataclass, replace
from functools import partial
import hashlib
from importlib import import_module
import inspect
import json
from pathlib import Path
import sys
from types import FunctionType, MethodType, BuiltinMethodType

from .accepted_input_state import AcceptedInputState
from .native_template_consumers import _Native, _need, _sample, _codes, _bound
from .native_template_accepted_consumers import _documents, _source_image
from .native_discovery_inputs import (
    async_observe_discovery_inputs, assert_discovery_managers_current,
)
from .native_registry_receipts import RegistryReceiptChain
from .native_unload_code_proof import prove_methods
from .template_source_evidence import CandidateTemplateSources
from .transaction import Position
from .yaml_source_binding import OriginalSourceEvidence
from .yaml_validation import _settled_executor
from .yaml_validation_worker import _fingerprint, template_state_runtime_projection

_SERVICE_PINS = {
    '2025.6.0': '6cdda27dd9e3cc25605e94365789062ffdddfe538f815bf7bf1f973bc1b8b157',
    '2026.10.2': 'ccf389ff17d99fb40d43de156f7dcea77ccf121f5425961f068529a28a105340', '2026.10.4': 'ccf389ff17d99fb40d43de156f7dcea77ccf121f5425961f068529a28a105340',
}


_SCHEMA_PINS = {
    'voluptuous.schema_builder': '403b79a3566d2dda93b4e779215cca7333413dfb4b28693bec2cf85052540f48',
    'probatio.schema': '779540034c1a70504a39e79b6633f251ffbb6dc1d371ce4ae9375a6828eab426',
    'probatio._engine': 'a9bcc5a1b05f944a8f638d8d1410a78acfe8121c61ed05c5e74d40f50e1feeb9',
    'probatio._compile': 'b6952a4932183ce2b51d10729de9910d276d80f41e96c89df754849a1828297a',
    'probatio._codegen': '1c065f7e75742ddcda47abb247a9e7d4116ad44d509524b9dacb95e67f42802d',
    'probatio._build_policy': '1a07080fbbbef4bdf1cf61392cba9e1a630c5d47ad024c3b2d92c1ac5d2f8fa4',
    'probatio._compile_policy': '258d77594266ea278b20af948dc6f69b12b3a5269a2ffc4a9001e25c8f46b5b4',
}


class _EmptyReloadSchemaProof:
    """Read-only code/closure/state proof of the native empty default validator.

    A declaration and object identity alone do not authenticate `_compiled`.
    No schema or candidate validator is executed or learned as an expectation.
    Current's native lazy/build/bootstrap/AUTO/generated transitions are allowed
    by their pinned shapes, not by saving whichever callable capture encountered.
    """
    def __init__(self, version, api):
        self.version, self.api, self.cls = version, api, api.Schema
        names = (('voluptuous.schema_builder',) if version == '2025.6.0' else
                 tuple(name for name in _SCHEMA_PINS if name.startswith('probatio.')))
        self.modules, self.sources, self.expected, self.refs = {}, {}, {}, []
        for name in names:
            module = import_module(name)
            path = Path(module.__file__)
            raw = path.read_bytes()
            _need(path.resolve() == path and hashlib.sha256(raw).hexdigest() == _SCHEMA_PINS[name],
                  'template_reload_schema_source_changed')
            self.modules[name] = module
            self.sources[name] = path
            for code in _codes(compile(raw, str(path), 'exec', dont_inherit=True,
                                       optimize=sys.flags.optimize)):
                self.expected[name, code.co_qualname] = code
        if version == '2025.6.0':
            module = self.modules['voluptuous.schema_builder']
            _need(self.cls is module.Schema, 'template_reload_schema_class_changed')
            refs = [(self.cls, name) for name in ('__call__','_compile_dict','_compile_mapping')]
        else:
            module = self.modules['probatio.schema']
            engine = self.modules['probatio._engine']
            _need(self.cls is module.Schema, 'template_reload_schema_class_changed')
            refs = [(self.cls, name) for name in ('__call__','_ensure_built','_build',
                '_arm_compile','_should_compile','_compilable','_bootstrap','_auto_counter',
                '_compile_from')]
            refs += [(engine._MappingValidator, name) for name in
                     ('__init__','__call__','_build_alias_index','_unmatched','_extra_key_error')]
            refs += [(module,'_not_built'),
                     (self.modules['probatio._compile'],'compile_node'),
                     (self.modules['probatio._compile'],'_compile_dict')]
            refs += [(self.modules['probatio._codegen'], name) for name in
                     ('compile_mapping','_is_generatable','_emit_seed','_emit_extra')]
            refs += [(self.modules['probatio._build_policy'],'get_build_policy'),
                     (self.modules['probatio._compile_policy'],'get_compile_policy')]
        for owner,name in refs:
            function = getattr(owner,name)
            module_name = function.__module__
            self._function(function,module_name,function.__qualname__)
            self.refs.append((owner,name,function,function.__code__))

    def _function(self, value, module, qualifier):
        _need(type(value) is FunctionType and value.__globals__ is vars(self.modules[module])
              and value.__code__ == self.expected[module,qualifier]
              and value.__code__.co_filename == str(self.sources[module])
              and value.__code__.co_qualname == qualifier,
              'template_reload_schema_callable_unverified')

    def _cells(self, value, module, qualifier, keys):
        self._function(value,module,qualifier)
        _need(value.__defaults__ is None and value.__kwdefaults__ is None,
              'template_reload_schema_callable_defaults_changed')
        cells = dict(zip(value.__code__.co_freevars,
                         (cell.cell_contents for cell in value.__closure__ or ()), strict=True))
        _need(set(cells) == set(keys), 'template_reload_schema_closure_changed')
        return cells

    @staticmethod
    def _empty(value, cls):
        return type(value) is cls and not value

    def _minimum(self, schema):
        _need(set(vars(schema)) == {'schema','required','extra','_compiled'},
              'template_reload_schema_instance_changed')
        name = 'voluptuous.schema_builder'
        cells = self._cells(schema._compiled,name,'Schema._compile_dict.<locals>.validate_dict',
                            ('base_validate','groups_of_exclusion','groups_of_inclusion'))
        _need(self._empty(cells['groups_of_exclusion'],dict)
              and self._empty(cells['groups_of_inclusion'],dict),
              'template_reload_schema_groups_changed')
        base = self._cells(cells['base_validate'],name,
            'Schema._compile_mapping.<locals>.validate_mapping',
            ('additional_candidates','all_default_keys','all_required_keys',
             'candidates_by_key','invalid_msg','self'))
        _need(base['self'] is schema and base['invalid_msg'] == 'dictionary value'
              and self._empty(base['additional_candidates'],list)
              and self._empty(base['all_default_keys'],set)
              and self._empty(base['all_required_keys'],set)
              and self._empty(base['candidates_by_key'],dict),
              'template_reload_schema_mapping_changed')

    def _mapping(self, validator):
        engine = self.modules['probatio._engine']
        _need(type(validator) is engine._MappingValidator,
              'template_reload_schema_mapping_required')
        fields = {'_candidates':list,'_literal':dict,'_validators':list,'_literal_fast':dict,
            '_finalizers':list,'_key_names':tuple,'_secret_keys':frozenset,
            '_exclusive_groups':list,'_inclusive_groups':list,'_alias_lookup':dict,
            '_alias_claimed':set}
        _need(set(vars(validator)) == set(fields) | {'_extra','_invalid_msg','_has_groups','_track_seen'}
              and all(self._empty(getattr(validator,key),cls) for key,cls in fields.items())
              and type(validator._extra) is int and validator._extra == self.api.PREVENT_EXTRA
              and validator._invalid_msg == 'dictionary value'
              and validator._has_groups is False and validator._track_seen is False,
              'template_reload_schema_mapping_changed')

    def _generated(self, value):
        # Exact output of the pinned native generator for zero fields and
        # PREVENT_EXTRA. Compile for comparison only; never execute this code.
        text = ('def _validate(data):\n'
                '    if type(data) is not dict:\n'
                '        return _interpreted(data)\n'
                '    out = dict(data)\n'
                '    try:\n'
                '        if not _issuperset(data.keys()):\n'
                '            raise _Bail\n'
                '    except _Bail:\n'
                '        return _interpreted(data)\n'
                '    return out')
        expected, = [code for code in _codes(compile(text,'<probatio-generated>','exec',
                        flags=__future__.annotations.compiler_flag,
                        dont_inherit=True,optimize=sys.flags.optimize)) if code.co_name == '_validate']
        _need(type(value) is FunctionType and value.__code__ == expected
              and value.__closure__ is None and value.__defaults__ is None
              and value.__kwdefaults__ is None
              and value.__code__.co_filename == '<probatio-generated>'
              and value.__builtins__ is vars(builtins),
              'template_reload_generated_schema_changed')
        codegen = self.modules['probatio._codegen']
        values = value.__globals__
        _need(set(values) == {'_MISSING','_Bail','_Invalid','_UNDEFINED_CLS','_interpreted',
                              '_Type','_issuperset','__builtins__','_validate'}
              and values['_MISSING'] is codegen._MISSING
              and values['_Bail'] is codegen._Bail and values['_Invalid'] is codegen.Invalid
              and values['_UNDEFINED_CLS'] is codegen.Undefined and values['_Type'] is None
              and values['__builtins__'] is vars(builtins) and values['_validate'] is value,
              'template_reload_generated_schema_namespace_changed')
        method = values['_issuperset']
        _need(type(method) is BuiltinMethodType and method.__name__ == 'issuperset'
              and self._empty(method.__self__,frozenset),
              'template_reload_generated_schema_keys_changed')
        self._mapping(values['_interpreted'])

    def _current(self, schema):
        module = self.modules['probatio.schema']
        compile_module = self.modules['probatio._compile']
        codegen = self.modules['probatio._codegen']
        engine = self.modules['probatio._engine']
        _need(module.compile_node is compile_module.compile_node
              and module.compile_mapping is codegen.compile_mapping
              and module._MappingValidator is compile_module._MappingValidator is engine._MappingValidator
              and module.get_build_policy is self.modules['probatio._build_policy'].get_build_policy
              and module.get_compile_policy is self.modules['probatio._compile_policy'].get_compile_policy,
              'template_reload_schema_builder_binding_changed')
        keys = {'schema','required','extra','_compile_requested','_uses_self','_built','_compiled'}
        _need(schema._compile_requested is None and schema._uses_self is False
              and type(schema._built) is bool, 'template_reload_schema_mode_changed')
        compiled = schema._compiled
        if not schema._built:
            _need(set(vars(schema)) == keys and compiled is module._not_built,
                  'template_reload_lazy_schema_changed')
        elif type(compiled) is MethodType:
            _need(set(vars(schema)) == keys | {'_interpreted'}
                  and _bound(compiled,schema,self.cls._bootstrap),
                  'template_reload_schema_bootstrap_changed')
            self._mapping(schema._interpreted)
        elif type(compiled) is engine._MappingValidator:
            _need(set(vars(schema)) == keys, 'template_reload_schema_instance_changed')
            self._mapping(compiled)
        elif type(compiled) is FunctionType and compiled.__code__.co_qualname == 'Schema._auto_counter.<locals>.validate':
            _need(set(vars(schema)) == keys, 'template_reload_schema_instance_changed')
            cells = self._cells(compiled,'probatio.schema','Schema._auto_counter.<locals>.validate',
                                ('calls','interpreted','self','validate'))
            _need(cells['self'] is schema and cells['validate'] is compiled
                  and type(cells['calls']) is int and cells['calls'] >= 0,
                  'template_reload_schema_auto_counter_changed')
            self._mapping(cells['interpreted'])
        else:
            _need(set(vars(schema)) == keys, 'template_reload_schema_instance_changed')
            self._generated(compiled)

    def check(self, schema):
        for name,path in self.sources.items():
            _need(sys.modules.get(name) is self.modules[name]
                  and hashlib.sha256(path.read_bytes()).hexdigest() == _SCHEMA_PINS[name],
                  'template_reload_schema_source_changed')
        for owner,name,function,code in self.refs:
            _need(getattr(owner,name) is function and function.__code__ is code,
                  'template_reload_schema_code_binding_changed')
        _need(type(schema) is self.cls and self.api.Schema is self.cls
              and self._empty(schema.schema,dict) and schema.required is False
              and type(schema.extra) is int and schema.extra == self.api.PREVENT_EXTRA,
              'template_reload_schema_declaration_changed')
        if self.version == '2025.6.0':
            self._minimum(schema)
        else:
            self._current(schema)


def _reload_registration(native):
    """Actual service -> pinned admin wrapper -> this hass's native reload."""
    from homeassistant.core import (HassJob, HassJobType, HomeAssistant, Service,
                                    ServiceRegistry, SupportsResponse)
    from homeassistant.helpers import service
    from homeassistant.components import template
    services = native.hass.services
    _need(type(services) is ServiceRegistry and services._hass is native.hass,
          'template_native_service_registry_required')
    for actual, owner, names in (
            (services, ServiceRegistry, ('async_call', '_execute_service')),
            (native.hass, HomeAssistant, ('async_run_hass_job', '_async_add_hass_job',
                                        'async_create_task', 'async_create_task_internal'))):
        _need(all(_bound(getattr(actual, name), actual, getattr(owner, name))
                  for name in names), 'template_native_dispatch_binding_changed')
    registered = services._services.get('template', {}).get('reload')
    job = getattr(registered, 'job', None)
    wrapper = getattr(job, 'target', None)
    _need(type(registered) is Service and type(job) is HassJob
          and type(wrapper) is partial and wrapper.func is service._async_admin_handler
          and not wrapper.keywords and len(wrapper.args) == 2
          and wrapper.args[0] is native.hass and type(wrapper.args[1]) is HassJob,
          'template_native_reload_required')
    defaults = service.async_register_admin_service.__defaults__
    schema_module = service.vol if native.version == '2025.6.0' else service.probatio
    _need(type(defaults) is tuple and len(defaults) == 2
          and type(defaults[0]) is schema_module.Schema
          and type(defaults[0].schema) is dict and not defaults[0].schema
          and defaults[0].required is False
          and defaults[0].extra == schema_module.PREVENT_EXTRA
          and registered.schema is defaults[0]
          and registered.supports_response is defaults[1] is SupportsResponse.NONE
          and job.job_type is HassJobType.Coroutinefunction,
          'template_native_reload_schema_or_kind_changed')
    native.reload_schema_proof.check(registered.schema)
    inner = wrapper.args[1]
    _need(inner.job_type is HassJobType.Coroutinefunction,
          'template_native_reload_inner_kind_changed')
    target = inner.target
    _need(type(target) is FunctionType
          and target.__qualname__ == 'async_setup.<locals>._reload_config'
          and any(target.__code__ is code for code in _codes(template.async_setup.__code__)),
          'template_native_reload_code_changed')
    cells = dict(zip(target.__code__.co_freevars,
                     (cell.cell_contents for cell in target.__closure__ or ())))
    _need(set(cells) == {'hass'} and cells['hass'] is native.hass,
          'template_native_reload_owner_changed')
    return (id(services), id(registered), id(job), id(wrapper), id(inner), id(target),
            id(registered.schema), registered.supports_response, job.job_type, inner.job_type)


def _pending_template_tasks(hass):
    """Only already-scheduled native Template discovery/platform tasks.

    Waiting these futures never initiates setup or executes a callback itself.
    No global HA drain is used under the YAML editor locks. Re-sampling catches
    children scheduled by the native parent; cancellation never cancels them.
    """
    from homeassistant.helpers.entity_platform import async_get_platforms
    current, pending = asyncio.current_task(), set()
    for platform in async_get_platforms(hass, 'template'):
        pending.update(task for task in platform._tasks if not task.done())
    for task in asyncio.all_tasks(hass.loop):
        if task is current or task.done():
            continue
        node, seen = task.get_coro(), set()
        while node is not None and id(node) not in seen:
            seen.add(id(node))
            frame = getattr(node, 'cr_frame', None) or getattr(node, 'gi_frame', None)
            if frame is not None:
                module = frame.f_globals.get('__name__', '')
                local = frame.f_locals
                if (module.startswith('homeassistant.components.template')
                        and (local.get('hass') is hass or getattr(local.get('self'), 'hass', None) is hass)
                        or module == 'homeassistant.helpers.discovery'
                        and frame.f_code.co_name == 'async_load_platform'
                        and local.get('hass') is hass and local.get('platform') == 'template'
                        or module in ('homeassistant.helpers.entity_platform',
                                      'homeassistant.helpers.entity_component')
                        and (local.get('hass') is hass
                             or getattr(local.get('self'), 'hass', None) is hass)
                        and (local.get('platform_type') == 'template'
                             or getattr(local.get('self'), 'platform_name', None) == 'template')):
                    pending.add(task)
                    break
            node = (getattr(node, 'cr_await', None) or getattr(node, 'gi_yieldfrom', None)
                    or getattr(node, 'ag_await', None))
    pending.discard(current)
    return pending


class NativeTemplateYAMLRuntimeReadback:
    """Exact subset observer for existing YAML-last transaction, no lifecycle.

    ConfigEntry Template, all other platforms/coordinators and unsupported
    sources refuse this activation. The ordinary inventory observer still keeps
    all their UNKNOWN rows; this class grants no whole-family completeness.
    """
    def __init__(self, hass, chain, evidence, *, guard):
        _need(type(chain) is RegistryReceiptChain
              and type(evidence) is OriginalSourceEvidence,
              'template_scoped_original_chain_required')
        self.hass, self.chain, self.evidence, self.guard = hass, chain, evidence, guard
        self._last_runtime = None
        self.receipt = chain.original_receipt
        evidence.assert_receipt(self.receipt)
        original, candidate = evidence.template_sources, evidence.template_candidate_sources
        _need(type(candidate) is CandidateTemplateSources and original is not None,
              'template_scoped_candidate_required')
        candidate.assert_original(self.receipt, original)
        _need(original.source_matched and candidate.sources.source_matched
              and not original.ledger.findings
              and all(row.domain in ('template.state', 'template.sensor', 'template.binary_sensor')
                      for row in original.ledger.definitions),
              'template_scoped_source_inventory_required')
        self.keys = tuple((domain.removeprefix('template.'), key) for domain, key, _ in original.rows
                          if domain in ('template.sensor', 'template.binary_sensor'))
        _need(bool(self.keys) and len(self.keys) == len(set(self.keys)),
              'template_scoped_nonempty_sources_required')
        documents = {row.key: json.loads(row.raw) for row in chain.baseline.files}
        _need(not any(row['domain'] == 'template'
                      for row in documents['core.config_entries']['data']['entries']),
              'template_config_entry_lifecycle_unverified')
        registry = [row for row in documents['core.entity_registry']['data']['entities']
                    if row['platform'] == 'template']
        from .template_physical_identity import is_physical_definition
        physical_keys = {(row.domain.removeprefix('template.'), row.native_key)
                         for row in original.ledger.definitions if is_physical_definition(row)}
        named_keys = set(self.keys) - physical_keys
        physical_consumers = ()
        if physical_keys:
            baseline, composition = chain.preparations.resolve_composition(chain.plan.native_inputs)
            _need(baseline == chain.baseline and composition.evidence == evidence,
                  'template_physical_original_preparation_required')
            physical_consumers = composition.physical_consumers
        _need(type(physical_consumers) is tuple and len(physical_consumers) == len(physical_keys)
              and {(domain, key) for domain, key, _ in physical_consumers} == physical_keys
              and len({entity_id for _, _, entity_id in physical_consumers}) == len(physical_keys),
              'template_physical_original_ids_required')
        self.physical_consumers = physical_consumers
        _need(len(registry) == len(named_keys)
              and {(row['entity_id'].split('.')[0], row['unique_id']) for row in registry} == named_keys
              and all(row['config_entry_id'] is None and row['disabled_by'] is None
                      and row['entity_id'].split('.')[0] in ('sensor', 'binary_sensor') for row in registry),
              'template_scoped_registry_inventory_required')
        self.identities = {(row['entity_id'].split('.')[0], row['unique_id']):
                           (row['entity_id'], row['id']) for row in registry}
        self.identities.update({(domain, key): (entity_id, None)
                                for domain, key, entity_id in physical_consumers})
        native = self.native = _Native(hass)
        _need(native.version == self.receipt.version, 'native_version_changed')
        from homeassistant.components import template
        from homeassistant.helpers import service
        from homeassistant.core import ServiceRegistry, Service, HassJob, HomeAssistant
        native.pins['helpers/service.py'] = _SERVICE_PINS[native.version]
        native.refs += [(template, 'async_setup'), (template, '_process_config'),
                        (service, '_async_admin_handler'), (service, 'async_register_admin_service'),
                        (ServiceRegistry, 'async_register'), (ServiceRegistry, 'async_call'),
                        (ServiceRegistry, '_execute_service'), (Service, '__init__'),
                        (HassJob, '__init__'), (HomeAssistant, 'async_run_hass_job'),
                        (HomeAssistant, '_async_add_hass_job'),
                        (HomeAssistant, 'async_create_task'),
                        (HomeAssistant, 'async_create_task_internal')]
        native.methods, native.codes = prove_methods(native.root, native.pins, native.refs)
        native.reload_schema_proof = _EmptyReloadSchemaProof(native.version,
            service.vol if native.version == '2025.6.0' else service.probatio)
        self.reload_token = _reload_registration(native)

    def assert_reload_current(self):
        self.native.bindings()
        _need(_reload_registration(self.native) == self.reload_token,
              'template_native_reload_registration_changed')

    def assert_change(self, change):
        self.evidence.assert_receipt(change.receipt)
        self.assert_reload_current()
        _need(change.receipt == self.receipt
              and self.receipt.changed_domains == ('template',)
              and self.receipt.ready_for_apply and len(self.receipt.runtime_expected) == 1
              and self.receipt.runtime_expected[0][0] == 'template'
              and None not in self.receipt.runtime_expected[0][1:]
              and self.receipt.runtime_expected[0][1] != self.receipt.runtime_expected[0][2],
              'template_scoped_runtime_expectations_required')
        self.evidence.template_candidate_sources.assert_original(
            self.receipt, self.evidence.template_sources)

    def _check(self, state):
        value = self.guard()
        if inspect.isawaitable(value):
            if inspect.iscoroutine(value):
                value.close()
            _need(False, 'synchronous_guard_required')
        _need(value is None or value is True, 'guard_rejected')
        _need(type(self.hass.config.config_dir) is str
              and self.hass.config.config_dir == self.receipt.config_root
              and type(state) is AcceptedInputState
              and state.original_json == self.chain.baseline.receipt_json
              and self.chain.matches_input_state(state, documents_only=True),
              'template_scoped_input_binding_required')
        if state._live_check is None:
            _need(self.chain.matches_input_state(state),
                  'template_scoped_input_head_changed')
        state._bound(self.receipt)
        _source_image(self.receipt, self.evidence, state)
        self.assert_reload_current()

    def _sample(self, observed):
        native, original = self.native, self.evidence
        before, tokens = _sample(native, original, observed, physical_identities=self.physical_consumers)
        candidate = replace(original, template_sources=original.template_candidate_sources.sources)
        after, after_tokens = _sample(native, candidate, observed, physical_identities=self.physical_consumers)
        _need(tokens == after_tokens, 'template_scoped_runtime_changed')
        for rows in (before, after):
            _need(not any(row.kind in ('registry', 'entry', 'coordinator') for row in rows),
                  'template_scoped_runtime_inventory_unverified')
            entities = [row for row in rows if row.kind == 'entity']
            _need(len(entities) == len(self.keys)
                  and {(row.platform, row.key) for row in entities} == set(self.keys)
                  and all(row.platform in ('sensor', 'binary_sensor') and row.entry_id is None
                          and row.consumer_verified
                          and (row.entity_id, row.registry_id) == self.identities[row.platform, row.key]
                          for row in entities), 'template_scoped_runtime_consumer_unverified')
        entities = [row for row in before if row.kind == 'entity']
        actual = _fingerprint(template_state_runtime_projection([
            ['template.' + row.platform, row.key, row.semantic_sha256] for row in entities]))
        _, old, new = self.receipt.runtime_expected[0]
        position = (Position.BEFORE if actual == old else
                    Position.AFTER if actual == new else Position.UNKNOWN)
        chosen = before if position is Position.BEFORE else after
        _need(position is not Position.UNKNOWN
              and all(row.source_verified for row in chosen if row.kind == 'entity'),
              'template_scoped_runtime_semantics_mismatch')
        return position, tokens

    async def position(self, change):
        # Generic observer invocation cannot invent an accepted/live input state.
        return Position.UNKNOWN

    async def settle_reload(self):
        """Only same-hass Template native descendants, after our actual reload.

        The effect adapter shields/drains this operation under its editor locks.
        Read-only preflight does not call this as an activation/global drain.
        """
        first_error = None
        while tasks := _pending_template_tasks(self.hass):
            await asyncio.wait(tasks)
            for task in tasks:
                try:
                    task.result()
                except BaseException as error:
                    if first_error is None:
                        first_error = error
        if first_error is not None:
            raise first_error

    def runtime_check(self, expected):
        witness = self._last_runtime
        _need(type(witness) is _TemplateRuntimeCheck and witness.owner is self
              and witness.sample[0] is expected, 'template_runtime_check_required')
        witness.check()
        return witness

    async def position_at_inputs(self, change, state):
        self._last_runtime = None
        try:
            self.assert_change(change)
            self._check(state)
            while tasks := _pending_template_tasks(self.hass):
                await asyncio.wait(tasks)
                self._check(state)
                for task in tasks:
                    task.result()
            def final_sources():
                self.native.sources()
                state.assert_current(self.receipt)
            await _settled_executor(self.hass, final_sources)
            self._check(state)
            observed = await async_observe_discovery_inputs(self.hass,
                                                           guard=lambda: self._check(state))
            self._check(state)
            _documents(state, observed)
            first = self._sample(observed)
            await asyncio.sleep(0)
            self._check(state)
            # Last await performs code/source pins followed by the full graph.
            await _settled_executor(self.hass, final_sources)
            self._check(state)
            assert_discovery_managers_current(self.hass, observed,
                                               guard=lambda: self._check(state))
            _documents(state, observed)
            second = self._sample(observed)
            if first != second:
                return Position.UNKNOWN
            witness = _TemplateRuntimeCheck(self, state, observed, second)
            witness.check()
            self._last_runtime = witness
            return first[0]
        except asyncio.CancelledError:
            raise
        except Exception:
            return Position.UNKNOWN

    async def verify_inventory(self, plan, *, rollback):
        # Full registry/history/source scope is independently supplied to the
        # receipted adapter; this observer supplies only its exact runtime slice.
        return False


@dataclass(frozen=True)
class _TemplateRuntimeCheck:
    """Actual successful getter's task-local observation; no effect authority."""
    owner: NativeTemplateYAMLRuntimeReadback
    state: AcceptedInputState
    observed: object
    sample: tuple

    def check(self):
        owner = self.owner
        owner._check(self.state)
        owner.native.sources()
        self.state.assert_current(owner.receipt)
        assert_discovery_managers_current(owner.hass, self.observed,
                                           guard=lambda: owner._check(self.state))
        _documents(self.state, self.observed)
        _need(owner._sample(self.observed) == self.sample, 'template_runtime_check_changed')
        owner.assert_reload_current()


class _TemplateCompletionGuard:
    """Local existing-Coordinator guard suffix, armed by final actual readback."""
    def __init__(self, base):
        self.base, self.witness = base, None

    def __call__(self):
        value = self.base()
        if inspect.isawaitable(value):
            if inspect.iscoroutine(value):
                value.close()
            _need(False, 'synchronous_guard_required')
        _need(value is None or value is True, 'guard_rejected')
        if self.witness is not None:
            self.witness.check()

    def arm(self, witness):
        _need(type(witness) is _TemplateRuntimeCheck, 'template_runtime_check_required')
        witness.check()
        self.witness = witness


def compose_template_yaml_coordinator(adapters, journal, guard, *, required_scopes, lock=None):
    """Genuine Coordinator with one local completion guard; fresh composition.

    Forward/fresh reverse reuse the SAME chain, immutable ORIGINAL and journal.
    The generic Coordinator and all other families retain their existing code.
    """
    from .native_yaml_transaction import ReceiptedYAMLTransactionAdapter
    from .transaction import Coordinator
    adapter = adapters.get('yaml')
    _need(type(adapter) is ReceiptedYAMLTransactionAdapter
          and type(adapter.observer) is NativeTemplateYAMLRuntimeReadback,
          'template_scoped_composition_required')
    completion = _TemplateCompletionGuard(guard)
    adapter._template_completion = completion
    return Coordinator(adapters, journal, completion, required_scopes=required_scopes, lock=lock)
