"""Independent full capture for a controlled Automation ID and name."""
import json
import hashlib
from pathlib import Path
import sqlite3
import unittest

import test_mixed_fixture_census_native as census_fixture
from batch_readback import Edit, RowChange, capture_tree, compare, discovery_coverage, native_location_snapshot, native_empty_auth_snapshot
from mixed_fixture_census import collect_native, consumer_evidence, census_coverage


class ControlledAutomationCensus(census_fixture.NativeMixedFixtureCensusTest):
    async def test_own_automation_name_id_census_b72(self):
        from homeassistant.helpers import area_registry, floor_registry, label_registry
        runtime, source_mapping, source_uuids, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        source_old, source_new = next(iter(source_mapping.items()))
        automation = next(e for e in hass.data['automation'].entities if e.unique_id == 'mixed_source')
        auto_old, auto_new = automation.entity_id, 'automation.census_renamed'
        auto_uuid = self.f.entity.async_get(auto_old).id
        mapping = {**source_mapping, auto_old: auto_new}
        managers = []
        for module, member in ((floor_registry, 'floors'), (label_registry, 'labels'), (area_registry, 'areas')):
            manager = hass.data.get(module.DATA_REGISTRY)
            if manager is None:
                await module.async_load(hass)
                manager = hass.data[module.DATA_REGISTRY]
            elif not hasattr(manager, member):
                await manager.async_load()
            managers.append(manager)
        floors, labels, areas = managers
        floor = floors.async_create('Independent floor', level=2)
        label = labels.async_create('Independent label', color='blue')
        area = areas.async_create('Independent area', floor_id=floor.floor_id, labels={label.label_id})
        self.f.entity.async_update_entity(source_old, area_id=area.id, labels={label.label_id})
        for manager in managers:
            await manager._store.async_save(manager._data_to_save())
        await hass.auth._store._store.async_save(hass.auth._store._data_to_save())
        output = Path('/qa/automation-census')
        output.mkdir()
        evidence = {'apply_attempted': False, 'inverse_attempted': False, 'snapshots': {}, 'mapping': mapping}
        def save():
            (output / 'result.json').write_text(json.dumps(evidence, sort_keys=True, default=str))
        def recorder_snapshot(label):
            source = root / 'fixture.db'
            destination = output / (label + '-recorder.sqlite')
            if not source.is_file() or destination.exists():
                raise RuntimeError('recorder_snapshot_source_or_destination_invalid')
            original = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
            try:
                copy = sqlite3.connect(destination)
                try:
                    original.backup(copy)
                finally:
                    copy.close()
            finally:
                original.close()
            check = sqlite3.connect(destination.as_uri() + '?mode=ro', uri=True)
            try:
                integrity = check.execute('PRAGMA quick_check').fetchone()[0]
                state_count = check.execute('SELECT COUNT(*) FROM states').fetchone()[0]
                entities = [row[0] for row in check.execute('SELECT entity_id FROM states_meta ORDER BY entity_id')]
            finally:
                check.close()
            if integrity != 'ok' or state_count < 1:
                raise RuntimeError('recorder_snapshot_empty_or_corrupt')
            return {'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
                    'bytes': destination.stat().st_size, 'states': state_count,
                    'entity_ids': entities, 'integrity': integrity}
        async def capture(label, source_direction):
            current = await collect_native(hass, root, self.producer, source_direction)
            await self._flush_documents()
            from homeassistant.components import recorder
            await recorder.get_instance(hass).async_block_till_done()
            recorder_copy = await hass.async_add_executor_job(recorder_snapshot, label)
            evidence.setdefault('recorder', {})[label] = recorder_copy
            save()
            graph = self.module('yaml_plan').plan_yaml(str(root), source_direction)
            row = self.f.entity.async_get(source_old) or self.f.entity.async_get(source_new)
            proposal = {'entities': [{'entity_id': source_old, 'proposed_entity_id': source_new,
                                      'device_id': row.device_id}], 'devices': [], 'statistics': []}
            discovery = await self.module('discovery').async_discover(hass, proposal, guard=lambda: None)
            families, external = discovery_coverage(discovery)
            families, external_consumers = census_coverage(current, families)
            destination = output / label
            info = capture_tree(root, destination, inventory_files=[(r.path, r.sha256) for r in graph.files],
                families=families, external_consumers=external_consumers,
                external_inventory_complete=current['external']['empty_in_bounded_fixture'],
                recorder_databases=('fixture.db',), native_locations=native_location_snapshot(hass, root),
                native_auth=native_empty_auth_snapshot(hass, root))
            (output / (label + '-census.json')).write_text(json.dumps(current, sort_keys=True, default=str))
            (output / (label + '-inventory.json')).write_text(json.dumps(info, sort_keys=True, default=str))
            evidence['snapshots'][label] = {'complete': current['complete'], 'unverified': current['unverified'],
                'external': external, 'auth': native_empty_auth_snapshot(hass, root),
                'locations': native_location_snapshot(hass, root)}
            save()
            return current, destination, info
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        before = after = restored = None
        try:
            before = await capture('before', source_mapping)
            review = await runtime.native_yaml_prepare(hass, mapping, actor_id='native-admin',
                entity_names={auto_old: 'Reviewed Automation'}, guard=lambda: None, refresh=refresh)
            evidence['review'] = review; save()
            evidence['apply_attempted'] = True; save()
            with census_fixture.mixed.native_diagnostics(self.module, sink=evidence):
                evidence['apply'] = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                    actor_id='native-admin', guard=lambda: None, refresh=refresh)
            save()
            after = await capture('after', {source_new: source_old})
            evidence['inspect'] = await runtime.recovery_inspect(hass, review['operation_id'], review['fingerprint'],
                guard=lambda: None, refresh=refresh); save()
            if evidence['inspect'].get('eligible') is True:
                evidence['inverse_attempted'] = True; save()
                evidence['inverse'] = await runtime.native_yaml_reverse(hass, review['operation_id'], review['fingerprint'],
                    guard=lambda: None, refresh=refresh); save()
                restored = await capture('restored', source_mapping)
            if before is not None and after is not None and restored is not None:
                edits = [Edit(path, source_old.encode(), source_new.encode(),
                              (before[1] / path).read_bytes().count(source_old.encode()))
                         for path in ('template.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml')]
                rows = [RowChange('.storage/core.entity_registry', auto_uuid, 'name', None, 'Reviewed Automation')]
                maps = [(source_uuids[source_old], source_old, source_new), (auto_uuid, auto_old, auto_new)]
                for label, observed, reverse in (('forward', after, False), ('restore', restored, True)):
                    inventory, checks, bindings = consumer_evidence(before[0], observed[0])
                    evidence[label + '_comparison'] = compare(before[1], observed[1], mappings=maps,
                        edits=edits, row_changes=rows, reverse=reverse, captures=(before[2], observed[2]),
                        consumer_inventory=inventory, consumer_inventory_complete=True,
                        consumer_checks=checks, source_consumers=bindings)
                    save()
            self.assertEqual(evidence['apply']['state'], 'applied')
            self.assertTrue(evidence['inspect']['eligible'])
            self.assertEqual(evidence['inverse']['state'], 'rolled_back')
            self.assertIn(source_old, evidence['recorder']['before']['entity_ids'])
            self.assertIn(source_new, evidence['recorder']['after']['entity_ids'])
            self.assertIn(source_old, evidence['recorder']['restored']['entity_ids'])
            for label in ('forward', 'restore'):
                result = evidence[label + '_comparison']
                self.assertEqual(result['findings'], [])
                self.assertTrue(result['complete'], result['unverified'])
        except BaseException as error:
            evidence['primary_error'] = {'type': type(error).__name__, 'message': str(error)}; save()
            if evidence['apply_attempted'] and not evidence['inverse_attempted']:
                try:
                    evidence['exception_inspect'] = await runtime.recovery_inspect(hass,
                        review['operation_id'], review['fingerprint'], guard=lambda: None, refresh=refresh)
                except BaseException as inspect_error:
                    evidence['exception_inspect_error'] = {'type': type(inspect_error).__name__, 'message': str(inspect_error)}
                save()
            raise
        finally:
            final_error = None
            try:
                evidence.setdefault('recorder', {})['final'] = await hass.async_add_executor_job(recorder_snapshot, 'final')
            except BaseException as error:
                final_error = error
                evidence['final_recorder_error'] = {'type': type(error).__name__, 'message': str(error)}
            save()
            try:
                if not (output / 'final').exists():
                    # Final after any exception is read-only: no stimulus,
                    # store flush, retry, or implicit restore of uncertain work.
                    info = capture_tree(root, output / 'final', inventory_files=[],
                        families={'final': 'UNKNOWN'}, external_consumers={},
                        recorder_databases=('fixture.db',),
                        native_locations=native_location_snapshot(hass, root),
                        native_auth=native_empty_auth_snapshot(hass, root))
                    (output / 'final-inventory.json').write_text(json.dumps(info, sort_keys=True, default=str))
            except BaseException as error:
                if final_error is None:
                    final_error = error
                evidence['final_error'] = {'type': type(error).__name__, 'message': str(error)}
            save()
            if final_error is not None and 'primary_error' not in evidence:
                raise final_error
