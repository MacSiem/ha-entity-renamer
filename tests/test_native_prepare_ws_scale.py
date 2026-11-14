"""Isolated Core: real WebSocket responsiveness and Runtime drift fence."""
import asyncio
from dataclasses import replace
import json
import os
from time import monotonic
import unittest
from unittest.mock import patch

import test_native_queue_prepare_auth as auth
import test_native_retained_name_fields as retained


@unittest.skipUnless(auth.backend_fixture.activation.fixtures.HAS_HA, 'native HA required')
class NativePrepareWebSocketScaleTests(auth.NativeQueuePrepareAuthTests):
    restored_source = retained.NativeRetainedNameFieldsTests.restored_source
    # Match the public backend fixture: install its bundled card and let the
    # real frontend configure HA editors exactly once.
    native_frontend_config = True
    async def _before_loader(self, hass, root):
        import shutil
        from pathlib import Path
        await auth.backend_fixture.NativeBackendLifecycleTests._before_loader(self, hass, root)
        target = self.installed_component / 'www/ha-entity-renamer.js'
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(Path(__file__).resolve().parents[1] / 'ha-entity-renamer.js', target)

    async def test_runtime_session_drift_at_resolve_await_refuses_review(self):
        runtime, mapping, uuids, original, device = await self.restored_source()
        hass = self.f.hass
        server = self.module('native_template_state_server').NativeTemplateStateServer
        store_class = self.module('prepared').PreparedStore
        prepare = server.prepare.__func__
        resolve = store_class.async_resolve_composition
        sessions = []
        async def capture(cls, *args, **kwargs):
            session = await prepare(cls, *args, **kwargs)
            sessions.append(session)
            return session
        async def drift(store, *args, **kwargs):
            result = await resolve(store, *args, **kwargs)
            sessions[0].receipt = replace(sessions[0].receipt)
            return result
        old, new = next(iter(mapping.items()))
        async def refresh(): pass
        with patch.object(server, 'prepare', classmethod(capture)), \
             patch.object(store_class, 'async_resolve_composition', drift):
            with self.assertRaises(self.module('transaction').Refused):
                await runtime.native_yaml_prepare(hass, mapping, actor_id='synthetic-admin', entity_names={old: 'Reviewed fixture'},
                    guard=lambda: None, refresh=refresh)
        self.assertEqual(len(sessions), 1)
        self.assertFalse(runtime.reviews)
        self.assertEqual(await runtime.journal.active(), ())
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)

    async def test_real_ws_first_and_502(self):
        from homeassistant.config_entries import ConfigEntry, ConfigEntryDisabler
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        from types import MappingProxyType
        runtime, mapping, _, _, _ = await self.restored_source()
        hass = self.f.hass
        entries = []
        for i in range(104):
            entry = ConfigEntry(domain='input_boolean', title=f'Fixture {i}',
                data={'fixture': i, 'settings': {'enabled': False}},
                options={'fixture': {'order': [i, True, None]}}, version=1, minor_version=1,
                source='user', unique_id=f'scale-entry-{i}', disabled_by=ConfigEntryDisabler.USER,
                discovery_keys=MappingProxyType({}), subentries_data=[])
            await hass.config_entries.async_add(entry)
            entries.append(entry)
        devices = [self.f.device.async_get_or_create(config_entry_id=entries[i % len(entries)].entry_id,
            identifiers={('input_boolean', f'synthetic-device-{i}')}, name=f'Fixture device {i}')
            for i in range(261)]
        selected = []
        initial_live = len(self.f.entity.entities)
        for i in range(37_224 + max(0, 3156 - initial_live)):
            deleted = i < 37_224
            local = i if deleted else i - 37_224
            options = (lambda n=local: {'sensor': {'display_precision': n % 3},
                'conversation': {'should_expose': n % 2 == 0}}) if local < (3059 if deleted else 3000) else None
            capabilities = ({'fixture_key': f'fixture-{local}', 'fixture_enabled': local % 2 == 0}
                            if not deleted and local < 1730 else None)
            row = self.f.entity.async_get_or_create('sensor', 'input_boolean', f'fixture-{i:05d}-' + 'u' * 18,
                config_entry=entries[i % len(entries)] if i % 5 else None,
                suggested_object_id=f'scale_row_{i:05d}_' + 'x' * 22,
                disabled_by=er.RegistryEntryDisabler.USER, get_initial_options=options,
                capabilities=capabilities, original_name=None if deleted else f'Fixture registry row {i}')
            if deleted: self.f.entity.async_remove(row.entity_id)
            elif len(selected) < 450: selected.append(row.entity_id)
        await hass.async_block_till_done()
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.tasks import SynchronizeTask
        future = hass.loop.create_future()
        get_instance(hass).queue_task(SynchronizeTask(future))
        await future
        from homeassistant.helpers import issue_registry as ir
        issue_store = ir.async_get(hass)._store
        if issue_store._data is not None or issue_store._delay_handle is not None:
            await issue_store.async_save(ir.async_get(hass)._data_to_save())
        await hass.async_block_till_done()
        await self._flush_documents(initialize=True)
        raw = _orjson_bytes_default_encoder(self.f.entity._data_to_save())
        doc = json.loads(raw)
        self.assertEqual((len(doc['entities']), len(doc['deleted_entities'])), (3156, 37224))
        self.assertGreater(len(raw), 24_000_000)
        self.assertLess(len(raw), 32_000_000)
        connection, user, unused = await self._connection(admin=True)
        token = await hass.auth.async_create_refresh_token(user, client_id='https://renamer.invalid/ws-scale')
        self.addCleanup(hass.auth.async_remove_refresh_token, token)
        access = hass.auth.async_create_access_token(token, remote_ip='127.0.0.1')
        if 'http/config/promote' in connection.handlers:
            from homeassistant.components.http.config import async_get_and_load_store
            store = await async_get_and_load_store(hass)
            if store.pending is not None:
                connection.async_handle({'id': 1, 'type': 'http/config/promote'})
                self.assertTrue((await asyncio.wait_for(unused, 10))['success'])
        old, new = next(iter(mapping.items()))
        cases = [dict(entities=[dict(entity_id=old, new_entity_id=new, name='Reviewed fixture')]),
                 dict(entities=[dict(entity_id=x, new_entity_id=x + '_renamed') for x in selected],
                      devices=[dict(device_id=x.id, name_by_user='Reviewed ' + str(i)) for i,x in enumerate(devices[:52])])]
        client = r'''import asyncio,json,sys,time,websockets
async def main():
    data=json.loads(sys.stdin.readline()); samples=[]; gaps=[]
    async with websockets.connect(data['url'], ping_interval=20, ping_timeout=20) as ws:
        assert json.loads(await ws.recv())['type']=='auth_required'
        await ws.send(json.dumps({'type':'auth','access_token':data['token']}))
        assert json.loads(await ws.recv())['type']=='auth_ok'
        async def probe():
            last=time.monotonic()
            while True:
                await asyncio.sleep(1)
                now=time.monotonic(); gaps.append(now-last); last=now
                start=time.monotonic(); pong=await ws.ping(); await asyncio.wait_for(pong,20)
                samples.append(time.monotonic()-start)
        task=asyncio.create_task(probe()); start=time.monotonic()
        try:
            await ws.send(json.dumps({'id':1,'type':'ha_entity_renamer/prepare',**data['payload']}))
            reply=json.loads(await asyncio.wait_for(ws.recv(),600))
            print(json.dumps({'response_id':reply.get('id'),'success':reply.get('success'),
                'error':reply.get('error'),'elapsed':time.monotonic()-start,
                'ping_count':len(samples),'max_ping_rtt':max(samples,default=None),
                'max_probe_gap':max(gaps,default=None),'closed':ws.close_code}),flush=True)
            if reply.get('id')!=1 or not reply.get('success'): sys.exit(1)
        finally:
            task.cancel()
            try: await task
            except asyncio.CancelledError: pass
asyncio.run(main())'''
        print(f'ws_fixture_bytes={len(raw)} entities=3156 deleted=37224 devices=261 config_entries=105', flush=True)
        only = os.environ.get('RENAMER_WS_CASE')
        original_prepare = type(runtime).native_yaml_prepare
        async def diagnosed(subject, *args, **kwargs):
            try:
                return await original_prepare(subject, *args, **kwargs)
            except Exception as error:
                print(f'ws_synthetic_prepare_refusal={type(error).__name__}:{error}', flush=True)
                raise
        prepare_patch = patch.object(type(runtime), 'native_yaml_prepare', diagnosed)
        prepare_patch.start()
        self.addCleanup(prepare_patch.stop)
        for label, payload in zip(('FIRST1', 'BATCH502'), cases):
            if only and label != only:
                continue
            process = await asyncio.create_subprocess_exec('python3', '-c', client,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            data = json.dumps(dict(url=f'ws://127.0.0.1:{hass.http.server_port}/api/websocket',
                                   token=access, payload=payload)).encode() + b'\n'
            stdout, stderr = await asyncio.wait_for(process.communicate(data), 630)
            print(f'ws_case={label} exit={process.returncode} result={stdout.decode()} stderr={stderr.decode()}', flush=True)
            self.assertEqual(process.returncode, 0)
