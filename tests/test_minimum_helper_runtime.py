"""Pinned helper compatibility regressions; NOTTESTED by artifact worker.

Unit boundary tests, not substitutes for actual installed HA lifecycle/receipts.
"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from custom_components.ha_entity_renamer.helper_observers import (
    NativeHelperObservers, ObservationUnavailable,
)
from custom_components.ha_entity_renamer.helper_persistence import NativeHelperPersistence
from custom_components.ha_entity_renamer.native_helper_transaction import _require_inactive
from custom_components.ha_entity_renamer.persistence import PersistenceError
from custom_components.ha_entity_renamer.transaction import Refused


class HelperVersionSchemaTests(unittest.IsolatedAsyncioTestCase):
    async def validate(self, version, domain, minor, *, data=None, subentries=None):
        options = {
            'threshold': {'entity_id': 'sensor.source', 'lower': None,
                          'upper': 42.5, 'hysteresis': 0.0},
            'min_max': {'entity_ids': ['sensor.source'], 'type': 'mean', 'round_digits': 2},
            'group': {'group_type': 'binary_sensor', 'entities': ['binary_sensor.source'],
                      'all': False, 'hide_members': False},
        }[domain]
        snapshot = {'identity': {'entry_id': 'helper', 'version': 1, 'minor_version': minor},
                    'domain': domain, 'options': options,
                    'data': {} if data is None else data,
                    'subentries': {} if subentries is None else subentries}
        observer = object.__new__(NativeHelperObservers)
        observer.before = {'helper': deepcopy(snapshot)}
        observed = []
        def schema(value):
            observed.append(deepcopy(value))
            return value
        async def binary_schema(handler):
            self.assertIsNone(handler)
            return schema
        flow = SimpleNamespace(OPTIONS_SCHEMA=schema, CONFIG_SCHEMA=schema,
                               binary_sensor_options_schema=binary_schema)
        with patch.dict(sys.modules, {'homeassistant.const': SimpleNamespace(__version__=version)}), patch(
                'custom_components.ha_entity_renamer.helper_observers.import_module', return_value=flow):
            result = await observer.validate_snapshot(snapshot)
        return result, observed

    async def test_pinned_native_schemas_reach_domain_validator(self):
        for version, threshold_minor in [('2025.6.0', 1), ('2026.10.2', 2)]:
            for domain, minor in [('threshold', threshold_minor), ('min_max', 1), ('group', 1)]:
                with self.subTest(version=version, domain=domain):
                    result, called = await self.validate(version, domain, minor)
                    self.assertIs(result, True)
                    self.assertEqual(len(called), 1)
                    if domain == 'threshold':
                        self.assertNotIn('lower', called[0])

    async def test_threshold_minor_does_not_cross_runtime_boundary(self):
        for version, minor in [('2025.6.0', 2), ('2026.10.2', 1)]:
            with self.subTest(version=version), self.assertRaisesRegex(
                    ObservationUnavailable, 'schema_version_unsupported'):
                await self.validate(version, 'threshold', minor)

    async def test_adjacent_and_future_versions_are_not_inferred_supported(self):
        for version in ('2025.6.1', '2025.7.0', '2026.10.1', '2026.10.3', '2027.1.0'):
            with self.subTest(version=version), self.assertRaisesRegex(
                    ObservationUnavailable, 'ha_version_unsupported'):
                await self.validate(version, 'threshold', 1)

    async def test_nonempty_data_and_subentries_still_refused_on_minimum(self):
        for extra in ({'data': {'entity_id': 'sensor.foreign'}},
                      {'subentries': {'foreign': {'data': {}}}}):
            with self.subTest(extra=extra), self.assertRaisesRegex(
                    ObservationUnavailable, 'layout_unsupported'):
                await self.validate('2025.6.0', 'threshold', 1, **extra)


class HelperPersistenceBoundaryTests(unittest.TestCase):
    def construct(self, version, *, store_minor=5, subclass=False, key='core.config_entries'):
        class ConfigEntries:
            pass
        class ForeignManager(ConfigEntries):
            pass
        manager = (ForeignManager if subclass else ConfigEntries)()
        manager._store = SimpleNamespace(key=key, version=1, minor_version=store_minor,
            path='/isolated/.storage/' + key)
        hass = SimpleNamespace(config_entries=manager,
            config=SimpleNamespace(path=lambda *parts: str(Path('/isolated').joinpath(*parts))))
        with patch.dict(sys.modules, {
                'homeassistant.const': SimpleNamespace(__version__=version),
                'homeassistant.config_entries': SimpleNamespace(ConfigEntries=ConfigEntries)}):
            return NativeHelperPersistence(hass)

    def test_both_exact_runtimes_keep_same_native_store_identity(self):
        for version in ('2025.6.0', '2026.10.2'):
            obj = self.construct(version)
            self.assertIs(obj.manager, obj.hass.config_entries)
            self.assertIs(obj.store, obj.manager._store)

    def test_unknown_runtime_manager_subclass_and_store_schema_are_refused(self):
        for version, options in [('2025.6.1', {}), ('2025.6.0', {'store_minor': 4}),
                                 ('2025.6.0', {'subclass': True}),
                                 ('2025.6.0', {'key': 'foreign'})]:
            with self.subTest(version=version, options=options), self.assertRaises(PersistenceError):
                self.construct(version, **options)

    def test_document_keeps_whole_manager_payload_through_native_encoder(self):
        obj = self.construct('2025.6.0')
        full = {'entries': [{'entry_id': 'helper', 'options': {'entity_id': 'sensor.source'}},
                            {'entry_id': 'unrelated', 'data': {'sentinel': 42}}],
                'full_document_sentinel': ['retain', None]}
        obj.manager._data_to_save = lambda: full
        calls = []
        def native_encoder(document):
            calls.append(document)
            return json.dumps(document).encode()
        with patch.dict(sys.modules, {'homeassistant.helpers.json': SimpleNamespace(
                _orjson_bytes_default_encoder=native_encoder)}):
            self.assertEqual(obj.document(), full)
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0], full)


class HelperLifecycleLayoutTests(unittest.TestCase):
    def entry(self):
        return SimpleNamespace(state=SimpleNamespace(value='not_loaded'), update_listeners=[],
            _on_unload=None, _async_cancel_retry_setup=None, setup_lock=asyncio.Lock(),
            _tasks=set(), _background_tasks=set())

    def test_every_missing_lifecycle_field_refuses_instead_of_defaulting_empty(self):
        for field in ('state', 'update_listeners', '_on_unload', '_async_cancel_retry_setup',
                      'setup_lock', '_tasks', '_background_tasks'):
            entry = self.entry()
            delattr(entry, field)
            with self.subTest(field=field), self.assertRaisesRegex(Refused, 'lifecycle_unknown'):
                _require_inactive(entry)

    def test_pending_tasks_and_unload_or_retry_callbacks_refuse(self):
        for field, value in [('_tasks', (SimpleNamespace(done=lambda: False),)),
                             ('_background_tasks', (SimpleNamespace(done=lambda: False),)),
                             ('_on_unload', [lambda: None]),
                             ('update_listeners', [lambda: None]),
                             ('_async_cancel_retry_setup', lambda: None)]:
            entry = self.entry()
            setattr(entry, field, value)
            with self.subTest(field=field), self.assertRaisesRegex(Refused, 'requires_quiescence'):
                _require_inactive(entry)
        _require_inactive(self.entry())
