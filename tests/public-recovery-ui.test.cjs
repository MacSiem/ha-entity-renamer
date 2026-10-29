'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {JSDOM} = require('jsdom');

const flush = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
};
const OP = {operation_id:'private-operation', fingerprint:'a'.repeat(64)};
const inspection = (overrides={}) => ({...OP,state:'applied',reason:'complete',eligible:true,migration_ready:false,family_complete:false,change_summary:{source:{current_entity_id:'sensor.changed',original_entity_id:'sensor.original'},consumers:{count:1,domains:['sensor']}},...overrides});
const ready = (overrides = {}) => ({
  version: '5.0.0.dev0', migration_ready: false,
  reason: 'dependency_coverage_incomplete',
  recovery_required: false, operations: [], ...overrides
});

async function fixture({admin = true, language = 'en', showHelp = true} = {}) {
  const dom = new JSDOM('', {url: 'http://localhost/', runScripts: 'dangerously', pretendToBeVisual: true});
  dom.window.eval(readFileSync('ha-entity-renamer.js', 'utf8'));
  const card = dom.window.document.createElement('ha-entity-renamer');
  // Internal V2 component qualification only: production A keeps this unavailable.
  card._recoveryAvailable = () => card._renameAuthorityAvailable();
  card.setConfig({show_support: false, show_help: showHelp});
  dom.window.document.body.append(card);
  const messages = [];
  let backend = async () => ready({operations:[OP]});
  let recover = async message => message.type.endsWith('/inspect') ? inspection() : inspection({state:'rolled_back',reason:'restored',eligible:false});
  const connection = new dom.window.EventTarget();
  connection.connected = true;
  const hass = {
    language, themes: {}, states: {}, connection,
    user: {id: 'admin-a', is_admin: admin},
    callWS: async message => {
      messages.push(message);
      if (message.type === 'config/device_registry/list') return [{id: 'qa-device', name: 'Test device'}];
      if (message.type === 'config/entity_registry/list') return [{entity_id: 'sensor.qa', device_id: 'qa-device', unique_id: 'fixture'}];
      if (message.type === 'ha_entity_renamer/status') return backend();
      if (message.type.startsWith('ha_entity_renamer/recovery/')) return recover(message);
      throw Error('Unexpected callWS message: ' + message.type);
    }
  };
  card.hass = hass;
  await flush();
  return {dom, card, hass, messages, connection, setBackend: fn => {backend = fn;}, setRecovery: fn => {recover = fn;}};
}


const button = (f,id) => f.card.shadowRoot.getElementById(id);
const click = async (f,id) => {assert.ok(button(f,id),id); button(f,id).click(); await flush();};
const packets = f => f.messages.filter(m=>m.type.startsWith('ha_entity_renamer/'));
const restores = f => packets(f).filter(m=>m.type.endsWith('/restore'));
const inspect = async f => {await click(f,'checkRenamerBackendStatus'); await click(f,'reviewRenamerRecovery');};
const close = f => f.dom.window.close();

test('opt-in status -> readonly inspect -> separate confirmation -> one exact restore packet', async()=>{
 const f=await fixture(); try {
 assert.equal(packets(f).length,0); const queue=f.card._renameQueue;
 await inspect(f); assert.equal(restores(f).length,0);
 assert.deepEqual(Object.keys(packets(f).at(-1)).sort(),['fingerprint','operation_id','type']);
 assert.equal(f.messages.some(m=>m.type.endsWith('/update')),false);
 assert.match(button(f,'renamerRecoveryPanel').textContent,/sensor.changed/);
 assert.match(button(f,'renamerRecoveryPanel').textContent,/sensor.original/);
 assert.equal(button(f,'confirmRenamerRecovery'),null);
 await click(f,'askRenamerRecovery');
 assert.equal(f.card.shadowRoot.activeElement,button(f,'cancelRenamerRecovery'));
 await click(f,'confirmRenamerRecovery');
 assert.equal(restores(f).length,1);
 assert.deepEqual(Object.keys(restores(f)[0]).sort(),['fingerprint','operation_id','type']);
 assert.equal(restores(f)[0].fingerprint,OP.fingerprint);
 assert.equal(restores(f)[0].operation_id,OP.operation_id);
 assert.match(button(f,'renamerRecoveryPanel').textContent,/restored/i);
 assert.equal(f.card._renameQueue,queue);
 assert.doesNotMatch(f.card.shadowRoot.innerHTML,/private-operation|aaaaaaaaaaaaaaaa/);
 } finally {close(f);}
});

