"""Resource registration boundaries; no live HA or user data."""
import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from custom_components.ha_entity_renamer.frontend import async_register_frontend, async_unregister_frontend, CARD_PATH

class Collection:
    def __init__(self, rows=(), storage=True):
        self.rows = [dict(row) for row in rows]
        self.store = object() if storage else None
        self.loaded = True
    def async_items(self): return list(self.rows)
    async def async_create_item(self, value): self.rows.append({'id':'new', 'type':value['res_type'], 'url':value['url']})
    async def async_update_item(self, ident, value):
        row = next(x for x in self.rows if x['id'] == ident)
        row.update(type=value['res_type'],url=value['url'])

class FakeHass:
    def __init__(self, rows=(), storage=True):
        self.resources=Collection(rows,storage);self.data={'lovelace':types.SimpleNamespace(resources=self.resources)}
        self.http=types.SimpleNamespace(async_register_static_paths=self.register);self.static=[]
    async def register(self, paths):self.static.extend(paths)
    async def async_add_executor_job(self, fn):return fn()

class FrontendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.extra=[]
        frontend=types.ModuleType('homeassistant.components.frontend')
        frontend.add_extra_js_url=lambda h,u:self.extra.append(u)
        frontend.remove_extra_js_url=lambda h,u:self.extra.remove(u)
        http=types.ModuleType('homeassistant.components.http')
        http.StaticPathConfig=lambda *a,**k:(a,k)
        components=types.ModuleType('homeassistant.components');components.frontend=frontend
        ha=types.ModuleType('homeassistant');ha.components=components
        self.patcher=patch.dict(sys.modules,{'homeassistant':ha,'homeassistant.components':components,'homeassistant.components.frontend':frontend,'homeassistant.components.http':http})
        self.patcher.start();self.addCleanup(self.patcher.stop)
    async def test_fresh_and_repeat_keep_one_resource_and_static_route(self):
        h=FakeHass();await async_register_frontend(h);await async_register_frontend(h)
        self.assertEqual(len(h.resources.rows),1);self.assertEqual(len(h.static),1)
        self.assertEqual(h.resources.rows[0]['url'],CARD_PATH+'?v=5.0.0')
        await async_unregister_frontend(h);self.assertEqual(len(h.resources.rows),1)
    async def test_legacy_is_replaced_in_place_and_foreign_untouched(self):
        rows=[{'id':'old','type':'module','url':'/hacsfiles/ha-entity-renamer/ha-entity-renamer.js?hacstag=123'}, {'id':'foreign','type':'module','url':'/local/other.js'}]
        h=FakeHass(rows);await async_register_frontend(h)
        self.assertEqual(h.resources.rows[0],{'id':'old','type':'module','url':CARD_PATH+'?v=5.0.0'})
        self.assertEqual(h.resources.rows[1],rows[1])
    async def test_ambiguous_and_unrecognized_copy_refuse_without_edits(self):
        for rows in ([{'id':'a','url':CARD_PATH},{'id':'b','url':'/hacsfiles/ha-entity-renamer/ha-entity-renamer.js'}], [{'id':'f','url':'/local/elsewhere/ha-entity-renamer.js'}], [{'id':'remote','url':'https://example.invalid/ha-entity-renamer.js'}]):
            h=FakeHass(rows)
            with self.assertRaisesRegex(ValueError,'ambiguous'):await async_register_frontend(h)
            self.assertEqual(h.resources.rows,rows)
    async def test_yaml_legacy_refuses_without_duplicate(self):
        rows=[{'id':'legacy','url':'/hacsfiles/ha-entity-renamer/ha-entity-renamer.js'}];h=FakeHass(rows,False)
        with self.assertRaisesRegex(ValueError,'yaml_card_resource_manual'):await async_register_frontend(h)
        self.assertEqual(h.resources.rows,rows);self.assertEqual(self.extra,[])
    async def test_yaml_fresh_registers_and_unload_removes_one_extra(self):
        h=FakeHass(storage=False);await async_register_frontend(h);await async_register_frontend(h)
        self.assertEqual(self.extra,[CARD_PATH+'?v=5.0.0']);await async_unregister_frontend(h);self.assertEqual(self.extra,[])

class RuntimeAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_overlapping_setup_allows_only_one_runtime(self):
        from custom_components.ha_entity_renamer import async_setup_entry
        from custom_components.ha_entity_renamer.const import DATA_RUNTIME, DATA_WEBSOCKETS
        class NotReady(Exception): pass
        errors=types.ModuleType('homeassistant.exceptions');errors.ConfigEntryNotReady=NotReady
        journal=types.ModuleType('custom_components.ha_entity_renamer.journal')
        prepared=types.ModuleType('custom_components.ha_entity_renamer.prepared')
        retention=types.ModuleType('custom_components.ha_entity_renamer.review_retention')
        ws=types.ModuleType('custom_components.ha_entity_renamer.websocket_api')
        frontend=types.ModuleType('custom_components.ha_entity_renamer.frontend')
        started=asyncio.Event();release=asyncio.Event();calls=[]
        class Store:
            async def initialize(self):
                calls.append('initialize')
                if len(calls)==1:started.set();await release.wait()
        j=Store();p=Store()
        journal.native_journal=lambda h:j;prepared.native_prepared_store=lambda h:p
        async def reclaim(h,guard):return p,j
        async def register_frontend(h):calls.append('frontend')
        retention.async_reclaim_reviews=reclaim;frontend.async_register_frontend=register_frontend
        ws.async_register=lambda h:calls.append('websocket')
        modules={errors.__name__:errors,journal.__name__:journal,prepared.__name__:prepared,retention.__name__:retention,ws.__name__:ws,frontend.__name__:frontend}
        h=types.SimpleNamespace(data={});one=types.SimpleNamespace(entry_id='one');two=types.SimpleNamespace(entry_id='two')
        with patch.dict(sys.modules,modules):
            a=asyncio.create_task(async_setup_entry(h,one));await started.wait()
            b=asyncio.create_task(async_setup_entry(h,two));await asyncio.sleep(0);release.set()
            self.assertTrue(await a)
            with self.assertRaises(NotReady):await b
        self.assertEqual(h.data[DATA_RUNTIME].entry_id,'one');self.assertTrue(h.data[DATA_WEBSOCKETS]);self.assertEqual(calls.count('websocket'),1);self.assertEqual(calls.count('frontend'),1)
