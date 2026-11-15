"""Real WS review/apply/inspect/inverse with persistent synthetic raw evidence."""
import json
import sqlite3
import traceback
import unittest
from pathlib import Path
from types import MappingProxyType

import test_native_queue_prepare_auth as auth
import test_native_backend_lifecycle as backend_fixture
import test_native_template_yaml_server as server


@unittest.skipUnless(backend_fixture.activation.fixtures.HAS_HA, 'actual pinned HA required')
class OrdinaryRegistryPublicTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    # The installed integration loads frontend -> config, which itself
    # registers the real editors. Do not register a second set in the fixture.
    native_frontend_config = True
    asyncSetUp = backend_fixture.NativeBackendLifecycleTests.asyncSetUp
    _before_start = backend_fixture.NativeBackendLifecycleTests._before_start
    _loaded_backend = backend_fixture.NativeBackendLifecycleTests._loaded_backend
    _flush_documents = server.NativeTemplateYAMLServerTests._flush_documents
    prepare = server.NativeTemplateYAMLServerTests.prepare
    emit = server.NativeTemplateYAMLServerTests.emit
    data = server.NativeTemplateYAMLServerTests.data
    _connection = auth.NativeQueuePrepareAuthTests._connection
    _transport = auth.NativeQueuePrepareAuthTests._transport
    _request = auth.NativeQueuePrepareAuthTests._request

    async def _before_loader(self, hass, root):
        import shutil
        await backend_fixture.NativeBackendLifecycleTests._before_loader(self, hass, root)
        target = self.installed_component / 'www/ha-entity-renamer.js'
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(Path(__file__).resolve().parents[1] / 'ha-entity-renamer.js', target)

    def module(self, name):
        return backend_fixture.installed_module(name)

    async def _runtime(self, module):
        backend = self._loaded_backend()
        return self.f.hass.data[backend.DATA_RUNTIME]

    async def _snapshot(self, label):
        """Persist independent complete fixture inputs before assertions/teardown."""
        import shutil
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        await self.f.hass.async_block_till_done()
        await self._flush_documents()
        dest = Path('/qa/evidence') / self._testMethodName / label
        dest.mkdir(parents=True, exist_ok=False)
        io = self.module('native_registry').NativeRegistryIO(self.f.hass)
        for kind in ('entity', 'device'):
            (dest / (kind + '-manager.json')).write_text(json.dumps(io.document(kind), sort_keys=True))
        (dest / 'config-entries-manager.json').write_bytes(_orjson_bytes_default_encoder(
            self.f.hass.config_entries._data_to_save()))
        for rel in ('configuration.yaml', 'template.yaml', '.storage/core.entity_registry',
                    '.storage/core.device_registry', '.storage/core.config_entries',
                    '.storage/ha_entity_renamer.migrations', '.storage/ha_entity_renamer.prepared',
                    '.storage/ha_entity_renamer.registry_receipts'):
            src = self.f.root / rel
            if src.is_file():
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, target)
        database = self.f.root / 'fixture.db'
        if database.exists():
            with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as source:
                with sqlite3.connect(dest / 'recorder.sqlite') as target:
                    source.backup(target)
        states = {state.entity_id: state.as_dict() for state in self.f.hass.states.async_all()}
        (dest / 'states.json').write_bytes(_orjson_bytes_default_encoder(states))

    def _result(self, name, value):
        path = Path('/qa/evidence') / self._testMethodName / (name + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True))

    async def _flow(self, kind, *, broken_subscription=False, preflight_only=False):
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        from homeassistant.config_entries import ConfigEntry, ConfigEntryState
        runtime, _, _, _ = await self.data(variant='sensor')
        hass = self.f.hass
        extra = type(self.producer)()
        extra._attr_unique_id = 'ordinary-public-' + kind
        extra._attr_name = 'Ordinary public source'
        await hass.data[DATA_INSTANCES]['sensor'].async_add_entities([extra])
        old, new = extra.entity_id, 'sensor.ordinary_public_next'
        original_uuid = self.f.entity.async_get(old).id
        entry = ConfigEntry(domain='threshold', title='Ordinary device owner', data={},
            options={'name': 'Ordinary helper', 'entity_id': self.source_id, 'lower': 20, 'upper': None, 'hysteresis': 0},
            discovery_keys=MappingProxyType({}), source='user', version=1, minor_version=1,
            subentries_data=[], unique_id='ordinary-device-' + kind)
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
        self.assertIs(entry.state, ConfigEntryState.LOADED)
        device = self.f.device.async_get_or_create(config_entry_id=entry.entry_id,
            identifiers={('threshold', 'ordinary-device-' + kind)}, name='Ordinary device')
        self.f.entity.async_update_entity(old, device_id=device.id)
        await hass.async_block_till_done()
        if not broken_subscription:
            # Actual native registration: platform owns a real ConfigEntry and
            # reports the same device through its native device_info on re-add.
            platform = extra.platform
            await platform.async_remove_entity(old)
            extra = type(self.producer)()
            extra._attr_unique_id = 'ordinary-public-' + kind
            extra._attr_name = 'Ordinary public source'
            extra._attr_has_entity_name = True
            extra._attr_device_info = {'identifiers': {('threshold', 'ordinary-device-' + kind)},
                                       'name': 'Ordinary device'}
            platform.config_entry = entry
            await platform.async_add_entities([extra])
            await hass.async_block_till_done()
            self.assertEqual(extra.registry_entry.id, original_uuid)
            self.assertEqual(extra.device_entry.id, device.id)
            self.assertIs(extra.platform.config_entry, entry)
            self.assertEqual(extra.device_info['identifiers'], {('threshold', 'ordinary-device-' + kind)})
        async with self._transport() as (ws, user):
            locks = self.module('yaml_editor_locks').NativeYAMLEditorLocks(hass).enumerate()
            self.assertEqual(len(locks), 3)
            self.assertEqual(len({name for _, name, _, _ in locks}), 3)
            await self._snapshot('BEFORE')
            entity = {'entity_id': old}
            if kind in ('id', 'combined'):
                entity['new_entity_id'] = new
            if kind in ('name', 'combined'):
                entity['name'] = 'Ordinary reviewed name'
            payload = {'entities': [] if kind == 'device' else [entity],
                       'devices': ([{'device_id': device.id, 'name_by_user': 'Ordinary reviewed device'}]
                                   if kind in ('device', 'combined') else [])}
            binding = None
            apply_attempted = inspected = False
            try:
                from test_native_template_mixed_yaml_server import native_diagnostics
                with native_diagnostics(self.module):
                    response = await self._request(ws, 1, 'prepare', **payload)
                self._result('prepare', response)
                if broken_subscription:
                    self.assertFalse(response['success'], response)
                    self.assertEqual(response['error']['code'], 'prepare_unavailable')
                    self.assertEqual(await runtime.journal.all(), ())
                    return
                self.assertTrue(response['success'], response)
                if preflight_only:
                    self.assertEqual(await runtime.journal.all(), ())
                    return
                review = response['result']
                binding = {key: review[key] for key in ('operation_id', 'fingerprint')}
                async with self._transport() as (other, _):
                    wrong = await self._request(other, 1, 'apply', **binding)
                    self._result('wrong_actor', wrong)
                    self.assertEqual(wrong['error']['code'], 'apply_refused')
                apply_attempted = True
                response = await self._request(ws, 2, 'apply', **binding)
                self._result('apply', response)
                await self._snapshot('AFTER')
                self.assertTrue(response['success'], response)
                self.assertEqual(response['result']['state'], 'applied')
                current_id = new if kind in ('id', 'combined') else old
                row = self.f.entity.async_get(current_id)
                self.assertIsNotNone(row)
                self.assertEqual(row.id, original_uuid)
                self.assertEqual(row.name, 'Ordinary reviewed name' if kind in ('name', 'combined') else None)
                self.assertEqual(self.f.device.async_get(device.id).name_by_user,
                                 'Ordinary reviewed device' if kind in ('device', 'combined') else None)
                inspect = await self._request(ws, 3, 'recovery/inspect', **binding)
                inspected = True
                self._result('inspect', inspect)
                await self._snapshot('INSPECT')
                self.assertTrue(inspect['success'], inspect)
                self.assertEqual((inspect['result']['state'], inspect['result']['eligible']), ('applied', True))
                duplicate = await self._request(ws, 4, 'apply', **binding)
                self._result('duplicate', duplicate)
                self.assertEqual(duplicate['error']['code'], 'apply_refused')
                inverse = await self._request(ws, 5, 'recovery/restore', **binding)
                self._result('inverse', inverse)
                await self._snapshot('RESTORED')
                self.assertTrue(inverse['success'], inverse)
                self.assertEqual(inverse['result']['state'], 'rolled_back')
                self.assertEqual((self.f.entity.async_get(old).id, self.f.entity.async_get(old).name),
                                 (original_uuid, None))
                self.assertIsNone(self.f.device.async_get(device.id).name_by_user)
                root = Path('/qa/evidence') / self._testMethodName
                for family, owned in (('entity', original_uuid), ('device', device.id)):
                    docs = [json.loads((root / label / (family + '-manager.json')).read_text())
                            for label in ('BEFORE', 'RESTORED')]
                    for doc in docs:
                        rows = doc['entities' if family == 'entity' else 'devices']
                        for row in rows:
                            if row['id'] == owned:
                                row.pop('modified_at', None)
                        rows.sort(key=lambda row: row['id'])
                    def typed_equal(a, b):
                        if type(a) is not type(b):
                            return False
                        if type(a) is dict:
                            return a.keys() == b.keys() and all(typed_equal(a[k], b[k]) for k in a)
                        if type(a) is list:
                            return len(a) == len(b) and all(typed_equal(x, y) for x, y in zip(a, b))
                        return a == b
                    self.assertTrue(typed_equal(*docs))
                self.assertEqual((root / 'BEFORE/template.yaml').read_bytes(),
                                 (root / 'RESTORED/template.yaml').read_bytes())
                self.assertEqual((root / 'BEFORE/config-entries-manager.json').read_bytes(),
                                 (root / 'RESTORED/config-entries-manager.json').read_bytes())
            except BaseException:
                path = Path('/qa/evidence') / self._testMethodName / 'exception.txt'
                path.write_text(traceback.format_exc())
                raise
            finally:
                if apply_attempted and not inspected and binding is not None:
                    try:
                        self._result('inspect_after_failure', await self._request(
                            ws, 99, 'recovery/inspect', **binding))
                    except BaseException:
                        self._result('inspect_after_failure', {'exception': traceback.format_exc()})
                await self._snapshot('FINAL')

    async def test_stale_and_collision_refuse_without_overwrite(self):
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        runtime, _, _, _ = await self.data(variant='sensor')
        hass = self.f.hass
        extra = type(self.producer)()
        extra._attr_unique_id = 'ordinary-negative-scope'
        extra._attr_name = 'Ordinary negative source'
        await hass.data[DATA_INSTANCES]['sensor'].async_add_entities([extra])
        await hass.async_block_till_done()
        old = extra.entity_id
        identity = self.f.entity.async_get(old).id
        binding = None
        async with self._transport() as (ws, _):
            await self._snapshot('BEFORE')
            try:
                collision = await self._request(ws, 1, 'prepare', entities=[{
                    'entity_id': old, 'new_entity_id': self.source_id}], devices=[])
                self._result('collision', collision)
                self.assertFalse(collision['success'], collision)
                self.assertEqual(await runtime.journal.all(), ())
                review = await self._request(ws, 2, 'prepare', entities=[{
                    'entity_id': old, 'name': 'Reviewed name'}], devices=[])
                self._result('prepare', review)
                self.assertTrue(review['success'], review)
                binding = {key: review['result'][key] for key in ('operation_id', 'fingerprint')}
                self.f.entity.async_update_entity(old, name='Foreign synthetic edit')
                await self._snapshot('FOREIGN')
                rejected = await self._request(ws, 3, 'apply', **binding)
                self._result('stale_apply', rejected)
                await self._snapshot('AFTER_STALE')
                self.assertFalse(rejected['success'], rejected)
                row = self.f.entity.async_get(old)
                self.assertEqual((row.id, row.name), (identity, 'Foreign synthetic edit'))
                duplicate = await self._request(ws, 4, 'apply', **binding)
                self._result('duplicate', duplicate)
                self.assertFalse(duplicate['success'], duplicate)
            except BaseException:
                (Path('/qa/evidence') / self._testMethodName / 'exception.txt').write_text(traceback.format_exc())
                raise
            finally:
                if binding is not None:
                    try:
                        self._result('inspect_after_stale', await self._request(ws, 99, 'recovery/inspect', **binding))
                    except BaseException:
                        self._result('inspect_after_stale', {'exception': traceback.format_exc()})
                # Restore only the exact synthetic fault introduced above.
                row = self.f.entity.async_get(old)
                if row is not None and row.id == identity and row.name == 'Foreign synthetic edit':
                    self.f.entity.async_update_entity(old, name=None)
                await self._snapshot('FINAL')

    async def test_device_subscription_preflight(self):
        from homeassistant.const import __version__
        if __version__ == '2025.6.0':
            self.skipTest('Core 2025 does not use this keyed subscription')
        await self._flow('device', broken_subscription=True)

    async def test_device_subscription_valid_preflight(self):
        from homeassistant.const import __version__
        if __version__ == '2025.6.0':
            self.skipTest('Core 2025 does not use this keyed subscription')
        await self._flow('device', preflight_only=True)

    async def test_readd_unqualified_preflight(self):
        await self._flow('combined', broken_subscription=True)

    async def test_readd_native_registration_preflight(self):
        await self._flow('combined', preflight_only=True)

    async def test_public_id_only(self):
        await self._flow('id')

    async def test_public_name_only(self):
        await self._flow('name')

    async def test_public_device_only(self):
        await self._flow('device')

    async def test_public_combined(self):
        await self._flow('combined')