test('Cancel default, Enter on Cancel, Escape and trapped Tab never restore',async()=>{
 const f=await fixture(); try {await inspect(f);
 await click(f,'askRenamerRecovery');
 button(f,'cancelRenamerRecovery').dispatchEvent(new f.dom.window.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
 button(f,'cancelRenamerRecovery').click(); await flush(); assert.equal(restores(f).length,0);
 await click(f,'askRenamerRecovery');
 button(f,'cancelRenamerRecovery').dispatchEvent(new f.dom.window.KeyboardEvent('keydown',{key:'Tab',bubbles:true,cancelable:true}));
 assert.equal(f.card.shadowRoot.activeElement,button(f,'confirmRenamerRecovery'));
 button(f,'confirmRenamerRecovery').dispatchEvent(new f.dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));
 assert.equal(button(f,'confirmRenamerRecovery'),null); assert.equal(restores(f).length,0);
 } finally {close(f);}
});

test('UNKNOWN or mismatched inspect cannot offer confirmation; private errors are never displayed',async()=>{
 for (const response of [inspection({state:'UNKNOWN',eligible:false}),inspection({fingerprint:'b'.repeat(64)}),inspection({state:'refused',eligible:false})]) {
 const f=await fixture(); try {f.setRecovery(async()=>response); await inspect(f);
 assert.equal(button(f,'askRenamerRecovery'),null); assert.equal(restores(f).length,0);
 } finally {close(f);}}
 const f=await fixture(); try {f.setRecovery(async()=>{throw Object.assign(Error('PRIVATE YAML /path'),{code:'unauthorized'});});await inspect(f);
 assert.match(button(f,'renamerRecoveryPanel').textContent,/permission/i);
 assert.doesNotMatch(f.card.shadowRoot.innerHTML,/PRIVATE YAML/);
 } finally {close(f);}
});

test('uncertain restore has no automatic retry and double click is coalesced',async()=>{
 const f=await fixture(); try {const wait=deferred();await inspect(f);await click(f,'askRenamerRecovery');
 f.setRecovery(()=>wait.promise);const confirm=button(f,'confirmRenamerRecovery');confirm.click();confirm.click();
 assert.equal(restores(f).length,1);assert.equal(confirm.disabled,true);
 wait.reject(Error('PRIVATE socket error'));await flush();await flush();
 assert.match(button(f,'renamerRecoveryPanel').textContent,/outcome.*unknown/i);
 assert.equal(button(f,'confirmRenamerRecovery'),null);assert.equal(restores(f).length,1);
 assert.doesNotMatch(f.card.shadowRoot.innerHTML,/PRIVATE socket/);
 } finally {close(f);}
});

test('account, role, connection and detach purge recovery and discard late inspect',async()=>{
 for (const mode of ['account','role','connection','disconnect','detach']) {
 const f=await fixture(); try {const wait=deferred();f.setRecovery(()=>wait.promise);
 await click(f,'checkRenamerBackendStatus');button(f,'reviewRenamerRecovery').click();
 if(mode==='account')f.card.hass={...f.hass,user:{id:'other',is_admin:true}};
 if(mode==='role')f.card.hass={...f.hass,user:{id:'admin-a',is_admin:false}};
 if(mode==='connection'){const c=new f.dom.window.EventTarget();c.connected=true;f.card.hass={...f.hass,connection:c};}
 if(mode==='disconnect')f.connection.dispatchEvent(new f.dom.window.Event('disconnected'));
 if(mode==='detach')f.card.remove();
 wait.resolve(inspection());await flush();
 assert.equal(f.card._recovery,null);assert.doesNotMatch(f.card.shadowRoot.innerHTML,/sensor.changed|private-operation/);
 } finally {close(f);}}
});

