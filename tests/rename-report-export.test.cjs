'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = path.resolve(__dirname, '..');
const tick = () => new Promise(resolve => setImmediate(resolve));
function fixture(t, language = 'en') {
  const dom = new JSDOM('', { runScripts: 'dangerously', pretendToBeVisual: true, url: 'http://localhost/' });
  t.after(() => dom.window.close());
  dom.window.eval(fs.readFileSync(path.join(root, 'ha-entity-renamer.js'), 'utf8'));
  const card = dom.window.document.createElement('ha-entity-renamer');
  dom.window.document.body.append(card);
  const connection = new dom.window.EventTarget(); connection.connected = true;
  const requests = [], downloads = [];
  const hass = { language, connection, user: { id: 'admin-a', is_admin: true }, states: {}, callWS: async msg => {
    requests.push(msg); if (msg.type.endsWith('/list')) return [];
    return {};
  } };
  card._hass = hass; card._lang = language; card._activeTab = 'log';
  card._renameLog = [{ time: '12:34:56', oldId: 'sensor.old', newId: 'sensor.new', status: 'ok', impact: {automations: ['private name'], scripts: [], scenes: [], dashboards: []} }];
  card._downloadRenameReport = async request => { downloads.push(request); return {outcome: 'initiated'}; };
  card.render();
  const open = async () => { card.shadowRoot.getElementById('exportRenameLog').click(); await tick(); return card.shadowRoot.querySelector('[data-ha-tools-export]').shadowRoot; };
  const node = (dialog, key) => dialog.querySelector(`[data-export="${key}"]`);
  return { dom, card, hass, connection, requests, downloads, open, node };
}
function payload(card, format = 'json', includePrivate = false) { return card._buildRenameReport({draft: {format, includePrivate}}); }

