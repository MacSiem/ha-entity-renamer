"""Native Script own-ID readback, independently checking every registry field."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

import test_native_template_mixed_yaml_server as mixed
from batch_readback import Edit, RowChange, compare


class NativeOwnIDReadbackTest(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = mixed.NativeTemplateMixedYAMLServerTests.asyncSetUp
    _before_start = mixed.NativeTemplateMixedYAMLServerTests._before_start
    _flush_documents = mixed.NativeTemplateMixedYAMLServerTests._flush_documents
    prepare = mixed.NativeTemplateMixedYAMLServerTests.prepare
    emit = mixed.NativeTemplateMixedYAMLServerTests.emit
    _runtime = mixed.NativeTemplateMixedYAMLServerTests._runtime
    module = mixed.NativeTemplateMixedYAMLServerTests.module
    stimulus = mixed.NativeTemplateMixedYAMLServerTests.stimulus
    data = mixed.NativeTemplateMixedYAMLServerTests.data

    async def test_native_script_own_id_full_fields_and_same_original_inverse(self):
        runtime, _, _, _, _ = await self.data(with_automation=False)
        hass, root = self.f.hass, self.f.root
        old, new = 'script.mixed_source', 'script.own_readback_target'
        uuid = self.f.entity.async_get(old).id
        paths = ('configuration.yaml', 'template.yaml', 'mixed_automations.yaml',
                 'mixed_scripts.yaml', '.storage/core.entity_registry',
                 '.storage/core.device_registry', '.storage/core.config_entries')
        snapshot = tempfile.TemporaryDirectory(dir=self.scratch_parent)
        self.addCleanup(snapshot.cleanup)
        events = []
        self.addCleanup(hass.bus.async_listen('mixed_yaml_script_result',
                         lambda event: events.append(str(event.data['value']))))
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        async def stimulus(key):
            await self.stimulus(13)
            events.clear()
            await hass.services.async_call('script', key, {}, blocking=True)
            await hass.async_block_till_done()
            self.assertEqual(events, ['13'])
        async def capture(name):
            await self._flush_documents()
            dest = Path(snapshot.name) / name
            for path in paths:
                target = dest / path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / path, target)
            if evidence := os.environ.get('RENAMER_BATCH_EVIDENCE'):
                shutil.copytree(dest, Path(evidence) / name)
            return dest
        await stimulus('mixed_source')
        before = await capture('own-before')
        doc = json.loads((before / '.storage/core.entity_registry').read_bytes())
        own, = [row for row in doc['data']['entities'] if row['id'] == uuid]
        self.assertEqual([own[k] for k in ('unique_id', 'previous_unique_id', 'suggested_object_id', 'original_name', 'name')],
                         ['mixed_source', None, 'mixed_source', 'mixed_source', None])
        registry = '.storage/core.entity_registry'
        changes = [RowChange(registry, uuid, 'unique_id', 'mixed_source', 'own_readback_target'),
                   RowChange(registry, uuid, 'previous_unique_id', None, 'mixed_source', 'own_readback_target'),
                   RowChange(registry, uuid, 'suggested_object_id', 'mixed_source', 'own_readback_target'),
                   RowChange(registry, uuid, 'original_name', 'mixed_source', 'own_readback_target'),
                   RowChange(registry, uuid, 'name', None, 'mixed_source')]
        edits = [Edit('mixed_scripts.yaml', b'mixed_source:', b'own_readback_target:', 1)]
        args = dict(old=old, new=new, uuid=uuid, edits=edits, row_changes=changes)
        review = await runtime.native_yaml_prepare(hass, {old: new}, actor_id='own-readback',
                                                   guard=lambda: None, refresh=refresh)
        applied = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                                                  actor_id='own-readback', guard=lambda: None, refresh=refresh)
        self.assertEqual(applied['state'], 'applied')
        try:
            await stimulus('own_readback_target')
            after = await capture('own-after')
            forward = compare(before, after, **args)
            print('OWN_INDEPENDENT_FORWARD', forward, flush=True)
            self.assertEqual(forward['findings'], [])
        finally:
            inspected = await runtime.recovery_inspect(hass, review['operation_id'], review['fingerprint'],
                                                        guard=lambda: None, refresh=refresh)
            self.assertTrue(inspected['eligible'])
            inverse = await runtime.native_yaml_reverse(hass, review['operation_id'], review['fingerprint'],
                                                        guard=lambda: None, refresh=refresh)
            self.assertEqual(inverse['state'], 'rolled_back')
            restored = await capture('own-restored')
        await stimulus('mixed_source')
        reverse = compare(before, restored, reverse=True, **args)
        print('OWN_INDEPENDENT_REVERSE', reverse, flush=True)
        self.assertEqual(reverse['findings'], [])
