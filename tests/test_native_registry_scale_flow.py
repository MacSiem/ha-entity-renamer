"""One costly owner-run cardinality qualifier, actual Core 2026.10.4 only.

NOTRUN by artifact author. Run this exact named test through the owner's FIFO.
No positive Plan/receipt/native input/prepared ref is constructed by this test.
Full real API serializers and files are retained, including tombstones/unknowns.
"""
import json
import sys
import threading
from collections import Counter
from pathlib import Path
from time import perf_counter
from types import MappingProxyType
import unittest

import test_native_template_yaml_server as server


@unittest.skipUnless(server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeRegistryScaleFlowTests(unittest.IsolatedAsyncioTestCase):
    # Aliases deliberately avoid inheriting the server's entire test matrix.
    with_yaml = True
    asyncSetUp = server.NativeTemplateYAMLServerTests.asyncSetUp
    _before_start = server.NativeTemplateYAMLServerTests._before_start
    _flush_documents = server.NativeTemplateYAMLServerTests._flush_documents
    prepare = server.NativeTemplateYAMLServerTests.prepare
    emit = server.NativeTemplateYAMLServerTests.emit
    _runtime = server.NativeTemplateYAMLServerTests._runtime
    module = server.NativeTemplateYAMLServerTests.module
    flow = server.NativeTemplateYAMLServerTests.flow
    DELETED = 37_275
    LIVE = 256
    operation_timeout = 600  # Full-cardinality correctness probe; no latency PASS.
    UNIQUE_WIDTH = 240  # 480 produced 40,068,053 B at the same native cardinality.

    def same(self, actual, expected, reason):
        # Do not let unittest dump full 25+ MB documents on a failed assertion.
        self.assertTrue(actual == expected, reason)

    def manager_documents(self):
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        return {m._store.key: json.loads(_orjson_bytes_default_encoder(m._data_to_save()))
                for m in self.f.managers}

    def assert_population(self):
        current = self.manager_documents()
        er_key = 'core.entity_registry'
        before, now = self.original_managers[er_key], current[er_key]
        self.assertEqual(len(now['deleted_entities']), self.DELETED)
        self.assertEqual(len(now['entities']), len(before['entities']))
        self.same(now['deleted_entities'], before['deleted_entities'], 'full deleted rows drifted')
        # Compare every untouched live row, all native fields, in native order.
        self.same([r for r in now['entities'] if r['id'] not in self.source_uuids],
                  [r for r in before['entities'] if r['id'] not in self.source_uuids],
                  'full untouched live rows drifted')
        self.same({k: v for k, v in now.items() if k not in ('entities', 'deleted_entities')},
                  {k: v for k, v in before.items() if k not in ('entities', 'deleted_entities')},
                  'native settings/unknown fields drifted')
        for key in ('core.device_registry', 'core.config_entries'):
            self.same(current[key], self.original_managers[key], 'untouched full manager drifted: ' + key)

    async def data(self, **kwargs):
        from homeassistant.config_entries import ConfigEntry, ConfigEntryDisabler
        from homeassistant.helpers import entity_registry as er
        self.assertEqual(self.f.version, '2026.10.4')
        start = perf_counter()
        result = await server.NativeTemplateYAMLServerTests.data(self, **kwargs)
        runtime, mapping, uuids, _ = result
        self.assertEqual(len(mapping), 1)
        self.scale_runtime, self.source_uuids = runtime, set(uuids.values())
        self.same(await runtime.journal.all(), (), 'isolated journal must begin empty')
        self.assertEqual(len(self.f.entity._data_to_save()['deleted_entities']), 0)
        # Public constructor and ConfigEntries API, disabled to avoid a platform
        # entity population. Opaque data/options exercise full Store retention.
        entry = ConfigEntry(domain='input_boolean', title='Synthetic cardinality',
            data={'synthetic_opaque_data': {'ordered': [1, True, None, 'kept']}},
            options={'synthetic_opaque_options': {'nested': ['second', 'first']}},
            version=1, minor_version=1, source='user', unique_id='scale-registry-entry',
            disabled_by=ConfigEntryDisabler.USER,
            discovery_keys=MappingProxyType({}), subentries_data=[])
        await self.f.hass.config_entries.async_add(entry)
        for index in range(self.DELETED + self.LIVE):
            deleted = index < self.DELETED
            prefix = ('scale-deleted-' if deleted else 'scale-live-') + f'{index:05d}-'
            unique = prefix + 'u' * (self.UNIQUE_WIDTH - len(prefix))
            row = self.f.entity.async_get_or_create('sensor', 'input_boolean', unique,
                config_entry=entry, suggested_object_id='scale_row_' + str(index),
                disabled_by=er.RegistryEntryDisabler.USER,
                original_name='Synthetic registry row ' + str(index))
            if deleted:
                self.f.entity.async_remove(row.entity_id)
            else:
                self.f.entity.async_update_entity(row.entity_id,
                    name='Synthetic retained native preference ' + str(index) + ':' + 'N' * 1024)
        self.profile_phase = 'fixture_recorder_drain'
        self.metrics['population_seconds'] = perf_counter() - start
        # Construction emitted tens of thousands of genuine registry events.
        # Drain their native Recorder work BEFORE the measured migration.
        # Keep the product's synchronization limit and all safety fences intact.
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.tasks import SynchronizeTask
        await self.f.hass.async_block_till_done()
        instance = get_instance(self.f.hass)
        start = perf_counter()
        future = self.f.hass.loop.create_future()
        instance.queue_task(SynchronizeTask(future))
        await future
        self.metrics['fixture_recorder_drain_seconds'] = perf_counter() - start
        start = perf_counter()
        self.original_files = await self._flush_documents(initialize=True)
        self.original_managers = self.manager_documents()
        self.original_identities = {}
        for manager in self.f.managers:
            info = Path(manager._store.path).stat()
            self.original_identities[manager._store.key] = (info.st_dev, info.st_ino, info.st_mode)
        self.assert_population()
        self.assertEqual(sum(r['disabled_by'] == 'user' for r in
            self.original_managers['core.entity_registry']['entities']), self.LIVE)
        raw_size = len(self.original_files['core.entity_registry'])
        self.metrics.update(raw_er_bytes=raw_size, deleted_rows=self.DELETED,
            extra_disabled_live_rows=self.LIVE,
            total_live_rows=len(self.original_managers['core.entity_registry']['entities']))
        # UNKNOWN until the owner runs: no schema-size estimate passes these.
        self.assertGreater(raw_size, 24_000_000, 'calibrate genuine API UID/name sizes upward')
        self.assertLess(raw_size, self.module('native_input_baseline').MAX_FILE_BYTES,
                        'calibrate genuine API UID/name sizes downward')
        self.metrics['initial_flush_and_readback_seconds'] = perf_counter() - start
        self.stimulus_count = 0
        self.profile_phase = 'forward'
        return result

    async def original_from_fresh_stores(self):
        prep = self.module('prepared').native_prepared_store(self.f.hass)
        log = self.module('journal').native_journal(self.f.hass)
        await prep.initialize()
        await log.initialize()
        records = await log.all()
        self.assertEqual(len(records), 1)
        record = records[0]
        baseline, composition = prep.resolve_composition(record.plan.native_inputs)
        self.same(baseline.digest, record.plan.native_inputs[2], 'native digest mismatch')
        self.assertEqual(tuple(s.adapter for s in record.plan.steps), ('registry', 'yaml'))
        for item in baseline.files:
            self.same(item.raw, self.original_files[item.key], 'ORIGINAL raw bytes changed: ' + item.key)
            self.same(json.loads(item.manager_json), self.original_managers[item.key],
                      'ORIGINAL full native serializer changed: ' + item.key)
            self.same(item.identity, self.original_identities[item.key],
                      'ORIGINAL pre-effect native identity changed: ' + item.key)
        payload = prep.resolve(*record.plan.native_inputs[:2])
        self.assertEqual(payload['schema'], 3)
        self.assertEqual(payload['encoding'], 'zlib+base64')
        if not hasattr(self, 'original_baseline'):
            self.original_baseline = baseline
            self.original_composition = composition
            self.original_plan = record.plan
            self.operation_id = record.operation_id
            self.metrics['prepared_bytes'] = len(json.dumps(payload, sort_keys=True,
                separators=(',', ':'), allow_nan=False).encode())
            self.metrics['native_codec_bytes'] = len(self.module('native_input_baseline').encode_baseline(baseline))
        else:
            self.same(baseline, self.original_baseline, 'fresh reopen changed full ORIGINAL bytes/identity/receipt')
            self.same(composition, self.original_composition, 'fresh reopen changed composition/history')
            self.same(record.plan, self.original_plan, 'fresh journal changed server-generated Plan')
            self.same(record.plan.fingerprint, self.original_plan.fingerprint, 'fingerprint drifted')
            self.same(record.operation_id, self.operation_id, 'operation identity drifted')
        return prep, record, baseline, composition

    async def stimulus(self, value):
        count = self.stimulus_count
        if count in (2, 4):
            self.metrics[('forward_seconds' if count == 2 else 'reverse_seconds')] = perf_counter() - self.phase_start
            _, record, _, _ = await self.original_from_fresh_stores()
            self.assertEqual(record.status, 'applied' if count == 2 else 'rolled_back')
            self.assert_population()
        await server.NativeTemplateYAMLServerTests.stimulus(self, value)
        self.stimulus_count += 1
        if self.stimulus_count in (2, 4):
            self.profile_phase = 'forward' if self.stimulus_count == 2 else 'reverse'
            self.phase_start = perf_counter()

    async def test_core104_real_37275_deleted_full_original_yaml_forward_fresh_reverse_history(self):
        await self._execute(readonly=False)

    async def test_core104_readonly_preflight_diagnosis(self):
        await self._execute(readonly=True)

    async def _execute(self, *, readonly):
        self.metrics = {}
        self.profile_phase = 'fixture'
        samples = Counter()
        stop = threading.Event()
        def sample():
            own = threading.get_ident()
            while not stop.wait(0.05):
                for ident, frame in sys._current_frames().items():
                    if ident == own:
                        continue
                    leaf = (Path(frame.f_code.co_filename).name, frame.f_code.co_name)
                    product = '-'
                    while frame is not None:
                        if '/custom_components/ha_entity_renamer/' in frame.f_code.co_filename:
                            product = Path(frame.f_code.co_filename).name + ':' + frame.f_code.co_name
                            break
                        frame = frame.f_back
                    samples[self.profile_phase, *leaf, product] += 1
        sampler = threading.Thread(target=sample, name='scale-readonly-stack-sampler', daemon=True)
        sampler.start()
        failures, inspections, graph_diffs = [], [], []
        tool = next(i for i in range(6) if sys.monitoring.get_tool(i) is None)
        sys.monitoring.use_tool_id(tool, 'actual scale readonly admission diagnosis')
        def raised(code, offset, error):
            if '/custom_components/ha_entity_renamer/' not in code.co_filename:
                return
            if isinstance(error, (StopIteration, StopAsyncIteration, GeneratorExit)):
                return  # Normal returns can contain huge private fixture data.
            message = str(error)
            if message == 'template_composition_rejected':
                return  # Expected typed Union decoder branch, not an admission failure.
            row = (Path(code.co_filename).name, code.co_name, type(error).__name__, message[:180])
            if row not in failures:
                failures.append(row)
                if len(failures) > 80:
                    del failures[0]
            if code.co_filename.endswith('accepted_input_state.py') and 'input_graph_changed' in message:
                frame = sys._getframe(1)
                while frame is not None and frame.f_code.co_name != 'assert_current':
                    frame = frame.f_back
                if frame is not None:
                    values = frame.f_locals
                    expected, current = values.get('expected'), values.get('current')
                    if isinstance(expected, (tuple, list)) and isinstance(current, (tuple, list)):
                        row = {'directory':values.get('directory'), 'names':values.get('names'),
                               'removed':sorted(set(map(tuple, expected))-set(map(tuple, current)))[:12],
                               'added':sorted(set(map(tuple, current))-set(map(tuple, expected)))[:12]}
                        if row not in graph_diffs:
                            graph_diffs.append(row)
        codes = [self.module('native_registry').NativeRegistryBridge.inspect.__code__,
                 self.module('native_registry_transaction').ReceiptedRegistryTransactionAdapter.inspect.__code__,
                 self.module('native_yaml_transaction').ReceiptedYAMLTransactionAdapter.inspect.__code__]
        def inspected(code, offset, value):
            row = {'file':Path(code.co_filename).name}
            if type(value).__name__ == 'RegistryEvidence':
                row.update({key:getattr(value,key) for key in ('position','reason','identity','runtime','durability','recorder_metadata','complete_history')})
            else:
                row['position'] = getattr(value, 'value', type(value).__name__)
            inspections.append(row)
        sys.monitoring.register_callback(tool, sys.monitoring.events.RAISE, raised)
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, inspected)
        sys.monitoring.set_events(tool, sys.monitoring.events.RAISE)
        for code in codes:
            sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_RETURN)
        start = perf_counter()
        try:
            # Reuses actual server producer, real Recorder, independent 5/7
            # output/UUID/history assertions and fresh reverse session checks.
            if readonly:
                runtime, mapping, _, _ = await self.data(variant='sensor')
                for value in (5, 7):
                    await self.stimulus(value)
                async def refresh():
                    self.assertTrue(runtime.operation_lock.locked())
                async with runtime.operation_lock:
                    factory = self.module('native_template_yaml_server').NativeTemplateYAMLServer
                    session = await factory.prepare(self.f.hass, mapping, guard=lambda: None, refresh=refresh)
                    try:
                        coordinator = session._coordinator()
                        positions = [await coordinator._adapter(step).inspect(step) for step in session.plan.steps]
                        self.assertEqual(await runtime.journal.all(), ())
                        self.assert_population()
                        self.assertTrue(all(p is self.module('transaction').Position.BEFORE for p in positions),
                                        'actual read-only native preflight must admit every real server step')
                    finally:
                        session.close()
                return
            await self.flow(variant='sensor')
            self.assertEqual(self.stimulus_count, 6)
            prep, record, baseline, composition = await self.original_from_fresh_stores()
            self.assertEqual(record.status, 'rolled_back')
            self.assertEqual(record.direction, 'rollback')
            self.assertEqual(record.phases, ('undone', 'undone'))
            self.assert_population()
            rr = self.module('native_registry_receipts')
            receipts = rr.native_registry_receipt_store(self.f.hass, prep)
            await receipts.initialize()
            chain = rr.RegistryReceiptChain.reopen(receipts, record.operation_id,
                record.plan, baseline.receipt, dict(composition.prepared_digests))
            self.assertFalse(chain.writable)
            self.assertIsNone(chain.active)
            accepted = chain.accepted_documents()
            # Full persisted final documents must match the validated receipt
            # chain, including the real API's own modified_at/order deltas.
            for manager in self.f.managers:
                key = manager._store.key
                raw = Path(manager._store.path).read_bytes()
                self.same(json.loads(raw), accepted[key], 'final persisted native image differs from chain: ' + key)
            # Source native fields restored; its timestamp may legitimately
            # advance only under the validated native receipt chain above.
            before = self.original_managers['core.entity_registry']['entities']
            after = self.manager_documents()['core.entity_registry']['entities']
            original_source = next(r for r in before if r['id'] in self.source_uuids)
            final_source = next(r for r in after if r['id'] in self.source_uuids)
            self.same({k: v for k, v in original_source.items() if k != 'modified_at'},
                      {k: v for k, v in final_source.items() if k != 'modified_at'},
                      'source native row did not return to SAME ORIGINAL')
        except Exception as error:
            trace = error.__traceback__
            while trace is not None:
                frame = trace.tb_frame
                if frame.f_code.co_name == 'assert_current' and frame.f_code.co_filename.endswith('accepted_input_state.py'):
                    values = frame.f_locals
                    expected, current = values.get('expected'), values.get('current')
                    if isinstance(expected, tuple) and isinstance(current, tuple):
                        print('NATIVE_SCALE_GRAPH_DIFF', json.dumps({
                            'directory': values.get('directory'), 'names': values.get('names'),
                            'removed': sorted(set(expected) - set(current))[:12],
                            'added': sorted(set(current) - set(expected))[:12]}))
                trace = trace.tb_next
            raise
        finally:
            sys.monitoring.set_events(tool, 0)
            for code in codes:
                sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.RAISE, None)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, None)
            sys.monitoring.free_tool_id(tool)
            print('NATIVE_SCALE_ADMISSION_FAILURES', json.dumps(failures))
            print('NATIVE_SCALE_INSPECTIONS', json.dumps(inspections[-20:]))
            print('NATIVE_SCALE_GRAPH_DIFFS', json.dumps(graph_diffs))
            stop.set()
            sampler.join(1)
            print('NATIVE_SCALE_STACK_SAMPLES', json.dumps([
                {'phase': key[0], 'file': key[1], 'function': key[2], 'product': key[3], 'samples': count}
                for key, count in samples.most_common(35)]))
            self.metrics['total_seconds'] = perf_counter() - start
            print('NATIVE_REGISTRY_SCALE_AGGREGATES', json.dumps(self.metrics, sort_keys=True))


if __name__ == '__main__':
    unittest.main()
