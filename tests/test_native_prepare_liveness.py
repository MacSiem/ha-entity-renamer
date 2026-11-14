"""Actual Core 11.1 prepare and resampling with production blocking detection."""
import asyncio
import importlib
from time import monotonic
import unittest
from unittest.mock import patch

import test_native_yaml_source_binding as fixture


@unittest.skipUnless(fixture.fixture.fixtures.HAS_HA, 'native HA dependencies required')
class NativePrepareLivenessTests(fixture.NativeYAMLSourceBindingTests):
    async def test_scale_prepare_resamples_and_rejects_changed_code(self):
        from homeassistant import block_async_io
        from homeassistant.const import __version__
        from homeassistant.components.script.entity import ScriptEntity
        if __version__ != '2026.11.1':
            self.skipTest('Core 11.1 required')
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.setup import async_setup_component
        hass, root = self.f.hass, self.f.root
        config = '''template:
  - sensor:
      - unique_id: liveness_source
        name: Liveness source
        state: "{{ states('sensor.original') }}"
script:
''' + ''.join(
            f'  liveness_{i}:\n    sequence:\n      - variables: {{sample: "{{{{ states(\'sensor.original\') }}}}"}}\n'
            for i in range(120))
        await hass.async_add_executor_job((root / 'configuration.yaml').write_text, config)
        parsed = await async_hass_config_yaml(hass)
        self.assertTrue(await async_setup_component(hass, 'script', parsed))
        self.assertTrue(await async_setup_component(hass, 'template', parsed))
        await hass.async_block_till_done()
        await self._flush_documents(initialize=True)
        package = fixture.fixture.fixtures.PACKAGE
        module = lambda name: importlib.import_module(package + '.' + name)
        plan = module('yaml_plan').plan_yaml(root, {'sensor.original': 'sensor.changed'})
        self.assertTrue(plan.ready, plan.issues)
        tick_times = []
        running = True
        async def heartbeat():
            while running:
                tick_times.append(monotonic())
                await asyncio.sleep(0.05)
        tick = asyncio.create_task(heartbeat())
        await asyncio.sleep(0)
        self.assertFalse(block_async_io._BLOCKED_CALLS.calls)
        try:
            with patch.object(block_async_io, '_IN_TESTS', False):
                block_async_io.enable()
            validation = await module('yaml_validation').async_validate_proposed_with_template_state_sources(
                hass, plan, {'sensor.original': 'sensor.changed'}, root.parent, with_state_program=True)
            receipt, evidence = validation.receipt, validation.evidence
            binding = module('yaml_source_binding')
            bound = await binding.async_bind_original_runtime(hass, receipt, evidence, guard=lambda: None)
            self.assertTrue(bound.verified, bound.blockers)
            observed = await module('native_discovery_inputs').async_observe_discovery_inputs(hass, guard=lambda: None)
            sampler = binding._RuntimeSourceSampler.bind(evidence, observed)
            sample = sampler.sample(hass, evidence, observed)
            self.assertFalse(sample[1])
            for _ in range(3):
                self.assertEqual(sampler.sample(hass, evidence, observed), sample)
            original_handler = ScriptEntity._service_handler
            def changed(*args, **kwargs):
                return None
            with patch.object(ScriptEntity, '_service_handler', changed):
                with self.assertRaises(ValueError):
                    sampler.sample(hass, evidence, observed)
            self.assertIs(ScriptEntity._service_handler, original_handler)
        finally:
            running = False
            await tick
            for row in block_async_io._BLOCKED_CALLS.calls:
                setattr(row.object, row.function, row.original_func)
            block_async_io._BLOCKED_CALLS.calls.clear()
        self.assertGreater(len(tick_times), 2)
        self.assertLess(max(b - a for a, b in zip(tick_times, tick_times[1:])), 2.0)

    async def test_proof_reads_file_once_and_rechecks_source_and_code(self):
        import hashlib
        import tempfile
        from pathlib import Path
        from types import ModuleType
        proof = importlib.import_module(fixture.fixture.fixtures.PACKAGE + '.native_unload_code_proof')
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            root = Path(directory).resolve()
            path = root / 'sample.py'
            source = b'def first(): return 1\ndef second(): return 2\n'
            path.write_bytes(source)
            owner = ModuleType('sample')
            exec(compile(source, str(path), 'exec'), owner.__dict__)
            refs = ((owner, 'first'), (owner, 'second'))
            pins = {'sample.py': hashlib.sha256(source).hexdigest()}
            original_read = Path.read_bytes
            reads = []
            def read_once(file):
                reads.append(file)
                return original_read(file)
            with patch.object(Path, 'read_bytes', read_once):
                proof.prove_methods(root, pins, refs)
            self.assertEqual(reads, [path])
            path.write_bytes(source + b'# changed\n')
            with self.assertRaisesRegex(ValueError, 'native_unload_source_pin'):
                proof.prove_methods(root, pins, refs)
            path.write_bytes(source)
            owner.first = lambda: None
            with self.assertRaises(ValueError):
                proof.prove_methods(root, pins, refs)
            with self.assertRaisesRegex(ValueError, 'native_unload_source_pin'):
                proof.prove_methods(root, {}, ((owner, 'second'),))

    async def test_registry_mismatch_log_is_closed(self):
        from types import SimpleNamespace
        server = importlib.import_module(fixture.fixture.fixtures.PACKAGE + '.native_template_state_server')
        actual = SimpleNamespace(position='private-position', reason='private-reason', identity=True,
            runtime=False, durability=1, recorder_metadata=None, complete_history='private-history')
        with self.assertLogs(server.__name__, level='WARNING') as logs:
            server._log_registry_mismatch(actual, server.Position.BEFORE)
        message = logs.output[0]
        self.assertIn('reason=other position=other wanted=before', message)
        self.assertIn('identity=True runtime=False durability=None metadata=None history=None', message)
        self.assertNotIn('private-', message)
        actual.position = 'unknown'
        actual.reason = 'scoped_evidence_incomplete'
        with self.assertLogs(server.__name__, level='WARNING') as logs:
            server._log_registry_mismatch(actual, server.Position.AFTER)
        self.assertIn('reason=scoped_evidence_incomplete position=unknown wanted=after', logs.output[0])
