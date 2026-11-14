"""Bounded comparator with an actual ordinary HA forward and fresh inverse."""
import shutil
import os
import json
from pathlib import Path
import tempfile
import unittest

import test_native_template_mixed_yaml_server as mixed
from batch_readback import ConsumerCheck, Edit, capture_tree, compare, discovery_coverage, native_location_snapshot


class NativeBatchReadbackTest(unittest.IsolatedAsyncioTestCase):
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

    async def test_ordinary_forward_independent_readback_and_fresh_inverse(self):
        runtime, mapping, uuids, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        old, new = next(iter(mapping.items()))
        paths = ('configuration.yaml', 'template.yaml', 'mixed_automations.yaml',
                 'mixed_scripts.yaml', '.storage/core.entity_registry',
                 '.storage/core.device_registry', '.storage/core.config_entries')
        snapshots = tempfile.TemporaryDirectory(dir=self.scratch_parent)
        self.addCleanup(snapshots.cleanup)

        async def capture(name):
            await self._flush_documents()
            dest = Path(snapshots.name) / name
            for path in paths:
                target = dest / path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / path, target)
            if evidence := os.environ.get('RENAMER_BATCH_EVIDENCE'):
                shutil.copytree(dest, Path(evidence) / name)
            return dest

        before = await capture('before')
        # Independently expected literal edits are fixed from the source fixture.
        edits = []
        for path in paths[:4]:
            count = (before / path).read_bytes().count(old.encode())
            if count:
                edits.append(Edit(path, old.encode(), new.encode(), count))
        self.assertEqual({edit.path for edit in edits},
                         {'template.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml'})
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        events = {'automation': [], 'script': []}
        for domain in events:
            self.addCleanup(hass.bus.async_listen('mixed_yaml_' + domain + '_result',
                lambda event, domain=domain: events[domain].append(str(event.data['value']))))
        async def stimulus(value):
            events['automation'].clear()
            events['script'].clear()
            await self.stimulus(value)
            await hass.services.async_call('script', 'mixed_source', {}, blocking=True)
            await hass.async_block_till_done()
            self.assertEqual(events, {'automation': [str(value)], 'script': [str(value)]})
        review = await runtime.native_yaml_prepare(hass, mapping, actor_id='independent-batch',
                                                   guard=lambda: None, refresh=refresh)
        result = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                                                 actor_id='independent-batch', guard=lambda: None, refresh=refresh)
        self.assertEqual(result['state'], 'applied')
        after = await capture('after')
        args = dict(old=old, new=new, uuid=uuids[old], edits=edits)
        forward = compare(before, after, **args)
        self.assertEqual(forward['findings'], [])
        self.assertFalse(forward['complete'])
        await stimulus(13)
        inspected = await runtime.recovery_inspect(hass, review['operation_id'], review['fingerprint'],
                                                    guard=lambda: None, refresh=refresh)
        self.assertTrue(inspected['eligible'])
        result = await runtime.native_yaml_reverse(hass, review['operation_id'], review['fingerprint'],
                                                   guard=lambda: None, refresh=refresh)
        self.assertEqual(result['state'], 'rolled_back')
        restored = await capture('restored')
        reverse = compare(before, restored, reverse=True, **args)
        self.assertEqual(reverse['findings'], [])
        self.assertFalse(reverse['complete'])
        await stimulus(17)
        print('BOUNDED_INDEPENDENT_FORWARD_REVERSE', forward, reverse, flush=True)

    async def test_native_full_fixture_capture_and_consumer_observation(self):
        await self._full_fixture_capture(False)

    async def test_native_verifier_error_still_uses_fresh_inverse(self):
        await self._full_fixture_capture(True)

    async def test_native_detects_omission_and_foreign_edit_outside_graph(self):
        await self._full_fixture_capture(False, inject_outside=True)

    async def _full_fixture_capture(self, inject_error, inject_outside=False):
        runtime, mapping, uuids, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        old, new = next(iter(mapping.items()))
        from homeassistant.helpers import area_registry, floor_registry, label_registry
        managers = []
        for module, member in ((floor_registry, 'floors'), (label_registry, 'labels'),
                               (area_registry, 'areas')):
            manager = hass.data.get(module.DATA_REGISTRY)
            if manager is None:
                await module.async_load(hass)
                manager = hass.data[module.DATA_REGISTRY]
            elif not hasattr(manager, member):
                await manager.async_load()
            managers.append(manager)
        floors, labels, areas = managers
        floor = floors.async_create('Synthetic floor', level=2)
        label = labels.async_create('Synthetic label', color='blue')
        area = areas.async_create('Synthetic room', floor_id=floor.floor_id, labels={label.label_id})
        self.f.entity.async_update_entity(old, area_id=area.id, labels={label.label_id})
        # A native unrelated device and disabled entity remain foreign to the
        # selected source, and their complete stored rows must stay unchanged.
        from types import MappingProxyType
        from homeassistant.config_entries import ConfigEntry
        from homeassistant.helpers.entity_registry import RegistryEntryDisabler
        entry = ConfigEntry(version=1, minor_version=1, domain='input_boolean',
            title='Synthetic foreign owner', source='user', unique_id='r8-foreign-owner',
            data={}, options={}, discovery_keys=MappingProxyType({}), subentries_data=[])
        hass.config_entries._entries[entry.entry_id] = entry
        await hass.config_entries._store.async_save(hass.config_entries._data_to_save())
        device = self.f.device.async_get_or_create(config_entry_id=entry.entry_id,
            identifiers={('input_boolean', 'r8-foreign-device')}, name='Synthetic foreign device')
        self.f.device.async_update_device(device.id, area_id=area.id, labels={label.label_id})
        disabled = self.f.entity.async_get_or_create('sensor', 'input_boolean', 'r8-disabled',
            suggested_object_id='r8_disabled', config_entry=entry, device_id=device.id,
            disabled_by=RegistryEntryDisabler.USER)
        self.assertEqual(disabled.disabled_by, RegistryEntryDisabler.USER)
        for manager in managers:
            await manager._store.async_save(manager._data_to_save())
        await hass.async_block_till_done()
        if inject_outside:
            (root / 'outside_reference.yaml').write_text('entity_id: ' + old + '\n')
            (root / 'outside_foreign.yaml').write_text('name: original\n')
        events = {'automation': [], 'script': []}
        for domain in events:
            self.addCleanup(hass.bus.async_listen('mixed_yaml_' + domain + '_result',
                lambda event, domain=domain: events[domain].append(str(event.data['value']))))
        async def stimulus():
            await self.stimulus(7)
            for values in events.values():
                values.clear()
            await self.stimulus(13)
            await hass.services.async_call('script', 'mixed_source', {}, blocking=True)
            await hass.async_block_till_done()
            result = {name: list(values) for name, values in events.items()}
            result['template'] = sorted((domain, hass.states.get(entity_id).state)
                for (domain, _), (entity_id, _) in self.identities.items())
            return result
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        snapshots = tempfile.TemporaryDirectory(dir=self.scratch_parent)
        self.addCleanup(snapshots.cleanup)
        async def capture(name):
            await self._flush_documents()
            self.assertEqual(self.f.entity.async_get(disabled.entity_id).id, disabled.id)
            self.assertEqual(self.f.entity.async_get(disabled.entity_id).disabled_by, RegistryEntryDisabler.USER)
            self.assertEqual(self.f.device.async_get(device.id).area_id, area.id)
            # Discovery contributes coverage and blockers only; expected effects
            # and all before/after comparisons remain independently specified.
            row = self.f.entity.async_get(old) or self.f.entity.async_get(new)
            proposal = {'entities': [{'entity_id': old, 'proposed_entity_id': new,
                                      'device_id': row.device_id}], 'devices': [], 'statistics': []}
            discovered = await self.module('discovery').async_discover(hass, proposal, guard=lambda: None)
            families, external = discovery_coverage(discovered)
            # The existing graph is an input to coverage, not the edit oracle.
            graph = self.module('yaml_plan').plan_yaml(str(root), mapping)
            self.assertFalse(graph.issues)
            if inject_outside:
                self.assertFalse({'outside_reference.yaml', 'outside_foreign.yaml'} & {row.path for row in graph.files})
            dest = Path(snapshots.name) / name
            info = capture_tree(root, dest,
                inventory_files=[(row.path, row.sha256) for row in graph.files],
                families=families,
                external_consumers={'discovery': external}, external_inventory_complete=external == 'COMPLETE',
                recorder_databases=('fixture.db',), native_locations=native_location_snapshot(hass, root))
            self.assertEqual({key: value['status'] for key, value in info['native_locations'].items()},
                             {key: 'COMPLETE' for key in ('area_registry', 'floor_registry', 'label_registry')})
            if evidence := os.environ.get('RENAMER_BATCH_EVIDENCE'):
                shutil.copytree(dest, Path(evidence) / name)
                (Path(evidence) / (name + '.inventory.json')).write_text(json.dumps(info, sort_keys=True))
            return dest, info
        original_behavior = await stimulus()
        before, binfo = await capture('full-before')
        edits = []
        for path in ('template.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml'):
            raw = (before / path).read_bytes()
            edits.append(Edit(path, old.encode(), new.encode(), raw.count(old.encode())))
        review = await runtime.native_yaml_prepare(hass, mapping, actor_id='full-batch',
                                                   guard=lambda: None, refresh=refresh)
        applied = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                                                  actor_id='full-batch', guard=lambda: None, refresh=refresh)
        self.assertEqual(applied['state'], 'applied')
        expected_template = [('binary_sensor', 'off'), ('sensor', '26')]
        inventory = {name: 'SUPPORTED' for name in (*events, 'template')}
        bindings = {'template.yaml': ['template'], 'mixed_automations.yaml': ['automation'],
                    'mixed_scripts.yaml': ['script']}
        injected = False
        foreign_written = False
        foreign_conflict = False
        try:
            after_behavior = await stimulus()
            if inject_outside:
                (root / 'outside_foreign.yaml').write_text('name: foreign edit\n')
                foreign_written = True
            after, ainfo = await capture('full-after')
            checks = [ConsumerCheck(name, ['13'], ['13'], after_behavior[name]) for name in events]
            checks.append(ConsumerCheck('template', expected_template, expected_template, after_behavior['template']))
            forward_report = compare(before, after, old=old, new=new, uuid=uuids[old], edits=edits,
                             captures=(binfo, ainfo), consumer_checks=checks,
                             consumer_inventory=inventory, consumer_inventory_complete=True,
                             source_consumers=bindings)
            print('NATIVE_FULL_CAPTURE_FORWARD', forward_report, 'original_behavior', original_behavior, flush=True)
            if inject_error:
                raise RuntimeError('controlled verifier interruption')
        except RuntimeError as error:
            if not inject_error or str(error) != 'controlled verifier interruption':
                raise
            injected = True
        finally:
            if inject_outside and foreign_written:
                outside = root / 'outside_foreign.yaml'
                # This is our own isolated test edit, restored only if exact.
                if outside.read_text() == 'name: foreign edit\n':
                    outside.write_text('name: original\n')
                else:
                    foreign_conflict = True
            inspected = await runtime.recovery_inspect(hass, review['operation_id'], review['fingerprint'],
                                                        guard=lambda: None, refresh=refresh)
            self.assertTrue(inspected['eligible'])
            reverse = await runtime.native_yaml_reverse(hass, review['operation_id'], review['fingerprint'],
                                                        guard=lambda: None, refresh=refresh)
            self.assertEqual(reverse['state'], 'rolled_back')
        restored_behavior = await stimulus()
        restored, rinfo = await capture('full-restored')
        checks = [ConsumerCheck(name, ['13'], ['13'], restored_behavior[name]) for name in events]
        checks.append(ConsumerCheck('template', expected_template, expected_template, restored_behavior['template']))
        report = compare(before, restored, old=old, new=new, uuid=uuids[old], edits=edits,
                         reverse=True, captures=(binfo, rinfo), consumer_checks=checks,
                         consumer_inventory=inventory, consumer_inventory_complete=True,
                         source_consumers=bindings)
        print('NATIVE_FULL_CAPTURE_REVERSE', report, flush=True)
        self.assertFalse(foreign_conflict)
        self.assertEqual(injected, inject_error)
        self.assertEqual(original_behavior, {'automation': ['13'], 'script': ['13'], 'template': expected_template})
        if inject_outside:
            self.assertIn(('unexpected_change', 'outside_foreign.yaml'), forward_report['findings'])
            self.assertIn(('remaining_reference', ['outside_reference.yaml']), forward_report['findings'])
        else:
            self.assertEqual(forward_report['findings'], [])
        self.assertEqual(report['findings'], [])
