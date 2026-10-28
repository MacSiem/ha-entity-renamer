"""Read-only native custom-profile IO proof shared by the two timer cases.

Installed runtime pins came from owner readback of both official HA images
(Python3.14.6 and3.14.9); no inferred semver or rewritten interpreter.
No loop/Task/State/Event/callback/cache replacement; no worker queue blocker.
"""
import asyncio
from functools import partial
import hashlib
import importlib
import json
from pathlib import Path
import sys
import threading
import time

_PYTHON = '3.14.6 (main, Jun 16 2026, 10:51:59) [GCC 15.2.0]'
_RUNTIME_FILES = {'/usr/local/lib/python3.14/asyncio/base_events.py': '02a3fb293559af5e053396d47a4090f009b0746bf1df25462736657c1663495d', '/usr/local/lib/python3.14/asyncio/futures.py': '19398482eef563b3f88d183116aba596c80b99937e9566f2b6b934c7bd0dafa2', '/usr/local/lib/python3.14/asyncio/tasks.py': '00c9c08f6ca8692d873d1b5aab067a5932ae90fecea632d00de8ad48325ce5ab', '/usr/local/lib/python3.14/asyncio/events.py': '62e2c619c38407309c0d2c98638642174ee7be5b874676c0225392fed4a00b0f', '/usr/local/lib/python3.14/asyncio/selector_events.py': '2c15f1e7279f5baa09bc1fe4c6390d106cff3df6cf1493064ec0e86d846bc169', '/usr/local/lib/python3.14/asyncio/unix_events.py': '8cb0f7ff2418c6428a87ba5095d770010c030ad89c65e86c8aca3ac33ecbcacd', '/usr/local/lib/python3.14/lib-dynload/_asyncio.cpython-314-aarch64-linux-musl.so': 'a3bdbc33b8e402584fce739e6fc3ffabce89ab52d6be81abd6f10d9b1f50811f'}


# Actual official HA image runtimes; source deltas reviewed, native IO qualification required.
_RUNTIME_PINS = {_PYTHON: _RUNTIME_FILES,
    '3.14.9 (main, Oct 22 2026, 08:07:04) [GCC 15.2.0]': {'/usr/local/lib/python3.14/asyncio/base_events.py': '3e54aa78ed18af0ee5a93384a63fd522496d1805b56ab6561e54c4fdb0869127', '/usr/local/lib/python3.14/asyncio/futures.py': '19398482eef563b3f88d183116aba596c80b99937e9566f2b6b934c7bd0dafa2', '/usr/local/lib/python3.14/asyncio/tasks.py': '85a314ab91924a253828211127d6498061b5f232cba01aec92de6172863e1f2e', '/usr/local/lib/python3.14/asyncio/events.py': '62e2c619c38407309c0d2c98638642174ee7be5b874676c0225392fed4a00b0f', '/usr/local/lib/python3.14/asyncio/selector_events.py': '3805eead73e63f10ef3600bb86bd08e960be82e186a27f6711891cc6894e42dc', '/usr/local/lib/python3.14/asyncio/unix_events.py': '4d6ce6127417781f059fcca8384754c04dfad4d6887bc6bd9a9b09004d4167ca', '/usr/local/lib/python3.14/lib-dynload/_asyncio.cpython-314-aarch64-linux-musl.so': '6169bbcbf697b75220ad09da3264235b72b1ba85a5b16c853f2d9947d2e39fe9'}}