test('household and unresolved admin have no recovery controls or direct requests',async()=>{
 for(const options of [{admin:false}]){const f=await fixture(options);try{
 await f.card._checkBackendStatus();await f.card._inspectRecovery();await f.card._restoreRecovery();
 assert.equal(packets(f).length,0);assert.equal(button(f,'reviewRenamerRecovery'),null);
 }finally{close(f);}}
 const f=await fixture();try{f.card.hass={...f.hass,user:{id:'admin-a'}};await f.card._checkBackendStatus();assert.equal(packets(f).length,0);}finally{close(f);}
});

test('late inspect only updates its panel, preserves draft focus and queue, PL/EN',async()=>{
 for(const language of ['en','pl']){const f=await fixture({language});try{
 await click(f,'checkRenamerBackendStatus');const wait=deferred();f.setRecovery(()=>wait.promise);
 const external=f.dom.window.document.createElement('input');f.card.shadowRoot.append(external);external.value='draft';external.focus();
 const queue=f.card._renameQueue;button(f,'reviewRenamerRecovery').click();external.focus();
 wait.resolve(inspection({change_summary:{source:{current_entity_id:'sensor.changed',original_entity_id:'sensor.original'},consumers:{count:1,domains:['sensor']}}}));await flush();
 assert.equal(f.card.shadowRoot.activeElement,external);assert.equal(external.value,'draft');assert.equal(f.card._renameQueue,queue);
 assert.equal(button(f,'renamerRecoveryPanel').querySelector('img'),null);
 assert.ok(button(f,'renamerRecoveryPanel').querySelector('wbr'));
 await click(f,'askRenamerRecovery');assert.equal(button(f,'renamerRecoveryConfirm').getAttribute('role'),'dialog');
 assert.match(button(f,'cancelRenamerRecovery').textContent,language==='pl'?/Anuluj/:/Cancel/);
 assert.match(readFileSync('ha-entity-renamer.js','utf8'),/min-height:44px/);
 }finally{close(f);}}
});

test('multiple opaque operations use numbered choices and malformed routing never becomes reviewable',async()=>{
 const f=await fixture();try{f.setBackend(async()=>ready({operations:[OP,{...OP,operation_id:'second-private'}, {operation_id:'invalid',fingerprint:'bad'},{operation_id:'invalid/route',fingerprint:'a'.repeat(64)}]}));await click(f,'checkRenamerBackendStatus');
 const select=button(f,'selectRenamerRecovery');assert.equal(select.options.length,2);assert.match(select.options[1].textContent,/2/);
 select.value='1';select.dispatchEvent(new f.dom.window.Event('change',{bubbles:true}));
 f.setRecovery(async()=>inspection({operation_id:'second-private'}));await click(f,'reviewRenamerRecovery');
 assert.equal(packets(f).at(-1).operation_id,'second-private');assert.doesNotMatch(f.card.shadowRoot.innerHTML,/second-private/);
 }finally{close(f);}
});

test('late restore cannot project results after account, connection or DOM authority loss',async()=>{
 for(const mode of ['account','connection','detach']){const f=await fixture();try{
 await inspect(f);await click(f,'askRenamerRecovery');const wait=deferred();f.setRecovery(()=>wait.promise);
 button(f,'confirmRenamerRecovery').click();assert.equal(restores(f).length,1);
 if(mode==='account')f.card.hass={...f.hass,user:{id:'other',is_admin:true}};
 if(mode==='connection')f.connection.dispatchEvent(new f.dom.window.Event('disconnected'));
 if(mode==='detach')f.card.remove();
 wait.resolve(inspection({state:'rolled_back',reason:'restored',eligible:false}));await flush();
 assert.equal(f.card._recovery,null);assert.doesNotMatch(f.card.shadowRoot.innerHTML,/Original identifiers and linked entities restored|sensor.changed/);
 assert.equal(restores(f).length,1);
 }finally{close(f);}}
});

