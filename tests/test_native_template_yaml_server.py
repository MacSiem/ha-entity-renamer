"""Owner-run native server producer tests; worker does not execute these.

Data setup reuses native fixtures with validation disabled. No fixture Plan,
receipt, preparations, chain, observer or positive verifier enters product IO.
"""
import asyncio
import importlib
import os
import sys
import unittest
from unittest.mock import patch

import test_native_template_yaml_activation as activation
import test_native_template_forward as entry_forward


@unittest.skipUnless(activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeTemplateYAMLServerTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = activation.NativeTemplateYAMLActivationTests.asyncSetUp
    _before_start = activation.NativeTemplateYAMLActivationTests._before_start
    _flush_documents = activation.NativeTemplateYAMLActivationTests._flush_documents
    prepare = activation.NativeTemplateYAMLActivationTests.prepare
    emit = activation.NativeTemplateYAMLActivationTests.emit
    _runtime = entry_forward.NativeTemplateForwardTests._runtime

    def module(self, name):
        return importlib.import_module(activation.fixture.fixtures.PACKAGE + '.' + name)

    async def data(self, *, multiple=False, variant='mixed', binary_extra=None,
                   uidless=False, duplicate_uidless=False, uidless_alias=False,
                   source_consumers=False):
        from homeassistant.const import __version__
        from homeassistant.core import Context
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        from homeassistant.helpers.entity_platform import async_get_platforms
        self.assertIn(__version__, ('2025.6.0', '2026.10.2', '2026.10.4'))
        await self.prepare('yaml', variant=variant, binary_extra=binary_extra, validate=False,
                           uidless=uidless, duplicate_uidless=duplicate_uidless, uidless_alias=uidless_alias)
        self.assertFalse(hasattr(self, 'plan'))
        self.assertFalse(hasattr(self, 'receipt'))
        self.assertFalse(hasattr(self, 'evidence'))
        hass, root = self.f.hass, self.f.root
        self.assertEqual(hass.config_entries.async_entries('template'), [])
        if source_consumers:
            from homeassistant.config import async_hass_config_yaml
            from homeassistant.setup import async_setup_component
            with (root / 'configuration.yaml').open('a') as handle:
                handle.write('\nautomation:\n- id: corpus_unrelated\n'
                    '  alias: Corpus unrelated\n  initial_state: false\n'
                    '  triggers:\n  - trigger: event\n    event_type: corpus_unrelated\n'
                    '  actions:\n  - variables: {retained: unrelated}\n'
                    'script:\n  corpus_unrelated:\n    sequence:\n'
                    '    - variables: {retained: unrelated}\n')
            config = await async_hass_config_yaml(hass)
            for domain in ('automation', 'script'):
                self.assertTrue(await async_setup_component(hass, domain, config))
            await hass.async_block_till_done()
            await self._flush_documents()
        mapping = {self.source_id: 'sensor.accepted_next'}
        self.other = None
        if multiple:
            self.other = type(self.producer)()
            self.other._attr_unique_id = 'template-runtime-second-producer'
            self.other._attr_name = 'Second source'
            await hass.data[DATA_INSTANCES]['sensor'].async_add_entities([self.other])
            mapping[self.other.entity_id] = 'sensor.accepted_second'
            with (root / 'template.yaml').open('a') as handle:
                handle.write('- unique_id: second\n  sensor:\n  - unique_id: value\n'
                    '    name: Second consumer\n    state: "{{ states(\'' + self.other.entity_id
                    + '\') | int(0) * 3 }}"\n')
            await hass.services.async_call('template', 'reload', {}, blocking=True, context=Context())
            await hass.async_block_till_done()
            await self._flush_documents()
        self.identities = {(p.domain, e.unique_id): (e.entity_id, None if (r := self.f.entity.async_get(e.entity_id)) is None else r.id)
            for p in async_get_platforms(hass, 'template') for e in p.entities.values()}
        source_uuids = {old: self.f.entity.async_get(old).id for old in mapping}
        before = (root / 'template.yaml').read_bytes()
        # Retain descriptors only AFTER the real mutator returned real IO audit.
        mutator = self.module('yaml_transaction')
        actual_write = mutator.apply_file_patch
        def retain(*args, **kwargs):
            result = actual_write(*args, **kwargs)
            fd = os.open(root / result['path'], os.O_RDONLY | os.O_NOFOLLOW)
            self.addCleanup(os.close, fd)
            info = os.fstat(fd)
            self.assertEqual((info.st_dev, info.st_ino, info.st_mode), tuple(result['after_identity']))
            return result
        self.enterContext(patch.object(mutator, 'apply_file_patch', retain))
        runtime = await self._runtime(self.module)
        return runtime, mapping, source_uuids, before

    async def stimulus(self, value):
        from homeassistant.helpers.entity_platform import async_get_platforms
        await self.emit(value)
        if self.other is not None:
            self.other._attr_native_value = value
            self.other.async_write_ha_state()
            await self.f.hass.async_block_till_done()
            consumer = next(e for p in async_get_platforms(self.f.hass, 'template')
                            for e in p.entities.values() if e.unique_id == 'second-value')
            self.assertEqual(self.f.hass.states.get(consumer.entity_id).state, str(value * 3))
        self.assertEqual({(p.domain, e.unique_id): (e.entity_id, None if (r := self.f.entity.async_get(e.entity_id)) is None else r.id)
            for p in async_get_platforms(self.f.hass, 'template') for e in p.entities.values()}, self.identities)

    async def flow(self, *, multiple=False, variant='mixed', uidless=False,
                   source_consumers=False):
        runtime, mapping, uuids, original = await self.data(
            multiple=multiple, variant=variant, uidless=uidless,
            **({'source_consumers': True} if source_consumers else {}))
        hass = self.f.hass
        for value in (5, 7):
            await self.stimulus(value)
        factory = self.module('native_template_yaml_server').NativeTemplateYAMLServer
        captured, caller = [], None
        code = factory._install.__code__
        def installed(code_arg, offset):
            frame = sys._getframe(1)
            if frame.f_code is code and frame.f_locals['self'].hass is hass:
                session = frame.f_locals['self']
                captured.append((asyncio.current_task(), session.chain,
                                 session.preparations, session.journal, session))
                self.assertTrue(runtime.operation_lock.locked())
                self.assertIsNot(asyncio.current_task(), caller)
        tool = next(i for i in range(6) if sys.monitoring.get_tool(i) is None)
        sys.monitoring.use_tool_id(tool, 'native YAML server owner observation')
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, installed)
        sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START)
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        try:
            caller = hass.async_create_task(runtime.native_forward(hass, mapping,
                guard=lambda: None, refresh=refresh), 'foreground YAML server forward', eager_start=False)
            result = await asyncio.wait_for(asyncio.shield(caller), getattr(self, 'operation_timeout', 180))
            self.assertEqual(result['state'], 'applied')
            record = await runtime.journal.load(result['operation_id'])
            self.assertEqual(tuple(s.adapter for s in record.plan.steps),
                             ('registry',) * len(mapping) + ('yaml',))
            baseline, composition = runtime.preparations.resolve_composition(record.plan.native_inputs)
            original_ref, original_json = record.plan.native_inputs, baseline.receipt_json
            if uidless:
                self.assertTrue(composition.physical_consumers)
                for domain, key, entity_id in composition.physical_consumers:
                    self.assertEqual(domain, variant)
                    self.assertIn((entity_id, None), self.identities.values())
                    self.assertIsNone(self.f.entity.async_get(entity_id))
                    self.assertTrue(key.startswith('@physical-template-v1:'))
            encoded = self.module('native_template_composition').encode_composition(composition, baseline)
            state_type = self.module('native_template_state_composition').TemplateStateComposition
            if type(composition) is state_type:
                self.assertEqual(encoded['schema'], 3)
                self.assertEqual(encoded['profile'], 'template_state_binding_v3')
                self.assertEqual(composition.mapping, tuple(sorted(mapping.items())))
                self.assertEqual(composition.state_config_program.sha256, composition.coverage_sha256)
            elif uidless:
                self.assertEqual(encoded['schema'], 2)
            else:
                self.assertEqual(encoded['schema'], 1)
                self.assertNotIn('physical_consumers', encoded)
            self.assertEqual(baseline.receipt.changed_domains, ('template',))
            for old, new in mapping.items():
                self.assertIsNone(self.f.entity.async_get(old))
                self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
            for value in (5, 7):
                await self.stimulus(value)
            # Only public operation identity crosses into the new session.
            caller = hass.async_create_task(runtime.native_yaml_reverse(hass,
                result['operation_id'], result['fingerprint'], guard=lambda: None, refresh=refresh),
                'foreground YAML server reverse', eager_start=False)
            reverse = await asyncio.wait_for(asyncio.shield(caller), getattr(self, 'operation_timeout', 180))
            self.assertEqual(reverse['state'], 'rolled_back')
            self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
            final = await runtime.journal.load(result['operation_id'])
            self.assertEqual(final.plan.native_inputs, original_ref)
            reopened, restored = runtime.preparations.resolve_composition(original_ref)
            self.assertEqual(reopened.receipt_json, original_json)
            self.assertEqual(restored, composition)
            self.assertEqual(final.phases, ('undone',) * len(final.plan.steps))
            self.assertEqual(len(captured), 2)
            self.assertIsNot(captured[0][0], captured[1][0])
            self.assertTrue(all(row[0].done() for row in captured))
            self.assertIsNot(captured[0][2], captured[1][2])
            self.assertIsNot(captured[0][3], captured[1][3])
            if source_consumers:
                before_consumers, after_consumers = (row[4].consumers for row in captured)
                sampler_type = self.module('yaml_source_binding')._RuntimeSourceSampler
                for consumers in (before_consumers, after_consumers):
                    self.assertIs(type(consumers.runtime_sampler), sampler_type)
                    self.assertEqual(len(consumers.runtime_sampler._rows), 2)
                    self.assertEqual(consumers.runtime_sampler.sample(hass,
                        consumers.evidence, consumers.observed), consumers.runtime_sample)
                self.assertIsNot(before_consumers.runtime_sampler, after_consumers.runtime_sampler)
                self.assertIsNot(before_consumers.observed, after_consumers.observed)
            self.assertFalse(captured[1][1].writable, 'fresh reader must remain readonly')
            self.assertIsNone(captured[1][1].active)
            self.assertEqual(captured[1][1].root, captured[0][1].root)
            for old in mapping:
                self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
            for value in (5, 7):
                await self.stimulus(value)
            history = self.module('history_guard').NativeHistoryGuard(
                self.module('recorder_guard').NativeRecorderGuard(hass))
            self.assertTrue(await history.verify(composition.source_history))
            self.assertTrue(await history.verify(composition.consumer_history))
            self.assertFalse(runtime.operation_lock.locked())
            self.assertIs(result['family_complete'], False)
            self.assertIs(reverse['migration_ready'], False)
        finally:
            sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.free_tool_id(tool)
            if caller is not None and not caller.done():
                caller.cancel()
                await asyncio.gather(caller, return_exceptions=True)

    async def test_nonempty_source_corpus_mixed_forward_and_fresh_reverse(self):
        await self.flow(variant='mixed', source_consumers=True)

    async def test_handoff_root_last_guard_refusal_restores_bindings_and_monitors(self):
        await self._handoff_root_guard_refusal('witness')

    async def test_handoff_root_source_guard_refusal_restores_bindings_and_monitors(self):
        await self._handoff_root_guard_refusal('source')

    async def _handoff_root_guard_refusal(self, seam):
        from homeassistant.helpers.entity_platform import async_get_platforms
        runtime, mapping, uuids, original = await self.data(variant='sensor')
        hass = self.f.hass
        await self.stimulus(5)
        entities = tuple(entity for platform in async_get_platforms(hass, 'template')
                         for entity in platform.entities.values())
        trackers = tuple(entity._template_result_info for entity in entities)
        witness_type = self.module('native_rename_emission_classifier').NativeRenameEmissions
        io_type = self.module('native_registry').NativeRegistryIO
        source_type = self.module('native_source_handoff').NativeSourceHandoff
        codes = (witness_type.mutate.__code__, source_type.mutate.__code__, io_type.mutate.__code__)
        witnesses, attempts = [], []
        revoked = False
        def guard():
            if revoked:
                raise PermissionError('actual final caller authorization revoked')
        async def refresh():
            guard()
        tool = next(index for index in range(6) if sys.monitoring.get_tool(index) is None)
        sys.monitoring.use_tool_id(tool, 'root last pre-API authorization refusal')
        def started(code, offset):
            nonlocal revoked
            actor = sys._getframe(1).f_locals.get('self')
            if actor.hass is not hass:
                return
            if code is codes[0]:
                witnesses.append(actor)
                revoked = seam == 'witness'
            elif code is codes[1]:
                revoked = seam == 'source'
            else:
                attempts.append(actor)
        try:
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, started)
            for code in codes:
                sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START)
            tools_before = tuple(sys.monitoring.get_tool(index) for index in range(6))
            with self.assertRaises((PermissionError, self.module('transaction').Refused)):
                await runtime.native_forward(hass, mapping, guard=guard, refresh=refresh)
            self.assertEqual(len(witnesses), 1)
            self.assertIs(witnesses[0].attempted, seam == 'source')
            self.assertFalse(witnesses[0].source_handoff.attempted)
            self.assertEqual(attempts, [])
            for old, new in mapping.items():
                self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
                self.assertIsNone(self.f.entity.async_get(new))
            self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
            self.assertTrue(all(entity._template_result_info is tracker
                                for entity, tracker in zip(entities, trackers, strict=True)))
            self.assertEqual(tuple(sys.monitoring.get_tool(index) for index in range(6)), tools_before)
            self.assertFalse(runtime.operation_lock.locked())
            await self.stimulus(23)
        finally:
            for code in codes:
                sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.free_tool_id(tool)

    async def test_selective_root_native_source_handoff_forward_and_fresh_inverse(self):
        from homeassistant.helpers.entity import Entity
        handoff = self.module('native_source_handoff').NativeSourceHandoff
        codes = (handoff.mutate.__code__, Entity.async_remove.__code__)
        calls, removed = [], []
        tool = next(index for index in range(6) if sys.monitoring.get_tool(index) is None)
        sys.monitoring.use_tool_id(tool, 'full root native source handoff acceptance')
        def started(code, offset):
            frame = sys._getframe(1)
            actor = frame.f_locals.get('self')
            if code is codes[0] and actor.hass is self.f.hass:
                calls.append((actor.source, actor.old, actor.new))
            elif code is codes[1] and any(actor is source for source, _, _ in calls):
                removed.append(frame.f_locals.get('force_remove'))
        try:
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, started)
            for code in codes:
                sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START)
            await self.flow(variant='sensor')
            self.assertEqual(len(calls), 2)
            self.assertIs(calls[0][0], calls[1][0])
            self.assertEqual(calls[0][1:], tuple(reversed(calls[1][1:])))
            self.assertEqual(removed, [])
        finally:
            for code in codes:
                sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.free_tool_id(tool)

    async def test_uidless_numeric_server_owned_forward_and_fresh_reverse_history(self):
        await self.flow(variant='sensor', uidless=True)

    async def _prepared_review(self):
        runtime, mapping, uuids, original = await self.data(variant='sensor', uidless=True)
        hass = self.f.hass
        factory = self.module('native_template_yaml_server').NativeTemplateYAMLServer
        drain = self.module('recovery_controller')._drain_restore
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        async def caller():
            async with runtime.operation_lock:
                async def effect():
                    session = await factory.prepare(hass, mapping, guard=lambda: None, refresh=refresh)
                    try:
                        return (session.operation_id, session.plan,
                                session.chain.baseline.receipt_json, session.composition)
                    finally:
                        session.close()
                return await drain(effect())
        review = await hass.async_create_task(caller(), 'prepare actual YAML review', eager_start=False)
        self.assertFalse(runtime.operation_lock.locked())
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        self.assertIsNone(await runtime.journal.load(review[0]))
        return runtime, mapping, uuids, original, factory, refresh, review

    async def test_uidless_review_gap_fresh_prepared_session_keeps_original(self):
        runtime, mapping, uuids, original, factory, refresh, review = await self._prepared_review()
        operation, plan, original_json, composition = review
        hass = self.f.hass
        for value in (5, 7):
            await self.stimulus(value)
        async def caller():
            async with runtime.operation_lock:
                async def effect():
                    session = await factory.bind_prepared(hass, operation, plan,
                        guard=lambda: None, refresh=refresh)
                    try:
                        self.assertEqual(session.plan, plan)
                        self.assertEqual(session.chain.baseline.receipt_json, original_json)
                        self.assertEqual(session.composition, composition)
                        return await session.apply()
                    finally:
                        session.close()
                return await self.module('recovery_controller')._drain_restore(effect())
        result = await hass.async_create_task(caller(), 'confirm actual YAML review', eager_start=False)
        self.assertEqual(result.status, 'applied')
        for old, new in mapping.items():
            self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
        restored = await runtime.native_yaml_reverse(hass, operation, plan.fingerprint,
            guard=lambda: None, refresh=refresh)
        self.assertEqual(restored['state'], 'rolled_back')
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        baseline, reopened = runtime.preparations.resolve_composition(plan.native_inputs)
        self.assertEqual(baseline.receipt_json, original_json)
        self.assertEqual(reopened, composition)
        for value in (5, 7):
            await self.stimulus(value)

    async def test_prepared_review_source_edit_refuses_without_adopting_new_original(self):
        runtime, mapping, uuids, _, factory, refresh, review = await self._prepared_review()
        operation, plan, original_json, _ = review
        path = self.f.root / 'template.yaml'
        path.write_bytes(path.read_bytes() + b'\n# foreign edit in review gap\n')
        edited = path.read_bytes()
        async with runtime.operation_lock:
            with self.assertRaises((ValueError, self.module('transaction').Refused)):
                await factory.bind_prepared(self.f.hass, operation, plan,
                    guard=lambda: None, refresh=refresh)
        self.assertEqual(path.read_bytes(), edited)
        self.assertIsNone(await runtime.journal.load(operation))
        for old in mapping:
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertEqual(runtime.preparations.resolve_composition(plan.native_inputs)[0].receipt_json,
                         original_json)

    async def test_uidless_binary_server_owned_forward_and_fresh_reverse_history(self):
        await self.flow(variant='binary_sensor', uidless=True)

    async def test_uidless_ambiguous_source_refuses_before_registry_or_yaml_effect(self):
        runtime, mapping, uuids, original = await self.data(
            variant='sensor', uidless=True, duplicate_uidless=True)
        async def refresh():
            return None
        with self.assertRaises((ValueError, self.module('transaction').Refused)):
            await runtime.native_forward(self.f.hass, mapping, guard=lambda: None, refresh=refresh)
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        for old in mapping:
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertFalse(await runtime.journal.active())

    async def test_uidless_aliased_source_refuses_server_before_any_effect(self):
        runtime, mapping, uuids, original = await self.data(
            variant='sensor', uidless=True, uidless_alias=True)
        async def refresh():
            return None
        with self.assertRaises((ValueError, self.module('transaction').Refused)):
            await runtime.native_forward(self.f.hass, mapping, guard=lambda: None, refresh=refresh)
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        for old in mapping:
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertFalse(await runtime.journal.active())

    async def test_uidless_delay_refuses_server_before_effect_and_keeps_original(self):
        runtime, mapping, uuids, original = await self.data(
            variant='binary_sensor', uidless=True, binary_extra='delay_on')
        async def refresh():
            return None
        with self.assertRaises((ValueError, self.module('transaction').Refused)):
            await runtime.native_forward(self.f.hass, mapping, guard=lambda: None, refresh=refresh)
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        for old in mapping:
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertFalse(await runtime.journal.active())

    async def test_uidless_source_drift_refuses_fresh_reverse_without_rollback_effect(self):
        runtime, mapping, uuids, _ = await self.data(variant='sensor', uidless=True)
        async def refresh():
            return None
        result = await runtime.native_forward(self.f.hass, mapping, guard=lambda: None, refresh=refresh)
        self.assertEqual(result['state'], 'applied')
        path = self.f.root / 'template.yaml'
        with path.open('ab') as handle:
            handle.write(b'\n# external source edit after UIDless forward\n')
        edited = path.read_bytes()
        with self.assertRaises((ValueError, self.module('transaction').Refused)):
            await runtime.native_yaml_reverse(self.f.hass, result['operation_id'], result['fingerprint'],
                guard=lambda: None, refresh=refresh)
        self.assertEqual(path.read_bytes(), edited)
        for old, new in mapping.items():
            self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
        record = await runtime.journal.load(result['operation_id'])
        self.assertEqual((record.status, record.direction), ('applied', 'forward'))

    async def test_foreground_mixed_one_plan_same_original_fresh_reverse(self):
        await self.flow()

    async def test_multiple_source_ids_one_plan_mixed_fresh_reverse(self):
        await self.flow(multiple=True)

    async def test_binary_numeric_scope_remains_supported(self):
        await self.flow(variant='binary_sensor')

    async def test_drift_refuses_fresh_reverse_without_any_rollback_effect(self):
        runtime, mapping, uuids, _ = await self.data()
        async def refresh():
            return None
        result = await runtime.native_forward(self.f.hass, mapping, guard=lambda: None, refresh=refresh)
        self.assertEqual(result['state'], 'applied')
        path = self.f.root / 'template.yaml'
        with path.open('ab') as handle:
            handle.write(b'\n# external edit after terminal forward\n')
        edited = path.read_bytes()
        with self.assertRaises((ValueError, self.module('transaction').Refused)):
            await runtime.native_yaml_reverse(self.f.hass, result['operation_id'], result['fingerprint'],
                guard=lambda: None, refresh=refresh)
        self.assertEqual(path.read_bytes(), edited)
        for old, new in mapping.items():
            self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
        record = await runtime.journal.load(result['operation_id'])
        self.assertEqual((record.status, record.direction), ('applied', 'forward'))

    async def test_authorization_revoked_while_waiting_for_runtime_lock(self):
        runtime, mapping, uuids, original = await self.data()
        authorized = True
        def guard():
            if not authorized:
                raise PermissionError('revoked by actual caller')
        async def refresh():
            guard()
        await runtime.operation_lock.acquire()
        caller = self.f.hass.async_create_task(runtime.native_forward(self.f.hass, mapping,
            guard=guard, refresh=refresh), 'queued YAML producer', eager_start=False)
        await asyncio.sleep(0)
        authorized = False
        runtime.operation_lock.release()
        with self.assertRaises(PermissionError):
            await caller
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        for old in mapping:
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertFalse(await runtime.journal.active())

    async def test_terminal_reverse_replay_refused(self):
        runtime, mapping, _, original = await self.data()
        async def refresh():
            return None
        result = await runtime.native_forward(self.f.hass, mapping, guard=lambda: None, refresh=refresh)
        reverse = await runtime.native_yaml_reverse(self.f.hass, result['operation_id'],
            result['fingerprint'], guard=lambda: None, refresh=refresh)
        self.assertEqual(reverse['state'], 'rolled_back')
        with self.assertRaises(self.module('transaction').Refused):
            await runtime.native_yaml_reverse(self.f.hass, result['operation_id'],
                result['fingerprint'], guard=lambda: None, refresh=refresh)
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)

    async def test_reverse_revoked_after_actual_admission_waits(self):
        runtime, mapping, uuids, _ = await self.data()
        async def refresh_forward():
            return None
        result = await runtime.native_forward(self.f.hass, mapping,
            guard=lambda: None, refresh=refresh_forward)
        before_reverse = (self.f.root / 'template.yaml').read_bytes()
        refreshes, authorized = 0, True
        def guard():
            if not authorized:
                raise PermissionError('reverse authorization revoked')
        async def refresh_reverse():
            nonlocal refreshes, authorized
            refreshes += 1
            # Runtime refresh, factory refresh, then fresh terminal admission.
            if refreshes == 3:
                authorized = False
        with self.assertRaises(PermissionError):
            await runtime.native_yaml_reverse(self.f.hass, result['operation_id'],
                result['fingerprint'], guard=guard, refresh=refresh_reverse)
        self.assertEqual(refreshes, 3)
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), before_reverse)
        for old, new in mapping.items():
            self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
        record = await runtime.journal.load(result['operation_id'])
        self.assertEqual((record.status, record.direction), ('applied', 'forward'))

    async def test_detached_actual_native_consumer_refuses_fresh_reverse(self):
        from homeassistant.helpers.entity_platform import async_get_platforms
        runtime, mapping, uuids, _ = await self.data()
        async def refresh():
            return None
        result = await runtime.native_forward(self.f.hass, mapping, guard=lambda: None, refresh=refresh)
        path = self.f.root / 'template.yaml'
        accepted = path.read_bytes()
        consumer = next(e for p in async_get_platforms(self.f.hass, 'template')
                        if p.domain == 'sensor' for e in p.entities.values())
        consumer._template_result_info._track_state_changes.async_remove()
        with self.assertRaises((ValueError, self.module('transaction').Refused)):
            await runtime.native_yaml_reverse(self.f.hass, result['operation_id'], result['fingerprint'],
                guard=lambda: None, refresh=refresh)
        self.assertEqual(path.read_bytes(), accepted)
        for old, new in mapping.items():
            self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
        record = await runtime.journal.load(result['operation_id'])
        self.assertEqual((record.status, record.direction), ('applied', 'forward'))

    async def test_cancel_owned_reload_drains_then_fresh_nonterminal_reverse_refuses(self):
        from homeassistant.helpers import discovery
        runtime, mapping, _, _ = await self.data()
        hass = self.f.hass
        factory = self.module('native_template_yaml_server').NativeTemplateYAMLServer
        native = self.module('native_template_yaml')
        install_code, discovery_code = factory._install.__code__, discovery.async_load_platform.__code__
        owner, descendants, cancelled = [], [], []
        def observe(code, offset):
            frame = sys._getframe(1)
            if code is install_code and frame.f_code is install_code:
                session = frame.f_locals['self']
                if session.hass is hass:
                    owner.append(session.task)
            if code is not discovery_code or frame.f_code is not discovery_code:
                return
            if frame.f_locals.get('hass') is not hass or frame.f_locals.get('platform') != 'template':
                return
            task = asyncio.current_task()
            if not owner or task is owner[0] or cancelled:
                return
            descendants.extend(native._pending_template_tasks(hass) | {task})
            self.assertTrue(runtime.operation_lock.locked())
            self.assertTrue(all(row[3].locked() for row in
                self.module('yaml_editor_locks').NativeYAMLEditorLocks(hass).enumerate()))
            cancelled.append(owner[0].cancel())
        tool = next(i for i in range(6) if sys.monitoring.get_tool(i) is None)
        sys.monitoring.use_tool_id(tool, 'owned YAML reload cancellation')
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, observe)
        for code in (install_code, discovery_code):
            sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START)
        async def refresh():
            return None
        caller = None
        try:
            caller = hass.async_create_task(runtime.native_forward(hass, mapping,
                guard=lambda: None, refresh=refresh), 'foreground cancelled YAML producer', eager_start=False)
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(caller), 180)
            self.assertEqual(cancelled, [True])
            self.assertTrue(descendants)
            self.assertTrue(all(task.done() for task in descendants))
            self.assertTrue(all(task.done() for task in owner))
            self.assertFalse(native._pending_template_tasks(hass))
            self.assertFalse(runtime.operation_lock.locked())
            active = await runtime.journal.active()
            self.assertEqual(len(active), 1)
            pending = active[0]
            self.assertNotEqual(pending.status, 'applied')
            preps = self.module('prepared').native_prepared_store(hass)
            await preps.initialize()
            store = self.module('native_registry_receipts').native_registry_receipt_store(hass, preps)
            await store.initialize()
            baseline, composition = preps.resolve_composition(pending.plan.native_inputs)
            reader = self.module('native_registry_receipts').RegistryReceiptChain.reopen(store,
                pending.operation_id, pending.plan, baseline.receipt, dict(composition.prepared_digests))
            self.assertFalse(reader.writable)
            # A valid pending graph can reopen for inspection. It cannot supply
            # an accepted input state or a replay capability.
            with self.assertRaisesRegex(ValueError, 'unknown_unresolved_intent'):
                reader.reconstruct_inputs()
            paths = [self.f.root / '.storage' / key for key in
                ('core.config_entries', 'core.entity_registry', 'core.device_registry')]
            paths.append(self.f.root / 'template.yaml')
            before = {path: path.read_bytes() for path in paths}
            with self.assertRaises(self.module('transaction').Refused):
                await runtime.native_yaml_reverse(hass, pending.operation_id, pending.plan.fingerprint,
                    guard=lambda: None, refresh=refresh)
            self.assertEqual({path: path.read_bytes() for path in paths}, before)
            self.assertEqual(await runtime.journal.load(pending.operation_id), pending)
        finally:
            for code in (install_code, discovery_code):
                sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.free_tool_id(tool)
            if caller is not None and not caller.done():
                caller.cancel()
                await asyncio.gather(caller, return_exceptions=True)

    async def test_stateful_binary_source_refuses_before_registry_effect(self):
        runtime, mapping, uuids, original = await self.data(variant='binary_sensor', binary_extra='delay_on')
        async def refresh():
            return None
        with self.assertRaises((ValueError, self.module('transaction').Refused)):
            await runtime.native_forward(self.f.hass, mapping, guard=lambda: None, refresh=refresh)
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        for old in mapping:
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertFalse(await runtime.journal.active())
