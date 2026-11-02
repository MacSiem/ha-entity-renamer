const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {JSDOM} = require('jsdom');
const fingerprint = 'a'.repeat(64);
const tick = () => new Promise(resolve => setImmediate(resolve));
function gate() { let resolve, reject; const promise = new Promise((a,b) => {resolve=a; reject=b;}); return {promise,resolve,reject}; }
function fixture(t, language='en') {
  const dom = new JSDOM('', {runScripts:'dangerously', pretendToBeVisual:true, url:'http://localhost/'});
  t.after(() => dom.window.close());
  dom.window.eval(readFileSync(join(__dirname,'../ha-entity-renamer.js'),'utf8'));
  const card=dom.window.document.createElement('ha-entity-renamer');
  card.setConfig({show_support:false}); dom.window.document.body.append(card);
  const requests=[], entries=[{id:'entry-a',entity_id:'sensor.a',device_id:'dev',unique_id:'a',platform:'mqtt',name:null}];
  const devices=[{id:'dev',name:'QA',name_by_user:null}];
  const review={operation_id:'review_qa',fingerprint,changes:[{entity_id:'sensor.a',fields:{entity_id:{before:'sensor.a',after:'sensor.b'}}}],
    dependencies:{domains:['template','automation','script'],yaml_file_count:3,template_consumer_count:2,files:['template.yaml','automation.yaml','script.yaml'].map(path=>({path,edit_count:1,references:[{before:'sensor.a',after:'sensor.b',occurrences:1}]}))},migration_ready:false,family_complete:false};
  const connection=new dom.window.EventTarget(); connection.connected=true;
  const handlers={};
  const hass={language,connection,user:{id:'admin-a',is_admin:true},states:{},callWS:async message => {
    requests.push(message);
    if(handlers[message.type]) return handlers[message.type](message);
    if(message.type==='config/entity_registry/list') return entries.map(row=>({...row}));
    if(message.type==='config/device_registry/list') return devices.map(row=>({...row}));
    if(message.type==='ha_entity_renamer/prepare') return review;
    if(message.type==='ha_entity_renamer/apply') { entries[0].entity_id='sensor.b'; return {operation_id:review.operation_id,fingerprint,state:'applied'}; }
    if(message.type==='ha_entity_renamer/status') return {operations:[{operation_id:review.operation_id,fingerprint,status:'applied',phases:[{adapter:'registry',phase:'verified'}],progress:{total_steps:2,verified_steps:1,undone_steps:0}}]};
    if(message.type==='ha_entity_renamer/recovery/inspect') return {operation_id:review.operation_id,fingerprint,state:'applied',eligible:true,migration_ready:false,family_complete:false};
    throw new Error('Unexpected '+message.type);
  }};
  card._hass=hass;card._lang=language;card._entities=entries.map(row=>({...row}));card._devices=devices;card._deviceEntities={dev:card._entities};card._bindRenameExportEvents();
  card._renameQueue=[{oldId:'sensor.a',newId:'sensor.b'}];card._showRenameConfirmation();
  return {card,dom,hass,connection,requests,entries,devices,review,handlers};
}
const writes = f => f.requests.filter(m=>m.type==='ha_entity_renamer/apply'||m.type.endsWith('/update'));

test('ordinary confirmation keeps registry-only and explicit server prepare as separate actions',async t=>{
  const f=fixture(t);assert.ok(f.card.shadowRoot.getElementById('confirmRenameApply'));assert.ok(f.card.shadowRoot.getElementById('prepareMigration'));
  f.card.shadowRoot.getElementById('prepareMigration').click();await tick();
  const request=f.requests.find(m=>m.type==='ha_entity_renamer/prepare');
  assert.deepEqual(JSON.parse(JSON.stringify(request)),{type:'ha_entity_renamer/prepare',entities:[{entity_id:'sensor.a',new_entity_id:'sensor.b'}],devices:[]});
  assert.equal(f.card.shadowRoot.getElementById('confirmRenameApply'),null);assert.equal(writes(f).length,0);
});

