"""NOT RUN by worker. Owner executes on integrated frozen candidate via FIFO.

Codec cases prove private format only. Native cases use the actual checked
source bundle, native managers and immutable receipt root. No native result,
receipt, Plan, registry postimage or runtime getter is fabricated for admission.
"""
import copy
from dataclasses import asdict, replace
import hashlib
import importlib
import json
import os
import unittest
from unittest.mock import patch

import test_yaml_transaction as legacy
import test_native_template_state_binding as binding_fixture
import test_native_template_yaml_server as server

subject = importlib.import_module(legacy.package.__name__ + '.native_template_state_yaml')


class SelectiveYAMLCodecTests(unittest.TestCase):
    def change(self):
        before, after = b'template: before\n', b'template: after\n'
        digest = lambda raw: hashlib.sha256(raw).hexdigest()
        receipt = legacy.module.ValidationReceipt('/config',
            (('configuration.yaml', digest(before), 1, 2),),
            (('.', (('configuration.yaml', 33152, 1, 2),), ('configuration.yaml',)),),
            (('configuration.yaml', digest(after)),), ('template',), ('template',),
            '2025.6.0', (('template', None, None),))
        return subject.PreparedTemplateStateYAML(receipt,
            (legacy.module.YAMLFile('configuration.yaml', before.hex(), after.hex(), 1, 2),), 'a' * 64)

    def test_new_exact_kind_retains_actual_unsupported_none_receipt(self):
        change = self.change().checked()
        encoded = legacy.prepared.encode_change(change)
        self.assertEqual(encoded['kind'], 'template_state_yaml')
        decoded = legacy.prepared.decode_change(encoded)
        self.assertIs(type(decoded), subject.PreparedTemplateStateYAML)
        self.assertEqual(decoded, change)
        self.assertEqual(decoded.receipt.runtime_expected, (('template', None, None),))
        self.assertEqual(decoded.receipt.unsupported_domains, ('template',))
        self.assertFalse(decoded.receipt.ready_for_apply)
        self.assertNotEqual(decoded.identity, replace(decoded, source_program_sha256='b' * 64).identity)
        with self.assertRaises(ValueError):
            legacy.module.decode_yaml(asdict(change))
        with self.assertRaises(ValueError):
            legacy.prepared.decode_change(dict(encoded, kind='yaml'))

    def test_legacy_yaml_envelope_and_identity_remain_exact(self):
        selective = self.change()
        receipt = replace(selective.receipt, changed_domains=('script',),
                          unsupported_domains=(), runtime_expected=())
        change = legacy.module.PreparedYAML(receipt, selective.files).checked()
        encoded = legacy.prepared.encode_change(change)
        self.assertEqual(encoded['kind'], 'yaml')
        self.assertEqual(set(encoded['data']), {'receipt', 'files'})
        self.assertEqual(legacy.prepared.decode_change(encoded), change)
        old_identity = asdict(change)
        old_identity['receipt'].pop('runtime_expected')
        self.assertEqual(change.identity, 'yaml:' + legacy.module._digest(old_identity))
        # Durable envelope created before runtime_expected must still decode.
        encoded['data']['receipt'].pop('runtime_expected')
        self.assertEqual(legacy.prepared.decode_change(encoded), change)

    def test_strict_shape_program_digest_and_file_cas(self):
        raw = legacy.prepared.encode_change(self.change())
        for mutate in (
            lambda r: r['data'].update(extra=True),
            lambda r: r['data'].update(source_program_sha256='x' * 64),
            lambda r: r['data']['files'][0].update(path='../outside.yaml'),
            lambda r: r['data']['files'][0].update(before_hex='00'),
            lambda r: r['data']['receipt'].update(unsupported_domains=['script']),
            lambda r: r['data']['receipt'].update(changed_domains=['template', 'script']),
        ):
            row = copy.deepcopy(raw)
            mutate(row)
            with self.assertRaises(ValueError):
                legacy.prepared.decode_change(row)

    def test_legacy_candidate_gate_still_refuses_unsupported_none(self):
        candidate = importlib.import_module(legacy.package.__name__ + '.native_candidate_validation')
        receipt = self.change().receipt
        result = dict(status='valid', version=receipt.version, changed_domains=['template'],
                      unsupported_domains=['template'], runtime_expected=[['template', None, None]])
        with self.assertRaises(ValueError):
            candidate._compare(result, receipt, False)


