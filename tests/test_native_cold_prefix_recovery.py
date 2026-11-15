"""Abrupt process loss at a durable registry prefix on native HA Stores."""
import asyncio
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_native_own_id_cold_process as own


def assert_native_restored(before, after, source_uuid):
    """Compare full three documents; admit only our row's native reorder/time."""
    before, after = deepcopy(before), deepcopy(after)
    rows_before = before['core.entity_registry']['data'].pop('entities')
    rows_after = after['core.entity_registry']['data'].pop('entities')
    def split(rows):
        ids = [row['id'] for row in rows]
        if len(ids) != len(set(ids)) or ids.count(source_uuid) != 1:
            raise AssertionError('registry identity ambiguity')
        source = deepcopy(rows[ids.index(source_uuid)])
        source.pop('modified_at', None)
        return source, [row for row in rows if row['id'] != source_uuid], ids.index(source_uuid)
    source_before, foreign_before, pos_before = split(rows_before)
    source_after, foreign_after, pos_after = split(rows_after)
    if source_before != source_after:
        raise AssertionError(('source fields differ', source_before, source_after))
    if foreign_before != foreign_after:
        raise AssertionError(('foreign fields or order differ', foreign_before, foreign_after))
    if before != after:
        raise AssertionError(('native documents differ', before, after))
    return {'source_index_before': pos_before, 'source_index_after': pos_after,
            'foreign_count': len(foreign_before), 'native_documents_equal': True}


