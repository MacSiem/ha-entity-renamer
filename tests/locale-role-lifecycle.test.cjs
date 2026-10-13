const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { JSDOM } = require('jsdom');
async function fixture(language = 'en', admin = true) {
 const dom = new JSDOM('', { runScripts: 'dangerously', pretendToBeVisual: true, url: 'http://localhost/' });
 dom.window.eval(readFileSync('ha-entity-renamer.js', 'utf8'));
 const card = dom.window.document.createElement('ha-entity-renamer'); card.setConfig({ show_support: false }); dom.window.document.body.append(card);
 const requests = [], devices = [{ id: 'qa-device', name: 'QA device' }], entities = [{ entity_id: 'sensor.qa_old', device_id: 'qa-device', unique_id: 'qa' }];
 const connection = new dom.window.EventTarget(); connection.connected = true;
 const hass = { language, connection, user: { id: 'admin-a', is_admin: admin }, themes: {}, states: {}, callWS: async message => {
  requests.push(message); if (message.type === 'config/device_registry/list') return devices;
  if (message.type === 'config/entity_registry/list') return entities;
  throw new Error('Fixture rejects '+message.type);
 } };
 card.hass = hass; await new Promise(resolve => setImmediate(resolve));
 return { card, dom, hass, requests };
}
test('ordinary language change translates two independent cards without reloading registries', async () => {
 const a = await fixture(), b = await fixture('pl');
 try {
  a.card.hass = { ...a.hass, language: 'pl' };
  assert.match(a.card.shadowRoot.getElementById('searchInput').placeholder, /Szukaj/);
  b.card.hass = { ...b.hass, language: 'en' };
  assert.match(b.card.shadowRoot.getElementById('searchInput').placeholder, /Search/);
  assert.match(a.card.shadowRoot.getElementById('searchInput').placeholder, /Szukaj/);
  assert.equal(a.requests.length, 2); assert.equal(b.requests.length, 2);
 } finally { a.dom.window.close(); b.dom.window.close(); }
});
test('ordinary locale preserves unqueued device and prefix drafts, focus and backward selection', async () => {
 const f = await fixture();
 try {
  f.card.shadowRoot.querySelector('.device-header[data-device-id="qa-device"]').click();
  const input = f.card.shadowRoot.getElementById('deviceName'); assert.ok(input);
  input.value = 'QA unsaved room'; f.card.shadowRoot.getElementById('prefixOld').value = 'qa_'; f.card.shadowRoot.getElementById('prefixNew').value = 'new_unsaved_';
  input.focus(); input.setSelectionRange(2, 8, 'backward'); f.card.hass = { ...f.hass, language: 'pl' };
  const current = f.card.shadowRoot.getElementById('deviceName'); assert.match(current.placeholder, /Nowa nazwa/);
  assert.equal(current.value, 'QA unsaved room'); assert.equal(f.card.shadowRoot.activeElement, current);
  assert.equal(current.selectionStart, 2); assert.equal(current.selectionEnd, 8); assert.equal(current.selectionDirection, 'backward');
  assert.equal(f.card.shadowRoot.getElementById('prefixOld').value, 'qa_'); assert.equal(f.card.shadowRoot.getElementById('prefixNew').value, 'new_unsaved_');
  assert.equal(Object.keys(f.card._deviceRenameQueue).length, 0); assert.equal(f.requests.length, 2);
 } finally { f.dom.window.close(); }
});
test('ordinary role loss closes confirmation and disables registry apply without executing it', async () => {
 const f = await fixture();
 try {
  f.card._renameQueue = [{ oldId: 'sensor.qa_old', newId: 'sensor.qa_new' }]; f.card._showRenameConfirmation(); assert.ok(f.card.shadowRoot.getElementById('confirmRenameApply'));
  f.card.hass = { ...f.hass, language: 'pl', user: { is_admin: false } };
  assert.equal(f.card.shadowRoot.getElementById('confirmRenameApply'), null);
  assert.equal(f.card.shadowRoot.getElementById('executeRenames'), null); assert.match(f.card._renderLogTab(), /administratora/);
  assert.equal(f.card._confirmDialogOpen, false); assert.equal(f.card._renameQueue.length, 0); assert.equal(f.requests.length, 2);
 } finally { f.dom.window.close(); }
});
test('unresolved role cannot confirm or send registry updates', async () => {
 const f = await fixture();
 try {
  f.card._renameQueue = [{ oldId: 'sensor.qa_old', newId: 'sensor.qa_new' }]; f.card._activeTab = 'queue'; f.card.hass = { ...f.hass, user: {} };
  f.card._showRenameConfirmation(); assert.equal(f.card._confirmDialogOpen, false);
  await f.card._executeRenames(true); assert.equal(f.requests.length, 2); assert.equal(f.card._renameQueue.length, 0);
  assert.equal(f.card.shadowRoot.getElementById('executeRenames'), null);
 } finally { f.dom.window.close(); }
});
test('same-language and same-role state updates keep the live form DOM', async () => {
 const f = await fixture();
 try {
  const input = f.card.shadowRoot.getElementById('searchInput'); input.value = 'QA unsubmitted search'; input.focus();
  f.card.hass = { ...f.hass, states: { 'sensor.qa': { state: '2' } } };
  assert.equal(f.card.shadowRoot.getElementById('searchInput'), input); assert.equal(f.card.shadowRoot.activeElement, input); assert.equal(f.requests.length, 2);
 } finally { f.dom.window.close(); }
});

