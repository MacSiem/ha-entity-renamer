"""Isolated actual Core + TCP WebSocket responsiveness; one test per process.

No replay of existing operations, fake admission, modified HA timeouts, or live QA.
The independent client thread can timestamp ping sends while HA's loop stalls.
"""
import asyncio
from contextlib import suppress
import hashlib
import json
import sys
from threading import get_ident
from pathlib import Path
from time import monotonic
import unittest

import test_native_public_yaml_recovery as existing
import test_native_template_mixed_yaml_server as mixed


async def network_client(port, access, mode, operation):
    """Own loop/thread, real TCP, authenticated HA WS; log safe fields only."""
    from aiohttp import ClientSession, WSMsgType
    events, pending = [], {}
    counter = 0
    finished = asyncio.Event()
    def mark(kind, **fields):
        event = {'kind': kind, 't': round(monotonic(), 6), **fields}
        events.append(event)
        print('RESP_NETWORK ' + json.dumps(event, separators=(',', ':')), flush=True)

    async with ClientSession() as session:
        async with session.ws_connect(f'http://127.0.0.1:{port}/api/websocket', heartbeat=None) as ws:
            first = await ws.receive_json()
            if first.get('type') != 'auth_required':
                raise AssertionError('network authentication challenge missing')
            await ws.send_json({'type': 'auth', 'access_token': access})
            if (await ws.receive_json()).get('type') != 'auth_ok':
                raise AssertionError('network authentication failed')
            mark('connected')

            async def reader():
                async for msg in ws:
                    if msg.type != WSMsgType.TEXT:
                        mark('network_closed', message_type=str(msg.type))
                        break
                    data = json.loads(msg.data)
                    ident = data.get('id')
                    future = pending.pop(ident, None)
                    if future is not None and not future.done():
                        future.set_result(data)
                mark('reader_ended')
                for future in pending.values():
                    if not future.done():
                        future.set_exception(ConnectionError('network connection closed'))

            async def request(kind, payload):
                nonlocal counter
                counter += 1
                ident = counter
                future = asyncio.get_running_loop().create_future()
                pending[ident] = future
                mark(kind + '_send', id=ident)
                await ws.send_json({'id': ident, **payload})
                response = await future
                mark(kind + '_receive', id=ident, success=response.get('success'),
                     response_type=response.get('type'))
                return response

            async def pings():
                while not finished.is_set():
                    response = await request('ping', {'type': 'ping'})
                    if response.get('type') != 'pong':
                        raise AssertionError('unexpected ping response')
                    try:
                        await asyncio.wait_for(finished.wait(), 2)
                    except asyncio.TimeoutError:
                        pass

            async def polling():
                while not finished.is_set():
                    try:
                        await asyncio.wait_for(finished.wait(), 2)
                        return
                    except asyncio.TimeoutError:
                        pass
                    response = await request('status', {'type': 'ha_entity_renamer/status'})
                    if response.get('success') is not True:
                        raise AssertionError('status failed')
                    if any(row.get('eligible') for row in response['result']['operations']):
                        raise AssertionError('inventory unexpectedly granted eligibility')

            reader_task = asyncio.create_task(reader())
            ping_task = asyncio.create_task(pings())
            poll_task = asyncio.create_task(polling()) if mode == 'poll' else None
            try:
                if mode == 'idle':
                    await asyncio.sleep(20)
                    result = None
                else:
                    response = await request('inspect', {'type': 'ha_entity_renamer/recovery/inspect',
                        'operation_id': operation['operation_id'], 'fingerprint': operation['fingerprint']})
                    result = response.get('result', {})
                    if response.get('success') is not True or (result.get('state'), result.get('eligible')) != ('applied', True):
                        raise AssertionError('actual inspect did not establish applied eligibility')
                finished.set()
                if poll_task:
                    await poll_task
                await ping_task
                mark('finished', inspect_state=None if result is None else result['state'])
            finally:
                finished.set()
                for task in (ping_task, poll_task, reader_task):
                    if task is not None:
                        task.cancel()
                await asyncio.gather(*(t for t in (ping_task, poll_task, reader_task) if t is not None),
                                     return_exceptions=True)
    return events


