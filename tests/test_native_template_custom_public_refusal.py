"""A real official custom consumer is refused before any public plan effect."""
from decimal import Decimal
import importlib
import unittest
import test_powercalc_runtime_native as vendor
import test_native_template_forward as forward

@unittest.skipUnless(vendor.fixtures.HAS_HA, 'actual pinned HA required')
class CustomTemplatePlanRefusalTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = vendor.PowercalcRuntimeNativeTests.asyncSetUp
    _setup_real_vendor = vendor.PowercalcRuntimeNativeTests._setup_real_vendor
    _flush_documents = vendor.PowercalcRuntimeNativeTests._flush_documents
    _runtime = forward.NativeTemplateForwardTests._runtime
    namespace_source_bytes = b''

    async def test_powercalc_gui_consumer_refuses_before_any_effect(self):
        def module(name):
            return importlib.import_module(vendor.fixtures.PACKAGE + '.' + name)
        hass = self.f.hass
        runtime = await self._runtime(module)
        source, new = self.gui_source, 'switch.custom_source_next'
        row = self.f.entity.async_get(source)
        before_id, before_data = row.id, dict(self.entry.data)
        consumer_id = self.entry.data['_power_entity']
        consumer_uuid = self.f.entity.async_get(consumer_id).id
        self.assertEqual(Decimal(hass.states.get(consumer_id).state), Decimal(10))
        self.sources[1]._attr_is_on = False
        self.sources[1].async_write_ha_state()
        await hass.async_block_till_done()
        self.assertEqual(Decimal(hass.states.get(consumer_id).state), Decimal(0))
        async def refresh():
            return None
        with self.assertRaises(module('transaction').Refused) as refused:
            await runtime.native_yaml_prepare(hass, {source: new}, actor_id='qa',
                                              guard=lambda: None, refresh=refresh)
        self.assertEqual(str(refused.exception), 'template_yaml_server_powercalc_scope_unsupported')
        self.assertEqual(self.f.entity.async_get(source).id, before_id)
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertEqual(self.f.entity.async_get(consumer_id).id, consumer_uuid)
        self.assertEqual(dict(self.entry.data), before_data)
        self.assertFalse(await runtime.journal.active())
        self.sources[1]._attr_is_on = True
        self.sources[1].async_write_ha_state()
        await hass.async_block_till_done()
        self.assertEqual(Decimal(hass.states.get(consumer_id).state), Decimal(10))
