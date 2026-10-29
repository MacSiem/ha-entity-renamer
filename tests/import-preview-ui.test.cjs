'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {JSDOM}=require('jsdom');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const proposal=JSON.stringify({entities:[{entity_id:'sensor.old',proposed_entity_id:'sensor.new'}]});
const entity=(id,device_id=null)=>({entity_id:id,id:'registry-'+id,unique_id:'unique-'+id,platform:'test',device_id,name:'Current name'});
async function fixture({admin=true,language='en'}={}){
 const dom=new JSDOM('',{url:'http://localhost',runScripts:'dangerously',pretendToBeVisual:true});
 dom.window.TextEncoder=TextEncoder;
 dom.window.eval(readFileSync('ha-entity-renamer.js','utf8'));
 const card=dom.window.document.createElement('ha-entity-renamer');card.setConfig({show_support:false,show_help:false});dom.window.document.body.append(card);
 const messages=[];let respond=async message=>message.type==='config/entity_registry/list'?[entity('sensor.old')]:[];
 const connection=new dom.window.EventTarget();connection.connected=true;
 const hass={language,themes:{},states:{},connection,user:{id:'admin-a',is_admin:admin},callWS:async message=>{
  messages.push(message);if(!['config/entity_registry/list','config/device_registry/list'].includes(message.type))throw Error('Unexpected request');return respond(message);}};
 card.hass=hass;await flush();messages.length=0;
 return {dom,card,hass,connection,messages,setResponse:fn=>respond=fn};
}
const node=(f,id)=>f.card.shadowRoot.getElementById(id);
async function file(f,text=proposal,size=text.length){await f.card._readMigrationFile({size,text:async()=>text});await flush();}
async function analyze(f){node(f,'analyzeRenamerImport').click();await flush();}
function subset(f,index=0){const mode=node(f,'renamerImportSubset');mode.checked=true;mode.dispatchEvent(new f.dom.window.Event('change'));
 const row=f.card.shadowRoot.querySelector('[data-import-entity="'+index+'"]');row.checked=true;row.dispatchEvent(new f.dom.window.Event('change'));}

