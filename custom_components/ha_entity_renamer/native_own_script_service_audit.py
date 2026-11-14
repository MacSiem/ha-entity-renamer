"""Read-only source and owner proof for future native Script service effects.

The returned object is task-local and grants no permission to call a service
API. Root must separately retain ORIGINAL, an effect intent, native event
receipts and all caller/lifecycle/undo fences before enabling any effect.
"""
from __future__ import annotations

import asyncio
import inspect
import dis
from pathlib import Path

from .transaction import Refused


def _need(value, reason):
    if not value:
        raise Refused(reason) from None


def _guard(guard):
    value = guard()
    if inspect.isawaitable(value):
        if inspect.iscoroutine(value):
            value.close()
        raise Refused('own_script_service_audit_synchronous_guard_required') from None
    _need(value is None or value is True, 'own_script_service_audit_guard_rejected')


class NativeOwnScriptServiceAudit:
    @classmethod
    async def capture(cls, hass, inventory, *, guard):
        import homeassistant
        from homeassistant.components import script
        from homeassistant.const import __version__
        from homeassistant.core import EventBus, HomeAssistant, ServiceRegistry
        from homeassistant.helpers import entity as entity_module, service as service_helper
        from homeassistant.helpers.entity import Entity
        from propcache.api import cached_property
        from .native_own_id_inventory import NativeOwnIDInventory
        from .native_source_handoff import _SCRIPT_PINS
        from .native_unload_code_proof import prove_methods
        from .native_utility_setup_audit import _SOURCES
        from .yaml_validation import _settled_executor

        service_pins = {
            '2025.6.0': '6cdda27dd9e3cc25605e94365789062ffdddfe538f815bf7bf1f973bc1b8b157',
            '2026.10.4': 'ccf389ff17d99fb40d43de156f7dcea77ccf121f5425961f068529a28a105340',
        }
        _need(type(hass) is HomeAssistant and type(inventory) is NativeOwnIDInventory
              and inventory.hass is hass and __version__ in service_pins
              and asyncio.current_task() is inventory.task and callable(guard),
              'own_script_service_audit_scope_unqualified')
        _guard(guard)
        inventory.check()
        pins = {'core.py': _SOURCES[__version__]['core.py'],
                'helpers/entity.py': _SOURCES[__version__]['helpers/entity.py'],
                'components/script/__init__.py': _SCRIPT_PINS[__version__],
                'helpers/service.py': service_pins[__version__]}
        refs = [(ServiceRegistry, 'async_remove'), (ServiceRegistry, '_async_remove'),
                (ServiceRegistry, 'async_register'), (ServiceRegistry, '_async_register'),
                (service_helper, 'async_set_service_schema'),
                (script.ScriptEntity, '_service_handler'),
                (script.ScriptEntity, 'async_added_to_hass'),
                (script.ScriptEntity, 'async_will_remove_from_hass'),
                (Entity, '_async_registry_updated'), (Entity, '_name_internal'),
                (EventBus, 'async_fire_internal'), (HomeAssistant, 'async_run_hass_job')]
        if __version__ == '2026.10.4':
            refs.append((EventBus, '_async_dispatch'))
        root = Path(homeassistant.__file__).resolve().parent
        def prove():
            import hashlib
            import sys
            from .native_code_cache import compiled_code_objects
            methods, codes = prove_methods(root, pins, refs)
            descriptor = inspect.getattr_static(Entity, 'unique_id')
            getter = getattr(descriptor, 'func', None)
            code = getattr(getter, '__code__', None)
            path = root / 'helpers/entity.py'
            raw = path.read_bytes()
            _need(path.resolve() == path and hashlib.sha256(raw).hexdigest() == pins['helpers/entity.py']
                  and type(descriptor) is cached_property and getter is not None
                  and getter.__globals__ is vars(entity_module)
                  and getter.__closure__ is None and getter.__defaults__ is None
                  and getter.__kwdefaults__ is None and code is not None
                  and code.co_qualname == 'Entity.unique_id'
                  and Path(code.co_filename) == path
                  and inspect.getattr_static(script.ScriptEntity, 'unique_id') is descriptor
                  and any(code == item and item.co_qualname == 'Entity.unique_id'
                          for item in compiled_code_objects(raw, str(path), sys.flags.optimize)),
                  'own_script_unique_getter_unqualified')
            name_descriptor = inspect.getattr_static(Entity, 'name')
            name_getter = getattr(name_descriptor, 'func', None)
            name_code = getattr(name_getter, '__code__', None)
            _need(type(name_descriptor) is cached_property and name_getter is not None
                  and name_getter.__globals__ is vars(entity_module)
                  and name_getter.__closure__ is None and name_getter.__defaults__ is None
                  and name_getter.__kwdefaults__ is None and name_code is not None
                  and name_code.co_qualname == 'Entity.name' and Path(name_code.co_filename) == path
                  and inspect.getattr_static(script.ScriptEntity, 'name') is name_descriptor
                  and any(name_code == item and item.co_qualname == 'Entity.name'
                          for item in compiled_code_objects(raw, str(path), sys.flags.optimize)),
                  'own_script_name_getter_unqualified')
            # The metaclass wraps both _attr_ owners in properties. Pin the
            # exact setter code and closure from this pinned Core source, even
            # though the move writes the already-owned backing/cache slots.
            from .native_own_id_inventory import _script_attr_descriptor
            attr_descriptors = tuple(_script_attr_descriptor(inventory.selected_entity, name)
                                     for name in ('unique_id', 'name'))
            compiled = compiled_code_objects(raw, str(path), sys.flags.optimize)
            _need(attr_descriptors == (inventory.unique_attr_descriptor, inventory.name_attr_descriptor)
                  and all(any(item == attr.fset.__code__ for item in compiled)
                          for attr in attr_descriptors),
                  'own_script_attr_setter_unqualified')
            return (methods, codes, descriptor, getter, code, name_descriptor, name_getter,
                    name_code, attr_descriptors, tuple(attr.fset.__code__ for attr in attr_descriptors))
        (methods, codes, descriptor, getter, getter_code, name_descriptor, name_getter,
         name_code, attr_descriptors, attr_codes) = await _settled_executor(hass, prove)
        _guard(guard)
        inventory.check()
        _need(script.async_set_service_schema is service_helper.async_set_service_schema
              and script.SCRIPT_SERVICE_SCHEMA is inventory.service_schema,
              'own_script_service_audit_native_alias_changed')
        obj = object.__new__(cls)
        obj.hass, obj.inventory, obj.guard, obj.task = hass, inventory, guard, asyncio.current_task()
        obj.refs, obj.methods, obj.codes = tuple(refs), methods, codes
        obj.unique_descriptor, obj.unique_getter, obj.unique_code = descriptor, getter, getter_code
        obj.name_descriptor, obj.name_getter, obj.name_code = name_descriptor, name_getter, name_code
        obj.attr_descriptors, obj.attr_codes = attr_descriptors, attr_codes
        obj.global_pins = tuple((method.__globals__, tuple((name, name in method.__globals__,
            method.__globals__.get(name)) for name in sorted({instruction.argval
                for instruction in dis.get_instructions(method)
                if instruction.opname in ('LOAD_GLOBAL', 'LOAD_NAME') and type(instruction.argval) is str})))
            for method in methods)
        obj.check()
        return obj

    def check(self, *, installed_row=None):
        from homeassistant.components import script
        from homeassistant.helpers import entity as entity_module, service as service_helper
        from homeassistant.helpers.entity import Entity
        from .native_unload_code_proof import unchanged
        from .native_own_id_inventory import _script_attr_descriptor
        _need(asyncio.current_task() is self.task and self.inventory.hass is self.hass,
              'own_script_service_audit_scope_changed')
        _guard(self.guard)
        unchanged(self.refs, self.methods, self.codes)
        _need(all(method.__globals__ is namespace
                  and all((name in namespace) is present and (not present or namespace[name] is value)
                          for name, present, value in bindings)
                  for method, (namespace, bindings) in zip(self.methods, self.global_pins, strict=True))
              and script.async_set_service_schema is service_helper.async_set_service_schema
              and script.SCRIPT_SERVICE_SCHEMA is self.inventory.service_schema
              and inspect.getattr_static(Entity, 'unique_id') is self.unique_descriptor
              and inspect.getattr_static(script.ScriptEntity, 'unique_id') is self.unique_descriptor
              and self.unique_descriptor.func is self.unique_getter
              and self.unique_getter.__code__ is self.unique_code
              and self.unique_getter.__globals__ is vars(entity_module)
              and inspect.getattr_static(Entity, 'name') is self.name_descriptor
              and inspect.getattr_static(script.ScriptEntity, 'name') is self.name_descriptor
              and self.name_descriptor.func is self.name_getter
              and self.name_getter.__code__ is self.name_code
              and self.name_getter.__globals__ is vars(entity_module)
              and all(_script_attr_descriptor(self.inventory.selected_entity, name) is descriptor
                      and descriptor.fset.__code__ is code
                      for name, descriptor, code in zip(('unique_id', 'name'), self.attr_descriptors,
                                                        self.attr_codes, strict=True)),
              'own_script_service_audit_native_alias_changed')
        self.inventory.check_service(installed_row=installed_row)
