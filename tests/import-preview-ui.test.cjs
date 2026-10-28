'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {JSDOM} = require('jsdom');
const flush = () => new Promise(resolve => setImmediate(resolve));
const proposal = JSON.stringify({entities:[{entity_id:'sensor.old',proposed_entity_id:'sensor.new'}]});
const preview = () => ({capture_id:'opaque',digest:'a'.repeat(64),migration_ready:false,inventory_complete:false,
 plan_supported:false,changes:{entities:1,devices:0,statistics:0},conflicts:[],families:[
 {family:'external',status:'UNKNOWN',reason:'external_inventory_unverified',blockers:[]}]});
async function fixture({admin=true,language='en'}={}) {
 const dom=new JSDOM('',{url:'http://localhost',runScripts:'dangerously',pretendToBeVisual:true});
 // JSDOM does not expose the browser TextEncoder; use Node's native implementation.
 dom.window.TextEncoder=TextEncoder;
 dom.window.eval(readFileSync('ha-entity-renamer.js','utf8'));
 const card=dom.window.document.createElement('ha-entity-renamer');card.setConfig({show_support:false,show_help:false});dom.window.document.body.append(card);
 const messages=[];let respond=async()=>preview();const connection=new dom.window.EventTarget();connection.connected=true;
 const hass={language,themes:{},states:{},connection,user:{id:'admin-a',is_admin:admin},callWS:async message=>{
  messages.push(message);if(message.type==='config/device_registry/list'||message.type==='config/entity_registry/list')return [];
  if(message.type==='ha_entity_renamer/import/preview')return respond(message);throw Error('Unexpected request');}};
 card.hass=hass;await flush();return {dom,card,hass,connection,messages,setResponse:fn=>respond=fn};
}
const node=(f,id)=>f.card.shadowRoot.getElementById(id);
const packets=f=>f.messages.filter(m=>m.type.startsWith('ha_entity_renamer/'));
async function file(f,text=proposal,size=text.length){await f.card._readMigrationFile({size,text:async()=>text});await flush();}
async function analyze(f){node(f,'analyzeRenamerImport').click();await flush();}

test('active admin import is independent of help, explicit and read-only',async()=>{
 const f=await fixture();try{
 assert.ok(node(f,'renamerMigrationPanel'));assert.equal(packets(f).length,0);
 const queue=f.card._renameQueue;await file(f);assert.equal(packets(f).length,0);
 await analyze(f);assert.equal(packets(f).length,1);assert.equal(packets(f)[0].manifest,proposal);
 assert.deepEqual(Object.keys(packets(f)[0]).sort(),['manifest','type']);
 assert.match(node(f,'renamerMigrationPanel').textContent,/1/);
 assert.match(node(f,'renamerMigrationPanel').textContent,/external|External/);
 assert.equal(f.messages.some(m=>m.type.endsWith('/update')||m.type.endsWith('/apply')),false);
 assert.equal(f.card._renameQueue,queue);assert.equal(f.dom.window.localStorage.length,0);
 }finally{f.dom.window.close();}
});

test('household cannot import or invoke analysis directly',async()=>{
 const f=await fixture({admin:false});try{
 assert.equal(node(f,'renamerMigrationPanel'),null);await file(f);await f.card._previewMigration();
 assert.equal(packets(f).length,0);assert.equal(f.card._migration,null);
 }finally{f.dom.window.close();}
});

test('oversized files and private transport errors never display input or private errors',async()=>{
 const f=await fixture();try{
 await file(f,'PRIVATE source',4_000_001);assert.equal(packets(f).length,0);
 assert.doesNotMatch(f.card.shadowRoot.innerHTML,/PRIVATE source/);
 await file(f);f.setResponse(async()=>{throw Error('PRIVATE token /path <img>');});await analyze(f);
 assert.doesNotMatch(f.card.shadowRoot.innerHTML,/PRIVATE token|<img>/);
 assert.equal(packets(f).length,1);
 }finally{f.dom.window.close();}
});

test('authority loss and detach purge manifest and discard late analysis',async()=>{
 for(const mode of ['role','account','disconnect','detach']){
 const f=await fixture();try{await file(f);let resolve;f.setResponse(()=>new Promise(r=>resolve=r));
 node(f,'analyzeRenamerImport').click();assert.equal(packets(f).length,1);
 if(mode==='role')f.card.hass={...f.hass,user:{id:'admin-a',is_admin:false}};
 if(mode==='account')f.card.hass={...f.hass,user:{id:'admin-b',is_admin:true}};
 if(mode==='disconnect')f.connection.dispatchEvent(new f.dom.window.Event('disconnected'));
 if(mode==='detach')f.card.remove();resolve(preview());await flush();
 assert.equal(f.card._migration,null);assert.doesNotMatch(f.card.shadowRoot.innerHTML,/sensor.old/);
 }finally{f.dom.window.close();}}
});