test('release A uses fresh native registries, shows actual and proposed names, never mutates',async()=>{
 const f=await fixture();try{
 const queue=f.card._renameQueue;await file(f);assert.equal(f.messages.length,0);await analyze(f);
 assert.equal(f.card._migration.phase,'reviewed');assert.deepEqual(f.messages.map(m=>m.type),['config/entity_registry/list','config/device_registry/list']);
 const text=node(f,'renamerMigrationPanel').textContent;
 assert.match(text,/sensor.old/);assert.match(text,/sensor.new/);assert.match(text,/Current name/);assert.match(text,/read.only/i);
 assert.equal(f.card._renameQueue,queue);assert.equal(f.dom.window.localStorage.length,0);assert.equal(node(f,'applyRenamerImport'),null);
 await analyze(f);assert.equal(f.messages.length,4,'every explicit preview fetches current registries');
 assert.equal(node(f,'checkRenamerBackendStatus'),null,'unpublished companion is not an active prerequisite');
 const beforeRecovery=f.messages.length;await f.card._checkBackendStatus();await f.card._inspectRecovery();await f.card._restoreRecovery();assert.equal(f.messages.length,beforeRecovery,'direct new recovery methods are disabled for admins too');
 const coming=f.card.shadowRoot.querySelectorAll('[data-coming-soon]');assert.ok(coming.length);for(const item of coming){assert.equal(item.disabled,true);assert.equal(item.checked,false);}
 }finally{f.dom.window.close();}
});
test('missing source, occupied destination, device mismatch and missing device are explicit',async()=>{
 const f=await fixture();try{
 const doc={entities:[{entity_id:'sensor.old',proposed_entity_id:'sensor.new',device_id:'d-a'},
 {entity_id:'sensor.missing',proposed_entity_id:'sensor.next'},
 {entity_id:'sensor.moved',proposed_entity_id:'sensor.moved2',device_id:'d-old'}],devices:[{device_id:'d-missing',proposed_name:'Wanted'}]};
 await file(f,JSON.stringify(doc));f.setResponse(async m=>m.type==='config/entity_registry/list'?[entity('sensor.old','d-a'),entity('sensor.new'),entity('sensor.moved','d-new'),entity('sensor.private')]:[]);
 await analyze(f);assert.equal(f.card._migration.phase,'reviewed');const text=node(f,'renamerMigrationPanel').textContent;
 assert.match(text,/occupied/i);assert.match(text,/source.*not found/i);assert.match(text,/association changed/i);assert.match(text,/Device not found/i);assert.doesNotMatch(text,/sensor.private|d-new/);
 }finally{f.dom.window.close();}
});
test('ambiguous registry refuses preview without disclosing unrelated IDs',async()=>{
 const f=await fixture();try{await file(f);f.setResponse(async m=>m.type==='config/entity_registry/list'?[entity('sensor.private'),entity('sensor.private')]:[]);
 await analyze(f);assert.equal(f.card._migration.phase,'reviewed');assert.match(node(f,'renamerMigrationPanel').textContent,/Ambiguous registry/);assert.doesNotMatch(node(f,'renamerMigrationPanel').textContent,/sensor.private/);
 }finally{f.dom.window.close();}
});
test('explicit selection compares only selected changes and never infers device renames',async()=>{
 const f=await fixture();try{await file(f,JSON.stringify({entities:[{entity_id:'sensor.old',proposed_entity_id:'sensor.new'},{entity_id:'sensor.second',proposed_entity_id:'sensor.next'}],devices:[{device_id:'device-a',proposed_name:'New device'}]}));
 subset(f);await analyze(f);const text=node(f,'renamerMigrationPanel').textContent;assert.equal(f.card._migration.summary.changes.entities,1);assert.equal(f.card._migration.summary.changes.devices,0);assert.doesNotMatch(text,/source.*not found/i);assert.equal(f.messages.length,2);
 }finally{f.dom.window.close();}
});
test('strict decoding rejects duplicates, cross-domain, overlapping, unlinked statistics and unknown instructions before IO',async()=>{
 const bad=['{"entities":null}', '{"entities":[],"entities":[]}', '{"entities":[{"entity_id":"sensor.old","proposed_entity_id":"switch.new"}]}',
 JSON.stringify({entities:[{entity_id:'sensor.old',proposed_entity_id:'sensor.new'},{entity_id:'sensor.new',proposed_entity_id:'sensor.third'}]}),
 JSON.stringify({entities:[{entity_id:'sensor.old',proposed_entity_id:'sensor.new'}],statistics:[{statistic_id:'sensor.other',proposed_statistic_id:'sensor.next'}]}),
 JSON.stringify({entities:[{entity_id:'sensor.old',proposed_entity_id:'sensor.new',execute:'yes'}]}),
 JSON.stringify({entities:[{entity_id:'sensor.old',proposed_entity_id:'sensor.new'},{entity_id:'sensor.old',proposed_entity_id:'sensor.next'}]}),
 '{"entities":[{"entity_id":"sensor.old","entity_\\u0069d":"sensor.other","proposed_entity_id":"sensor.new"}]}'];
 const f=await fixture();try{for(const text of bad){await file(f,text);assert.equal(f.card._migration.phase,'invalid',text);assert.equal(node(f,'analyzeRenamerImport').disabled,true);}assert.equal(f.messages.length,0);
 }finally{f.dom.window.close();}
});
test('household has no import or new recovery controls even through direct methods',async()=>{
 const f=await fixture({admin:false});try{await file(f);await f.card._previewMigration();assert.equal(f.card._migration,null);assert.equal(node(f,'renamerMigrationPanel'),null);assert.equal(f.messages.length,0);assert.equal(f.card._recoveryAvailable(),false);
 }finally{f.dom.window.close();}
});
test('file bound and transport errors show no private input or error',async()=>{
 const f=await fixture();try{await file(f,'PRIVATE source',4_000_001);assert.equal(f.card._migration.phase,'invalid');await file(f);
 f.setResponse(async()=>{throw Error('PRIVATE token /path <img>');});await analyze(f);assert.equal(f.card._migration.phase,'unavailable');assert.doesNotMatch(f.card.shadowRoot.innerHTML,/PRIVATE source|PRIVATE token|<img>/);
 }finally{f.dom.window.close();}
});
test('role, account, disconnect and detach discard late native results',async()=>{
 for(const mode of ['role','account','disconnect','detach']){const f=await fixture();try{await file(f);let resolve;f.setResponse(()=>new Promise(r=>resolve=r));node(f,'analyzeRenamerImport').click();assert.equal(f.messages.length,1);
 if(mode==='role')f.card.hass={...f.hass,user:{id:'admin-a',is_admin:false}};
 if(mode==='account')f.card.hass={...f.hass,user:{id:'admin-b',is_admin:true}};
 if(mode==='disconnect')f.connection.dispatchEvent(new f.dom.window.Event('disconnected'));
 if(mode==='detach')f.card.remove();const afterAuthorityChange=f.messages.length;resolve([entity('sensor.old')]);await flush();assert.equal(f.card._migration,null);assert.equal(f.messages.length,afterAuthorityChange,'late preview must not issue another read');assert.doesNotMatch(f.card.shadowRoot.innerHTML,/sensor.old/);
 }finally{f.dom.window.close();}}
});
test('new file or selection discards pending result; clicks coalesce',async()=>{
 for(const mode of ['file','selection']){const f=await fixture();try{await file(f);let resolve;f.setResponse(()=>new Promise(r=>resolve=r));const button=node(f,'analyzeRenamerImport');button.click();button.click();assert.equal(f.messages.length,1);
 if(mode==='file')await file(f,JSON.stringify({entities:[{entity_id:'sensor.second',proposed_entity_id:'sensor.third'}]}));else subset(f);
 resolve([entity('sensor.old')]);await flush();assert.equal(f.card._migration.phase,'loaded');assert.equal(f.card._migration.summary,null);assert.equal(f.messages.length,1);
 }finally{f.dom.window.close();}}
});
test('localized preview escapes imported names and preserves external focus and draft',async()=>{
 for(const language of ['pl','en']){const f=await fixture({language});try{await file(f,JSON.stringify({entities:[{entity_id:'sensor.old',proposed_entity_id:'sensor.new',proposed_name:'<img src=x onerror=alert(1)>'}]}));
 const draft=f.dom.window.document.createElement('input');draft.value='draft';f.card.shadowRoot.append(draft);let resolve;f.setResponse(m=>m.type==='config/entity_registry/list'?new Promise(r=>resolve=r):[]);node(f,'analyzeRenamerImport').click();draft.focus();resolve([entity('sensor.old')]);await flush();
 assert.equal(draft.value,'draft');assert.equal(f.card.shadowRoot.activeElement,draft);assert.equal(node(f,'renamerMigrationPanel').querySelector('img'),null);assert.match(node(f,'renamerMigrationPanel').textContent,language==='pl'?/tylko odczyt/:/read.only/i);
 }finally{f.dom.window.close();}}
});