test('review displays every server change and all dependency aggregates with PL/EN escaping',async t=>{
  const f=fixture(t,'pl');const literal='<img src=x onerror="alert(1)">';
  f.review.changes=Array.from({length:12},(_,i)=>({entity_id:'sensor.server_'+i,fields:{name:{before:null,after:literal+i}}}));
  await f.card._prepareMigration();
  let dialog=f.card.shadowRoot.querySelector('[role="dialog"]');assert.match(dialog.textContent,/Zmiany serwera/);assert.match(dialog.textContent,/Zależności/);
  for(let i=0;i<12;i++) assert.ok(dialog.textContent.includes('sensor.server_'+i));
  assert.ok(dialog.textContent.includes(literal+'11'));assert.equal(dialog.querySelector('img'),null);
  assert.match(dialog.textContent,/Pliki YAML: 3/);assert.match(dialog.textContent,/Odbiorcy Template: 2/);
  assert.match(dialog.textContent,/nie jest potwierdzone/);assert.ok(dialog.getAttribute('aria-labelledby'));
  f.card.hass={...f.hass,language:'en'};dialog=f.card.shadowRoot.querySelector('[role="dialog"]');
  assert.match(dialog.textContent,/Server changes/);assert.match(dialog.textContent,/Dependencies/);assert.match(dialog.textContent,/not confirmed/);
  assert.equal(dialog.querySelector('img'),null);assert.equal(f.requests.filter(m=>m.type==='ha_entity_renamer/prepare').length,1);
});

test('unsupported prepare refuses honestly and cannot silently use registry-only',async t=>{
  const f=fixture(t);f.handlers['ha_entity_renamer/prepare']=()=>Promise.reject({code:'unsupported_selection',message:'<private>'});
  await f.card._prepareMigration();assert.equal(f.card._migration.stage,'refused');
  assert.match(f.card.shadowRoot.textContent,/refused or could not be verified/);assert.ok(!f.card.shadowRoot.textContent.includes('<private>'));
  await f.card._executeRenames(true);await f.card._applyMigration();assert.equal(writes(f).length,0);
  f.card._cancelRenameConfirmation();assert.equal(f.card._migration,null);
});

test('malformed server review cannot offer apply',async t=>{
  const f=fixture(t);f.review.changes[0].fields=null;
  await f.card._prepareMigration();assert.equal(f.card._migration.stage,'refused');assert.equal(f.card.shadowRoot.getElementById('applyMigration'),null);
  assert.equal(writes(f).length,0);
});