test('hiding help preserves explicit pending operation status in the active card',async()=>{
 const f=await fixture();try{const wait=deferred();f.setBackend(()=>wait.promise);button(f,'checkRenamerBackendStatus').click();
 f.card.setConfig({show_help:false,show_support:false});wait.resolve(ready({operations:[OP]}));await flush();
 assert.ok(f.card._recovery);assert.ok(button(f,'renamerRecoveryPanel'));
 assert.equal(f.card.shadowRoot.querySelectorAll('#checkRenamerBackendStatus').length,1);
 assert.equal(restores(f).length,0);
 }finally{close(f);}
});

test('recovery remains usable with help disabled and import changes do not replace its confirmation',async()=>{
 const f=await fixture({showHelp:false});try{
 await inspect(f);await click(f,'askRenamerRecovery');
 const confirm=button(f,'confirmRenamerRecovery');assert.ok(confirm);
 f.card._updateMigrationPanel();assert.equal(button(f,'confirmRenamerRecovery'),confirm);
 await click(f,'confirmRenamerRecovery');assert.equal(restores(f).length,1);
 assert.match(button(f,'renamerRecoveryPanel').textContent,/restored/i);
 assert.equal(f.card.shadowRoot.querySelectorAll('#renamerRecoveryPanel').length,1);
 }finally{close(f);}
});


test('an in-flight recovery prevents opening a concurrent legacy rename confirmation',async()=>{
 const f=await fixture();try{
 await inspect(f);await click(f,'askRenamerRecovery');const wait=deferred();f.setRecovery(()=>wait.promise);
 button(f,'confirmRenamerRecovery').click();assert.equal(restores(f).length,1);
 f.card._renameQueue.push({oldId:'sensor.qa',newId:'sensor.next',newName:''});
 f.card._showRenameConfirmation();
 assert.equal(f.card._confirmDialogOpen,false,'recovery must retain exclusive card write intent');
 wait.resolve(inspection({state:'rolled_back',reason:'restored',eligible:false}));await flush();
 assert.equal(f.card._renameQueue.length,1);
 }finally{close(f);}
});

test('an existing legacy rename confirmation cannot acquire recovery confirmation',async()=>{
 const f=await fixture();try{
 await inspect(f);f.card._renameQueue.push({oldId:'sensor.qa',newId:'sensor.next',newName:''});
 f.card._showRenameConfirmation();assert.equal(f.card._confirmDialogOpen,true);
 button(f,'askRenamerRecovery').click();await flush();
 assert.equal(f.card._recovery.phase,'inspected');assert.equal(button(f,'renamerRecoveryConfirm'),null);
 assert.equal(restores(f).length,0);assert.equal(f.card._renameQueue.length,1);
 }finally{close(f);}
});

const pluralSummary = () => ({sources:[
 {current_entity_id:'sensor.changed',original_entity_id:'sensor.original'},
 {current_entity_id:'sensor.changed_second',original_entity_id:'sensor.original_second'}],
 consumers:{count:3,domains:['binary_sensor','sensor']}});

