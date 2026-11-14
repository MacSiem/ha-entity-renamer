"""Prepared evidence stays exactly bound on every reconstruction."""
import ast
import asyncio
from copy import deepcopy
from dataclasses import replace
import importlib.util
from pathlib import Path
import unittest
from types import SimpleNamespace
import time
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('_source_fence_mixed',
    Path(__file__).with_name('test_native_mixed_input_receipts.py'))
mixed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mixed)
receipts = mixed.receipts


class SourceFenceCostTests(unittest.IsolatedAsyncioTestCase):
    async def test_common_yields_and_rechecks_scope_and_witness(self):
        server = importlib.import_module(mixed.base.PACKAGE + '.native_template_state_server')
        registry = importlib.import_module(mixed.base.PACKAGE + '.native_registry')
        binding = importlib.import_module(mixed.base.PACKAGE + '.native_template_state_binding')
        class Stop(Exception): pass
        for changed in ('guard', 'physical', 'prepared', 'native', 'phases', None):
            with self.subTest(changed=changed):
                events = []
                stale = set()
                class Observer:
                    async def position_at_inputs(self, *_):
                        time.sleep(.04)  # Deliberately occupy the loop before yielding.
                        return server.Position.BEFORE
                    def runtime_check(self, _wanted):
                        events.append('witness')
                        if stale:
                            raise Stop('witness-drift')
                        return SimpleNamespace()
                def scope():
                    events.append('scope')
                    if 'guard' in stale:
                        raise Stop('guard-drift')
                async def next_observation(*_args, **_kwargs):
                    events.append('next-observation')
                    raise Stop('observation-reached')
                owner = SimpleNamespace(hass=object(), recorder=object(), registry_changes=(),
                    chain=SimpleNamespace(accepted_documents=lambda: {}, reconstruct_inputs=lambda: object()),
                    observer=Observer(), change=object(), _scope=scope, composition=object(),
                    program=object())
                def competing_callback():
                    events.append('timer')
                    if changed is not None:
                        stale.add(changed)
                asyncio.get_running_loop().call_later(.02, competing_callback)
                with patch.object(registry, 'NativeRegistryIO', lambda *_: object()), \
                     patch.object(registry, 'NativeRegistryBridge', lambda *_a, **_kw: object()), \
                     patch.object(binding, 'async_observe_accepted_state_bindings', next_observation):
                    with self.assertRaises(Stop):
                        await server.NativeTemplateStateServer._check_common(owner, False, initial=True)
                self.assertEqual(events[0], 'timer')
                self.assertEqual(events[1], 'scope')
                if changed is not None:
                    self.assertNotIn('next-observation', events)
                else:
                    self.assertEqual(events, ['timer', 'scope', 'witness', 'next-observation'])

    async def test_state_binding_check_fences_once_and_rejects_drift(self):
        # Execute the actual observer check with a real synthetic receipt chain.
        binding = importlib.import_module(mixed.base.PACKAGE + '.native_template_state_binding')
        program_type = importlib.import_module(mixed.base.PACKAGE + '.template_state_program').StateConfigProgram
        fixture = await mixed.mixed_fixture()
        chain = fixture['chain']
        mapping = {old: new for change in fixture['changes'] for old, new in change.mapping.items()}
        program = program_type('a', 'b', b'{}', b'{}', tuple(mapping.items()), (), (), b'[]')
        state = chain.reconstruct_inputs()
        arguments = {'live_intent': None, 'live_context': None, 'live_calls': ()}
        prefix = binding.accepted_state_prefix(chain, program, input_state=state, **arguments)
        observer = next(node for node in ast.parse(Path(binding.__file__).read_text()).body
                        if isinstance(node, ast.AsyncFunctionDef)
                        and node.name == 'async_observe_accepted_state_bindings')
        check = next(node for node in observer.body
                     if isinstance(node, ast.FunctionDef) and node.name == 'check')
        source = ast.fix_missing_locations(ast.Module(body=[check], type_ignores=[]))
        guard_current = True
        def guard():
            return guard_current
        scope = dict(guard=guard, inspect=__import__('inspect'), _need=binding._need,
                     accepted_prefix=prefix, accepted_state_prefix=binding.accepted_state_prefix,
                     chain=chain, program=program, state=state, arguments=arguments)
        exec(compile(source, binding.__file__, 'exec'), scope)
        with patch.object(binding, '_state_inputs', wraps=binding._state_inputs) as inputs:
            scope['check']()
        self.assertEqual(inputs.call_count, 1)
        guard_current = False
        with self.assertRaises(ValueError):
            scope['check']()
        guard_current = True
        row = fixture['preparations'].plans['yaml-change']['payload']
        row['data']['receipt']['source_files'][0][1] = '0' * 64
        with self.assertRaises((ValueError, receipts.RegistryReceiptError)):
            scope['check']()

    async def test_prefix_reuses_only_the_same_checked_documents(self):
        binding = importlib.import_module(mixed.base.PACKAGE + '.native_template_state_binding')
        program_type = importlib.import_module(mixed.base.PACKAGE + '.template_state_program').StateConfigProgram
        fixture = await mixed.mixed_fixture()
        chain = fixture['chain']
        mapping = {old: new for change in fixture['changes'] for old, new in change.mapping.items()}
        program = program_type('a', 'b', b'{}', b'{}', tuple(mapping.items()), (), (), b'[]')
        state = chain.reconstruct_inputs()
        expected = binding.accepted_state_prefix(chain, program)
        with patch.object(chain, 'accepted_registry_id_positions', wraps=chain.accepted_registry_id_positions) as positions:
            self.assertEqual(binding.accepted_state_prefix(chain, program, input_state=state), expected)
            self.assertEqual(positions.call_count, 1)
        with patch.object(chain, 'accepted_registry_id_positions', return_value=()):
            with self.assertRaises(ValueError):
                binding.accepted_state_prefix(chain, program, input_state=state)
        self.assertEqual(binding.accepted_state_prefix(chain, program, input_state=state), expected)

    async def test_async_baseline_refuses_preparation_and_guard_drift(self):
        from types import SimpleNamespace
        from dataclasses import replace
        from contextlib import asynccontextmanager
        for drift in ('payload', 'guard', 'digests', 'receipt', 'cancel', None):
            fixture = await mixed.base.fixture()
            chain, store = fixture['chain'], fixture['store']
            @asynccontextmanager
            async def pin(_hass, _receipt, *, guard):
                guard()
                async def verify(): guard()
                yield chain.baseline, verify
            self.enterContext(patch.object(mixed.base.baseline, 'pinned_original_inputs', pin))
            receipt = replace(chain.original_receipt)
            digests = dict(fixture['digests'])
            started, resume = asyncio.Event(), asyncio.Event()
            async def executor(function):
                started.set()
                await resume.wait()
                return function()
            hass = SimpleNamespace(async_add_executor_job=executor)
            guards = [True]
            def guard():
                if not guards[0]:
                    raise ValueError('scope changed')
            before = len(store.rows)
            operation = 'async-fixture-' + str(drift)
            task = asyncio.create_task(receipts.RegistryReceiptChain.start(
                store, operation, chain.plan, receipt, digests, hass=hass, guard=guard))
            await started.wait()
            if drift == 'payload':
                ref = chain.plan.native_inputs[0]
                fixture['preparations'].plans[ref]['payload']['codec'] += 'A'
            elif drift == 'guard':
                guards[0] = False
            elif drift == 'digests':
                digests[next(iter(digests))] = '0' * 64
            elif drift == 'receipt':
                object.__setattr__(receipt, 'version', 'changed')
            elif drift == 'cancel':
                task.cancel()
            resume.set()
            if drift is None:
                result = await task
                self.assertEqual(result.plan, chain.plan)
                self.assertEqual(len(store.rows), before + 1)
            elif drift == 'cancel':
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(len(store.rows), before)
            else:
                with self.assertRaises(ValueError):
                    await task
                self.assertEqual(len(store.rows), before)

    async def test_async_baseline_requires_synchronous_positive_guard(self):
        from types import SimpleNamespace
        fixture = await mixed.base.fixture()
        chain, store = fixture['chain'], fixture['store']
        hass = SimpleNamespace(async_add_executor_job=lambda _fn: None)
        async def async_guard():
            return True
        for guard in (lambda: False, async_guard):
            with self.assertRaises(ValueError):
                await receipts.RegistryReceiptChain.start(store, 'invalid-guard',
                    chain.plan, chain.original_receipt, fixture['digests'], hass=hass, guard=guard)

    async def test_async_native_payload_and_fence_drift(self):
        from contextlib import asynccontextmanager
        from types import SimpleNamespace
        fixture = await mixed.base.fixture()
        store, baseline = fixture['preparations'], fixture['chain'].baseline
        async def executor(function):
            return function()
        hass = SimpleNamespace(async_add_executor_job=executor)
        @asynccontextmanager
        async def pin(_hass, _receipt, *, guard):
            guard()
            async def verify(): guard()
            yield baseline, verify
        expected = mixed.base.baseline.baseline_digest(baseline)
        with patch.object(mixed.base.baseline, 'pinned_original_inputs', pin):
            reference = await store.create_native_inputs('async-original', baseline, hass=hass, guard=lambda: None)
        self.assertEqual(reference[2], expected)
        self.assertEqual(store.resolve_native_inputs(reference, fixture['receipt']), baseline)
        entered, release = asyncio.Event(), asyncio.Event()
        @asynccontextmanager
        async def delayed_pin(_hass, _receipt, *, guard):
            async def verify():
                entered.set()
                await release.wait()
                guard()
            yield baseline, verify
        before = set(store.plans)
        with patch.object(mixed.base.baseline, 'pinned_original_inputs', delayed_pin):
            guard_ok = [True]
            task = asyncio.create_task(store.create_native_inputs('async-drift', baseline,
                hass=hass, guard=lambda: guard_ok[0]))
            await entered.wait()
            guard_ok[0] = False
            release.set()
            with self.assertRaises(ValueError):
                await task
        self.assertEqual(set(store.plans), before)

    async def test_review_session_binding_rejects_change_across_await(self):
        from types import SimpleNamespace
        from dataclasses import replace
        fixture = await mixed.base.fixture()
        spec = importlib.util.spec_from_file_location(mixed.base.PACKAGE + '.review_runtime',
            mixed.base.COMPONENT / '__init__.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for drift in ('operation', 'plan', 'changes', 'receipt'):
            chain = fixture['chain']
            session = SimpleNamespace(plan=chain.plan, operation_id=chain.operation_id,
                registry_changes=list(chain.changes), receipt=chain.original_receipt)
            _, _, _, _, check = module._review_session_binding(session)
            check()
            await asyncio.sleep(0)
            if drift == 'operation': session.operation_id = 'other'
            elif drift == 'plan': session.plan = replace(session.plan)
            elif drift == 'changes': session.registry_changes = []
            else: session.receipt = replace(session.receipt)
            with self.assertRaises(ValueError):
                check()

    async def test_async_resolve_refuses_payload_drift_and_cancel(self):
        from contextlib import asynccontextmanager
        from types import SimpleNamespace
        fixture = await mixed.base.fixture()
        store, chain = fixture['preparations'], fixture['chain']
        reference = chain.plan.native_inputs
        for mode in ('payload', 'cancel'):
            entered, release = asyncio.Event(), asyncio.Event()
            async def executor(function):
                entered.set()
                await release.wait()
                return function()
            @asynccontextmanager
            async def pin(_hass, _receipt, *, guard):
                async def verify(): guard()
                yield chain.baseline, verify
            hass = SimpleNamespace(async_add_executor_job=executor)
            with patch.object(mixed.base.baseline, 'pinned_original_inputs', pin), \
                 patch.object(mixed.base.prepared.PreparedStore, 'resolve_composition',
                              return_value=(chain.baseline, object())):
                task = asyncio.create_task(store.async_resolve_composition(reference,
                    hass=hass, guard=lambda: None, receipt=chain.original_receipt))
                await entered.wait()
                row = store.plans[reference[0]]['payload']
                original = row['codec']
                if mode == 'payload': row['codec'] += 'A'
                else: task.cancel()
                release.set()
                try:
                    with self.assertRaises(ValueError if mode == 'payload' else asyncio.CancelledError):
                        await task
                finally:
                    row['codec'] = original

    async def test_runtime_projection_is_detached_from_accepted_evidence(self):
        fixture = await mixed.mixed_fixture()
        chain = fixture['chain']
        state = chain.reconstruct_inputs()
        image = chain.accepted_runtime_documents(include_devices=True, include_entries=True)
        entities = image['core.entity_registry']['data']['entities']
        original = entities[0]['id']
        entities[0]['id'] = 'private-copy-mutation'
        image['core.config_entries']['data']['entries'].clear()
        image['core.device_registry']['data']['devices'].clear()
        self.assertTrue(chain.matches_input_state(state))
        again = chain.accepted_runtime_documents(include_devices=True, include_entries=True)
        self.assertEqual(again['core.entity_registry']['data']['entities'][0]['id'], original)
        self.assertTrue(again['core.config_entries']['data']['entries'])
        self.assertTrue(again['core.device_registry']['data']['devices'])

    def test_callback_census_rechecks_each_code_and_source_changes(self):
        import hashlib
        import sys
        import tempfile
        from types import ModuleType
        transport = importlib.import_module(mixed.base.PACKAGE + '.native_template_binding_transport')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / 'fixture_callbacks.py'
            raw = b'def first(): return 1\ndef second(): return 2\n'
            path.write_bytes(raw)
            module = ModuleType('_fixture_callbacks')
            sys.modules[module.__name__] = module
            self.addCleanup(sys.modules.pop, module.__name__, None)
            exec(compile(raw, str(path), 'exec'), vars(module))
            native = SimpleNamespace(root=root, pins={path.name: hashlib.sha256(raw).hexdigest()}, switch=str)
            source_codes = {}
            transport._callback(native, module.first, object(), source_codes=source_codes)
            transport._callback(native, module.second, object(), source_codes=source_codes)
            self.assertEqual(len(source_codes), 1)
            transport._Runtime.check_callback_sources(source_codes)
            module.second.__code__ = (lambda: 3).__code__
            with self.assertRaises(ValueError):
                transport._callback(native, module.second, object(), source_codes=source_codes)
            path.write_bytes(raw + b'# drift\n')
            with self.assertRaises(ValueError):
                transport._Runtime.check_callback_sources(source_codes)

    def test_detached_evidence_distinguishes_signed_zero_and_nested_type_drift(self):
        original = {'nested': [0.0, -0.0, 1, True, {'x': 1.5}]}
        evidence = receipts._freeze_json(original)
        self.assertTrue(receipts._exact_json_equal(deepcopy(original), evidence))
        for changed in (
            {'nested': [-0.0, -0.0, 1, True, {'x': 1.5}]},
            {'nested': [0.0, 0.0, 1, True, {'x': 1.5}]},
            {'nested': [0.0, -0.0, 1.0, True, {'x': 1.5}]},
        ):
            with self.subTest(changed=changed):
                self.assertFalse(receipts._exact_json_equal(changed, evidence))
        self.assertTrue(receipts._exact_json_equal(deepcopy(original), evidence))

    def test_receipt_tuple_snapshot_catches_nested_identity_and_type_changes(self):
        original = ('/config', (('file', 1, 2),), (('dir', (('file', 33188),)),))
        evidence = receipts._freeze_json(original)
        self.assertTrue(receipts._exact_json_equal(original, evidence))
        self.assertFalse(receipts._exact_json_equal(('/config', (('file', True, 2),), original[2]), evidence))
        self.assertFalse(receipts._exact_json_equal(('/config', (('file', 1, 3),), original[2]), evidence))
        self.assertFalse(receipts._exact_json_equal(('/config', [['file', 1, 2]], original[2]), evidence))
        self.assertTrue(receipts._exact_json_equal(original, evidence))

    def test_yaml_fold_computes_identity_once_and_rejects_wrong_call_binding(self):
        accepted = importlib.import_module(mixed.base.PACKAGE + '.accepted_input_state')
        transaction = importlib.import_module(mixed.base.PACKAGE + '.yaml_transaction')
        class Change:
            files = tuple(SimpleNamespace(path=name) for name in ('one', 'two', 'three'))
            calls = 0
            @property
            def identity(self):
                self.calls += 1
                return 'original-identity'
        change = Change()
        ids = ['yaml_' + transaction._digest(('original-identity', row.path, False))[:64]
               for row in change.files]
        self.assertEqual(accepted.fold_yaml_calls(change, {}, [], 'forward', ids, partial=True), {})
        self.assertEqual(change.calls, 1)
        with self.assertRaises(receipts.RegistryReceiptError):
            accepted.fold_yaml_calls(change, {}, [], 'forward', ids[:-1] + ['foreign'], partial=True)

    async def test_reconstruction_skips_only_yaml_decode_and_returns_detached_value(self):
        f = await mixed.mixed_fixture()
        chain = f['chain']
        with patch.object(receipts, 'decode_change', wraps=receipts.decode_change) as decoder:
            first = chain._original_change(3)
            second = chain._original_change(3)
            chain.reconstruct_inputs()
        self.assertEqual(first, f['yaml'])
        self.assertEqual(second, f['yaml'])
        self.assertIsNot(first, second)
        self.assertTrue(all(call.args[0]['kind'] != 'yaml' for call in decoder.call_args_list))
        self.assertEqual(len(decoder.call_args_list), 3)  # registry steps still re-resolve

    async def test_every_stored_node_and_binding_revalidated_even_with_cached_chain(self):
        f = await mixed.mixed_fixture()
        chain = f['chain']
        chain.reconstruct_inputs()
        row = f['preparations'].plans['yaml-change']['payload']
        original = deepcopy(row)
        for mutate in (
            lambda r: r['data']['receipt']['source_files'][0].__setitem__(1, '0' * 64),
            lambda r: r['data']['receipt']['source_files'][0].__setitem__(2, True),
            lambda r: r['data']['receipt']['source_directories'].append(['extra', [], None]),
            lambda r: r['data']['files'][0].__setitem__('after_hex', '00'),
        ):
            row.clear(); row.update(deepcopy(original)); mutate(row)
            with self.assertRaises(receipts.RegistryReceiptError):
                chain.reconstruct_inputs()
        row.clear(); row.update(original)
        chain.changes[3] = replace(f['yaml'], files=())
        with self.assertRaises(receipts.RegistryReceiptError):
            chain.reconstruct_inputs()
        chain.changes[3] = f['yaml']
        chain.root['bindings'][3]['yaml_digest'] = '0' * 64
        with self.assertRaises(receipts.RegistryReceiptError):
            chain._original_change(3)