@unittest.skipUnless(server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class SelectiveYAMLNativeRootTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = binding_fixture.NativeTemplateStateBindingTests.asyncSetUp
    _before_start = binding_fixture.NativeTemplateStateBindingTests._before_start
    _flush_documents = binding_fixture.NativeTemplateStateBindingTests._flush_documents
    prepare = binding_fixture.NativeTemplateStateBindingTests.prepare
    module = binding_fixture.NativeTemplateStateBindingTests.module
    boot = binding_fixture.NativeTemplateStateBindingTests.boot

    async def test_actual_read_only_root_codec_and_original_runtime(self):
        bundle, mapping = await self.boot(variables=True)
        hass = self.f.hass
        module = self.module('native_template_state_yaml')
        baseline = await self.module('native_input_baseline').async_capture_native_input_baseline(
            hass, bundle.receipt, guard=lambda: None)
        planner = self.module('yaml_plan').plan_yaml(self.f.root, mapping)
        change = await self.module('yaml_validation')._settled_executor(
            hass, module.capture_template_state_yaml, bundle, planner, mapping)
        prep, nr, tx = self.module('prepared'), self.module('native_registry'), self.module('transaction')
        recorder = self.module('recorder_guard').NativeRecorderGuard(hass)
        history = self.module('history_guard').NativeHistoryGuard(recorder)
        bridge = nr.NativeRegistryBridge(nr.NativeRegistryIO(hass), recorder)
        preparations = prep.native_prepared_store(hass)
        await preparations.initialize()
        old, new = next(iter(mapping.items()))
        source = await bridge.prepare('entity', self.f.entity.async_get(old).id,
                                      {'entity_id': new}, lambda: None)
        digests, steps = {}, []
        for index, (adapter, item) in enumerate((('registry', source), ('yaml', change))):
            ref = 'selective-root-' + str(index)
            digests[ref] = await preparations.create(ref, prep.encode_change(item))
            steps.append(tx.Step(ref, adapter, item.identity, item.before_revision, item.after_revision, ref))
        from homeassistant.helpers.entity_platform import async_get_platforms
        consumers = {entity.entity_id: entity.entity_id
            for platform in async_get_platforms(hass, 'template') for entity in platform.entities.values()}
        composition = self.module('native_template_state_composition').TemplateStateComposition(
            bundle.evidence, bundle.original_sources, bundle.candidate_sources,
            bundle.state_config_program, planner, tuple(sorted(mapping.items())), tuple(sorted(digests.items())),
            await history.capture(mapping), await history.capture(consumers), bundle.state_config_program.sha256)
        native_ref = await preparations.create_native_inputs('selective-original', baseline, composition=composition)
        scopes = ('registry', 'yaml')
        plan = tx.Plan('selective-readonly', scopes, scopes, tuple(steps), composition.coverage_sha256,
                       scopes, native_ref)
        rr = self.module('native_registry_receipts')
        store = rr.native_registry_receipt_store(hass, preparations)
        await store.initialize()
        chain = await rr.RegistryReceiptChain.start(store, 'selective-readonly-operation', plan,
                                                     bundle.receipt, digests)
        state = chain.reconstruct_inputs()
        observer = module.NativeTemplateStateYAMLRuntimeReadback(hass, chain, composition, guard=lambda: None)
        self.assertIs(await observer.position(change), tx.Position.UNKNOWN)
        self.assertIs(await observer.position_at_inputs(change, state), tx.Position.BEFORE)
        observer.runtime_check(tx.Position.BEFORE).check()
        self.assertEqual(observer.program.sha256, change.source_program_sha256)
        self.assertEqual(bundle.receipt, baseline.receipt)
        self.assertFalse(bundle.receipt.ready_for_apply)
        original = baseline.receipt_json
        reopened_store = prep.native_prepared_store(hass)
        await reopened_store.initialize()
        reopened_baseline, reopened_composition = reopened_store.resolve_composition(native_ref)
        self.assertEqual(reopened_baseline.receipt_json, original)
        self.assertEqual(reopened_composition, composition)
        reopened_receipts = rr.native_registry_receipt_store(hass, reopened_store)
        await reopened_receipts.initialize()
        readonly = rr.RegistryReceiptChain.reopen(reopened_receipts, chain.operation_id,
                                                  plan, bundle.receipt, digests)
        self.assertFalse(readonly.writable)
        fresh = module.NativeTemplateStateYAMLRuntimeReadback(hass, readonly, reopened_composition,
                                                              guard=lambda: None)
        self.assertIs(await fresh.position_at_inputs(change, readonly.reconstruct_inputs()), tx.Position.BEFORE)
        with self.assertRaises(self.module('transaction').Refused):
            module.assert_state_yaml_composition(readonly, replace(change, source_program_sha256='0' * 64))
        saved = copy.deepcopy(reopened_store.plans[native_ref[0]])
        try:
            reopened_store.plans[native_ref[0]]['payload']['composition']['coverage_sha256'] = 'f' * 64
            with self.assertRaises(self.module('transaction').Refused):
                module.assert_state_yaml_composition(readonly, change)
        finally:
            reopened_store.plans[native_ref[0]] = saved
        original_manager = readonly.baseline.files[0].manager_json
        try:
            object.__setattr__(readonly.baseline.files[0], 'manager_json', original_manager + b' ')
            with self.assertRaises(self.module('transaction').Refused):
                module.assert_state_yaml_composition(readonly, change)
        finally:
            object.__setattr__(readonly.baseline.files[0], 'manager_json', original_manager)
        for row in change.files:
            self.assertEqual((self.f.root / row.path).read_bytes(), row.before)
        self.assertEqual(self.f.entity.async_get(old).id, source.identity)
        self.assertIsNone(self.f.entity.async_get(new))


@unittest.skipUnless(server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class SelectiveYAMLNativeEffectTests(unittest.IsolatedAsyncioTestCase):
    """Requires the owner's integrated exact root; skip only absent root module."""
    with_yaml = True
    asyncSetUp = server.NativeTemplateYAMLServerTests.asyncSetUp
    _before_start = server.NativeTemplateYAMLServerTests._before_start
    _flush_documents = server.NativeTemplateYAMLServerTests._flush_documents
    prepare = server.NativeTemplateYAMLServerTests.prepare
    module = server.NativeTemplateYAMLServerTests.module
    data = server.NativeTemplateYAMLServerTests.data
    _runtime = server.NativeTemplateYAMLServerTests._runtime

    async def test_actual_selective_yaml_no_reload_and_fresh_reverse_same_original(self):
        try:
            factory = self.module('native_template_state_server').NativeTemplateStateServer
        except ModuleNotFoundError as error:
            if error.name.endswith('.native_template_state_server'):
                self.skipTest('owner exact selective root not integrated')
            raise
        from homeassistant.core import Context
        runtime, mapping, uuids, _ = await self.data(multiple=True, variant='sensor')
        hass, root = self.f.hass, self.f.root
        first, second = sorted(mapping)
        (root / 'template.yaml').write_text('- unique_id: scope\n  sensor:\n'
            '  - unique_id: value\n    name: Runtime consumer\n'
            f'    state: "{{{{ states(\'{first}\') | int(0) + states(\'{second}\') | int(0) }}}}"\n'
            '    attributes:\n      tick: "{{ now().minute }}"\n'
            '      memory: "{{ this.attributes.get(\'memory\', 0) + 1 }}"\n'
            '- unique_id: second\n  sensor:\n  - unique_id: value\n    name: Second consumer\n'
            f'    state: "{{{{ states(\'{self.other.entity_id}\') | int(0) * 3 }}}}"\n')
        await hass.services.async_call('template', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        await self._flush_documents()
        original_yaml = (root / 'template.yaml').read_bytes()
        original_consumers = dict((entity.entity_id, entity) for platform in
            __import__('homeassistant.helpers.entity_platform', fromlist=['async_get_platforms']).async_get_platforms(hass, 'template')
            for entity in platform.entities.values())
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        calls = []
        # Observe actual service dispatch with CPython monitoring, preserving
        # native methods, registered handlers and their independent code pins.
        import sys, time
        profile_codes = {
            self.module('native_template_state_composition').TemplateStateComposition.assert_baseline.__code__,
            self.module('template_state_program').StateConfigProgram.assert_sources.__code__,
            self.module('native_template_state_yaml').assert_state_yaml_composition.__code__,
        }
        timing, entered = {}, {}
        def profile_start(code_arg, offset):
            entered.setdefault(code_arg, []).append(time.perf_counter())
        def profile_return(code_arg, offset, value):
            starts = entered.get(code_arg)
            if starts:
                row = timing.setdefault(code_arg.co_qualname, [0, 0.0])
                row[0] += 1; row[1] += time.perf_counter() - starts.pop()
                if code_arg.co_qualname.endswith('assert_baseline') and row[0] % 100 == 0:
                    print('STATE_ROOT_PROFILE ' + json.dumps(timing), flush=True)
        tool = next(i for i in range(6) if sys.monitoring.get_tool(i) is None)
        code = type(hass.services).async_call.__code__
        def called(code_arg, offset):
            frame = sys._getframe(1)
            if frame.f_code is code and frame.f_locals.get('self') is hass.services:
                calls.append((frame.f_locals.get('domain'), frame.f_locals.get('service')))
        sys.monitoring.use_tool_id(tool, 'selective YAML actual service observation')
        def started(code_arg, offset):
            if code_arg in profile_codes:
                profile_start(code_arg, offset)
            else:
                called(code_arg, offset)
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, started)
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, profile_return)
        for observed_code in profile_codes:
            sys.monitoring.set_local_events(tool, observed_code, sys.monitoring.events.PY_START | sys.monitoring.events.PY_RETURN)
        sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START)
        forward = reverse = None
        try:
            async with runtime.operation_lock:
                forward = await factory.prepare(hass, mapping, guard=lambda: None, refresh=refresh)
                ref, receipt_json = forward.plan.native_inputs, forward.chain.baseline.receipt_json
                applied = await forward.apply()
                self.assertEqual(applied.status, 'applied')
                self.assertEqual(forward.change.receipt, forward.receipt)
                for old, new in mapping.items():
                    self.assertIsNone(self.f.entity.async_get(old))
                    self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
                operation, fingerprint = forward.operation_id, forward.plan.fingerprint
                forward.close()
                forward = None
            async with runtime.operation_lock:
                reverse = await factory.bind_reverse(hass, operation, fingerprint,
                                                       guard=lambda: None, refresh=refresh)
                rolled_back = await reverse.rollback()
                self.assertEqual(rolled_back.status, 'rolled_back')
                self.assertEqual(reverse.plan.native_inputs, ref)
                self.assertEqual(reverse.chain.baseline.receipt_json, receipt_json)
                self.assertEqual((root / 'template.yaml').read_bytes(), original_yaml)
                reverse.close()
                reverse = None
            self.assertNotIn(('template', 'reload'), calls)
            for old in mapping:
                self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
            from homeassistant.helpers.entity_platform import async_get_platforms
            final_consumers = {entity.entity_id: entity for platform in async_get_platforms(hass, 'template')
                               for entity in platform.entities.values()}
            self.assertEqual(final_consumers, original_consumers)
            self.assertTrue(all(final_consumers[key] is entity for key, entity in original_consumers.items()))
        finally:
            if forward is not None:
                forward.close()
            if reverse is not None:
                reverse.close()
            print('STATE_ROOT_PROFILE_FINAL ' + json.dumps(timing), flush=True)
            for observed_code in profile_codes:
                sys.monitoring.set_local_events(tool, observed_code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, None)
            sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.free_tool_id(tool)
