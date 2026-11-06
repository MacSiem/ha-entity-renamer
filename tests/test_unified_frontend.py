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
        self.assertEqual(h.resources.rows[0]['url'],CARD_PATH+'?v=5.0.0.dev0')
        await async_unregister_frontend(h);self.assertEqual(len(h.resources.rows),1)
    async def test_legacy_is_replaced_in_place_and_foreign_untouched(self):
        rows=[{'id':'old','type':'module','url':'/hacsfiles/ha-entity-renamer/ha-entity-renamer.js?hacstag=123'}, {'id':'foreign','type':'module','url':'/local/other.js'}]
        h=FakeHass(rows);await async_register_frontend(h)
        self.assertEqual(h.resources.rows[0],{'id':'old','type':'module','url':CARD_PATH+'?v=5.0.0.dev0'})
        self.assertEqual(h.resources.rows[1],rows[1])
    async def test_ambiguous_and_unrecognized_copy_refuse_without_edits(self):
        for rows in ([{'id':'a','url':CARD_PATH},{'id':'b','url':'/hacsfiles/ha-entity-renamer/ha-entity-renamer.js'}], [{'id':'f','url':'/local/elsewhere/ha-entity-renamer.js'}]):
            h=FakeHass(rows)
            with self.assertRaisesRegex(ValueError,'ambiguous'):await async_register_frontend(h)
            self.assertEqual(h.resources.rows,rows)
    async def test_yaml_legacy_refuses_without_duplicate(self):
        rows=[{'id':'legacy','url':'/hacsfiles/ha-entity-renamer/ha-entity-renamer.js'}];h=FakeHass(rows,False)
        with self.assertRaisesRegex(ValueError,'yaml_card_resource_manual'):await async_register_frontend(h)
        self.assertEqual(h.resources.rows,rows);self.assertEqual(self.extra,[])
    async def test_yaml_fresh_registers_and_unload_removes_one_extra(self):
        h=FakeHass(storage=False);await async_register_frontend(h);await async_register_frontend(h)
        self.assertEqual(self.extra,[CARD_PATH+'?v=5.0.0.dev0']);await async_unregister_frontend(h);self.assertEqual(self.extra,[])