class CustomProfileIOProof:
    """Mixin for the existing real timer observer; never a product policy."""
    async def prepare(self):
        await super().prepare()
        import _asyncio
        from homeassistant.core import HomeAssistant
        from asyncio import base_events, futures, unix_events
        self.io_rows = []
        self.deadline_wait = None
        self.io_loop_thread = threading.get_ident()
        self.case.assertIn(sys.version, _RUNTIME_PINS)
        runtime_files = _RUNTIME_PINS[sys.version]
        self.case.assertIs(type(self.hass.loop), unix_events._UnixSelectorEventLoop)
        self.case.assertIs(asyncio.SelectorEventLoop, unix_events._UnixSelectorEventLoop)
        self.case.assertIs(asyncio.Future, _asyncio.Future)
        self.case.assertIs(asyncio.Task, _asyncio.Task)
        self.case.assertIsNone(self.hass.loop.get_task_factory())
        self.case.assertIs(self.hass.loop.run_in_executor.__func__, base_events.BaseEventLoop.run_in_executor)
        self.case.assertIs(self.hass.loop._run_once.__func__, base_events.BaseEventLoop._run_once)
        proof = self.case.module('native_unload_code_proof')
        pins = {str(Path(path).relative_to('/usr/local/lib/python3.14')): value
                for path, value in runtime_files.items()}
        self.runtime_refs = ((base_events.BaseEventLoop, 'run_in_executor'),
                             (base_events.BaseEventLoop, '_run_once'),
                             (futures, 'wrap_future'), (futures, '_chain_future'))
        def runtime_prove():
            for path, checksum in runtime_files.items():
                assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == checksum, path
            return proof.prove_methods(Path('/usr/local/lib/python3.14'), pins, self.runtime_refs)
        self.runtime_prove = runtime_prove
        executor = self.case.module('yaml_validation')._settled_executor
        self.runtime_methods, self.runtime_codes = await executor(self.hass, runtime_prove)
        self.refs += ((HomeAssistant, 'async_add_executor_job'),)
        self.methods, self.codes = await executor(self.hass, self.prove)
        self.tags[self.codes[-1]] = 'custom_executor'
        local = self.vendor.classes['LocalLoader']
        library = self.vendor.classes['ProfileLibrary']
        native = sys.modules['custom_components.powercalc']
        factory = sys.modules['custom_components.powercalc.power_profile.factory']
        self.io_refs = ((local, 'load_model'), (local, '_load_custom_model'), (local, '_load_json'),
                        (library, '_load_model_data'), (factory, 'get_power_profile'),
                        (native, '_async_load_yaml_sensor'))
        def io_prove():
            self.vendor.sources()
            return proof.prove_methods(self.vendor.root,
                self.case.module('powercalc_source_binding')._pins(), self.io_refs)
        self.io_prove = io_prove
        self.io_methods, self.io_codes = await executor(self.hass, io_prove)
        self.tags.update({self.io_codes[0]: 'custom_load', self.io_codes[1]: 'custom_worker',
                          self.io_codes[2]: 'custom_json'})
        self.io_cls = local
        graph = json.loads(self.case.evidence.powercalc_sources.closed_graph_json)
        by_uid = {w.unique_id: w for w in self.old_w.values()}
        self.custom_specs = {}
        for profile in graph['profiles']:
            old = by_uid.get(profile['unique_id'])
            if old is not None:
                locator = profile['custom_model_directory']
                self.case.assertEqual(profile['directory'], locator)
                self.case.assertEqual(old._sensor_config['custom_model_directory'], locator)
                self.custom_specs[str(Path(self.hass.config.config_dir) / locator)] = profile
        self.case.assertEqual(len(self.custom_specs), 2)

    def _runtime_unchanged(self):
        self.unchanged(self.runtime_refs, self.runtime_methods, self.runtime_codes)
        self.unchanged(self.io_refs, self.io_methods, self.io_codes)
        from asyncio import base_events, unix_events
        assert type(self.hass.loop) is unix_events._UnixSelectorEventLoop
        assert self.hass.loop.get_task_factory() is None
        assert self.hass.loop.run_in_executor.__func__ is base_events.BaseEventLoop.run_in_executor
        assert self.hass.loop._run_once.__func__ is base_events.BaseEventLoop._run_once

    def wait_at_native_custom_setup(self):
        """Only wait for actual observed deadlines, immediately before real IO."""
        if self.deadline_wait is not None:
            return True
        live = self.live_typed()
        if set(live) != set(self.old_e):
            return False
        assert not self.numeric and not self.cutovers
        scope = next(iter(live.values()))['action'].owner.scope
        assert all(i['action'].owner.scope is scope for i in live.values())
        assert scope.started == 1 and scope.returned == 0
        assert asyncio.current_task().get_context().get(scope.causal, None) is scope.marker
        assert all(scope.clock_owner.states[name].phase == 'retained' for name in self.old_e)
        assert all(self.baselines[name].handle.cancelled() for name in self.old_e)
        due = max(row['deadline'] for row in live.values())
        # A real deadline wait, without arbitrary padding or clock mutation.
        # This alone never qualifies a delivery. The following native pending
        # custom file-read yield must allow HA to deliver BOTH callbacks.
        while True:
            remaining = due - self.hass.loop.time()
            if remaining <= 0:
                break
            time.sleep(remaining)
        self.deadline_wait = {'scope': scope, 'handles': dict(live), 'deadline': due,
                              'loop': self.hass.loop.time()}
        return True

    def _io_loader(self, loader):
        if type(loader) is not self.io_cls or loader._hass is not self.hass:
            return False
        assert loader._is_custom_directory is True
        assert loader._data_directory in self.custom_specs
        for name in ('load_model', '_load_custom_model', '_load_json'):
            actual = getattr(loader, name)
            assert actual.__self__ is loader and actual.__func__ is getattr(self.io_cls, name)
        return True

    def _io_row(self, loader):
        rows = [row for row in self.io_rows if row['loader'] is loader]
        assert len(rows) == 1, 'one actual LocalLoader invocation per fresh native loader'
        return rows[0]

    def _observe_io(self, frame, phase, result):
        self._runtime_unchanged()
        tag = self.tags[frame.f_code]
        loader = frame.f_locals.get('self')
        if tag == 'custom_load':
            if not self._io_loader(loader):
                return
            if phase == 'start':
                assert threading.get_ident() == self.io_loop_thread
                task = asyncio.current_task()
                assert type(task) is asyncio.Task
                parents = list(self._ancestors(frame, self.io_codes[3]))
                assert any(p.get('custom_directory') == loader._data_directory for p in parents)
                factories = list(self._ancestors(frame, self.io_codes[4]))
                assert any(p.get('hass') is self.hass for p in factories)
                # The synchronous product guard is not called at every YAML
                # frame. Stress scheduling at the observed REAL native read,
                # then let the original loader submit and await its real IO.
                assert self.wait_at_native_custom_setup(), 'both retained clocks before native model IO'
                scope = self.deadline_wait['scope']
                assert task.get_context().get(scope.causal, None) is scope.marker
                self.io_rows.append({'loader': loader, 'task': task,
                    'directory': loader._data_directory, 'started_loop': self.hass.loop.time(),
                    'qualifying': not self.io_rows, 'resumes': []})
            else:
                row = self._io_row(loader)
                assert asyncio.current_task() is row['task']
                if phase == 'yield':
                    assert type(result) is asyncio.Future and result is row['future']
                    assert result.get_loop() is self.hass.loop and not result.done()
                    row['yielded'] = result
                    row['yield_loop'] = self.hass.loop.time()
                    if row['qualifying']:
                        assert not self.numeric and not self.cutovers
                        assert row['yield_loop'] >= self.deadline_wait['deadline']
                elif phase == 'resume':
                    row['resumes'].append(self.hass.loop.time())
                    if row['qualifying']:
                        assert row.get('yielded') is row.get('future')
                        for name in self.old_e:
                            delivered = [d for d in self.deliveries if d['issue']['name'] == name
                                and 'return_loop' in d and d['return_loop'] <= row['resumes'][-1]]
                            assert delivered, 'actual BOTH FIRED+return before native read resume: ' + name
                elif phase == 'return':
                    assert 'yielded' in row and row['resumes']
                    assert type(result) is tuple and len(result) == 2
                    assert type(result[0]) is dict and result[1] == loader._data_directory
                    # load_model reverses _load_custom_model's directory/dict tuple.
                    assert result[0] is row['worker_result'][1]
                    row['returned_loop'] = self.hass.loop.time()
        elif tag == 'custom_executor':
            if phase != 'return' or frame.f_locals.get('self') is not self.hass:
                return
            target = frame.f_locals['target']
            if type(target) is not partial or getattr(target.func, '__func__', None) is not self.io_methods[1]:
                return
            loader = target.func.__self__
            if not self._io_loader(loader):
                return
            row = self._io_row(loader)
            assert asyncio.current_task() is row['task']
            assert target.func.__func__.__code__ is self.io_codes[1]
            assert not frame.f_locals['args'] and len(target.args) == 2 and not target.keywords
            assert type(result) is asyncio.Future and not result.done()
            assert result.get_loop() is self.hass.loop
            row.update(future=result, target=target, executor_return_loop=self.hass.loop.time())
        else:
            if not self._io_loader(loader):
                return
            assert threading.get_ident() != self.io_loop_thread
            row = self._io_row(loader)
            if tag == 'custom_worker':
                if phase == 'start':
                    row['worker_thread'] = threading.get_ident()
                elif phase == 'return':
                    assert type(result) is tuple and result[0] == loader._data_directory
                    assert type(result[1]) is dict
                    row['worker_result'] = result
            elif tag == 'custom_json':
                assert frame.f_locals['model_json_path'] == str(Path(loader._data_directory) / 'model.json')
                assert any(p.get('self') is loader for p in self._ancestors(frame, self.io_codes[1]))
                if phase == 'return':
                    assert type(result) is dict
                    row['actual_json'] = result

    def observe(self, frame, phase, result=None):
        if self.tags[frame.f_code].startswith('custom_'):
            try:
                self._observe_io(frame, phase, result)
            except BaseException as error:
                if self.error is None:
                    self.error = (frame.f_code.co_qualname, frame.f_lineno, type(error).__name__, str(error))
            return
        super().observe(frame, phase, result)

    def install(self):
        super().install()
        monitor = sys.monitoring
        def yielded(code, offset, result):
            self.observe(sys._getframe(1), 'yield', result)
        def resumed(code, offset):
            self.observe(sys._getframe(1), 'resume')
        monitor.register_callback(self.tool, monitor.events.PY_YIELD, yielded)
        monitor.register_callback(self.tool, monitor.events.PY_RESUME, resumed)
        monitor.set_local_events(self.tool, self.io_codes[0], monitor.events.PY_START |
            monitor.events.PY_RETURN | monitor.events.PY_YIELD | monitor.events.PY_RESUME)

    def assert_custom_read(self):
        self.case.assertIsNotNone(self.deadline_wait)
        self.case.assertTrue(self.io_rows, 'actual custom native read required')
        first = self.io_rows[0]
        self.case.assertTrue(first['qualifying'])
        self.case.assertIs(first.get('yielded'), first.get('future'))
        self.case.assertIn('returned_loop', first)
        self.case.assertIs(first['worker_result'][1], first['actual_json'])
        self.case.assertEqual({row['directory'] for row in self.io_rows}, set(self.custom_specs))
        for row in self.io_rows:
            self.case.assertIs(row['yielded'], row['future'])
            self.case.assertIs(row['actual_json'], row['worker_result'][1])
            self.case.assertTrue(row['future'].done())
        self.case.assertIsNone(self.error, repr(self.error))

    def close(self):
        if self.tool is not None:
            sys.monitoring.register_callback(self.tool, sys.monitoring.events.PY_YIELD, None)
            sys.monitoring.register_callback(self.tool, sys.monitoring.events.PY_RESUME, None)
        super().close()
