"""Baseline native Powercalc restore at precision two, without a rename."""
import asyncio
import base64
from decimal import Decimal
import json
from pathlib import Path
import unittest

from test_controlled_powercalc_public_native import ControlledPowercalcPublicNativeTests


class EnergyContinuityBaselineNative(ControlledPowercalcPublicNativeTests):
    async def test_native_precision_two_dump_reload(self):
        from homeassistant.core import callback
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
        from ordinary_network_scope import native_dummy_mdns

        hass, root = self.f.hass, self.f.root
        group, entry = self.sum_energy, self.group
        old_id, uuid = group.entity_id, group.registry_entry.id
        evidence = {'network_before': native_dummy_mdns(hass), 'observed': []}
        destination = Path('/qa/native-precision-two-baseline.json')

        async def snapshot(label):
            def read():
                result = {}
                for name in ('core.restore_state', 'powercalc_group', 'core.entity_registry',
                             'core.config_entries', 'energy'):
                    path = root / '.storage' / name
                    result[name] = base64.b64encode(path.read_bytes()).decode() if path.exists() else None
                return result
            evidence[label] = {'raw': await hass.async_add_executor_job(read),
                'entities': {entity.entity_id: {'uuid': entity.registry_entry.id,
                    'group_exact': str(getattr(entity, '_native_value_exact', None)),
                    'state': str(getattr(entity, '_state', None)),
                    'last_valid': str(getattr(entity, '_last_valid_state', None))}
                    for platform in async_get_platforms(hass, 'powercalc')
                    for entity in platform.entities.values()}}
            destination.write_text(json.dumps(evidence, sort_keys=True))
            return evidence[label]

        @callback
        def observe(event):
            if event.data.get('entity_id') != old_id or event.data.get('new_state') is None:
                return
            entities = [entity for platform in async_get_platforms(hass, 'powercalc')
                        for name, entity in platform.entities.items() if name == old_id and entity is not group]
            if len(entities) == 1 and entities[0].registry_entry.id == uuid and not evidence['observed']:
                evidence['observed'].append(str(entities[0]._native_value_exact))

        stop = hass.bus.async_listen('state_changed', observe)
        try:
            await self._fixture_preflight()
            await self.sources[0].async_turn_on(brightness=255)
            await asyncio.sleep(2)
            await self.sources[0].async_turn_off()
            await hass.async_block_till_done()
            exact = group._native_value_exact
            self.assertIs(type(exact), Decimal)
            self.assertGreater(exact, 0)
            self.assertEqual(group._rounding_digits, 2)
            self.assertNotEqual(exact, round(exact, 2))
            await group._prev_state_store.persist_states()
            await hass.data[DATA_RESTORE_STATE].async_dump_states()
            before = await snapshot('before')
            restore = json.loads(base64.b64decode(before['raw']['core.restore_state']))['data']
            rows = [row for row in restore if row['state']['entity_id'] == old_id]
            self.assertEqual(len(rows), 1)
            from homeassistant.components.sensor import SensorExtraStoredData
            extra = SensorExtraStoredData.from_dict(rows[0]['extra_data'])
            stored = Decimal(str(extra.native_value))
            evidence['actual_before'] = str(exact)
            evidence['native_stored'] = str(stored)
            self.assertEqual(stored, round(exact, 2))
            self.assertTrue(await hass.config_entries.async_reload(entry.entry_id))
            await hass.async_block_till_done()
            await snapshot('after')
            self.assertTrue(evidence['observed'])
            # A zero delta can change only the Decimal exponent after native
            # setup; compare the value while retaining both original strings.
            self.assertEqual(Decimal(evidence['observed'][0]), stored)
            current = next(entity for platform in async_get_platforms(hass, 'powercalc')
                           for name, entity in platform.entities.items() if name == old_id)
            self.assertEqual(current.registry_entry.id, uuid)
            initial = current._native_value_exact
            await self.sources[0].async_turn_on(brightness=255)
            await asyncio.sleep(2)
            await self.sources[0].async_turn_off()
            await hass.async_block_till_done()
            await asyncio.sleep(2)
            await hass.async_block_till_done()
            await snapshot('stimulus')
            self.assertGreater(current._native_value_exact, initial)
        finally:
            stop()
            try:
                evidence['network_after'] = native_dummy_mdns(hass)
                await snapshot('final')
            finally:
                destination.write_text(json.dumps(evidence, sort_keys=True))


def load_tests(loader, tests, pattern):
    return unittest.TestSuite([EnergyContinuityBaselineNative('test_native_precision_two_dump_reload')])