class PrefixPhase(own.OwnIDColdPhase):
    async def _independent_history(self):
        # Same finite-row readback method as the accepted R7 independent
        # before/after helper, using a native SQLite backup and full columns.
        mod = own.backend_fixture.installed_module
        await mod('recorder_guard').NativeRecorderGuard(self.f.hass).async_synchronize()
        db = self.f.root / 'fixture.db'
        def read():
            result = {}
            with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as live, sqlite3.connect(':memory:') as copy:
                live.backup(copy)
                for table in ('states', 'states_meta', 'state_attributes',
                              'statistics', 'statistics_short_term', 'statistics_meta'):
                    cursor = copy.execute(f'SELECT * FROM "{table}"')
                    columns = [row[0] for row in cursor.description]
                    # HA updates only this heartbeat on an existing state row.
                    keep = [i for i, name in enumerate(columns) if name != 'last_reported_ts']
                    def encode(value):
                        return {'sqlite_blob_base64': base64.b64encode(value).decode('ascii')} if type(value) is bytes else value
                    result[table] = [[encode(row[i]) for i in keep] for row in cursor]
            return result
        return await self.f.hass.async_add_executor_job(read)

    async def test_phase(self):
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.setup import async_setup_component
        self.maxDiff = None
        hass, root = self.f.hass, self.f.root
        old, new = 'input_boolean.cold_worker', 'input_boolean.cold_worker_next'
        runtime = self._runtime()
        mode = os.environ.get('RENAMER_PREFIX_MODE', 'prefix')
        cut_step = os.environ.get('RENAMER_PREFIX_STEP', 'resume')
        uncertain = mode in ('intent', 'effect') or (mode == 'compensation' and cut_step == 'check')
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        if self.phase == 'forward':
            (root / 'cold_scripts.yaml').write_text('')
            (root / 'template.yaml').write_text(
                '- unique_id: cold_observer\n  sensor:\n  - unique_id: view\n'
                '    name: Cold observer\n    state: "{{ states(\'input_boolean.cold_worker\') }}"\n')
            (root / 'configuration.yaml').write_text(
                'input_boolean:\n  cold_worker:\n    name: Cold worker\n'
                'script: {}\ntemplate: !include template.yaml\n')
            config = await async_hass_config_yaml(hass)
            for domain in ('input_boolean', 'script', 'template'):
                self.assertTrue(await async_setup_component(hass, domain, config))
            await hass.async_block_till_done()
            await self._flush_documents(initialize=True)
            original = {name: base64.b64encode((root / name).read_bytes()).decode()
                        for name in ('configuration.yaml', 'cold_scripts.yaml', 'template.yaml')}
            uuid = self.f.entity.async_get(old).id
            mod = own.backend_fixture.installed_module
            history = mod('history_guard').NativeHistoryGuard(mod('recorder_guard').NativeRecorderGuard(hass))
            history_before = await history.capture({old: new})
            sql_before = await self._independent_history()
            registry_path = root / '.storage/core.entity_registry'
            registry_before = json.loads(registry_path.read_bytes())['data']['entities']
            native_before = {key: json.loads((root / '.storage' / key).read_bytes())
                for key in ('core.entity_registry', 'core.device_registry', 'core.config_entries')}
            foreign_before = {row['id']: row for row in registry_before if row['id'] != uuid}
            # Validate the independent oracle before any migration effect.
            json.dumps({'sql_before': sql_before, 'foreign_before': foreign_before,
                        'native_before': native_before}, allow_nan=False)
            # The product sanitizes staging errors. Capture original exception
            # at its executor boundary, including OS errno, before it is wrapped.
            import traceback
            def trace(frame, event, arg):
                if event == 'exception' and frame.f_code.co_name == '_stage_owned':
                    _, error, tb = arg
                    if isinstance(error, (OSError, ValueError)):
                        traceback.print_exception(type(error), error, tb)
                return trace
            validation = own.backend_fixture.installed_module('yaml_validation')
            stage = validation._stage_owned
            def traced_stage(*args, **kwargs):
                sys.settrace(trace)
                try:
                    return stage(*args, **kwargs)
                finally:
                    sys.settrace(None)
            with patch.object(validation, '_stage_owned', traced_stage):
                review = await runtime.native_yaml_prepare(hass, {old: new}, actor_id='cold-prefix',
                    guard=lambda: None, refresh=refresh)
            journal_type = own.backend_fixture.installed_module('journal').DurableJournal
            save = journal_type.save
            async def cut(journal, record):
                await save(journal, record)
                boundary = ('intent', 'pending') if mode in ('intent', 'effect') else ('verified', 'pending')
                if record.direction == 'forward' and record.phases == boundary:
                    baseline, _ = runtime.preparations.resolve_composition(record.plan.native_inputs)
                    data = {'pid': os.getpid(), 'backend_entry_id': self.backend_entry_id,
                        'public_source_sha256': self.public_source_sha256, 'uuid': uuid,
                        'operation': record.operation_id, 'fingerprint': record.plan.fingerprint,
                        'baseline_digest': baseline.digest,
                        'receipt_b64': base64.b64encode(baseline.receipt_json).decode(),
                        'original': original, 'history': history_before,
                        'sql_before': sql_before, 'foreign_before': foreign_before,
                        'native_before': native_before}
                    with self.meta_path.open('x') as stream:
                        json.dump(data, stream); stream.flush(); os.fsync(stream.fileno())
                    print('COLD_PREFIX_DURABLE_CUT', record.phases, flush=True)
                    if mode != 'effect':
                        os._exit(73)
            native_io = own.backend_fixture.installed_module('native_registry').NativeRegistryIO
            actual_mutate = native_io.mutate
            def cut_effect(io, change, target):
                result = actual_mutate(io, change, target)
                print('COLD_NATIVE_FORWARD_EFFECT_CUT', flush=True)
                os._exit(73)
            with patch.object(journal_type, 'save', cut), patch.object(
                    native_io, 'mutate', cut_effect if mode == 'effect' else actual_mutate):
                await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                    actor_id='cold-prefix', guard=lambda: None, refresh=refresh)
            self.fail('durable prefix was not reached')
        config = await async_hass_config_yaml(hass)
        self.assertTrue(await async_setup_component(hass, 'input_boolean', config))
        await hass.async_block_till_done()
        meta = self.meta
        record = await runtime.journal.load(meta['operation'])
        expected_phases = (('undo_intent', 'pending') if mode == 'compensation' and cut_step == 'check'
                           else ('intent', 'pending') if mode in ('intent', 'effect')
                           else ('verified', 'pending'))
        self.assertEqual(record.phases, expected_phases)
        self.assertEqual(record.plan.fingerprint, meta['fingerprint'])
        baseline, _ = runtime.preparations.resolve_composition(record.plan.native_inputs)
        self.assertEqual(baseline.digest, meta['baseline_digest'])
        self.assertEqual(baseline.receipt_json, base64.b64decode(meta['receipt_b64']))
        mod = own.backend_fixture.installed_module
        history = mod('history_guard').NativeHistoryGuard(mod('recorder_guard').NativeRecorderGuard(hass))
        self.assertTrue(await history.verify(meta['history']))
        rows = [row for key in (old, new) if (row := self.f.entity.async_get(key)) is not None]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, meta['uuid'])
        if mode in ('prefix', 'foreign') or mode == 'compensation' and cut_step == 'restore':
            self.assertEqual(rows[0].entity_id, new)
        status = await runtime.recovery_status(hass, guard=lambda: None, refresh=lambda: asyncio.sleep(0))
        print('COLD_PREFIX_STATUS', status, flush=True)
        # Public recovery deliberately sanitizes failures. Capture the original
        # local exception chain at the projection boundary for this fixture.
        import traceback
        controller = own.backend_fixture.installed_module('recovery_controller')
        project = controller._result
        def observed_result(*args, **kwargs):
            error = sys.exception()
            if error is not None:
                traceback.print_exception(error)
            return project(*args, **kwargs)
        self.enterContext(patch.object(controller, '_result', observed_result))
        if uncertain:
            paths = [root / name for name in ('.storage/ha_entity_renamer.migrations',
                '.storage/ha_entity_renamer.prepared', '.storage/ha_entity_renamer.registry_receipts',
                '.storage/core.entity_registry', '.storage/core.device_registry',
                '.storage/core.config_entries', 'configuration.yaml', 'template.yaml')]
            before = {str(path): path.read_bytes() for path in paths}
            native_io = own.backend_fixture.installed_module('native_registry').NativeRegistryIO
            with patch.object(native_io, 'mutate', side_effect=AssertionError('uncertain effect replayed')) as mutation:
                for method in (runtime.recovery_inspect, runtime.recovery_restore):
                    result = await method(hass, record.operation_id, record.plan.fingerprint,
                        guard=lambda: None, refresh=refresh)
                    self.assertEqual((result['state'], result['eligible']), ('UNKNOWN', False))
                    self.assertEqual({str(path): path.read_bytes() for path in paths}, before)
                    mutation.assert_not_called()
            if mode == 'intent':
                self.assertIsNone(self.f.entity.async_get(new))
            self.assertEqual(await runtime.journal.load(record.operation_id), record)
            print('COLD_UNCERTAIN_NO_REPLAY', mode, cut_step, flush=True)
            return
        if mode == 'foreign':
            path = root / 'template.yaml'
            path.write_bytes(path.read_bytes() + b'\n# foreign synthetic edit\n')
            before = path.read_bytes()
            native_io = own.backend_fixture.installed_module('native_registry').NativeRegistryIO
            with patch.object(native_io, 'mutate', side_effect=AssertionError('foreign edit overwritten')) as mutation:
                for method in (runtime.recovery_inspect, runtime.recovery_restore):
                    result = await method(hass, record.operation_id, record.plan.fingerprint,
                        guard=lambda: None, refresh=refresh)
                    self.assertEqual((result['state'], result['eligible']), ('UNKNOWN', False))
                    self.assertEqual(path.read_bytes(), before)
                    self.assertEqual(await runtime.journal.load(record.operation_id), record)
                    mutation.assert_not_called()
            self.assertEqual(self.f.entity.async_get(new).id, meta['uuid'])
            print('COLD_FOREIGN_EDIT_REFUSED', flush=True)
            return
        # Inspect intentionally sanitizes exceptions; preserve the original
        # traceback and chained cause from the same read-only admission first.
        import traceback
        backend = self._loaded_backend()
        async with runtime.operation_lock:
            factory = backend._yaml_factory_for_plan(runtime.preparations, record.plan)
            try:
                session = await factory.bind_reverse(hass, record.operation_id, record.plan.fingerprint,
                    guard=lambda: None, refresh=refresh)
                session.close()
            except Exception as error:
                traceback.print_exception(error)
        inspected = await runtime.recovery_inspect(hass, record.operation_id, record.plan.fingerprint,
            guard=lambda: None, refresh=refresh)
        print('COLD_PREFIX_INSPECT', inspected, flush=True)
        self.assertEqual(inspected['state'], 'recoverable_prefix')
        native_io = own.backend_fixture.installed_module('native_registry').NativeRegistryIO
        actual_mutate = native_io.mutate
        def cut_compensation(io, change, target):
            result = actual_mutate(io, change, target)
            print('COLD_NATIVE_COMPENSATION_EFFECT_CUT', flush=True)
            os._exit(74)
        with patch.object(native_io, 'mutate', cut_compensation if mode == 'compensation' else actual_mutate):
            restored = await runtime.recovery_restore(hass, record.operation_id, record.plan.fingerprint,
                guard=lambda: None, refresh=refresh)
        print('COLD_PREFIX_RESTORE', restored, flush=True)
        self.assertEqual(restored['state'], 'rolled_back')
        self.assertEqual(self.f.entity.async_get(old).id, meta['uuid'])
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertTrue(await history.verify(meta['history']))
        sql_after = await self._independent_history()
        for table, before_rows in meta['sql_before'].items():
            after_by_id = {row[0]: row for row in sql_after[table]}
            differences = [(row, after_by_id.get(row[0])) for row in before_rows
                           if after_by_id.get(row[0]) != row]
            self.assertEqual(differences, [], table)
        await self._flush_documents()
        registry_after = json.loads((root / '.storage/core.entity_registry').read_bytes())['data']['entities']
        foreign_after = {row['id']: row for row in registry_after if row['id'] != meta['uuid']}
        self.assertEqual(foreign_after, meta['foreign_before'])
        native_after = {key: json.loads((root / '.storage' / key).read_bytes())
            for key in ('core.entity_registry', 'core.device_registry', 'core.config_entries')}
        # Registry reinsertion can move our row while preserving the exact
        # relative order and all fields of every foreign row.
        proof = assert_native_restored(meta['native_before'], native_after, meta['uuid'])
        print('COLD_INDEPENDENT_FULL_DOCUMENTS', json.dumps(proof, sort_keys=True), flush=True)
        for name, encoded in meta['original'].items():
            self.assertEqual((root / name).read_bytes(), base64.b64decode(encoded))
        final = await runtime.journal.load(record.operation_id)
        self.assertEqual(final.phases, ('undone', 'pending'))
        inspected = await runtime.recovery_inspect(hass, record.operation_id, record.plan.fingerprint,
            guard=lambda: None, refresh=refresh)
        print('COLD_PREFIX_RESTORED_INSPECT', inspected, flush=True)
        self.assertEqual((inspected['state'], inspected['reason']), ('rolled_back', 'restored_verified'))
        self.assertNotIn('frozen_review', inspected)
        self.assertEqual(await runtime.journal.load(record.operation_id), final)