test('first-run introduction and dismiss translate per instance after ordinary locale updates', async () => {
 const a = await fixture('pl'), b = await fixture('en');
 try {
  assert.match(a.card.shadowRoot.querySelector('.intro-headline').textContent, /Masowa zmiana/);
  assert.equal(a.card.shadowRoot.querySelector('.intro-dismiss').getAttribute('aria-label'), 'Ukryj instrukcję');
  assert.match(b.card.shadowRoot.querySelector('.intro-headline').textContent, /Bulk-rename/);
  a.card.hass = { ...a.hass, language: 'en' };
  assert.match(a.card.shadowRoot.querySelector('.intro-headline').textContent, /Bulk-rename/);
  assert.equal(a.card.shadowRoot.querySelector('.intro-dismiss').getAttribute('aria-label'), 'Dismiss instructions');
  assert.equal(a.requests.length, 2); assert.equal(b.requests.length, 2);
 } finally { a.dom.window.close(); b.dom.window.close(); }
});

test('all queued entity and device remove controls have translated accessible names', async () => {
 const f = await fixture('pl');
 try {
  f.card._renameQueue = [{ oldId: 'sensor.qa_old', newId: 'sensor.qa_new' }];
  f.card._deviceRenameQueue = { 'qa-device': 'QA renamed' };
  f.card.shadowRoot.querySelector('.device-header[data-device-id="qa-device"]').click();
  assert.equal(f.card.shadowRoot.querySelector('[data-remove-queue]').getAttribute('aria-label'), 'Usuń z kolejki');
  f.card.shadowRoot.querySelector('[data-tab="queue"]').click();
  const buttons = [...f.card.shadowRoot.querySelectorAll('[data-remove-queue],[data-remove-dev-queue]')];
  assert.equal(buttons.length, 2);
  assert.ok(buttons.every(b => b.getAttribute('aria-label') === 'Usuń z kolejki'));
  f.card.hass = { ...f.hass, language: 'en' };
  assert.ok([...f.card.shadowRoot.querySelectorAll('[data-remove-queue],[data-remove-dev-queue]')].every(b => b.getAttribute('aria-label') === 'Remove from queue'));
  assert.equal(f.requests.length, 2);
 } finally { f.dom.window.close(); }
});

test('resolved household keeps authorized read-only browsing while rename history stays private', async () => {
 const f=await fixture('en',false);
 try {
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(f.requests.map(x=>x.type).sort(),['config/device_registry/list','config/entity_registry/list']);
  assert.equal(f.card._devices.length,1);
  assert.match(f.card.shadowRoot.textContent,/QA device/);
  assert.match(f.card._renderLogTab(),/administrator/i);
  f.card._renameQueue=[{oldId:'sensor.qa_old',newId:'sensor.forbidden'}];
  await f.card._executeRenames(true);
  assert.equal(f.requests.filter(x=>x.type.endsWith('/update')).length,0);
 } finally { f.dom.window.close(); }
});

test('household retains allowed automatic-ID and impact previews without registry writes',async ()=>{
 const f=await fixture('en',false);
 try {
  const base=f.hass.callWS;
  f.hass.callWS=async msg=>{
   if(msg.type==='config/entity_registry/get_automatic_entity_ids'){f.requests.push(msg);return {'sensor.qa_old':'sensor.qa_restored'};}
   if(msg.type==='search/related'){f.requests.push(msg);return {automation:[],script:[],scene:[]};}
   if(msg.type==='lovelace/dashboards/list'){f.requests.push(msg);return [];}
   if(msg.type==='lovelace/config'){f.requests.push(msg);return {views:[]};}
   return base(msg);
  };
  await f.card._loadAutomaticIds();assert.equal(f.card._automaticRows.length,1);
  f.card._renameQueue=[{oldId:'sensor.qa_old',newId:'sensor.qa_restored'}];
  await f.card._analyzeImpact();assert.equal(f.card._impactResults['sensor.qa_old'].dashboardCoverage,'complete');
  assert.equal(f.requests.filter(x=>x.type.endsWith('/update')).length,0);
  assert.match(f.card._renderLogTab(),/administrator/i);
 } finally {f.dom.window.close();}
});

