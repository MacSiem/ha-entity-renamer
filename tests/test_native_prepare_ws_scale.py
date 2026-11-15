"""Isolated Core: real WebSocket responsiveness and Runtime drift fence."""
from pathlib import Path
import asyncio
from dataclasses import replace
import json
import os
import sys
import threading
from collections import Counter
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

    async def test_native_disabled_device_members_are_observable(self):
        from homeassistant.helpers import entity_registry as er
        from types import MappingProxyType
        from homeassistant.config_entries import ConfigEntry, ConfigEntryDisabler
        _, _, _, _, _ = await self.restored_source()
        hass = self.f.hass
        entry = ConfigEntry(domain='input_boolean', title='Device fixture', data={}, options={},
            version=1, minor_version=1, source='user', unique_id='device-fixture-entry',
            disabled_by=ConfigEntryDisabler.USER, discovery_keys=MappingProxyType({}), subentries_data=[])
        await hass.config_entries.async_add(entry)
        devices = [self.f.device.async_get_or_create(config_entry_id=entry.entry_id,
                   identifiers={('input_boolean', f'qualified-device-{i}')}, name=f'Device {i}')
                   for i in range(3)]
        for i, device in enumerate(devices):
            self.f.entity.async_get_or_create('sensor', 'input_boolean', f'qualified-entity-{i}',
                config_entry=entry, device_id=device.id, suggested_object_id=f'qualified_entity_{i}',
                disabled_by=er.RegistryEntryDisabler.USER)
        await hass.async_block_till_done()
        await self._flush_documents()
        nr = self.module('native_registry')
        bridge = nr.NativeRegistryBridge(nr.NativeRegistryIO(hass),
                                        self.module('recorder_guard').NativeRecorderGuard(hass))
        requests = [('device', device.id, {'name_by_user': f'Reviewed {i}'})
                    for i, device in enumerate(devices)]
        batch = await bridge.prepare_many(requests, lambda: None)
        singles = [await bridge.prepare(*request, lambda: None) for request in requests]
        self.assertEqual(batch, singles)
        observer = self.module('native_name_observers').NativeNameObserver
        class FullDocumentIO:
            def __init__(self, io): self.io, self.hass = io, io.hass
            def document(self, kind): return self.io.document(kind)
        selective = observer(bridge.io)
        full = observer(FullDocumentIO(bridge.io))
        for change in batch:
            self.assertEqual(selective.capture(change), full.capture(change))
            self.assertEqual(selective.verify(change, change.before), full.verify(change, change.before))

    async def test_native_microbatch_receipts_match_individual(self):
        from homeassistant.helpers import entity_registry as er
        runtime, _, _, _, _ = await self.restored_source()
        hass = self.f.hass
        rows = [self.f.entity.async_get_or_create('sensor', 'input_boolean',
                f'batch-parity-{i}', suggested_object_id=f'batch_parity_{i}',
                disabled_by=er.RegistryEntryDisabler.USER) for i in range(10)]
        await hass.async_block_till_done()
        await self._flush_documents()
        nr = self.module('native_registry')
        recorder = self.module('recorder_guard').NativeRecorderGuard(hass)
        bridge = nr.NativeRegistryBridge(nr.NativeRegistryIO(hass), recorder)
        requests = [('entity', row.id, {'entity_id': row.entity_id + '_new'}) for row in rows]
        for size in (3, 10):
            batch = await bridge.prepare_many(requests[:size], lambda: None)
            singles = [await bridge.prepare(*request, lambda: None) for request in requests[:size]]
            self.assertEqual(batch, singles)

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

    async def test_registry_device_with_unrelated_automation_script_apply_inverse(self):
        """Registry-only changes retain unrelated native sources and identities."""
        runtime, mapping, uuids, original, device = await self.restored_source()
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        before_config = (self.f.root / 'configuration.yaml').read_bytes()
        before_automation = {state.entity_id: state.state for state in hass.states.async_all('automation')}
        before_scripts = {state.entity_id: state.state for state in hass.states.async_all('script')}
        self.assertTrue(before_automation)
        self.assertTrue(before_scripts)
        old_device_name = self.f.device.async_get(device.id).name_by_user
        async def refresh(): pass
        review = await runtime.native_yaml_prepare(hass, mapping,
            entity_names={old: 'Reviewed fixture'}, device_names={device.id: 'Reviewed device'},
            actor_id='synthetic-admin', guard=lambda: None, refresh=refresh)
        self.assertEqual((review['entity_count'], review['device_count']), (1, 1))
        result = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
            actor_id='synthetic-admin', guard=lambda: None, refresh=refresh)
        self.assertEqual(result['state'], 'applied')
        self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
        self.assertEqual(self.f.device.async_get(device.id).name_by_user, 'Reviewed device')
        await hass.services.async_call('script', 'corpus_unrelated', {}, blocking=True)
        await hass.services.async_call('automation', 'trigger', {'entity_id': next(iter(before_automation))}, blocking=True)
        self.assertIsNotNone(hass.states.get('script.corpus_unrelated').attributes.get('last_triggered'))
        self.assertIsNotNone(hass.states.get(next(iter(before_automation))).attributes.get('last_triggered'))
        first_script_run = hass.states.get('script.corpus_unrelated').attributes['last_triggered']
        first_automation_run = hass.states.get(next(iter(before_automation))).attributes['last_triggered']
        result = await runtime.native_yaml_reverse(hass, review['operation_id'], review['fingerprint'],
            guard=lambda: None, refresh=refresh)
        self.assertEqual(result['state'], 'rolled_back')
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertEqual(self.f.device.async_get(device.id).name_by_user, old_device_name)
        await hass.services.async_call('script', 'corpus_unrelated', {}, blocking=True)
        await hass.services.async_call('automation', 'trigger', {'entity_id': next(iter(before_automation))}, blocking=True)
        self.assertGreater(hass.states.get('script.corpus_unrelated').attributes['last_triggered'], first_script_run)
        self.assertGreater(hass.states.get(next(iter(before_automation))).attributes['last_triggered'], first_automation_run)
        self.assertEqual((self.f.root / 'configuration.yaml').read_bytes(), before_config)
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        self.assertEqual({state.entity_id: state.state for state in hass.states.async_all('automation')}, before_automation)
        self.assertEqual({state.entity_id: state.state for state in hass.states.async_all('script')}, before_scripts)

    async def test_pending_native_flush_restarts_prepare_before_review(self):
        native = self.module('native_discovery_inputs')
        original = native.async_observe_discovery_inputs
        injected = []
        async def observe(hass, *, guard, settle=False, on_settle=None):
            if settle and on_settle is not None and not injected:
                manager = hass.config_entries
                store = manager._store
                before = Path(store.path).stat().st_ino
                raw = Path(store.path).read_bytes()
                store.async_delay_save(manager._data_to_save, 3600)
                injected.append((before, raw))
            result = await original(hass, guard=guard, settle=settle, on_settle=on_settle)
            if settle and on_settle is not None and len(injected) == 1:
                before, raw = injected[0]
                self.assertNotEqual(Path(hass.config_entries._store.path).stat().st_ino, before)
                self.assertEqual(Path(hass.config_entries._store.path).read_bytes(), raw)
                injected.append('settled')
            return result
        with patch.object(native, 'async_observe_discovery_inputs', observe):
            await self.test_registry_device_with_unrelated_automation_script_apply_inverse()
        self.assertEqual(len(injected), 2)

    async def test_initial_accepted_state_pending_restarts_before_review(self):
        import inspect
        native = self.module('native_discovery_inputs')
        original = native.async_observe_discovery_inputs
        injected = []
        async def observe(hass, *, guard, settle=False, on_settle=None):
            frames = inspect.stack()
            initial = any(frame.function == 'position_at_inputs'
                          and frame.frame.f_locals.get('propagate_pending') is True
                          for frame in frames)
            accepted = any(frame.function == 'async_observe_accepted_state_bindings'
                           for frame in frames)
            if initial and accepted and not settle and not injected:
                manager = hass.config_entries
                store = manager._store
                injected.append((Path(store.path).stat().st_ino, Path(store.path).read_bytes()))
                store.async_delay_save(manager._data_to_save, 3600)
            return await original(hass, guard=guard, settle=settle, on_settle=on_settle)
        backend = __import__('custom_components.ha_entity_renamer', fromlist=['Runtime'])
        original_retry = backend._retry_pending_review
        retries = []
        async def retry(*args, **kwargs):
            retries.append(True)
            return await original_retry(*args, **kwargs)
        with patch.object(native, 'async_observe_discovery_inputs', observe), \
             patch.object(backend, '_retry_pending_review', retry):
            await self.test_registry_device_with_unrelated_automation_script_apply_inverse()
        self.assertEqual(retries, [True])
        self.assertEqual(len(injected), 1)
        before, raw = injected[0]
        self.assertNotEqual(Path(self.f.hass.config_entries._store.path).stat().st_ino, before)
        self.assertEqual(Path(self.f.hass.config_entries._store.path).read_bytes(), raw)

    async def test_pre_baseline_pending_restarts_before_review(self):
        inputs = self.module('native_input_baseline')
        original = inputs.async_capture_native_input_baseline
        seen = []
        async def capture(hass, receipt, *, guard):
            if not seen:
                seen.append(True)
                hass.config_entries._store.async_delay_save(hass.config_entries._data_to_save, 0.05)
            return await original(hass, receipt, guard=guard)
        with patch.object(inputs, 'async_capture_native_input_baseline', capture):
            await self.test_registry_device_with_unrelated_automation_script_apply_inverse()
        self.assertEqual(seen, [True])

    async def test_initial_foreign_device_change_replans_and_preserves_apply_inverse(self):
        import inspect
        native = self.module('native_discovery_inputs')
        original = native.async_observe_discovery_inputs
        runtime_class = __import__('custom_components.ha_entity_renamer', fromlist=['Runtime']).Runtime
        prepare_once = runtime_class._native_yaml_prepare_once
        attempts, foreign = [], []
        async def wrapped(runtime, *args, **kwargs):
            attempts.append(kwargs['retry'])
            if kwargs['retry']:
                self.assertFalse(any(row['payload'].get('kind') == 'ordinary_review_lease'
                    for row in runtime.preparations.plans.values() if type(row['payload']) is dict))
                self.assertFalse(runtime.reviews)
            return await prepare_once(runtime, *args, **kwargs)
        async def observe(hass, *, guard, settle=False, on_settle=None):
            frames = inspect.stack()
            initial = any(frame.function == 'position_at_inputs'
                          and frame.frame.f_locals.get('propagate_pending') is True for frame in frames)
            accepted = any(frame.function == 'async_observe_accepted_state_bindings' for frame in frames)
            if initial and accepted and not settle and not foreign:
                entry = hass.config_entries.async_entries('threshold')[0]
                row = self.f.device.async_get_or_create(config_entry_id=entry.entry_id,
                    identifiers={('threshold', 'foreign-retry-device')}, name='Foreign retained')
                foreign.append(row.id)
                self.f.device._store.async_delay_save(self.f.device._data_to_save, 3600)
            return await original(hass, guard=guard, settle=settle, on_settle=on_settle)
        with patch.object(native, 'async_observe_discovery_inputs', observe), \
             patch.object(runtime_class, '_native_yaml_prepare_once', wrapped):
            await self.test_registry_device_with_unrelated_automation_script_apply_inverse()
        self.assertEqual(attempts, [False, True])
        self.assertEqual(len(foreign), 1)
        self.assertEqual(self.f.device.async_get(foreign[0]).name, 'Foreign retained')

    async def _selected_retry_drift(self, kind):
        import inspect
        from homeassistant.helpers.entity_registry import RegistryEntryDisabler
        runtime, mapping, uuids, original, device = await self.restored_source()
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        if kind == 'remove-member':
            member = self.f.entity.async_get_or_create('sensor', 'threshold', 'retry-peer-remove',
                config_entry=hass.config_entries.async_get_entry(self.f.entity.async_get(old).config_entry_id),
                device_id=device.id, disabled_by=RegistryEntryDisabler.USER)
            await self._flush_documents()
        native = self.module('native_discovery_inputs')
        observe_original = native.async_observe_discovery_inputs
        runtime_class = type(runtime)
        prepare_once = runtime_class._native_yaml_prepare_once
        triggered, changed = [], []
        async def observe(hass, *, guard, settle=False, on_settle=None):
            frames = inspect.stack()
            initial = any(frame.function == 'position_at_inputs'
                          and frame.frame.f_locals.get('propagate_pending') is True for frame in frames)
            accepted = any(frame.function == 'async_observe_accepted_state_bindings' for frame in frames)
            if initial and accepted and not settle and not triggered:
                triggered.append(True)
                hass.config_entries._store.async_delay_save(hass.config_entries._data_to_save, 3600)
            return await observe_original(hass, guard=guard, settle=settle, on_settle=on_settle)
        async def wrapped(subject, *args, **kwargs):
            if kwargs['retry'] and not changed:
                changed.append(True)
                if kind == 'identity':
                    self.f.entity.async_update_entity(old, new_unique_id='foreign-rebound-identity')
                elif kind == 'remove-member':
                    self.f.entity.async_remove(member.entity_id)
                else:
                    self.f.entity.async_get_or_create('sensor', 'threshold', 'retry-peer-add',
                        config_entry=hass.config_entries.async_get_entry(self.f.entity.async_get(old).config_entry_id),
                        device_id=device.id, disabled_by=RegistryEntryDisabler.USER)
                await self._flush_documents()
            return await prepare_once(subject, *args, **kwargs)
        async def refresh(): pass
        with patch.object(native, 'async_observe_discovery_inputs', observe), \
             patch.object(runtime_class, '_native_yaml_prepare_once', wrapped):
            with self.assertRaisesRegex((ValueError, self.module('transaction').Refused),
                                        'review_selected_source_changed'):
                await runtime.native_yaml_prepare(hass, mapping, actor_id='synthetic-admin',
                    entity_names={old: 'Reviewed fixture'}, device_names={device.id: 'Reviewed device'},
                    guard=lambda: None, refresh=refresh)
        self.assertEqual((triggered, changed), ([True], [True]))
        self.assertFalse(runtime.reviews)
        self.assertFalse(await runtime.journal.all())
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertIsNone(self.f.device.async_get(device.id).name_by_user)
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)

    async def test_retry_selected_unique_id_change_refuses(self):
        await self._selected_retry_drift('identity')

    async def test_retry_selected_device_member_added_refuses(self):
        await self._selected_retry_drift('add-member')

    async def test_retry_selected_device_member_removed_refuses(self):
        await self._selected_retry_drift('remove-member')

    async def test_initial_pending_twice_refuses_without_effect(self):
        import inspect
        runtime, mapping, _, _, device = await self.restored_source()
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        native = self.module('native_discovery_inputs')
        original = native.async_observe_discovery_inputs
        seen = []
        async def observe(hass, *, guard, settle=False, on_settle=None):
            frames = inspect.stack()
            initial = any(frame.function == 'position_at_inputs'
                          and frame.frame.f_locals.get('propagate_pending') is True
                          for frame in frames)
            accepted = any(frame.function == 'async_observe_accepted_state_bindings'
                           for frame in frames)
            if initial and accepted and not settle and len(seen) < 2:
                seen.append(True)
                hass.config_entries._store.async_delay_save(hass.config_entries._data_to_save, 0.05)
            return await original(hass, guard=guard, settle=settle, on_settle=on_settle)
        async def refresh(): pass
        with patch.object(native, 'async_observe_discovery_inputs', observe):
            with self.assertRaisesRegex(self.module('transaction').Refused, 'review_native_settle_repeated'):
                await runtime.native_yaml_prepare(hass, mapping, actor_id='synthetic-admin',
                    entity_names={old: 'Reviewed fixture'}, device_names={device.id: 'Reviewed device'},
                    guard=lambda: None, refresh=refresh)
        self.assertEqual(seen, [True, True])
        self.assertFalse(runtime.reviews)
        self.assertFalse(await runtime.journal.all())
        self.assertIsNotNone(self.f.entity.async_get(old))
        self.assertIsNone(self.f.entity.async_get(new))

    async def test_initial_pending_replans_source_change_during_settle(self):
        import inspect
        runtime, mapping, _, _, device = await self.restored_source()
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        native = self.module('native_discovery_inputs')
        original = native.async_observe_discovery_inputs
        injected = []
        async def observe(hass, *, guard, settle=False, on_settle=None):
            frames = inspect.stack()
            initial = any(frame.function == 'position_at_inputs'
                          and frame.frame.f_locals.get('propagate_pending') is True
                          for frame in frames)
            accepted = any(frame.function == 'async_observe_accepted_state_bindings'
                           for frame in frames)
            if initial and accepted and not settle and not injected:
                injected.append(True)
                hass.config_entries._store.async_delay_save(hass.config_entries._data_to_save, 0.05)
            result = await original(hass, guard=guard, settle=settle, on_settle=on_settle)
            if settle and injected == [True]:
                injected.append('changed')
                path = self.f.root / 'configuration.yaml'
                path.write_bytes(path.read_bytes() + b'\n# foreign source change\n')
            return result
        async def refresh(): pass
        with patch.object(native, 'async_observe_discovery_inputs', observe):
            review = await runtime.native_yaml_prepare(hass, mapping, actor_id='synthetic-admin',
                entity_names={old: 'Reviewed fixture'}, device_names={device.id: 'Reviewed device'},
                guard=lambda: None, refresh=refresh)
        self.assertEqual(injected, [True, 'changed'])
        self.assertIn(review['operation_id'], runtime.reviews)
        self.assertIn(b'# foreign source change', (self.f.root / 'configuration.yaml').read_bytes())
        self.assertFalse(await runtime.journal.all())
        self.assertIsNotNone(self.f.entity.async_get(old))
        self.assertIsNone(self.f.entity.async_get(new))

    async def test_initial_review_allows_unrelated_storage_write_after_worker(self):
        runtime, mapping, uuids, original = await self.data(variant='sensor', source_consumers=True)
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        baseline = self.module('native_input_baseline')
        drain = baseline._drained
        changed = []
        async def injected(executor, function):
            result = await drain(executor, function)
            import inspect
            state = (inspect.getclosurevars(function).nonlocals.get('detached')
                     if inspect.isfunction(function) and 'detached' in function.__code__.co_freevars else None)
            accepted = self.module('accepted_input_state').AcceptedInputState
            if type(state) is accepted and not changed:
                scopes = {path: names for path, _, names in state.source_directories}
                self.assertIsNotNone(scopes['.storage'])
                self.assertNotIn('unrelated-retry-test', scopes['.storage'])
                (self.f.root / '.storage' / 'unrelated-retry-test').write_bytes(b'unrelated')
                changed.append(True)
            return result
        async def refresh(): pass
        with patch.object(baseline, '_drained', injected):
            review = await runtime.native_yaml_prepare(hass, mapping, actor_id='synthetic-admin',
                entity_names={old: 'Reviewed fixture'}, guard=lambda: None, refresh=refresh)
        self.assertEqual(changed, [True])
        self.assertIn(review['operation_id'], runtime.reviews)
        self.assertFalse(await runtime.journal.all())
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)

    async def test_initial_review_source_rewrite_after_worker_refuses(self):
        runtime, mapping, uuids, original = await self.data(variant='sensor', source_consumers=True)
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        baseline = self.module('native_input_baseline')
        drain = baseline._drained
        changed = []
        async def injected(executor, function):
            result = await drain(executor, function)
            import inspect
            state = (inspect.getclosurevars(function).nonlocals.get('detached')
                     if inspect.isfunction(function) and 'detached' in function.__code__.co_freevars else None)
            accepted = self.module('accepted_input_state').AcceptedInputState
            if type(state) is accepted and not changed:
                path = self.f.root / 'template.yaml'
                data, info = path.read_bytes(), path.stat()
                path.write_bytes(data.replace(b'sensor', b'SENSOR', 1))
                os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
                changed.append(True)
            return result
        async def refresh(): pass
        with patch.object(baseline, '_drained', injected):
            with self.assertRaises((ValueError, self.module('transaction').Refused)):
                await runtime.native_yaml_prepare(hass, mapping, actor_id='synthetic-admin',
                    entity_names={old: 'Reviewed fixture'}, guard=lambda: None, refresh=refresh)
        self.assertEqual(changed, [True])
        self.assertFalse(runtime.reviews)
        self.assertFalse(await runtime.journal.all())
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertNotEqual((self.f.root / 'template.yaml').read_bytes(), original)

    async def test_initial_review_source_rewrite_after_discovery_refuses(self):
        runtime, mapping, uuids, original = await self.data(variant='sensor', source_consumers=True)
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        discovery = self.module('discovery_final_fence')
        finalize = discovery.async_finalize_discovery
        changed = []
        worker_finished = threading.Event()
        accepted = self.module('accepted_input_state').AcceptedInputState
        physical = accepted.prepare_review_identity
        def marked(*args):
            result = physical(*args)
            worker_finished.set()
            return result
        async def injected(*args, **kwargs):
            result = await finalize(*args, **kwargs)
            if worker_finished.is_set() and not changed:
                path = self.f.root / 'template.yaml'
                data, info = path.read_bytes(), path.stat()
                path.write_bytes(data.replace(b'sensor', b'SENSOR', 1))
                os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
                changed.append(True)
            return result
        async def refresh(): pass
        with patch.object(discovery, 'async_finalize_discovery', injected), \
             patch.object(accepted, 'prepare_review_identity', marked):
            with self.assertRaises((ValueError, self.module('transaction').Refused)) as refusal:
                await runtime.native_yaml_prepare(hass, mapping, actor_id='synthetic-admin',
                    entity_names={old: 'Reviewed fixture'}, guard=lambda: None, refresh=refresh)
        self.assertEqual(changed, [True], f'earlier refusal: {refusal.exception!r}')
        self.assertFalse(runtime.reviews)
        self.assertFalse(await runtime.journal.all())
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertNotEqual((self.f.root / 'template.yaml').read_bytes(), original)

    async def _review_token_replan(self, mode):
        runtime, mapping, uuids, original = await self.data(variant='sensor', source_consumers=True)
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        discovery = self.module('discovery_final_fence')
        finalize = discovery.async_finalize_discovery
        accepted = self.module('accepted_input_state').AcceptedInputState
        physical = accepted.prepare_review_identity
        prepare_once = type(runtime)._native_yaml_prepare_once
        worker_finished = threading.Event()
        attempts, invalidated = [], []
        def marked(*args):
            result = physical(*args)
            worker_finished.set()
            return result
        async def wrapped(subject, *args, **kwargs):
            attempts.append(kwargs['retry'])
            if kwargs['retry']:
                self.assertFalse(subject.reviews)
                self.assertFalse(any(row['payload'].get('kind') == 'ordinary_review_lease'
                    for row in subject.preparations.plans.values() if type(row['payload']) is dict))
                if mode == 'selected':
                    self.f.entity.async_update_entity(old, new_unique_id='rebound-review-identity')
                    await self._flush_documents()
            return await prepare_once(subject, *args, **kwargs)
        async def injected(*args, **kwargs):
            result = await finalize(*args, **kwargs)
            if worker_finished.is_set() and (not invalidated or mode == 'twice'):
                path = self.f.root / 'template.yaml'
                data = path.read_bytes()
                path.write_bytes(data)  # Real ctime/mtime change, same source bytes.
                invalidated.append(True)
                worker_finished.clear()
            return result
        async def refresh(): pass
        with self.assertLogs(self.module('native_template_state_yaml').__name__, level='WARNING') as logs, \
             patch.object(discovery, 'async_finalize_discovery', injected), \
             patch.object(accepted, 'prepare_review_identity', marked), \
             patch.object(type(runtime), '_native_yaml_prepare_once', wrapped):
            if mode == 'success':
                review = await runtime.native_yaml_prepare(hass, mapping, actor_id='synthetic-admin',
                    entity_names={old: 'Reviewed fixture'}, guard=lambda: None, refresh=refresh)
                self.assertIn(review['operation_id'], runtime.reviews)
                applied = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                    actor_id='synthetic-admin', guard=lambda: None, refresh=refresh)
                self.assertEqual(applied['state'], 'applied')
                self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
                self.assertIsNone(self.f.entity.async_get(old))
                self.assertNotEqual((self.f.root / 'template.yaml').read_bytes(), original)
                reversed_result = await runtime.native_yaml_reverse(hass, review['operation_id'],
                    review['fingerprint'], guard=lambda: None, refresh=refresh)
                self.assertEqual(reversed_result['state'], 'rolled_back')
            else:
                reason = 'review_selected_source_changed' if mode == 'selected' else 'review_source_invalidated_repeated'
                with self.assertRaisesRegex(self.module('transaction').Refused, reason):
                    await runtime.native_yaml_prepare(hass, mapping, actor_id='synthetic-admin',
                        entity_names={old: 'Reviewed fixture'}, guard=lambda: None, refresh=refresh)
                self.assertFalse(runtime.reviews)
        self.assertEqual(attempts, [False, True])
        self.assertTrue(any('source_file:ctime' in line for line in logs.output), logs.output)
        self.assertTrue(all('template.yaml' not in line for line in logs.output))
        self.assertEqual(len(invalidated), 2 if mode == 'twice' else 1)
        if mode != 'success':
            self.assertFalse(await runtime.journal.all())
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)

    async def test_review_token_diagnostic_categories_are_closed(self):
        from types import SimpleNamespace
        module = self.module('native_template_state_yaml')
        state = SimpleNamespace(source_directories=(('.', (), None), ('.storage', (), ('secret',))),
                                _sources={'private-source.yaml': ()})
        before = (('root', (1, 2, 3)), ('.', (1, 2, 3, 4, 5, 6)),
                  ('.storage', (1, 2, 3)), ('.storage/secret', None),
                  ('private-source.yaml', (1, 2, 3, 4, 5, 6)),
                  ('/private/native.py', (1, 2, 3, 4, 5, 6)))
        after = (('root', (1, 2, 3)), ('.', (1, 2, 3, 4, 7, 6)),
                 ('.storage', (1, 9, 3)), ('.storage/secret', (1, 2, 3, 4, 5, 6)),
                 ('private-source.yaml', (1, 2, 3, 4, 5, 7)),
                 ('/private/native.py', (1, 2, 3, 4, 5, 7)))
        with self.assertLogs(module.__name__, level='WARNING') as logs:
            with self.assertRaises(module.ReviewSourceInvalidated):
                module._review_source_current(after, before, state)
        line = logs.output[0]
        for category in ('directory_full:mtime', 'directory_scoped:inode',
                         'scoped_child:presence', 'source_file:ctime', 'native_file:ctime'):
            self.assertIn(category, line)
        self.assertNotIn('private', line)
        self.assertNotIn('secret', line)
        self.assertNotIn('.storage', line)
        module._review_source_current(before, before, state)

    async def test_review_token_inventory_diagnostics_are_closed_and_strict(self):
        from types import SimpleNamespace
        module = self.module('native_template_state_yaml')
        state = SimpleNamespace(source_directories=(('.', (), None),),
                                _sources={'private-source.yaml': ()})
        a = ('private-source.yaml', (1, 2, 3, 4, 5, 6))
        b = ('/private/native.py', (1, 2, 3, 4, 5, 6))
        cases = [((b, a), (a, b), ('inventory:order',)),
                 ((a, b, b), (a, b), ('inventory:count', 'inventory:key_set',
                                       'inventory:added_native_file')),
                 ((a,), (a, b), ('inventory:count', 'inventory:key_set',
                                 'inventory:removed_native_file')),
                 ((a, ('/private/other.py', b[1])), (a, b),
                  ('inventory:key_set', 'inventory:added_native_file',
                   'inventory:removed_native_file'))]
        for current, expected, categories in cases:
            with self.subTest(categories=categories), self.assertLogs(module.__name__, level='WARNING') as logs:
                with self.assertRaises(module.ReviewSourceInvalidated):
                    module._review_source_current(current, expected, state)
            for category in categories:
                self.assertIn(category, logs.output[0])
            self.assertNotIn('private', logs.output[0])

    async def test_review_token_change_replans(self):
        await self._review_token_replan('success')

    async def test_review_token_change_selected_drift_refuses(self):
        await self._review_token_replan('selected')

    async def test_review_token_change_twice_refuses(self):
        await self._review_token_replan('twice')

    async def test_initial_review_worker_cancel_drains_without_effect(self):
        runtime, mapping, uuids, original = await self.data(variant='sensor', source_consumers=True)
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        accepted = self.module('accepted_input_state').AcceptedInputState
        physical = accepted.prepare_review_identity
        entered, release = threading.Event(), threading.Event()
        def held(*args):
            entered.set()
            if not release.wait(60):
                raise AssertionError('test worker was not released')
            return physical(*args)
        async def refresh(): pass
        with patch.object(accepted, 'prepare_review_identity', held):
            task = asyncio.create_task(runtime.native_yaml_prepare(hass, mapping,
                actor_id='synthetic-admin', entity_names={old: 'Reviewed fixture'},
                guard=lambda: None, refresh=refresh))
            try:
                self.assertTrue(await hass.async_add_executor_job(entered.wait, 120))
                task.cancel()
                await asyncio.sleep(0.05)  # Deliver cancellation while the worker is held.
                self.assertFalse(task.done())
                self.assertTrue(runtime.operation_lock.locked())
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(runtime.operation_lock.locked())
        self.assertFalse(runtime.reviews)
        self.assertFalse(await runtime.journal.all())
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)

    async def test_initial_pending_cancel_cleans_preparation(self):
        import inspect
        runtime, mapping, _, _, device = await self.restored_source()
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        native = self.module('native_discovery_inputs')
        retention = self.module('review_retention')
        original = native.async_observe_discovery_inputs
        entered = asyncio.Event()
        injected = []
        async def observe(hass, *, guard, settle=False, on_settle=None):
            frames = inspect.stack()
            initial = any(frame.function == 'position_at_inputs'
                          and frame.frame.f_locals.get('propagate_pending') is True
                          for frame in frames)
            accepted = any(frame.function == 'async_observe_accepted_state_bindings'
                           for frame in frames)
            if initial and accepted and not settle and not injected:
                injected.append(True)
                hass.config_entries._store.async_delay_save(hass.config_entries._data_to_save, 30)
            if settle and injected:
                entered.set()
                await asyncio.Event().wait()
            return await original(hass, guard=guard, settle=settle, on_settle=on_settle)
        async def refresh(): pass
        with patch.object(native, 'async_observe_discovery_inputs', observe):
            task = asyncio.create_task(runtime.native_yaml_prepare(hass, mapping,
                actor_id='synthetic-admin', entity_names={old: 'Reviewed fixture'},
                device_names={device.id: 'Reviewed device'}, guard=lambda: None, refresh=refresh))
            await asyncio.wait_for(entered.wait(), 180)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(runtime.operation_lock.locked())
        self.assertFalse(runtime.reviews)
        self.assertFalse(await runtime.journal.all())
        self.assertIsNotNone(self.f.entity.async_get(old))
        self.assertIsNone(self.f.entity.async_get(new))
        async with runtime.operation_lock:
            stores, _ = await retention.async_reclaim_reviews(hass, keep=runtime.reviews, guard=lambda: None)
        self.assertFalse(any(row['payload'].get('kind') == 'ordinary_review_lease'
                             for row in stores.plans.values() if type(row['payload']) is dict))

    async def test_initial_pending_refuses_foreign_native_change(self):
        import inspect
        from homeassistant.helpers.storage import Store
        runtime, mapping, _, _, device = await self.restored_source()
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        native = self.module('native_discovery_inputs')
        original = native.async_observe_discovery_inputs
        injected = []
        async def observe(hass, *, guard, settle=False, on_settle=None):
            frames = inspect.stack()
            initial = any(frame.function == 'position_at_inputs'
                          and frame.frame.f_locals.get('propagate_pending') is True
                          for frame in frames)
            accepted = any(frame.function == 'async_observe_accepted_state_bindings'
                           for frame in frames)
            if initial and accepted and not settle and not injected:
                injected.append(True)
                hass.config_entries._store.async_delay_save(hass.config_entries._data_to_save, 3600)
            return await original(hass, guard=guard, settle=settle, on_settle=on_settle)
        original_write = Store._async_write_data
        async def write(store, data):
            if store is hass.config_entries._store:
                self.f.entity.async_update_entity(old, name='Foreign native change')
            await original_write(store, data)
        async def refresh(): pass
        with patch.object(native, 'async_observe_discovery_inputs', observe), \
             patch.object(Store, '_async_write_data', write):
            with self.assertRaises((ValueError, self.module('transaction').Refused)):
                await runtime.native_yaml_prepare(hass, mapping, actor_id='synthetic-admin',
                    entity_names={old: 'Reviewed fixture'}, device_names={device.id: 'Reviewed device'},
                    guard=lambda: None, refresh=refresh)
        self.assertEqual(injected, [True])
        self.assertEqual(self.f.entity.async_get(old).name, 'Foreign native change')
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertFalse(runtime.reviews)
        self.assertFalse(await runtime.journal.all())

    async def test_initial_pending_timeout_refuses_without_effect(self):
        import inspect
        runtime, mapping, _, _, device = await self.restored_source()
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        native = self.module('native_discovery_inputs')
        original = native.async_observe_discovery_inputs
        injected = []
        async def observe(hass, *, guard, settle=False, on_settle=None):
            frames = inspect.stack()
            initial = any(frame.function == 'position_at_inputs'
                          and frame.frame.f_locals.get('propagate_pending') is True
                          for frame in frames)
            accepted = any(frame.function == 'async_observe_accepted_state_bindings'
                           for frame in frames)
            if initial and accepted and not settle and not injected:
                injected.append(True)
                hass.config_entries._store._load_future = hass.loop.create_future()
            return await original(hass, guard=guard, settle=settle, on_settle=on_settle)
        async def refresh(): pass
        with patch.object(native, 'async_observe_discovery_inputs', observe), \
             patch.object(native, '_SETTLE_TIMEOUT', 0.05), \
             patch.object(native, '_SETTLE_INTERVAL', 0.01):
            with self.assertRaises(native.DiscoveryNativeInputsError):
                await runtime.native_yaml_prepare(hass, mapping, actor_id='synthetic-admin',
                    entity_names={old: 'Reviewed fixture'}, device_names={device.id: 'Reviewed device'},
                    guard=lambda: None, refresh=refresh)
        self.assertEqual(injected, [True])
        hass.config_entries._store._load_future = None
        self.assertFalse(runtime.reviews)
        self.assertFalse(await runtime.journal.all())
        self.assertIsNotNone(self.f.entity.async_get(old))
        self.assertIsNone(self.f.entity.async_get(new))

    async def test_post_effect_pending_never_retries_prepare(self):
        runtime, mapping, _, _, device = await self.restored_source()
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        async def refresh(): pass
        review = await runtime.native_yaml_prepare(hass, mapping, actor_id='synthetic-admin',
            entity_names={old: 'Reviewed fixture'}, device_names={device.id: 'Reviewed device'},
            guard=lambda: None, refresh=refresh)
        observer = self.module('native_template_state_yaml').NativeTemplateStateYAMLRuntimeReadback
        original = observer.position_at_inputs
        backend = __import__('custom_components.ha_entity_renamer', fromlist=['Runtime'])
        injected, retries = [], []
        async def position(instance, change, state, **kwargs):
            if self.f.entity.async_get(new) is not None and not injected:
                self.assertFalse(kwargs.get('propagate_pending', False))
                injected.append(True)
                hass.config_entries._store.async_delay_save(hass.config_entries._data_to_save, 30)
            return await original(instance, change, state, **kwargs)
        async def retry(*args, **kwargs):
            retries.append(True)
            raise AssertionError('post-effect prepare retry')
        with patch.object(observer, 'position_at_inputs', position), \
             patch.object(backend, '_retry_pending_review', retry):
            try:
                result = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                    actor_id='synthetic-admin', guard=lambda: None, refresh=refresh)
            except Exception:
                result = None
        self.assertEqual(injected, [True])
        self.assertFalse(retries)
        if result is not None:
            self.assertNotEqual(result['state'], 'applied')
        self.assertFalse(runtime.reviews)

    async def test_discovery_retry_replans_foreign_source_change(self):
        runtime, mapping, _, _, device = await self.restored_source()
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        native = self.module('native_discovery_inputs')
        original = native.async_observe_discovery_inputs
        injected = False
        async def observe(hass, *, guard, settle=False, on_settle=None):
            nonlocal injected
            if settle and on_settle is not None and not injected:
                injected = True
                hass.config_entries._store.async_delay_save(hass.config_entries._data_to_save, 0.05)
                callback = on_settle
                def changed(value):
                    path = self.f.root / 'configuration.yaml'
                    path.write_bytes(path.read_bytes() + b'\n# foreign change\n')
                    callback(value)
                on_settle = changed
            return await original(hass, guard=guard, settle=settle, on_settle=on_settle)
        async def refresh(): pass
        with patch.object(native, 'async_observe_discovery_inputs', observe):
            review = await runtime.native_yaml_prepare(hass, mapping, actor_id='synthetic-admin', entity_names={old: 'Reviewed fixture'},
                device_names={device.id: 'Reviewed device'}, guard=lambda: None, refresh=refresh)
        self.assertTrue(injected)
        self.assertIsNotNone(self.f.entity.async_get(old))
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertIn(review['operation_id'], runtime.reviews)
        self.assertIn(b'# foreign change', (self.f.root / 'configuration.yaml').read_bytes())
        self.assertFalse(await runtime.journal.all())

    async def test_cancel_before_retry_reclaims_lease_without_effect(self):
        runtime, mapping, _, _, device = await self.restored_source()
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        native = self.module('native_discovery_inputs')
        retention = self.module('review_retention')
        original_observe, original_reclaim = native.async_observe_discovery_inputs, retention.async_reclaim_reviews
        entered = asyncio.Event()
        injected = False
        calls = 0
        async def observe(hass, *, guard, settle=False, on_settle=None):
            nonlocal injected
            if settle and on_settle is not None and not injected:
                injected = True
                hass.config_entries._store.async_delay_save(hass.config_entries._data_to_save, 0.05)
            return await original_observe(hass, guard=guard, settle=settle, on_settle=on_settle)
        async def reclaim(hass, *, keep=(), guard):
            nonlocal calls
            calls += 1
            if calls == 2:
                entered.set()
                await asyncio.Event().wait()
            return await original_reclaim(hass, keep=keep, guard=guard)
        async def refresh(): pass
        with patch.object(native, 'async_observe_discovery_inputs', observe), \
             patch.object(retention, 'async_reclaim_reviews', reclaim):
            task = asyncio.create_task(runtime.native_yaml_prepare(hass, mapping,
                actor_id='synthetic-admin', entity_names={old: 'Reviewed fixture'},
                device_names={device.id: 'Reviewed device'}, guard=lambda: None, refresh=refresh))
            await asyncio.wait_for(entered.wait(), 120)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertIsNotNone(self.f.entity.async_get(old))
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertFalse(runtime.reviews)
        self.assertFalse(await runtime.journal.all())
        async with runtime.operation_lock:
            stores, _ = await original_reclaim(hass, keep=runtime.reviews, guard=lambda: None)
        self.assertFalse(any(row['payload'].get('kind') == 'ordinary_review_lease'
                             for row in stores.plans.values() if type(row['payload']) is dict))

    async def test_prepare_refuses_missing_selected_entity_and_device(self):
        runtime, mapping, uuids, original, device = await self.restored_source()
        hass = self.f.hass
        async def refresh(): pass
        old = next(iter(mapping))
        cases = (({'sensor.missing_selected': 'sensor.missing_target'}, {}, 'source_uuid_required'),
                 (mapping, {'entity_names': {old: 'Reviewed fixture'},
                            'device_names': {device.id: 'Reviewed device',
                                             'missing-device-id': 'Missing'}},
                  'selected_device_source_required'))
        for selected_mapping, kwargs, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(self.module('transaction').Refused, reason):
                    await runtime.native_yaml_prepare(hass, selected_mapping, actor_id='synthetic-admin',
                        guard=lambda: None, refresh=refresh, **kwargs)
                self.assertFalse(runtime.reviews)
                self.assertEqual(await runtime.journal.active(), ())
                self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
                self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)

    async def test_registry_device_refuses_automation_owner_before_effect(self):
        runtime, mapping, uuids, original, device = await self.restored_source()
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        automation = next(iter(hass.states.async_all('automation'))).entity_id
        self.f.entity.async_update_entity(automation, device_id=device.id)
        await hass.async_block_till_done()
        await self._flush_documents()
        async def refresh(): pass
        with self.assertRaises(self.module('transaction').Refused):
            await runtime.native_yaml_prepare(hass, mapping,
                entity_names={old: 'Reviewed fixture'}, device_names={device.id: 'Reviewed device'},
                actor_id='synthetic-admin', guard=lambda: None, refresh=refresh)
        self.assertFalse(runtime.reviews)
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertIsNone(self.f.device.async_get(device.id).name_by_user)
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)

    async def test_registry_device_source_drift_refuses_before_effect(self):
        runtime, mapping, uuids, original, device = await self.restored_source()
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        async def refresh(): pass
        review = await runtime.native_yaml_prepare(hass, mapping,
            entity_names={old: 'Reviewed fixture'}, device_names={device.id: 'Reviewed device'},
            actor_id='synthetic-admin', guard=lambda: None, refresh=refresh)
        path = self.f.root / 'configuration.yaml'
        path.write_bytes(path.read_bytes() + b'\n# synthetic external drift\n')
        with self.assertRaises((ValueError, self.module('transaction').Refused)):
            await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                actor_id='synthetic-admin', guard=lambda: None, refresh=refresh)
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertIsNone(self.f.device.async_get(device.id).name_by_user)
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)

    async def test_real_ws_first_and_502(self):
        from homeassistant.config_entries import ConfigEntry, ConfigEntryDisabler
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        from types import MappingProxyType
        runtime, mapping, _, _ = await self.data(variant='sensor', source_consumers=True)
        hass = self.f.hass
        accepted = self.module('accepted_input_state').AcceptedInputState
        physical = accepted.prepare_review_identity
        loop_thread = threading.get_ident()
        physical_threads = []
        def checked_physical(state, *args):
            physical_threads.append(threading.get_ident())
            print(f'ws_pre_review_source_files={len(state.source_files)} '
                  f'source_directories={len(state.source_directories)}', flush=True)
            if threading.get_ident() == loop_thread:
                raise AssertionError('pre-review physical guard ran on HA event loop')
            return physical(state, *args)
        proof_patch = patch.object(accepted, 'prepare_review_identity', checked_physical)
        proof_patch.start()
        self.addCleanup(proof_patch.stop)
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
                config_entry=(entries[local % len(entries)] if not deleted and local < 52
                              else entries[i % len(entries)] if i % 5 else None),
                device_id=(devices[local].id if not deleted and local < 52 else None),
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
            print(json.dumps({'diagnostic_ping_count':len(samples),'max_ping_rtt':max(samples,default=None),'max_probe_gap':max(gaps,default=None),'closed':ws.close_code}),flush=True)
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
            counts = Counter()
            stop = threading.Event()
            loop_thread = threading.get_ident()
            def sample():
                last = monotonic()
                while not stop.wait(.1):
                    frame = sys._current_frames().get(loop_thread)
                    stack = []
                    while frame is not None:
                        filename = frame.f_code.co_filename
                        if 'ha_entity_renamer/' in filename:
                            stack.append(f'{filename.rsplit("/", 1)[-1]}:{frame.f_code.co_name}:{frame.f_lineno}')
                        frame = frame.f_back
                    if stack:
                        counts[';'.join(reversed(stack[-8:]))] += 1
                    if monotonic() - last >= 60:
                        print(f'ws_profile_case={label} samples={counts.most_common(12)}', flush=True)
                        last = monotonic()
            sampler = threading.Thread(target=sample, daemon=True)
            sampler.start()
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(data), 630)
            finally:
                stop.set()
                sampler.join(timeout=2)
                print(f'ws_profile_final_case={label} samples={counts.most_common(20)}', flush=True)
            print(f'ws_case={label} exit={process.returncode} result={stdout.decode()} stderr={stderr.decode()}', flush=True)
            self.assertEqual(process.returncode, 0)
            self.assertTrue(physical_threads, 'pre-review physical guard was not exercised')
            print(f'ws_pre_review_physical_executor_calls={len(physical_threads)}', flush=True)