test('history producer exports ACK as unknown, preflight as failed; proposals and raw errors never become report rows', t => {
 const f = fixture(t); const secret = 'TOKEN user@example.com https://secret yaml: !include';
 f.card._renameQueue = [{oldId:'sensor.queued',newId:'sensor.proposed'}];
 f.card._renameLog.push({status:'error', oldId:'sensor.fail',newId:'sensor.next',time:'12:00:00',writePhase:'not_started',error:secret,impact:null});
 const p = payload(f.card), r = JSON.parse(p.serialized);
 assert.equal(r.counts.applied,0); assert.equal(r.counts.failed,1); assert.equal(r.counts.unknown,1);
 assert.equal(r.rows[0].reason,'ack_without_readback'); assert.equal(r.rows[1].reason,'preflight_rejected');
 assert.equal(r.rows[0].references.automations,1); assert.equal(r.rows[0].references.scripts,0); assert.equal(r.rows[1].references.automations,null);
 assert.equal(r.status,'partial'); assert.match(r.limits,/readback|odczytu/i);
 assert.equal(r.rows[0].manualRepair,'verify_registry_then_review_known_references');
 for(const value of [secret,'private name','sensor.queued','sensor.old','sensor.new']) assert.ok(!p.serialized.includes(value));
 assert.equal(f.requests.length,0);
});
test('legacy errors, unknown phases and fake applied claims cannot count as applied or known rejected writes', t => {
 const f = fixture(t); f.card._renameLog = [{status:'ok', oldId:'sensor.a',newId:'sensor.b', applied:true,writePhase:'readback',time:'secret name'}, {status:'error',oldId:'sensor.b',newId:'sensor.c',error:'secret'}];
 const r = JSON.parse(payload(f.card).serialized); assert.equal(r.counts.applied,0); assert.equal(r.counts.unknown,2); assert.equal(r.rows[0].timestamp,null);
});
test('paired pseudonyms relate repeated strict IDs and ON includes only exact reviewed IDs', t => {
 const f = fixture(t); f.card._renameLog.push({status:'ok',oldId:'sensor.new',newId:'sensor.next (private friendly)',time:'12:00:00',newName:'secret',user:'secret',config:'secret'});
 const off=JSON.parse(payload(f.card).serialized), on=JSON.parse(payload(f.card,'json',true).serialized);
 assert.equal(off.rows[0].new,off.rows[1].old); assert.equal(on.rows[0].old,'sensor.old'); assert.equal(on.rows[1].new,null);
 assert.ok(!JSON.stringify(on).includes('private friendly')); assert.ok(!JSON.stringify(on).includes('secret'));
});
for(const format of ['json','csv','txt']) test(format+' retains partial, source, unknown/failed, zero/null and excludes unsafe free text in both modes', t=>{
 const f=fixture(t); f.card._renameLog.push({status:'error',oldId:'=HYPERLINK("https://private")',newId:'@private',time:'=private',error:'rawerror',impact:null});
 for(const includePrivate of [false,true]) { const p=payload(f.card,format,includePrivate); assert.equal(p.status,'partial'); assert.match(p.serialized,/unknown/); assert.match(p.serialized,/partial/); assert.match(p.serialized,/null/); assert.ok(!p.serialized.includes('HYPERLINK')); assert.ok(!p.serialized.includes('rawerror')); }
});
test('no log blocks Save and opening/refresh/export causes no HA calls', async t=>{
 const f=fixture(t); f.card._renameLog=[]; f.card.render(); const d=await f.open(); assert.equal(f.node(d,'save').disabled,true); assert.equal(f.node(d,'status').dataset.resultState,'no_data');
 f.node(d,'refresh').click(); await tick(); assert.equal(f.requests.length,0); assert.equal(f.downloads.length,0);
});
test('Save passes exactly preview and one frozen Blob, double click is latched and initiated stays unconfirmed',async t=>{
 const f=fixture(t); const d=await f.open(), bytes=f.node(d,'preview').textContent; let release;
 f.card._downloadRenameReport = request=>{f.downloads.push(request); return new Promise(resolve=>{release=resolve;});};
 f.node(d,'save').click(); f.node(d,'save').click(); assert.equal(f.downloads.length,1); assert.equal(f.downloads[0].serialized,bytes); assert.equal(f.downloads[0].blob.size,new TextEncoder().encode(bytes).length);
 release({outcome:'initiated'}); await tick(); assert.equal(f.node(d,'status').dataset.resultState,'unconfirmed'); assert.equal(f.node(d,'save').disabled,true); assert.equal(f.requests.length,0);
});
for(const change of ['account','role','connection','typed','offline','language','source']) test(change+' invalidates preview/consent without HA calls',async t=>{
 const f=fixture(t); const d=await f.open(); const privateInput=f.node(d,'private'); privateInput.checked=true; privateInput.dispatchEvent(new f.dom.window.Event('change')); f.node(d,'refresh').click(); await tick(); assert.equal(f.node(d,'save').disabled,false);
 if(change==='source') { f.card._renameLog.push({status:'error',oldId:'sensor.x',newId:'sensor.y'}); f.card._saveHistoryToStorage(); }
 else if(change==='offline') f.dom.window.dispatchEvent(new f.dom.window.Event('offline'));
 else if(change==='typed') { f.connection.connected='true'; f.connection.dispatchEvent(new f.dom.window.Event('ready')); }
 else f.card.hass={...f.hass,language:change==='language'?'pl':'en',user:change==='account'?{id:'admin-b',is_admin:true}:change==='role'?{id:'admin-a',is_admin:false}:f.hass.user,connection:change==='connection'?{connected:true}:f.connection};
 assert.equal(f.downloads.length,0); assert.equal(f.requests.length,0);
 if(d.host.isConnected) {assert.equal(f.node(d,'save').disabled,true); assert.equal(f.node(d,'private').checked,false);}
});
test('source mutation is checked again at Save even without a history event',async t=>{const f=fixture(t);const d=await f.open();f.card._renameLog[0].newId='sensor.changed';f.node(d,'save').click();assert.equal(f.downloads.length,0);assert.equal(f.node(d,'private').checked,false);});
test('Cancel and late producer cannot repopulate a reopened dialog or retain private consent',async t=>{
 const f=fixture(t);let release;const original=f.card._buildRenameReport.bind(f.card);f.card._buildRenameReport=()=>new Promise(resolve=>{release=resolve;});const d=await f.open();f.node(d,'cancel').click();f.card._buildRenameReport=original;const next=await f.open();release(original({draft:{format:'json',includePrivate:true}}));await tick();assert.equal(f.node(next,'private').checked,false);assert.ok(!f.node(next,'preview').textContent.includes('sensor.old'));assert.equal(f.downloads.length,0);
});
test('unsettled cancelled download stays latched across reopen and ignores late saved outcome',async t=>{
 const f=fixture(t);let release;f.card._downloadRenameReport=request=>{f.downloads.push(request);return new Promise(resolve=>{release=resolve;});};const d=await f.open();f.node(d,'save').click();f.node(d,'cancel').click();const next=await f.open();f.node(next,'save').click();assert.equal(f.downloads.length,1);release({outcome:'saved'});await tick();assert.notEqual(f.node(next,'status').dataset.resultState,'success');assert.equal(f.node(next,'save').disabled,true);
});
for(const language of ['pl','en']) test(language+' dialog traps focus, Escape cancels and returns to 44px opener',async t=>{
 const f=fixture(t,language);const opener=f.card.shadowRoot.getElementById('exportRenameLog');opener.focus();const d=await f.open();const cancel=f.node(d,'cancel');cancel.focus();cancel.dispatchEvent(new f.dom.window.KeyboardEvent('keydown',{key:'Tab',bubbles:true,composed:true,cancelable:true}));assert.equal(d.activeElement,f.node(d,'filename'));
 assert.match(f.node(d,'save').textContent,language==='pl'?/Zapisz/:/Save/);f.node(d,'filename').dispatchEvent(new f.dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true,composed:true,cancelable:true}));assert.equal(f.card.shadowRoot.activeElement,opener);assert.equal(f.downloads.length,0);
});
test('disconnect aborts preview and removes browser and connection listeners',async t=>{const f=fixture(t);const d=await f.open();f.card.remove();assert.equal(d.host.isConnected,false);f.connection.dispatchEvent(new f.dom.window.Event('disconnected'));assert.equal(f.downloads.length,0);});
test('actual native completion records phase without adding registry readback or rewriting references',async t=>{
 const f=fixture(t);f.card._renameLog=[];f.card._renameQueue=[{oldId:'sensor.a',newId:'sensor.b'}];f.card._showRenameConfirmation();await f.card._executeRenames(true);
 assert.equal(f.card._renameLog[0].writePhase,'acknowledged');assert.equal(JSON.parse(payload(f.card).serialized).counts.applied,0);
 assert.equal(f.requests.filter(msg=>msg.type.endsWith('/update')).length,1);assert.equal(f.requests.filter(msg=>msg.type==='config/entity_registry/list').length,1);
});
test('uncertain attempted write is unknown',async t=>{
 const f=fixture(t);f.card._renameLog=[];f.card._renameQueue=[{oldId:'sensor.a',newId:'sensor.b'}];f.hass.callWS=async msg=>{if(msg.type.endsWith('/update'))throw new Error('private uncertain timeout');return msg.type.endsWith('/list')?[]:{};};f.card._showRenameConfirmation();await f.card._executeRenames(true);assert.equal(f.card._renameLog[0].writePhase,'attempted');assert.equal(JSON.parse(payload(f.card).serialized).counts.unknown,1);
});
test('native automatic preflight rejection is failed and records no registry update',async t=>{
 const f=fixture(t);f.card._renameLog=[];f.card._renameQueue=[{oldId:'sensor.a',newId:'sensor.b',automatic:true}];f.card._revalidateAutomaticQueue=async()=>new Map([['sensor.a','Controlled preflight rejection']]);f.card._showRenameConfirmation();await f.card._executeRenames(true);assert.equal(f.card._renameLog[0].writePhase,'not_started');assert.equal(JSON.parse(payload(f.card).serialized).counts.failed,1);assert.equal(f.requests.filter(msg=>msg.type.endsWith('/update')).length,0);
});
test('disconnected event blocks refreshing even before HA mutates connected=true',async t=>{
 const f=fixture(t);const d=await f.open();f.connection.dispatchEvent(new f.dom.window.Event('disconnected'));f.node(d,'refresh').click();await tick();assert.equal(f.node(d,'save').disabled,true);assert.equal(f.downloads.length,0);f.connection.dispatchEvent(new f.dom.window.Event('ready'));assert.equal(f.node(d,'save').disabled,true);
});
test('reviewed vendors have exact source hashes, one local factory and no globals',()=>{
 const crypto=require('node:crypto'),hash=s=>crypto.createHash('sha256').update(s).digest('hex');const p=JSON.parse(fs.readFileSync(path.join(root,'shared/renamer-report-provenance.json')));
 assert.equal(hash(fs.readFileSync(path.join(root,'shared/ha-tools-export.js'))),'1f9e9c5444ec3b6de44eec136f14ae9e7d89dae1002650b33f746ab9633982f1');assert.equal(hash(fs.readFileSync(path.join(root,'shared/ha-tools-ui.js'))),'029c06206e55abad9aa09dfcf65bb13027ec24ce6a0bfacc45a938891b27254e');
 const source=fs.readFileSync(path.join(root,'ha-entity-renamer.js'),'utf8');assert.equal((source.match(/function createExportDialog\(/g)||[]).length,1);assert.equal((source.match(/function createUiV3\(/g)||[]).length,1);assert.ok(!source.includes('window.createExportDialog'));assert.equal(p.baseHead,'41cb4b56ef428fde08d9a854252e9a702c55894b');
});

for(const persisted of [true,false,'identity_mismatch','name_mismatch','read_failed'])test(`existing independent registry refresh confirms applied only when stable identity and all requested fields match (${persisted})`,async t=>{
 const f=fixture(t),source={id:'stable-registry',entity_id:'sensor.old',platform:'fixture',unique_id:'fixture-unique',device_id:'fixture-device',name:null};let registry=[{...source}];f.card._entities=registry;f.card.hass={...f.hass};f.card._renameLog=[];f.card._renameQueue=[{oldId:'sensor.old',newId:'sensor.new',newName:'Reviewed private name',entity:source,sourceIdentity:f.card._sourceIdentity(source)}];
 f.hass.callWS=async msg=>{f.requests.push(msg);if(msg.type==='config/entity_registry/list'){if(persisted==='read_failed'&&f.requests.filter(x=>x.type===msg.type).length===2)throw Error('Synthetic readonly failure');return registry.map(x=>({...x}));}if(msg.type==='config/device_registry/list')return [];if(msg.type==='config/entity_registry/update'){if(persisted)registry=[{...source,id:persisted==='identity_mismatch'?'foreign-registry':source.id,entity_id:msg.new_entity_id,name:persisted==='name_mismatch'?'Unchanged private name':msg.name}];return {...source,entity_id:msg.new_entity_id,name:msg.name};}return {};};f.card._hass=f.hass;
 f.card._showRenameConfirmation();await f.card._executeRenames(true);const r=JSON.parse(payload(f.card).serialized);assert.equal(r.counts.applied,persisted===true?1:0);assert.equal(f.card._lastApplyResult.ok,persisted===true?1:0);assert.notEqual(f.card._message.type,persisted===true?'warning':'success');assert.ok(f.card.shadowRoot.querySelector('[data-result-state="'+(persisted===true?'success':'unconfirmed')+'"]'));assert.equal(r.counts.unknown,persisted===true?0:1);assert.equal(f.requests.filter(x=>x.type==='config/entity_registry/update').length,1);assert.equal(f.requests.filter(x=>x.type==='config/entity_registry/list').length,2);assert.ok(!r.rows[0].old.includes('sensor.'));assert.ok(!JSON.stringify(r).includes('Reviewed private name'));
});

test('offline event remains authoritative after replacing a connection with a cached true flag',async t=>{
 const f=fixture(t);let d=await f.open();f.dom.window.dispatchEvent(new f.dom.window.Event('offline'));const connection=new f.dom.window.EventTarget();connection.connected=true;f.card.hass={...f.hass,connection};d=await f.open();assert.equal(f.node(d,'save').disabled,true);assert.equal(f.requests.length,0);
});