test('new file invalidates pending preview, requests coalesce, result cannot authorize apply',async()=>{
 const f=await fixture();try{await file(f);let resolve;f.setResponse(()=>new Promise(r=>resolve=r));
 const button=node(f,'analyzeRenamerImport');button.click();button.click();assert.equal(packets(f).length,1);
 await file(f,JSON.stringify({entities:[{entity_id:'sensor.second',proposed_entity_id:'sensor.third'}]}));
 resolve({...preview(),migration_ready:true});await flush();assert.equal(f.card._migration.phase,'loaded');
 f.setResponse(async()=>({...preview(),migration_ready:true}));await analyze(f);
 assert.equal(f.card._migration.phase,'unavailable');assert.equal(node(f,'applyRenamerImport'),null);
 }finally{f.dom.window.close();}
});

test('localized result safely lists known families and preserves external focus and draft',async()=>{
 for(const language of ['pl','en']){const f=await fixture({language});try{
 await file(f);const draft=f.dom.window.document.createElement('input');draft.value='draft';f.card.shadowRoot.append(draft);
 let resolve;f.setResponse(()=>new Promise(r=>resolve=r));node(f,'analyzeRenamerImport').click();draft.focus();
 resolve(preview());await flush();assert.equal(draft.value,'draft');assert.equal(f.card.shadowRoot.activeElement,draft);
 assert.match(node(f,'renamerMigrationPanel').textContent,language==='pl'?/zależności|zewnętrzni/:/dependencies|External/);
 }finally{f.dom.window.close();}}
});

test('missing sources are explicit conflicts tied only to the imported scope',async()=>{
 const f=await fixture();try{
 await file(f);f.setResponse(async()=>({...preview(),registry_conflicts:[
 {kind:'entity',index:0,reason:'source_missing',entity_id:'sensor.old'}]}));await analyze(f);
 assert.match(node(f,'renamerMigrationPanel').textContent,/sensor.old/);
 assert.match(node(f,'renamerMigrationPanel').textContent,/source.*missing|source.*not found/i);
 assert.equal(node(f,'applyRenamerImport'),null);
 f.setResponse(async()=>({...preview(),registry_conflicts:[
 {kind:'entity',index:0,reason:'source_missing',entity_id:'sensor.unrequested_private'}]}));await analyze(f);
 assert.equal(f.card._migration.phase,'unavailable');
 assert.doesNotMatch(node(f,'renamerMigrationPanel').textContent,/unrequested_private/);
 }finally{f.dom.window.close();}
});

test('explicit subset sends only chosen rows and never infers device renames',async()=>{
 const f=await fixture();try{
 await file(f,JSON.stringify({entities:[
 {entity_id:'sensor.old',proposed_entity_id:'sensor.new'},
 {entity_id:'sensor.second',proposed_entity_id:'sensor.next'},
 {entity_id:'sensor.keep',proposed_entity_id:'sensor.keep'}],devices:[
 {device_id:'device-a',current_name:'Old device',proposed_name:'New device'}]}));
 const mode=node(f,'renamerImportSubset');assert.ok(mode,'explicit subset choice');
 mode.checked=true;mode.dispatchEvent(new f.dom.window.Event('change'));
 assert.equal(node(f,'analyzeRenamerImport').disabled,true);
 const row=f.card.shadowRoot.querySelector('[data-import-entity="0"]');assert.ok(row);
 row.checked=true;row.dispatchEvent(new f.dom.window.Event('change'));
 assert.equal(f.card.shadowRoot.querySelectorAll('[data-import-entity]').length,2);
 await analyze(f);
 assert.deepEqual(Array.from(packets(f)[0].entity_ids),['sensor.old']);
 assert.deepEqual(Array.from(packets(f)[0].device_ids),[]);
 assert.equal(f.dom.window.localStorage.length,0);
 }finally{f.dom.window.close();}
});

test('changing subset discards a pending result and requires explicit fresh analysis',async()=>{
 const f=await fixture();try{
 await file(f);let resolve;f.setResponse(()=>new Promise(r=>resolve=r));
 node(f,'analyzeRenamerImport').click();
 const mode=node(f,'renamerImportSubset');assert.ok(mode);
 mode.checked=true;mode.dispatchEvent(new f.dom.window.Event('change'));
 resolve(preview());await flush();
 assert.equal(f.card._migration.summary,null);
 assert.equal(packets(f).length,1);
 assert.equal(node(f,'analyzeRenamerImport').disabled,true);
 }finally{f.dom.window.close();}
});