test('plural active card review, cancel focus, confirmation and coalesced exact operation restore',async()=>{
 for(const language of ['en','pl']){const f=await fixture({language,showHelp:false});try{
 f.setRecovery(async()=>inspection({change_summary:pluralSummary()}));await inspect(f);
 const panel=button(f,'renamerRecoveryPanel');
 assert.match(panel.textContent,/sensor.changed_second/);assert.match(panel.textContent,/sensor.original_second/);
 assert.match(panel.textContent,/3/);assert.equal(panel.querySelectorAll('code').length,4);
 assert.equal(button(f,'confirmRenamerRecovery'),null);assert.equal(restores(f).length,0);
 await click(f,'askRenamerRecovery');assert.equal(f.card.shadowRoot.activeElement,button(f,'cancelRenamerRecovery'));
 assert.equal(button(f,'renamerRecoveryConfirm').querySelectorAll('code').length,4);
 await click(f,'cancelRenamerRecovery');assert.equal(f.card.shadowRoot.activeElement,button(f,'askRenamerRecovery'));
 assert.equal(restores(f).length,0);await click(f,'askRenamerRecovery');const wait=deferred();f.setRecovery(()=>wait.promise);
 const confirm=button(f,'confirmRenamerRecovery');confirm.click();confirm.click();assert.equal(restores(f).length,1);
 assert.deepEqual(JSON.parse(JSON.stringify(restores(f)[0])),{type:'ha_entity_renamer/recovery/restore',...OP});
 wait.resolve(inspection({state:'rolled_back',reason:'restored',eligible:false}));await flush();
 assert.equal(f.card.shadowRoot.querySelectorAll('#renamerRecoveryPanel').length,1);
 assert.equal(f.card.shadowRoot.querySelectorAll('#confirmRenamerRecovery').length,0);
 }finally{close(f);}}
});

test('malformed plural scope cannot acquire confirmation or render unexpected authority',async()=>{
 const invalid = [
 {...pluralSummary(),sources:[]},
 {...pluralSummary(),sources:[pluralSummary().sources[0],pluralSummary().sources[0]]},
 {...pluralSummary(),sources:Array(1001).fill(pluralSummary().sources[0])},
 {...pluralSummary(),source:pluralSummary().sources[0]},
 {...pluralSummary(),path:'/PRIVATE/configuration.yaml'},
 {...pluralSummary(),sources:[{...pluralSummary().sources[0],uuid:'PRIVATE_UUID'}]},
 {...pluralSummary(),sources:[{current_entity_id:'sensor.<img src=x>',original_entity_id:'sensor.original'}]},
 {...pluralSummary(),sources:[{current_entity_id:'sensor.same',original_entity_id:'sensor.same'}]},
 {...pluralSummary(),consumers:{count:10001,domains:['sensor']}},
 {...pluralSummary(),consumers:{count:1,domains:['sensor','sensor']}},
 {...pluralSummary(),consumers:{count:1,domains:['automation']}},
 {...pluralSummary(),consumers:{count:1,domains:[]}},
 {...pluralSummary(),consumers:{count:0,domains:['sensor']}},
 ];
 for(const summary of invalid){const f=await fixture({showHelp:false});try{
 f.setRecovery(async()=>inspection({change_summary:summary}));await inspect(f);
 assert.equal(button(f,'askRenamerRecovery'),null);assert.equal(button(f,'confirmRenamerRecovery'),null);
 assert.equal(restores(f).length,0);assert.doesNotMatch(button(f,'renamerRecoveryPanel').innerHTML,/PRIVATE|<img/);
 }finally{close(f);}}
});

test('late plural authority is discarded after role revocation and preserves queue',async()=>{
 const f=await fixture({showHelp:false});try{const wait=deferred();f.setRecovery(()=>wait.promise);
 const queue=f.card._renameQueue;await click(f,'checkRenamerBackendStatus');button(f,'reviewRenamerRecovery').click();
 f.card.hass={...f.hass,user:{id:'admin-a',is_admin:false}};
 wait.resolve(inspection({change_summary:pluralSummary()}));await flush();
 assert.equal(f.card._recovery,null);assert.deepEqual(f.card._renameQueue,queue);
 assert.doesNotMatch(f.card.shadowRoot.innerHTML,/sensor.changed_second/);assert.equal(restores(f).length,0);
 }finally{close(f);}
});