test('prepare and review block all queue mutations and duplicate prepare',async t=>{
  const f=fixture(t);const pending=gate();f.handlers['ha_entity_renamer/prepare']=()=>pending.promise;
  const first=f.card._prepareMigration();await f.card._prepareMigration();
  const attempt=()=>{f.card._addToQueue('sensor.a','sensor.other');f.card._removeFromQueue('sensor.a');f.card._clearQueue();f.card._queueAutomaticIds();};
  attempt();assert.equal(f.card._renameQueue[0].newId,'sensor.b');assert.equal(f.requests.filter(m=>m.type==='ha_entity_renamer/prepare').length,1);
  pending.resolve(f.review);await first;attempt();assert.equal(f.card._renameQueue.length,1);
  assert.equal(f.card.shadowRoot.getElementById('clearQueue').disabled,true);assert.equal(f.card.shadowRoot.getElementById('executeRenames').disabled,true);
  f.card.shadowRoot.querySelector('[role="dialog"]').dispatchEvent(new f.dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
  assert.equal(f.card._migration,null);assert.equal(writes(f).length,0);
});

test('apply sends only retained identity once and refreshes registry plus journal and inspect',async t=>{
  const f=fixture(t);await f.card._prepareMigration();const pending=gate();
  f.handlers['ha_entity_renamer/apply']=()=>{f.entries[0].entity_id='sensor.b';return pending.promise;};
  const first=f.card._applyMigration();await f.card._applyMigration();await f.card._executeRenames(true);
  assert.equal(writes(f).length,1);assert.deepEqual(JSON.parse(JSON.stringify(writes(f)[0])),{type:'ha_entity_renamer/apply',operation_id:'review_qa',fingerprint});
  pending.resolve({operation_id:'review_qa',fingerprint,state:'applied'});await first;
  assert.equal(f.card._migration.stage,'applied');assert.equal(f.card._entities[0].entity_id,'sensor.b');
  assert.match(f.card.shadowRoot.textContent,/Verified steps: 1 \/ 2/);assert.match(f.card.shadowRoot.textContent,/Undone steps: 0/);
  assert.ok(f.card.shadowRoot.getElementById('inspectMigration'));assert.equal(f.requests.some(m=>m.type.endsWith('/restore')),false);
  f.card._finishMigration();assert.equal(f.card._renameQueue.length,0);assert.equal(f.card._migration,null);
});

test('lost apply response uses status/inspect only, retaining unknown and locking queue',async t=>{
  const f=fixture(t);await f.card._prepareMigration();
  f.handlers['ha_entity_renamer/apply']=()=>Promise.reject(new Error('lost response'));
  f.handlers['ha_entity_renamer/recovery/inspect']=()=>({operation_id:'review_qa',fingerprint,state:'recoverable_prefix',eligible:true});
  await f.card._applyMigration();assert.equal(f.card._migration.stage,'unknown');assert.match(f.card.shadowRoot.textContent,/Partial or unknown/);
  await f.card._applyMigration();await f.card._executeRenames(true);await f.card._inspectMigration();
  f.card._clearQueue();f.card._cancelRenameConfirmation();assert.equal(f.card._renameQueue.length,1);
  assert.equal(writes(f).length,1);assert.equal(f.card.shadowRoot.getElementById('finishMigration'),null);
  assert.equal(f.requests.filter(m=>m.type==='ha_entity_renamer/recovery/inspect').length,2);
});

test('unobserved registry read cannot turn an ACK or applied inspect into complete',async t=>{
  const f=fixture(t);await f.card._prepareMigration();f.handlers['config/entity_registry/list']=()=>Promise.reject(new Error('offline'));
  await f.card._applyMigration();assert.equal(f.card._migration.stage,'unknown');assert.equal(f.card._migration.registryRead,false);
  f.card._finishMigration();assert.equal(f.card._renameQueue.length,1);assert.equal(writes(f).length,1);
});

for(const fence of ['account','role','connection','disconnect']) test('late prepare cannot revive review after '+fence,async t=>{
  const f=fixture(t);const pending=gate();f.handlers['ha_entity_renamer/prepare']=()=>pending.promise;
  const first=f.card._prepareMigration();
  if(fence==='account') f.card.hass={...f.hass,user:{id:'other-admin',is_admin:true}};
  if(fence==='role') f.card.hass={...f.hass,user:{id:'admin-a',is_admin:false}};
  if(fence==='connection') {const connection=new f.dom.window.EventTarget();connection.connected=true;f.card.hass={...f.hass,connection};}
  if(fence==='disconnect') {f.connection.connected=false;f.connection.dispatchEvent(new f.dom.window.Event('disconnected'));}
  pending.resolve(f.review);await first;await tick();
  assert.equal(f.card._migration,null);assert.equal(f.card.shadowRoot.getElementById('applyMigration'),null);assert.equal(writes(f).length,0);
});

test('disconnect after apply consumes intent, ignores late ACK and reconnect offers reads only',async t=>{
  const f=fixture(t);await f.card._prepareMigration();const pending=gate();f.handlers['ha_entity_renamer/apply']=()=>pending.promise;
  const first=f.card._applyMigration();f.connection.connected=false;f.connection.dispatchEvent(new f.dom.window.Event('disconnected'));
  pending.resolve({operation_id:'review_qa',fingerprint,state:'applied'});await first;
  assert.equal(f.card._migration.stage,'unknown');assert.equal(f.card._migration.applyState,undefined);
  f.connection.connected=true;f.connection.dispatchEvent(new f.dom.window.Event('ready'));await tick();
  await f.card._applyMigration();await f.card._executeRenames(true);await f.card._inspectMigration();
  assert.equal(writes(f).length,1);assert.equal(f.card.shadowRoot.getElementById('applyMigration'),null);
});

test('late status and inspect cannot populate a different account',async t=>{
  const f=fixture(t);await f.card._prepareMigration();const pending=gate();
  f.handlers['ha_entity_renamer/recovery/inspect']=()=>pending.promise;
  const first=f.card._applyMigration();await tick();f.card.hass={...f.hass,user:{id:'other-admin',is_admin:true}};
  pending.resolve({operation_id:'review_qa',fingerprint,state:'applied',change_summary:{name:'private previous actor'}});await first;await tick();
  assert.equal(f.card._migration,null);assert.ok(!f.card.shadowRoot.textContent.includes('private previous actor'));assert.equal(writes(f).length,1);
});


test('pending apply reads journal progress without timing out or repeating the write',async t=>{
  const f=fixture(t);await f.card._prepareMigration();const pending=gate();
  f.handlers['ha_entity_renamer/apply']=()=>pending.promise;
  const first=f.card._applyMigration();await tick();
  assert.equal(f.card._migration.stage,'applying');
  assert.match(f.card.shadowRoot.textContent,/Verified steps: 1 \/ 2/);
  assert.equal(f.requests.filter(m=>m.type==='ha_entity_renamer/status').length,1);
  await f.card._applyMigration();assert.equal(writes(f).length,1);
  pending.resolve({operation_id:'review_qa',fingerprint,state:'applied'});await first;
  assert.equal(f.card._migration.applying,false);assert.equal(writes(f).length,1);
});

test('a detached registry-only button cannot dismiss a prepared migration or write',async t=>{
  const f=fixture(t);const oldButton=f.card.shadowRoot.getElementById('confirmRenameApply');
  await f.card._prepareMigration();oldButton.click();f.card.render();
  assert.ok(f.card.shadowRoot.getElementById('applyMigration'));assert.equal(writes(f).length,0);
});


test('mixed queue sends only ordinary name fields and review includes server device changes',async t=>{
  const f=fixture(t);f.card._renameQueue[0].newName='Literal <name>';f.card._deviceRenameQueue={dev:'Literal <device>'};f.card._showRenameConfirmation();
  f.review.changes.push({device_id:'dev',fields:{name_by_user:{before:null,after:'Literal <device>'}}});
  await f.card._prepareMigration();const sent=f.requests.find(m=>m.type==='ha_entity_renamer/prepare');
  assert.deepEqual(JSON.parse(JSON.stringify(sent.entities)),[{entity_id:'sensor.a',new_entity_id:'sensor.b',name:'Literal <name>'}]);
  assert.deepEqual(JSON.parse(JSON.stringify(sent.devices)),[{device_id:'dev',name_by_user:'Literal <device>'}]);
  assert.ok(f.card.shadowRoot.querySelector('[role="dialog"]').textContent.includes('Literal <device>'));
  assert.equal(f.card.shadowRoot.querySelector('[role="dialog"] device'),null);assert.equal(writes(f).length,0);
});

test('cancel during read-only prepare releases queue and ignores its late review',async t=>{
  const f=fixture(t);const pending=gate();f.handlers['ha_entity_renamer/prepare']=()=>pending.promise;
  const first=f.card._prepareMigration();await tick();
  f.card._cancelRenameConfirmation();assert.equal(f.card._migration,null);
  f.card._addToQueue('sensor.a','sensor.other');
  pending.resolve(f.review);await first;
  assert.equal(f.card._migration,null);assert.equal(f.card.shadowRoot.getElementById('applyMigration'),null);
  assert.equal(writes(f).length,0);
});

test('replacement of queued source identity refuses before preparing migration',async t=>{
  const f=fixture(t);
  f.card._renameQueue[0].sourceIdentity=f.card._sourceIdentity(f.entries[0]);
  f.card._showRenameConfirmation();
  f.entries[0]={...f.entries[0],id:'foreign-replacement',unique_id:'foreign'};
  await f.card._prepareMigration();
  assert.equal(f.card._migration.stage,'refused');
  assert.equal(f.requests.some(m=>m.type==='ha_entity_renamer/prepare'),false);
  assert.equal(writes(f).length,0);
});

test('changed automatic target refuses before preparing migration',async t=>{
  const f=fixture(t);
  Object.assign(f.card._renameQueue[0],{automatic:true,uniqueId:'a',deviceId:'dev',sourceIdentity:f.card._sourceIdentity(f.entries[0])});
  f.card._showRenameConfirmation();
  f.handlers['config/entity_registry/get_automatic_entity_ids']=()=>({'sensor.a':'sensor.another_target'});
  await f.card._prepareMigration();
  assert.equal(f.card._migration.stage,'refused');
  assert.equal(f.requests.some(m=>m.type==='ha_entity_renamer/prepare'),false);
  assert.equal(writes(f).length,0);
});

test('unassigned source is reachable through ordinary list and editor without a synthetic device',async t=>{
  const f=fixture(t);f.card._cancelRenameConfirmation();f.card._renameQueue=[];
  f.entries[0].device_id=null;f.card._entities=f.entries.map(r=>({...r}));f.card._deviceEntities={};f.card._searchQuery='sensor.a';
  const groups=f.card._getFilteredDevices();assert.equal(groups.length,1);assert.equal(groups[0].unassigned,true);
  assert.equal(f.card._devices.length,1,'real device inventory unchanged');
  f.card._expandedDevices.add(groups[0].id);f.card._selectedDevice=groups[0].id;f.card.setActiveTab('devices');
  assert.match(f.card.shadowRoot.textContent,/No device/);
  assert.match(f.card.shadowRoot.querySelector('[data-list-filter=all]').textContent,/All \(1\)/);
  assert.match(f.card.shadowRoot.querySelector('[data-list-filter=available]').textContent,/To queue \(1\)/);
  assert.equal(f.card.shadowRoot.getElementById('applyDeviceName'),null);
  f.card.shadowRoot.querySelector('[data-add-single="sensor.a"]').click();
  f.card.shadowRoot.getElementById('entityEditObjectId').value='next';
  f.card.shadowRoot.getElementById('queueEntityEdit').click();
  assert.equal(f.card._renameQueue[0].newId,'sensor.next');assert.equal(writes(f).length,0);
  f.card.hass={...f.hass,language:'pl'};assert.match(f.card.shadowRoot.textContent,/Bez urządzenia/);
});

test('dependency files and every reference change are visible; missing file review refuses',async t=>{
  const f=fixture(t);await f.card._prepareMigration();
  const dialog=f.card.shadowRoot.querySelector('[role="dialog"]');
  for(const name of ['template.yaml','automation.yaml','script.yaml']) assert.ok(dialog.textContent.includes(name));
  assert.equal(dialog.querySelectorAll('details li').length,3);
  f.card._cancelRenameConfirmation();f.card._showRenameConfirmation();
  f.review.dependencies.files.pop();await f.card._prepareMigration();
  assert.equal(f.card._migration.stage,'refused');assert.equal(writes(f).length,0);
});

const restores = f => f.requests.filter(m=>m.type==='ha_entity_renamer/recovery/restore');
async function appliedFixture(t, language='en') {
  const f=fixture(t,language);await f.card._prepareMigration();await f.card._applyMigration();
  assert.equal(f.card._migration.stage,'applied');return f;
}
const restoreAck = () => ({operation_id:'review_qa',fingerprint,state:'rolled_back'});
function finishedInspection(f) {
  f.handlers['ha_entity_renamer/recovery/inspect']=()=>({operation_id:'review_qa',fingerprint,state:'refused',reason:'already_finished',eligible:false});
  f.handlers['ha_entity_renamer/status']=()=>({operations:[{operation_id:'review_qa',fingerprint,status:'rolled_back'}]});
}

test('restore review reverses registry and dependency deltas with escaping, PL/EN and focused Cancel',async t=>{
  const f=fixture(t,'pl');const literal='<img src=x onerror="alert(1)">';
  f.review.changes[0].fields.name={before:null,after:literal};
  f.review.dependencies.files[0].path=literal+'.yaml';
  f.review.dependencies.files[0].references[0]={before:'sensor.<old>',after:'sensor.<new>',occurrences:3};
  f.handlers['ha_entity_renamer/apply']=()=>{Object.assign(f.entries[0],{entity_id:'sensor.b',name:literal});return {operation_id:'review_qa',fingerprint,state:'applied'};};
  await f.card._prepareMigration();await f.card._applyMigration();
  const retained=JSON.stringify(f.card._migration.review);
  f.card.shadowRoot.getElementById('reviewMigrationRestore').click();
  let dialog=f.card.shadowRoot.querySelector('[role="dialog"]');
  assert.match(dialog.textContent,/Wycofaj tę operację/);assert.equal(dialog.querySelector('img'),null);
  assert.equal(f.card.shadowRoot.activeElement.id,'cancelMigrationRestore');
  const rows=[...dialog.querySelectorAll('tbody tr')].map(r=>[...r.children].map(c=>c.textContent));
  assert.deepEqual(rows.map(r=>r.slice(2)),[['sensor.b','sensor.a'],[literal,'(domyślna)']]);
  assert.ok(dialog.textContent.includes(literal+'.yaml'));
  assert.match(dialog.querySelector('details li').textContent,/sensor\.<new>.*sensor\.<old>.*3/);
  assert.equal(dialog.querySelectorAll('details').length,3);assert.equal(restores(f).length,0);
  dialog.dispatchEvent(new f.dom.window.KeyboardEvent('keydown',{key:'Tab',bubbles:true}));
  assert.equal(f.card.shadowRoot.activeElement.id,'restoreMigration');
  f.card.hass={...f.hass,language:'en'};dialog=f.card.shadowRoot.querySelector('[role="dialog"]');
  assert.match(dialog.textContent,/Restore this operation/);assert.equal(dialog.querySelector('img'),null);
  assert.equal(JSON.stringify(f.card._migration.review),retained);
  dialog.dispatchEvent(new f.dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
  assert.equal(f.card._migration.restoreConsent,false);assert.equal(restores(f).length,0);
  f.card._reviewMigrationRestore();f.card.shadowRoot.getElementById('cancelMigrationRestore').click();
  assert.equal(restores(f).length,0);assert.equal(f.card._renameQueue.length,1);
});

test('restore requires exact applied eligible inspection and current role/session',async t=>{
  const f=await appliedFixture(t);const m=f.card._migration;
  for(const override of [{operation_id:'other'},{fingerprint:'b'.repeat(64)},{eligible:false},{eligible:1},{state:'recoverable_prefix'}]) {
    const previous=m.inspection;m.inspection={...previous,...override};
    f.card._reviewMigrationRestore();await f.card._restoreMigration();assert.equal(m.restoreConsent,false);m.inspection=previous;
  }
  m.stage='unknown';f.card._reviewMigrationRestore();assert.equal(m.restoreConsent,false);m.stage='applied';
  f.card.hass={...f.hass,user:{id:'admin-a',is_admin:false}};
  await f.card._restoreMigration();assert.equal(restores(f).length,0);
});

test('restore sends identity once, consumes consent before IO and requires a separate fresh original read',async t=>{
  const f=await appliedFixture(t);const pending=gate(), read=gate();
  f.handlers['ha_entity_renamer/recovery/restore']=()=>pending.promise;
  const statusBefore=f.requests.filter(m=>m.type==='ha_entity_renamer/status').length;
  f.card._reviewMigrationRestore();const first=f.card._restoreMigration();
  assert.ok(f.requests.filter(m=>m.type==='ha_entity_renamer/status').length>statusBefore);
  assert.equal(f.card._migration.restoreConsent,false);assert.equal(f.card._migration.stage,'restoring');
  await f.card._restoreMigration();await f.card._applyMigration();await f.card._executeRenames(true);
  f.card._clearQueue();assert.equal(f.card._renameQueue.length,1);
  assert.equal(restores(f).length,1);assert.deepEqual(JSON.parse(JSON.stringify(restores(f)[0])),{type:'ha_entity_renamer/recovery/restore',operation_id:'review_qa',fingerprint});
  f.entries[0].entity_id='sensor.a';finishedInspection(f);
  f.handlers['config/entity_registry/list']=()=>read.promise;
  pending.resolve(restoreAck());await tick();assert.notEqual(f.card._migration.stage,'restored');
  read.resolve(f.entries.map(r=>({...r})));await first;
  assert.equal(f.card._migration.stage,'restored');assert.match(f.card.shadowRoot.textContent,/original registry fields independently verified/);
  f.handlers['config/entity_registry/list']=()=>f.entries.map(r=>({...r}));
  await f.card._inspectMigration();assert.equal(f.card._migration.stage,'restored');
  assert.equal(restores(f).length,1);assert.equal(writes(f).length,1);
  f.card._finishMigration();assert.equal(f.card._migration,null);assert.equal(f.card._renameQueue.length,0);
});

for(const failure of ['lost ACK','foreign ACK','refused','foreign registry','wrong original field','missing registry']) test('restore stays unknown after '+failure,async t=>{
  const f=await appliedFixture(t);
  f.handlers['ha_entity_renamer/recovery/restore']=()=>{
    f.entries[0].entity_id='sensor.a';finishedInspection(f);
    if(failure==='lost ACK') throw new Error('lost');
    if(failure==='foreign ACK') return {...restoreAck(),operation_id:'foreign'};
    if(failure==='refused') return {...restoreAck(),state:'refused'};
    if(failure==='foreign registry') Object.assign(f.entries[0],{id:'replacement',unique_id:'foreign'});
    if(failure==='wrong original field') f.entries[0].entity_id='sensor.wrong';
    if(failure==='missing registry') f.handlers['config/entity_registry/list']=()=>Promise.reject(new Error('offline'));
    return restoreAck();
  };
  f.card._reviewMigrationRestore();await f.card._restoreMigration();assert.equal(f.card._migration.stage,'unknown');
  await f.card._inspectMigration();f.card._reviewMigrationRestore();await f.card._restoreMigration();f.card._finishMigration();
  assert.equal(f.card._migration.stage,'unknown');assert.equal(f.card._renameQueue.length,1);
  assert.equal(restores(f).length,1);assert.equal(writes(f).length,1);
});

for(const fence of ['role','connection','disconnect']) test('restore revocation ignores late ACK and never revives restore consent: '+fence,async t=>{
  const f=await appliedFixture(t);const pending=gate();
  f.handlers['ha_entity_renamer/recovery/restore']=()=>pending.promise;
  f.card._reviewMigrationRestore();const first=f.card._restoreMigration();
  if(fence==='role') f.card.hass={...f.hass,user:{id:'admin-a',is_admin:false}};
  if(fence==='connection') {const connection=new f.dom.window.EventTarget();connection.connected=true;f.card.hass={...f.hass,connection};}
  if(fence==='disconnect') {f.connection.connected=false;f.connection.dispatchEvent(new f.dom.window.Event('disconnected'));}
  f.entries[0].entity_id='sensor.a';finishedInspection(f);pending.resolve(restoreAck());await first;
  if(fence==='role') assert.equal(f.card._migration,null);
  else {assert.equal(f.card._migration.stage,'unknown');assert.equal(f.card._migration.restoreAcknowledged,undefined);}
  f.connection.connected=true;f.card.hass={...f.hass};f.connection.dispatchEvent(new f.dom.window.Event('ready'));await tick();
  await f.card._inspectMigration();f.card._reviewMigrationRestore();await f.card._restoreMigration();
  if(fence==='role') assert.equal(f.card._migration,null);
  else assert.equal(f.card._migration.stage,'unknown');assert.equal(restores(f).length,1);
});

test('revoked confirmation and previously applied operation cannot authorize restore after read rebind',async t=>{
  const f=await appliedFixture(t);f.card._reviewMigrationRestore();
  f.card.hass={...f.hass,user:{id:'admin-a',is_admin:false}};
  f.card.hass={...f.hass};await f.card._inspectMigration();
  f.card._reviewMigrationRestore();await f.card._restoreMigration();
  assert.equal(restores(f).length,0);assert.equal(f.card._migration,null);
});


test('restored read verifies original entity name and device name in addition to ID',async t=>{
  const f=fixture(t);f.review.changes[0].fields.name={before:null,after:'Applied entity'};
  f.review.changes.push({device_id:'dev',fields:{name_by_user:{before:null,after:'Applied device'}}});
  f.handlers['ha_entity_renamer/apply']=()=>{Object.assign(f.entries[0],{entity_id:'sensor.b',name:'Applied entity'});f.devices[0].name_by_user='Applied device';return {operation_id:'review_qa',fingerprint,state:'applied'};};
  await f.card._prepareMigration();await f.card._applyMigration();
  f.handlers['ha_entity_renamer/recovery/restore']=()=>{Object.assign(f.entries[0],{entity_id:'sensor.a',name:null});finishedInspection(f);return restoreAck();};
  f.card._reviewMigrationRestore();await f.card._restoreMigration();assert.equal(f.card._migration.stage,'unknown');
  f.devices[0].name_by_user=null;f.entries[0].name='Foreign edit';await f.card._inspectMigration();assert.equal(f.card._migration.stage,'unknown');
  f.entries[0].name=null;await f.card._inspectMigration();assert.equal(f.card._migration.stage,'restored');assert.equal(restores(f).length,1);
});


async function completeUnknownFixture(t) {
  const f=fixture(t);await f.card._prepareMigration();
  f.handlers['ha_entity_renamer/apply']=()=>{f.entries[0].entity_id='sensor.b';throw new Error('uncertain final verifier');};
  f.handlers['ha_entity_renamer/status']=()=>({operations:[{operation_id:'review_qa',fingerprint,status:'unknown',direction:'forward'}]});
  f.handlers['ha_entity_renamer/recovery/inspect']=()=>({operation_id:'review_qa',fingerprint,state:'recoverable_complete',eligible:true});
  await f.card._applyMigration();return f;
}

test('complete UNKNOWN inspection offers current restore while keeping UNKNOWN and apply consumed',async t=>{
  const f=await completeUnknownFixture(t);const m=f.card._migration;
  assert.equal(m.stage,'unknown');assert.equal(f.card._canRestoreMigration(),true);
  assert.equal(f.card.shadowRoot.getElementById('reviewMigrationRestore').disabled,false);
  assert.equal(f.card.shadowRoot.getElementById('finishMigration'),null);
  await f.card._applyMigration();assert.equal(writes(f).length,1);
  f.card._finishMigration();assert.equal(f.card._renameQueue.length,1);
  f.handlers['ha_entity_renamer/recovery/restore']=()=>{f.entries[0].entity_id='sensor.a';finishedInspection(f);return restoreAck();};
  f.card._reviewMigrationRestore();await f.card._restoreMigration();
  assert.equal(m.stage,'restored');assert.equal(restores(f).length,1);
  await f.card._inspectMigration();f.card._reviewMigrationRestore();await f.card._restoreMigration();
  assert.equal(restores(f).length,1);assert.equal(writes(f).length,1);
});

for(const fence of ['operation','fingerprint','eligible','partial','registry','journal','role','connection']) test('complete UNKNOWN restore refuses '+fence,async t=>{
  const f=await completeUnknownFixture(t);const m=f.card._migration;
  if(fence==='operation') m.inspection.operation_id='foreign';
  if(fence==='fingerprint') m.inspection.fingerprint='b'.repeat(64);
  if(fence==='eligible') m.inspection.eligible=1;
  if(fence==='partial') m.inspection.state='recoverable_prefix';
  if(fence==='registry') m.registryMatches=false;
  if(fence==='journal') m.journal.status='applying';
  if(fence==='role') f.card.hass={...f.hass,user:{id:'admin-a',is_admin:false}};
  if(fence==='connection') {const connection=new f.dom.window.EventTarget();connection.connected=true;f.card.hass={...f.hass,connection};}
  f.card._reviewMigrationRestore();await f.card._restoreMigration();
  assert.equal(restores(f).length,0);assert.equal(writes(f).length,1);
});

test('complete UNKNOWN restore lost ACK never becomes success or a second write',async t=>{
  const f=await completeUnknownFixture(t);const m=f.card._migration;
  f.handlers['ha_entity_renamer/recovery/restore']=()=>{f.entries[0].entity_id='sensor.a';finishedInspection(f);throw new Error('lost ACK');};
  f.card._reviewMigrationRestore();await f.card._restoreMigration();
  assert.equal(m.stage,'unknown');
  await f.card._inspectMigration();f.card._reviewMigrationRestore();await f.card._restoreMigration();
  assert.equal(m.stage,'unknown');assert.equal(restores(f).length,1);assert.equal(writes(f).length,1);
});