test('initial unresolved HA user loads permitted registries when identity arrives later', async () => {
 const f = await fixture('en', undefined);
 try {
  // Reproduce HA startup: the first hass projection has no resolved user yet.
  f.card.hass = { ...f.hass, user: {} };
  await new Promise(resolve => setImmediate(resolve));
  const before = f.requests.length;
  f.card.hass = { ...f.hass, user: { id: 'late-admin', is_admin: true } };
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.card._entities.length, 1);
  assert.equal(f.card._devices.length, 1);
  assert.equal(f.requests.length, before + 2);
  assert.equal(f.requests.filter(x => x.type.endsWith('/update')).length, 0);
 } finally { f.dom.window.close(); }
});

test('HA card reattachment retries a registry read cancelled during initial mounting', async () => {
 const f = await fixture();
 try {
  const card = f.dom.window.document.createElement('ha-entity-renamer');
  card.setConfig({show_support:false}); f.dom.window.document.body.append(card);
  let resolveRead; const ready = new Promise(resolve => {resolveRead = resolve;});
  const reads=[];
  card.hass = {...f.hass, callWS:async message => {reads.push(message);await ready;return message.type==='config/device_registry/list'?[{id:'mounted-device',name:'Mounted device'}]:[{entity_id:'sensor.mounted',device_id:'mounted-device'}];}};
  card.remove();f.dom.window.document.body.append(card);
  resolveRead();await new Promise(resolve => setImmediate(resolve));
  assert.equal(card._entities.length,1);assert.equal(card._devices.length,1);
  assert.equal(card._loading,false);assert.equal(reads.filter(x=>x.type.endsWith('/update')).length,0);
 } finally {f.dom.window.close();}
});

test('resolved role loss reloads permitted registries and never restores private proposals', async () => {
 const f=await fixture();
 try {
  f.card._renameQueue=[{oldId:'sensor.qa_old',newId:'sensor.private_old_proposal'}];
  f.card.hass={...f.hass,user:{id:'admin-a',is_admin:false}};
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(f.card._entities.length,1);assert.equal(f.card._devices.length,1);
  assert.equal(f.card._renameQueue.length,0);assert.equal(f.requests.length,4);
  assert.equal(f.requests.filter(x=>x.type.endsWith('/update')).length,0);
  assert.match(f.card._renderLogTab(),/administrator/);
 } finally {f.dom.window.close();}
});

test('another resolved account receives freshly read registry rows instead of an empty or old projection', async () => {
 const f=await fixture();
 try {
  const reads=[];
  f.card.hass={...f.hass,user:{id:'admin-b',is_admin:true},callWS:async message=>{reads.push(message);return message.type==='config/device_registry/list'?[{id:'new-account-device',name:'New account device'}]:[{entity_id:'sensor.new_account',device_id:'new-account-device'}];}};
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(f.card._entities[0].entity_id,'sensor.new_account');assert.equal(f.card._devices[0].id,'new-account-device');
  assert.equal(reads.length,2);assert.equal(f.card._renameQueue.length,0);assert.equal(f.card._renameLog.length,0);
 } finally {f.dom.window.close();}
});

test('recovery retries a cancelled initial read and its late response cannot close a new export dialog', async () => {
 const f=await fixture();
 try {
  const card=f.dom.window.document.createElement('ha-entity-renamer');card.setConfig({show_support:false});f.dom.window.document.body.append(card);
  let resolveOld;const old=new Promise(resolve=>{resolveOld=resolve;});const connection=new f.dom.window.EventTarget();connection.connected=true;
  const hass={...f.hass,connection,callWS:async message=>{await old;return message.type==='config/device_registry/list'?[{id:'old-device'}]:[{entity_id:'sensor.old_cancelled'}];}};
  card.hass=hass;connection.connected=false;connection.dispatchEvent(new f.dom.window.Event('disconnected'));
  hass.callWS=async message=>message.type==='config/device_registry/list'?[{id:'recovered-device'}]:[{entity_id:'sensor.recovered',device_id:'recovered-device'}];
  connection.connected=true;connection.dispatchEvent(new f.dom.window.Event('ready'));
  await new Promise(resolve=>setImmediate(resolve));assert.equal(card._entities[0]?.entity_id,'sensor.recovered');assert.equal(card._loading,false);
  card._activeTab='log';card.render();card._openRenameExport(card.shadowRoot.querySelector('button'));
  const dialog=card.shadowRoot.querySelector('[role="dialog"]');assert.ok(dialog);
  resolveOld();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(card._entities[0].entity_id,'sensor.recovered');assert.equal(card.shadowRoot.querySelector('[role="dialog"]'),dialog);
 } finally {f.dom.window.close();}
});

test('mounting a card with an existing Polish HA context retains the HA locale', async () => {
 const f=await fixture('pl');
 try {
  const cold=f.dom.window.document.createElement('ha-entity-renamer');cold.setConfig({show_support:false});cold.hass=f.hass;f.dom.window.document.body.append(cold);
  await new Promise(resolve=>setImmediate(resolve));assert.match(cold.shadowRoot.getElementById('searchInput').placeholder,/Szukaj/);
 } finally {f.dom.window.close();}
});