@unittest.skipUnless(existing.yaml_fixture.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeInspectResponsivenessTests(unittest.IsolatedAsyncioTestCase):
    source = existing.NativePublicYAMLRecoveryTests
    with_yaml = True
    asyncSetUp = mixed.NativeTemplateMixedYAMLServerTests.asyncSetUp
    _before_loader = source._before_loader
    _before_start = source._before_start
    _loaded_backend = source._loaded_backend
    _flush_documents = source._flush_documents
    prepare = source.prepare
    emit = source.emit
    data = source.data
    stimulus = source.stimulus
    _connection = source._connection
    _runtime = source._runtime
    _files = source._files
    _forward = source._forward
    module = source.module

    async def run_case(self, mode):
        hass = self.f.hass
        # These inert, public synthetic source files exist before ORIGINAL capture.
        # Their metadata contributes to normal native graph validation and staging.
        tree = self.f.root / 'custom_templates' / 'responsiveness_fixture'
        tree.mkdir(parents=True)
        # The rejected 100-directory preflight measured 2349 files/114 dirs;
        # use 18 synthetic subdirectories and 2261 files to approximate 2410/32.
        for group in range(18):
            folder = tree / f'package_{group:03}'
            folder.mkdir()
            count = 126 if group < 17 else 119
            for item in range(count):
                (folder / f'input_{item:03}.jinja').write_text('{# synthetic fixture #}\n')
        # Check the exact Core APIs and local TCP path before costly prepare/apply.
        from aiohttp import ClientSession
        connection, user, _ = await self._connection(admin=True)
        token = await hass.auth.async_create_refresh_token(user, client_id='https://renamer.invalid/responsiveness')
        self.addCleanup(hass.auth.async_remove_refresh_token, token)
        access = hass.auth.async_create_access_token(token, remote_ip='127.0.0.1')
        port = hass.http.server_port
        self.assertTrue(type(port) is int and 0 < port < 65536, 'Core HTTP port unavailable')
        for event in ('PY_START', 'PY_RETURN', 'PY_UNWIND'):
            self.assertTrue(hasattr(sys.monitoring.events, event), event)
        free_tool = next((i for i in range(6) if sys.monitoring.get_tool(i) is None), None)
        self.assertIsNotNone(free_tool, 'no monitoring slot')
        sys.monitoring.use_tool_id(free_tool, 'renamer responsiveness preflight')
        def probe():
            raise ValueError('synthetic monitoring preflight')
        seen = []
        def preflight_start(code, offset):
            if code is probe.__code__:
                seen.append('start')
        def preflight_unwind(code, offset, error):
            if code is probe.__code__:
                seen.append('unwind')
        try:
            sys.monitoring.register_callback(free_tool, sys.monitoring.events.PY_START, preflight_start)
            sys.monitoring.register_callback(free_tool, sys.monitoring.events.PY_UNWIND, preflight_unwind)
            sys.monitoring.set_local_events(free_tool, probe.__code__, sys.monitoring.events.PY_START)
            # PY_UNWIND is global on Python 3.12–3.14; local only from 3.15.
            sys.monitoring.set_events(free_tool, sys.monitoring.events.PY_UNWIND)
            try:
                probe()
            except ValueError:
                pass
            self.assertEqual(seen, ['start', 'unwind'])
        finally:
            sys.monitoring.set_events(free_tool, 0)
            sys.monitoring.set_local_events(free_tool, probe.__code__, 0)
            sys.monitoring.register_callback(free_tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.register_callback(free_tool, sys.monitoring.events.PY_UNWIND, None)
            sys.monitoring.free_tool_id(free_tool)
        async with ClientSession() as client:
            async with client.ws_connect(f'http://127.0.0.1:{port}/api/websocket') as ws:
                self.assertEqual((await ws.receive_json())['type'], 'auth_required')
                await ws.send_json({'type': 'auth', 'access_token': access})
                self.assertEqual((await ws.receive_json())['type'], 'auth_ok')
                await ws.send_json({'id': 1, 'type': 'ping'})
                self.assertEqual((await ws.receive_json())['type'], 'pong')
        # Resolve and verify the actual production code objects now, before the
        # potentially expensive real prepare/apply, not after its effects.
        targets = {
            'template_check': self.module('native_template_yaml').NativeTemplateYAMLRuntimeReadback._check,
            'reconstruct': self.module('native_registry_receipts').RegistryReceiptChain._reconstruct,
            'input_bound': self.module('accepted_input_state').AcceptedInputState._bound,
            'prepared_resolve': self.module('prepared').PreparedStore.resolve,
        }
        codes = {getattr(fn, '__wrapped__', fn).__code__: label for label, fn in targets.items()}
        self.assertEqual(len(codes), len(targets), 'diagnostic source code mismatch')
        # The existing mixed data helper creates native Template, Automation
        # and Script consumers for one source. It supplies no plan or verifier.
        runtime, mapping, uuids, dependents, _ = await mixed.NativeTemplateMixedYAMLServerTests.data(self)
        self.assertEqual(len(mapping), 1)
        self.assertEqual({row[0] for row in dependents}, {'automation', 'script'})
        for value in (5, 7):
            await self.stimulus(value)
        # HA may create its real repairs store after source capture. Settle the
        # fixture-owned native store before ORIGINAL, following the existing
        # mixed public QA fixture; never alter or relax the physical graph fence.
        from homeassistant.helpers import issue_registry
        issues = issue_registry.async_get(hass)
        await issues._store.async_save(issues._data_to_save())
        self.assertIsNone(issues._store._data)
        self.assertIsNone(issues._store._delay_handle)
        self.assertEqual(json.loads(Path(issues._store.path).read_bytes())['data'],
                         issues._data_to_save())
        guard, refresh = self.module('websocket_api')._recovery_authority(hass, connection, runtime)
        review = await runtime.native_yaml_prepare(hass, mapping, actor_id=user.id, guard=guard, refresh=refresh)
        self.assertEqual(review['dependencies']['domains'], ['automation', 'script', 'template'])
        self.assertEqual(review['dependencies']['yaml_file_count'], 3)
        self.assertEqual({row['path'] for row in review['dependencies']['files']},
                         {'template.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml'})
        plan = runtime.reviews[review['operation_id']].plan
        self.assertEqual(tuple(step.adapter for step in plan.steps), ('registry', 'yaml'))
        baseline, composition = runtime.preparations.resolve_composition(plan.native_inputs)
        self.assertEqual(set(baseline.receipt.changed_domains), {'template', 'automation', 'script'})
        self.assertIsNotNone(composition.dependent_config_program)
        prepared = self.f.root / '.storage' / 'ha_entity_renamer.prepared'
        preflight = {'prepared': prepared.stat().st_size,
                     'source_files': len(baseline.receipt.source_files),
                     'source_directories': len(baseline.receipt.source_directories),
                     'original_receipt_bytes': len(baseline.receipt_json), 'steps': len(plan.steps)}
        print('RESP_GEOMETRY_PRE_APPLY ' + json.dumps(preflight, separators=(',', ':')), flush=True)
        # A preflight outside these deliberately broad bounds is not comparable.
        # Abort before the one actual native apply; no store padding or rebasing.
        self.assertTrue(3_500_000 <= preflight['prepared'] <= 14_000_000, preflight)
        self.assertTrue(1900 <= preflight['source_files'] <= 2900, preflight)
        # Existing native diagnostics observes the actual mismatch site; it
        # does not alter the physical fence or convert a refusal into success.
        with mixed.native_diagnostics(self.module):
            operation = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                                                        actor_id=user.id, guard=guard, refresh=refresh)
        self.assertEqual(operation['state'], 'applied')
        record = await runtime.journal.load(operation['operation_id'])
        self.assertEqual(record.plan, plan)
        for old, new in mapping.items():
            self.assertIsNone(self.f.entity.async_get(old))
            self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
        receipts = self.f.root / '.storage' / 'ha_entity_renamer.registry_receipts'
        journal = self.f.root / '.storage' / 'ha_entity_renamer.migrations'
        source = self.installed_component
        actual = {p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in source.rglob('*') if p.is_file() and p.suffix in ('.py', '.json')}
        self.assertEqual(actual, self.public_source_sha256, 'installed component must equal exact selected candidate')
        source_digest = hashlib.sha256(json.dumps(actual, sort_keys=True).encode()).hexdigest()
        sizes = {'prepared': prepared.stat().st_size, 'receipts': receipts.stat().st_size,
                 'journal': journal.stat().st_size, 'steps': len(record.plan.steps),
                 'source_files': len(baseline.receipt.source_files),
                 'source_directories': len(baseline.receipt.source_directories),
                 'original_receipt_bytes': len(baseline.receipt_json)}
        def files():
            return self._files() | {name: (self.f.root / name).read_bytes()
                for name in ('mixed_automations.yaml', 'mixed_scripts.yaml')}
        before = files()
        lag, stop = [], asyncio.Event()
        async def observe_loop():
            target = monotonic() + .25
            while not stop.is_set():
                await asyncio.sleep(max(0, target - monotonic()))
                now = monotonic()
                sample = {'t': round(now, 6), 'delay_ms': round(max(0, now - target) * 1000, 3)}
                lag.append(sample)
                if sample['delay_ms'] >= 100:
                    print('RESP_LAG ' + json.dumps(sample, separators=(',', ':')), flush=True)
                target = now + .25
        monitor = asyncio.create_task(observe_loop())
        # Observe real synchronous code, without patching functions or source.
        # Nested durations overlap; this diagnostic itself changes timing.
        sync_starts, sync_totals, sync_long = {}, {}, []
        tool = next(i for i in range(6) if sys.monitoring.get_tool(i) is None)
        sys.monitoring.use_tool_id(tool, 'renamer responsiveness synchronous spans')
        def sync_start(code, offset):
            sync_starts.setdefault((get_ident(), code), []).append(monotonic())
        def sync_end(code, offset, value):
            stack = sync_starts.get((get_ident(), code), [])
            if not stack:
                return
            started = stack.pop()
            elapsed = monotonic() - started
            label = codes[code]
            aggregate = sync_totals.setdefault(label, {'count': 0, 'seconds': 0.0, 'max': 0.0})
            aggregate['count'] += 1
            aggregate['seconds'] += elapsed
            aggregate['max'] = max(aggregate['max'], elapsed)
            if elapsed >= .1:
                item = {'kind': label, 'start': round(started, 6), 'seconds': round(elapsed, 6)}
                sync_long.append(item)
                print('RESP_SYNC ' + json.dumps(item, separators=(',', ':')), flush=True)
        def sync_unwind(code, offset, error):
            if code not in codes:
                return
            stack = sync_starts.get((get_ident(), code), [])
            if stack:
                stack.pop()
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_UNWIND, sync_unwind)
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, sync_start)
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, sync_end)
        sys.monitoring.set_events(tool, sys.monitoring.events.PY_UNWIND)
        for code in codes:
            sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START | sys.monitoring.events.PY_RETURN)
        try:
            # Separate OS thread and event loop; TCP timestamps continue through HA stalls.
            events = await asyncio.to_thread(lambda: asyncio.run(network_client(
                hass.http.server_port, access, mode, operation)))
        finally:
            sys.monitoring.set_events(tool, 0)
            for code in codes:
                sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_UNWIND, None)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, None)
            sys.monitoring.free_tool_id(tool)
            stop.set()
            await monitor
        self.assertEqual(before, files(), 'inventory/inspect must be read-only')
        self.assertEqual(actual, {p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in source.rglob('*') if p.is_file() and p.suffix in ('.py', '.json')})
        print('INSPECT_RESPONSIVENESS ' + json.dumps({'mode': mode, 'version': self.f.version,
              'candidate_sha256': source_digest, 'fixture': sizes, 'network_events': events,
              'loop_lag': lag, 'sync_totals': sync_totals, 'sync_long': sync_long,
              'unclosed_sync_counts': sum(len(v) for v in sync_starts.values())}, separators=(',', ':')), flush=True)

    async def test_idle(self):
        await self.run_case('idle')

    async def test_inspect_without_poll(self):
        await self.run_case('no_poll')

    async def test_inspect_with_poll(self):
        await self.run_case('poll')