@unittest.skipUnless(own.backend_fixture.activation.fixtures.HAS_HA, 'actual pinned HA required')
class NativeColdPrefixRecoveryTests(unittest.TestCase):
    def test_cold_prefix_public_inspect_and_inverse(self):
        self._run_case('prefix')

    def test_cold_unproven_intent_refuses_without_replay(self):
        self._run_case('intent')

    def test_cold_native_effect_unconfirmed_is_not_replayed(self):
        self._run_case('effect')

    def test_cold_compensation_unconfirmed_is_not_replayed(self):
        self._run_case('compensation')

    def test_cold_foreign_yaml_is_preserved(self):
        self._run_case('foreign')

    def _run_case(self, mode):
        parent = os.environ.get('RENAMER_TEST_SCRATCH') or tempfile.gettempdir()
        with tempfile.TemporaryDirectory(prefix='renamer-prefix-cold-', dir=parent) as directory:
            root = Path(directory) / 'config'; root.mkdir()
            evidence = Path('/qa/cold-evidence') / Path(directory).name
            evidence.mkdir(parents=True)
            env = dict(os.environ, RENAMER_OWN_COLD_CONFIG=str(root),
                       RENAMER_OWN_COLD_META=str(Path(directory) / 'oracle.json'),
                       RENAMER_PREFIX_MODE=mode)
            phases = [('forward', 'forward', 73), ('resume', 'restore', 74), ('resume', 'check', 0)] if mode == 'compensation' else [('forward', 'forward', 73), ('resume', 'resume', 0)]
            for phase, step, expected in phases:
                env['RENAMER_OWN_COLD_PHASE'] = phase
                env['RENAMER_PREFIX_STEP'] = step
                result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()),
                    'PrefixPhase.test_phase'], env=env, capture_output=True, text=True, timeout=600)
                (evidence / (step + '.stdout')).write_text(result.stdout)
                (evidence / (step + '.stderr')).write_text(result.stderr)
                if Path(env['RENAMER_OWN_COLD_META']).exists():
                    shutil.copyfile(env['RENAMER_OWN_COLD_META'], evidence / 'oracle.json')
                print(result.stdout, result.stderr, flush=True)
                if result.returncode != expected:
                    shutil.copytree(root, evidence / (step + '-failed-config'))
                self.assertEqual(result.returncode, expected)


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(NativeColdPrefixRecoveryTests)

if __name__ == '__main__':
    unittest.main()
