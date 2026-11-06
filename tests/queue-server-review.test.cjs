const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {JSDOM} = require('jsdom');
const fingerprint = 'a'.repeat(64);
const tick = () => new Promise(resolve => setImmediate(resolve));
function gate() { let resolve, reject; const promise = new Promise((a,b) => {resolve=a; reject=b;}); return {promise,resolve,reject}; }
async function fixture(t, language='en', connected=true) {
  const dom = new JSDOM('', {runScripts:'dangerously', pretendToBeVisual:true, url:'http://localhost/'});
  t.after(() => dom.window.close());
  dom.window.eval(readFileSync(join(__dirname,'../ha-entity-renamer.js'),'utf8'));
  const card=dom.window.document.createElement('ha-entity-renamer');
  card.setConfig({show_support:false}); dom.window.document.body.append(card);
  const requests=[], entries=[{id:'entry-a',entity_id:'sensor.a',device_id:'dev',unique_id:'a',platform:'mqtt',name:null}];
  const devices=[{id:'dev',name:'QA',name_by_user:null}];
  const review={operation_id:'review_qa',fingerprint,changes:[{entity_id:'sensor.a',fields:{entity_id:{before:'sensor.a',after:'sensor.b'}}}],
    dependencies:{domains:['template','automation','script'],yaml_file_count:3,template_consumer_count:2,files:['template.yaml','automation.yaml','script.yaml'].map(path=>({path,edit_count:1,references:[{before:'sensor.a',after:'sensor.b',occurrences:1}]}))},migration_ready:false,family_complete:false};
  const connection=new dom.window.EventTarget(); connection.connected=connected;
  const handlers={};
  const hass={language,connection,user:{id:'admin-a',is_admin:true},states:{},callWS:async message => {
    requests.push(message);
    if(handlers[message.type]) return handlers[message.type](message);
    if(message.type==='config/entity_registry/list') return entries.map(row=>({...row}));
    if(message.type==='config/device_registry/list') return devices.map(row=>({...row}));
    if(message.type==='ha_entity_renamer/prepare') return review;
    if(message.type==='ha_entity_renamer/apply') { entries[0].entity_id='sensor.b'; return {operation_id:review.operation_id,fingerprint,state:'applied'}; }
    if(message.type==='ha_entity_renamer/status') return {operations:[{operation_id:review.operation_id,fingerprint,status:'applied',phases:[{adapter:'registry',phase:'verified'}],progress:{total_steps:2,verified_steps:1,undone_steps:0}}]};
    if(message.type==='ha_entity_renamer/recovery/inspect') return {operation_id:review.operation_id,fingerprint,state:'applied',eligible:true,restore_attempt_available:true,migration_ready:false,family_complete:false};
    throw new Error('Unexpected '+message.type);
  }};
  // Exercise HA's real connected + public hass setter path, including registry reads.
  card.hass=hass;await tick();
  assert.equal(card._loading,false);
  assert.deepEqual(Array.from(card._entities, row=>row.entity_id),['sensor.a']);
  assert.deepEqual(Array.from(card._devices, row=>row.id),['dev']);
  card._renameQueue=[{oldId:'sensor.a',newId:'sensor.b'}];card._showRenameConfirmation();
  return {card,dom,hass,connection,requests,entries,devices,review,handlers};
}
const writes = f => f.requests.filter(m=>m.type==='ha_entity_renamer/apply'||m.type.endsWith('/update'));

for (const language of ['en','pl']) test(`write warning ${language}: cancel and escape preserve queue, continue applies once`,async t=>{
  const f=await fixture(t,language);await f.card._prepareMigration();
  f.card.shadowRoot.getElementById('applyMigration').click();
  let modal=f.card.shadowRoot.querySelector('.write-warning-backdrop [role="dialog"]');
  assert.equal(modal.getAttribute('aria-modal'),'true');
  assert.ok(f.card.shadowRoot.querySelector('.card').hasAttribute('inert'));
  assert.match(modal.textContent,language==='pl' ? /pełnej kopii zapasowej/ : /full Home Assistant backup/);
  assert.match(modal.textContent,language==='pl' ? /przerwać trwające automatyzacje i skrypty/ : /interrupt running automations and scripts/);
  assert.equal(modal.querySelector('a').href,'https://github.com/MacSiem/ha-entity-renamer/issues/new');
  assert.equal(f.card.shadowRoot.activeElement.id,'cancelWriteWarning');
  modal.dispatchEvent(new f.dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
  assert.equal(writes(f).length,0);assert.equal(f.card._renameQueue.length,1);
  assert.equal(f.card.shadowRoot.activeElement.id,'applyMigration');
  f.card.shadowRoot.getElementById('applyMigration').click();
  f.card.shadowRoot.getElementById('cancelWriteWarning').click();
  assert.equal(writes(f).length,0);assert.equal(f.card._renameQueue.length,1);
  f.card.shadowRoot.getElementById('applyMigration').click();
  f.card.shadowRoot.getElementById('continueWriteWarning').click();await tick();await tick();
  assert.equal(writes(f).length,1);assert.equal(f.card._migration.stage,'applied');
});

test('warning continue cannot revive consent after authority changes',async t=>{
  const f=await fixture(t);await f.card._prepareMigration();
  f.card.shadowRoot.getElementById('applyMigration').click();
  f.card.hass={...f.hass,user:{id:'different',is_admin:true}};
  f.card.shadowRoot.getElementById('continueWriteWarning').click();await tick();
  assert.equal(writes(f).length,0);assert.equal(f.card._migration,null);
});

test('restore warning cancels without issuing an inverse',async t=>{
  const f=await fixture(t);await f.card._prepareMigration();await f.card._applyMigration();
  f.card.shadowRoot.getElementById('reviewMigrationRestore').click();
  f.card.shadowRoot.getElementById('restoreMigration').click();
  assert.ok(f.card.shadowRoot.querySelector('.write-warning-backdrop'));
  f.card.shadowRoot.getElementById('cancelWriteWarning').click();
  assert.equal(f.requests.some(m=>m.type==='ha_entity_renamer/recovery/restore'),false);
  assert.equal(f.card.shadowRoot.activeElement.id,'restoreMigration');
});

test('ordinary confirmation offers only server review and cancel writes nothing',async t=>{
  const f=await fixture(t);assert.equal(f.card.shadowRoot.getElementById('confirmRenameApply'),null);
  f.card.shadowRoot.getElementById('cancelRenameDialog').click();
  assert.equal(writes(f).length,0);assert.equal(f.card._renameQueue.length,1);
  f.card._showRenameConfirmation();f.card.shadowRoot.getElementById('prepareMigration').click();await tick();
  assert.equal(f.card._migration.stage,'review');assert.equal(writes(f).length,0);
  const request=f.requests.find(m=>m.type==='ha_entity_renamer/prepare');
  assert.deepEqual(JSON.parse(JSON.stringify(request)),{type:'ha_entity_renamer/prepare',entities:[{entity_id:'sensor.a',new_entity_id:'sensor.b'}],devices:[]});
});

test('review displays every server change and all dependency aggregates with PL/EN escaping',async t=>{
  const f=await fixture(t,'pl');const literal='<img src=x onerror="alert(1)">';
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

test('review names admitted dashboard changes in both languages before apply',async t=>{
  const f=await fixture(t,'pl');f.review.dependencies.domains.push('dashboard');
  f.review.dependencies.dashboards=[{route:null,references:[{before:'sensor.a',after:'sensor.b',occurrences:1}]}];
  await f.card._prepareMigration();
  assert.match(f.card.shadowRoot.querySelector('[role="dialog"]').textContent,/Pulpity/);
  assert.match(f.card.shadowRoot.querySelector('[role="dialog"]').textContent,/Domyślny/);
  f.card.hass={...f.hass,language:'en'};
  assert.match(f.card.shadowRoot.querySelector('[role="dialog"]').textContent,/Dashboards/);
  assert.equal(writes(f).length,0);
});

test('server review displays specific scan limitations without presenting them as dependencies',async t=>{
  const f=await fixture(t,'pl');
  f.review.dependencies.coverage=[{source:'custom:example',status:'UNKNOWN',reason:'integration_code_not_enumerated',blockers:[]},
    {source:'external',status:'UNKNOWN',reason:'external_consumers_not_enumerated',blockers:[]}];
  await f.card._prepareMigration();
  let dialog=f.card.shadowRoot.querySelector('[role="dialog"]');
  assert.match(dialog.textContent,/Ograniczenia skanowania/);assert.match(dialog.textContent,/custom:example/);
  assert.match(dialog.textContent,/Sama ich obecność nie dowodzi zależności/);
  assert.ok(f.card.shadowRoot.getElementById('applyMigration'));assert.equal(writes(f).length,0);
  f.card.hass={...f.hass,language:'en'};dialog=f.card.shadowRoot.querySelector('[role="dialog"]');
  assert.match(dialog.textContent,/Scan limitations/);assert.match(dialog.textContent,/does not by itself prove a dependency/);
});

test('unsupported entry shows source and selected reference without a write',async t=>{
  const f=await fixture(t,'pl');
  f.handlers['ha_entity_renamer/prepare']=()=>Promise.reject({code:'unsupported_config_entry',
    message:JSON.stringify({sources:[{domain:'custom_example',entry_id:'entry-1',references:['sensor.a'],reasons:['unsupported_helper_reference']}]})});
  await f.card._prepareMigration();const dialog=f.card.shadowRoot.querySelector('[role="dialog"]');
  assert.match(dialog.textContent,/Wpis konfiguracji/);assert.match(dialog.textContent,/custom_example/);
  assert.match(dialog.textContent,/entry-1/);assert.match(dialog.textContent,/sensor.a/);
  assert.equal(f.card.shadowRoot.getElementById('applyMigration'),null);assert.equal(writes(f).length,0);
});

for (const language of ['en','pl']) test(`state trigger refusal names the affected automation and script ${language}`,async t=>{
  const f=await fixture(t,language);
  f.handlers['ha_entity_renamer/prepare']=()=>Promise.reject({code:'unsupported_script_state_trigger',
    message:JSON.stringify({automation:'automation.watcher',script:'script.selected'})});
  await f.card._prepareMigration();const dialog=f.card.shadowRoot.querySelector('[role="dialog"]');
  assert.match(dialog.textContent,language==='pl' ? /obserwuje stan tego skryptu/ : /monitors this script’s state/);
  assert.match(dialog.textContent,/automation\.watcher/);assert.match(dialog.textContent,/script\.selected/);
  assert.equal(f.card.shadowRoot.getElementById('applyMigration'),null);assert.equal(writes(f).length,0);
});

test('state trigger refusal does not render unvalidated server text',async t=>{
  const f=await fixture(t);f.handlers['ha_entity_renamer/prepare']=()=>Promise.reject({code:'unsupported_script_state_trigger',
    message:JSON.stringify({automation:'<img src=x>',script:'script.selected'})});
  await f.card._prepareMigration();const dialog=f.card.shadowRoot.querySelector('[role="dialog"]');
  assert.equal(dialog.querySelector('img'),null);assert.ok(!dialog.textContent.includes('<img'));
  assert.equal(f.card.shadowRoot.getElementById('applyMigration'),null);assert.equal(writes(f).length,0);
});

test('unsupported prepare refuses honestly and cannot silently use registry-only',async t=>{
  const f=await fixture(t);f.handlers['ha_entity_renamer/prepare']=()=>Promise.reject({code:'unsupported_selection',message:'<private>'});
  await f.card._prepareMigration();assert.equal(f.card._migration.stage,'refused');
  assert.match(f.card.shadowRoot.textContent,/refused or could not be verified/);assert.ok(!f.card.shadowRoot.textContent.includes('<private>'));
  await f.card._executeRenames(true);await f.card._applyMigration();assert.equal(writes(f).length,0);
  f.card._cancelRenameConfirmation();assert.equal(f.card._migration,null);
});

test('malformed server review cannot offer apply',async t=>{
  const f=await fixture(t);f.review.changes[0].fields=null;
  await f.card._prepareMigration();assert.equal(f.card._migration.stage,'refused');assert.equal(f.card.shadowRoot.getElementById('applyMigration'),null);
  assert.equal(writes(f).length,0);
});

test('prepare and review block all queue mutations and duplicate prepare',async t=>{
  const f=await fixture(t);const pending=gate();f.handlers['ha_entity_renamer/prepare']=()=>pending.promise;
  const first=f.card._prepareMigration();await f.card._prepareMigration();
  const attempt=()=>{f.card._addToQueue('sensor.a','sensor.other');f.card._removeFromQueue('sensor.a');f.card._clearQueue();f.card._queueAutomaticIds();};
  attempt();assert.equal(f.card._renameQueue[0].newId,'sensor.b');assert.equal(f.requests.filter(m=>m.type==='ha_entity_renamer/prepare').length,1);
  pending.resolve(f.review);await first;attempt();assert.equal(f.card._renameQueue.length,1);
  assert.equal(f.card.shadowRoot.getElementById('clearQueue').disabled,true);assert.equal(f.card.shadowRoot.getElementById('executeRenames').disabled,true);
  f.card.shadowRoot.querySelector('[role="dialog"]').dispatchEvent(new f.dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
  assert.equal(f.card._migration,null);assert.equal(writes(f).length,0);
});

test('apply sends only retained identity once and refreshes registry plus journal and inspect',async t=>{
  const f=await fixture(t);await f.card._prepareMigration();const pending=gate();
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
  const f=await fixture(t);await f.card._prepareMigration();
  f.handlers['ha_entity_renamer/apply']=()=>Promise.reject(new Error('lost response'));
  f.handlers['ha_entity_renamer/recovery/inspect']=()=>({operation_id:'review_qa',fingerprint,state:'recoverable_prefix',eligible:true});
  await f.card._applyMigration();assert.equal(f.card._migration.stage,'unknown');assert.match(f.card.shadowRoot.textContent,/Partial or unknown/);
  await f.card._applyMigration();await f.card._executeRenames(true);await f.card._inspectMigration();
  f.card._clearQueue();f.card._cancelRenameConfirmation();assert.equal(f.card._renameQueue.length,1);
  assert.equal(writes(f).length,1);assert.equal(f.card.shadowRoot.getElementById('finishMigration'),null);
  assert.equal(f.requests.filter(m=>m.type==='ha_entity_renamer/recovery/inspect').length,2);
});

test('unobserved registry read cannot turn an ACK or applied inspect into complete',async t=>{
  const f=await fixture(t);await f.card._prepareMigration();f.handlers['config/entity_registry/list']=()=>Promise.reject(new Error('offline'));
  await f.card._applyMigration();assert.equal(f.card._migration.stage,'unknown');assert.equal(f.card._migration.registryRead,false);
  f.card._finishMigration();assert.equal(f.card._renameQueue.length,1);assert.equal(writes(f).length,1);
});

for(const fence of ['account','role','connection','disconnect']) test('late prepare cannot revive review after '+fence,async t=>{
  const f=await fixture(t);const pending=gate();f.handlers['ha_entity_renamer/prepare']=()=>pending.promise;
  const first=f.card._prepareMigration();
  if(fence==='account') f.card.hass={...f.hass,user:{id:'other-admin',is_admin:true}};
  if(fence==='role') f.card.hass={...f.hass,user:{id:'admin-a',is_admin:false}};
  if(fence==='connection') {const connection=new f.dom.window.EventTarget();connection.connected=true;f.card.hass={...f.hass,connection};}
  if(fence==='disconnect') {f.connection.connected=false;f.connection.dispatchEvent(new f.dom.window.Event('disconnected'));}
  pending.resolve(f.review);await first;await tick();
  assert.equal(f.card._migration,null);assert.equal(f.card.shadowRoot.getElementById('applyMigration'),null);assert.equal(writes(f).length,0);
});

test('disconnect after apply consumes intent, ignores late ACK and reconnect offers reads only',async t=>{
  const f=await fixture(t);await f.card._prepareMigration();const pending=gate();f.handlers['ha_entity_renamer/apply']=()=>pending.promise;
  const first=f.card._applyMigration();f.connection.connected=false;f.connection.dispatchEvent(new f.dom.window.Event('disconnected'));
  pending.resolve({operation_id:'review_qa',fingerprint,state:'applied'});await first;
  assert.equal(f.card._migration.stage,'unknown');assert.equal(f.card._migration.applyState,undefined);
  f.connection.connected=true;f.connection.dispatchEvent(new f.dom.window.Event('ready'));await tick();
  await f.card._applyMigration();await f.card._executeRenames(true);await f.card._inspectMigration();
  assert.equal(writes(f).length,1);assert.equal(f.card.shadowRoot.getElementById('applyMigration'),null);
});

test('late status and inspect cannot populate a different account',async t=>{
  const f=await fixture(t);await f.card._prepareMigration();const pending=gate();
  f.handlers['ha_entity_renamer/recovery/inspect']=()=>pending.promise;
  const first=f.card._applyMigration();await tick();f.card.hass={...f.hass,user:{id:'other-admin',is_admin:true}};
  pending.resolve({operation_id:'review_qa',fingerprint,state:'applied',change_summary:{name:'private previous actor'}});await first;await tick();
  assert.equal(f.card._migration,null);assert.ok(!f.card.shadowRoot.textContent.includes('private previous actor'));assert.equal(writes(f).length,1);
});


test('pending apply reads journal progress without timing out or repeating the write',async t=>{
  const f=await fixture(t);await f.card._prepareMigration();const pending=gate();
  f.handlers['ha_entity_renamer/apply']=()=>pending.promise;
  const first=f.card._applyMigration();await tick();
  assert.equal(f.card._migration.stage,'applying');
  assert.match(f.card.shadowRoot.textContent,/Verified steps: 1 \/ 2/);
  assert.equal(f.requests.filter(m=>m.type==='ha_entity_renamer/status').length,1);
  await f.card._applyMigration();assert.equal(writes(f).length,1);
  pending.resolve({operation_id:'review_qa',fingerprint,state:'applied'});await first;
  assert.equal(f.card._migration.applying,false);assert.equal(writes(f).length,1);
});

for (const kind of ['name', 'device']) test(`ordinary ${kind}-only queue uses the server review`,async t=>{
  const f=await fixture(t);
  f.card._renameQueue=kind==='name' ? [{oldId:'sensor.a',newName:'Reviewed name'}] : [];
  f.card._deviceRenameQueue=kind==='device' ? {dev:'Reviewed device'} : {};
  f.review.changes=kind==='name' ? [{entity_id:'sensor.a',fields:{name:{before:null,after:'Reviewed name'}}}]
    : [{device_id:'dev',fields:{name_by_user:{before:null,after:'Reviewed device'}}}];
  f.review.dependencies={domains:[],yaml_file_count:0,template_consumer_count:0,files:[]};
  f.card._showRenameConfirmation();f.card.shadowRoot.getElementById('prepareMigration').click();await tick();
  const sent=f.requests.find(m=>m.type==='ha_entity_renamer/prepare');
  assert.deepEqual(JSON.parse(JSON.stringify(sent.entities)),kind==='name' ? [{entity_id:'sensor.a',name:'Reviewed name'}] : []);
  assert.deepEqual(JSON.parse(JSON.stringify(sent.devices)),kind==='device' ? [{device_id:'dev',name_by_user:'Reviewed device'}] : []);
  assert.equal(f.card._migration.stage,'review');assert.equal(writes(f).length,0);
  assert.equal(f.card.shadowRoot.getElementById('confirmRenameApply'),null);
});

test('mixed queue sends only ordinary name fields and review includes server device changes',async t=>{
  const f=await fixture(t);f.card._renameQueue[0].newName='Literal <name>';f.card._deviceRenameQueue={dev:'Literal <device>'};f.card._showRenameConfirmation();
  f.review.changes.push({device_id:'dev',fields:{name_by_user:{before:null,after:'Literal <device>'}}});
  await f.card._prepareMigration();const sent=f.requests.find(m=>m.type==='ha_entity_renamer/prepare');
  assert.deepEqual(JSON.parse(JSON.stringify(sent.entities)),[{entity_id:'sensor.a',new_entity_id:'sensor.b',name:'Literal <name>'}]);
  assert.deepEqual(JSON.parse(JSON.stringify(sent.devices)),[{device_id:'dev',name_by_user:'Literal <device>'}]);
  assert.ok(f.card.shadowRoot.querySelector('[role="dialog"]').textContent.includes('Literal <device>'));
  assert.equal(f.card.shadowRoot.querySelector('[role="dialog"] device'),null);assert.equal(writes(f).length,0);
});

test('cancel during read-only prepare releases queue and ignores its late review',async t=>{
  const f=await fixture(t);const pending=gate();f.handlers['ha_entity_renamer/prepare']=()=>pending.promise;
  const first=f.card._prepareMigration();await tick();
  f.card._cancelRenameConfirmation();assert.equal(f.card._migration,null);
  f.card._addToQueue('sensor.a','sensor.other');
  pending.resolve(f.review);await first;
  assert.equal(f.card._migration,null);assert.equal(f.card.shadowRoot.getElementById('applyMigration'),null);
  assert.equal(writes(f).length,0);
});

test('replacement of queued source identity refuses before preparing migration',async t=>{
  const f=await fixture(t);
  f.card._renameQueue[0].sourceIdentity=f.card._sourceIdentity(f.entries[0]);
  f.card._showRenameConfirmation();
  f.entries[0]={...f.entries[0],id:'foreign-replacement',unique_id:'foreign'};
  await f.card._prepareMigration();
  assert.equal(f.card._migration.stage,'refused');
  assert.equal(f.requests.some(m=>m.type==='ha_entity_renamer/prepare'),false);
  assert.equal(writes(f).length,0);
});

test('changed automatic target refuses before preparing migration',async t=>{
  const f=await fixture(t);
  Object.assign(f.card._renameQueue[0],{automatic:true,uniqueId:'a',deviceId:'dev',sourceIdentity:f.card._sourceIdentity(f.entries[0])});
  f.card._showRenameConfirmation();
  f.handlers['config/entity_registry/get_automatic_entity_ids']=()=>({'sensor.a':'sensor.another_target'});
  await f.card._prepareMigration();
  assert.equal(f.card._migration.stage,'refused');
  assert.equal(f.requests.some(m=>m.type==='ha_entity_renamer/prepare'),false);
  assert.equal(writes(f).length,0);
});

test('unassigned source is reachable through ordinary list and editor without a synthetic device',async t=>{
  const f=await fixture(t);f.card._cancelRenameConfirmation();f.card._renameQueue=[];
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
  const f=await fixture(t);await f.card._prepareMigration();
  const dialog=f.card.shadowRoot.querySelector('[role="dialog"]');
  for(const name of ['template.yaml','automation.yaml','script.yaml']) assert.ok(dialog.textContent.includes(name));
  assert.equal(dialog.querySelectorAll('details li').length,3);
  f.card._cancelRenameConfirmation();f.card._showRenameConfirmation();
  f.review.dependencies.files.pop();await f.card._prepareMigration();
  assert.equal(f.card._migration.stage,'refused');assert.equal(writes(f).length,0);
});

const restores = f => f.requests.filter(m=>m.type==='ha_entity_renamer/recovery/restore');
async function appliedFixture(t, language='en') {
  const f=await fixture(t,language);await f.card._prepareMigration();await f.card._applyMigration();
  assert.equal(f.card._migration.stage,'applied');return f;
}
const restoreAck = () => ({operation_id:'review_qa',fingerprint,state:'rolled_back'});
function finishedInspection(f) {
  f.handlers['ha_entity_renamer/recovery/inspect']=()=>({operation_id:'review_qa',fingerprint,state:'refused',reason:'already_finished',eligible:false});
  f.handlers['ha_entity_renamer/status']=()=>({operations:[{operation_id:'review_qa',fingerprint,status:'rolled_back'}]});
}

test('restore review reverses registry and dependency deltas with escaping, PL/EN and focused Cancel',async t=>{
  const f=await fixture(t,'pl');const literal='<img src=x onerror="alert(1)">';
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
  const f=await fixture(t);f.review.changes[0].fields.name={before:null,after:'Applied entity'};
  f.review.changes.push({device_id:'dev',fields:{name_by_user:{before:null,after:'Applied device'}}});
  f.handlers['ha_entity_renamer/apply']=()=>{Object.assign(f.entries[0],{entity_id:'sensor.b',name:'Applied entity'});f.devices[0].name_by_user='Applied device';return {operation_id:'review_qa',fingerprint,state:'applied'};};
  await f.card._prepareMigration();await f.card._applyMigration();
  f.handlers['ha_entity_renamer/recovery/restore']=()=>{Object.assign(f.entries[0],{entity_id:'sensor.a',name:null});finishedInspection(f);return restoreAck();};
  f.card._reviewMigrationRestore();await f.card._restoreMigration();assert.equal(f.card._migration.stage,'unknown');
  f.devices[0].name_by_user=null;f.entries[0].name='Foreign edit';await f.card._inspectMigration();assert.equal(f.card._migration.stage,'unknown');
  f.entries[0].name=null;await f.card._inspectMigration();assert.equal(f.card._migration.stage,'restored');assert.equal(restores(f).length,1);
});


async function completeUnknownFixture(t) {
  const f=await fixture(t);await f.card._prepareMigration();
  f.handlers['ha_entity_renamer/apply']=()=>{f.entries[0].entity_id='sensor.b';throw new Error('uncertain final verifier');};
  f.handlers['ha_entity_renamer/status']=()=>({operations:[{operation_id:'review_qa',fingerprint,status:'unknown',direction:'forward'}]});
  f.handlers['ha_entity_renamer/recovery/inspect']=()=>({operation_id:'review_qa',fingerprint,state:'recoverable_complete',eligible:true,restore_attempt_available:true});
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

test('all verified applying steps remain pending final confirmation in both languages',async t=>{
  for(const language of ['en','pl']) {
    const f=await fixture(t,language);await f.card._prepareMigration();const pending=gate();
    f.handlers['ha_entity_renamer/apply']=()=>pending.promise;
    f.handlers['ha_entity_renamer/status']=()=>({operations:[{operation_id:'review_qa',fingerprint,status:'applying',direction:'forward',phases:[{adapter:'registry',phase:'verified'},{adapter:'yaml',phase:'verified'}],progress:{total_steps:2,verified_steps:2,undone_steps:0}}]});
    const first=f.card._applyMigration();await tick();
    const result=f.card.shadowRoot.querySelector('.apply-result');
    assert.equal(result.getAttribute('aria-busy'),'true');assert.match(result.textContent,/2 \/ 2/);
    assert.match(result.textContent,language==='pl'?/Liczba zweryfikowanych kroków nie potwierdza końcowego wyniku/:/Verified step count does not confirm the final outcome/);
    assert.equal(f.card.shadowRoot.getElementById('finishMigration'),null);
    assert.equal(f.card.shadowRoot.getElementById('reviewMigrationRestore').disabled,true);
    pending.reject(new Error('uncertain'));await first;
  }
});

test('unknown complete remains unknown and recovery guidance follows fresh eligible inspection',async t=>{
  const f=await completeUnknownFixture(t);const m=f.card._migration;
  assert.match(f.card.shadowRoot.textContent,/Partial or unknown/);
  assert.match(f.card.shadowRoot.textContent,/Fresh inspection allows review of this operation’s restore scope/);
  assert.equal(m.stage,'unknown');assert.equal(f.card.shadowRoot.getElementById('reviewMigrationRestore').disabled,false);
  const retained=JSON.stringify(m.review);f.card.render();assert.equal(JSON.stringify(m.review),retained);
  m.inspection.eligible=false;f.card.render();
  assert.match(f.card.shadowRoot.textContent,/Restore is unavailable until a fresh inspection permits it/);
  assert.equal(f.card.shadowRoot.getElementById('reviewMigrationRestore').disabled,true);
  assert.equal(writes(f).length,1);
});

test('only matching current status is shown and server phases are explained without treating counts as success',async t=>{
  const f=await fixture(t);await f.card._prepareMigration();const pending=gate();
  f.handlers['ha_entity_renamer/apply']=()=>pending.promise;
  f.handlers['ha_entity_renamer/status']=()=>({operations:[{operation_id:'other',fingerprint,status:'applied',phases:[{adapter:'foreign',phase:'verified'}],progress:{total_steps:2,verified_steps:2,undone_steps:0}},{operation_id:'review_qa',fingerprint,status:'applying',direction:'forward',phases:[{adapter:'registry',phase:'intent'},{adapter:'yaml',phase:'pending'}],progress:{total_steps:2,verified_steps:0,undone_steps:0}}]});
  const first=f.card._applyMigration();await tick();let text=f.card.shadowRoot.getElementById('migrationJournal').textContent;
  assert.match(text,/applying/);assert.match(text,/intent/);assert.match(text,/pending/);assert.doesNotMatch(text,/foreign/);
  f.handlers['ha_entity_renamer/status']=()=>({operations:[{operation_id:'review_qa',fingerprint:'b'.repeat(64),status:'applied',progress:{total_steps:2,verified_steps:2,undone_steps:0}}]});
  await f.card._refreshMigrationProgress();text=f.card.shadowRoot.getElementById('migrationJournal').textContent;
  assert.match(text,/unavailable/);assert.doesNotMatch(text,/2 \/ 2/);
  pending.reject(new Error('uncertain'));await first;
});

test('late progress from before inspection cannot replace a fresh journal',async t=>{
  const f=await fixture(t);await f.card._prepareMigration();const apply=gate(),oldStatus=gate();let calls=0;
  f.handlers['ha_entity_renamer/apply']=()=>apply.promise;
  f.handlers['ha_entity_renamer/status']=()=>++calls===1?oldStatus.promise:{operations:[{operation_id:'review_qa',fingerprint,status:'unknown',direction:'forward'}]};
  const first=f.card._applyMigration();await tick();
  apply.reject(new Error('uncertain'));await first;
  assert.equal(f.card._migration.journal.status,'unknown');
  oldStatus.resolve({operations:[{operation_id:'review_qa',fingerprint,status:'applied',progress:{total_steps:2,verified_steps:2,undone_steps:0}}]});await tick();
  assert.equal(f.card._migration.journal.status,'unknown');assert.equal(f.card._migration.stage,'unknown');
  assert.doesNotMatch(f.card.shadowRoot.getElementById('migrationJournal').textContent,/2 \/ 2/);
});

test('failed progress read no longer presents a stale snapshot as current',async t=>{
  const f=await fixture(t);await f.card._prepareMigration();const pending=gate();
  f.handlers['ha_entity_renamer/apply']=()=>pending.promise;
  const first=f.card._applyMigration();await tick();assert.match(f.card.shadowRoot.getElementById('migrationJournal').textContent,/1 \/ 2/);
  f.handlers['ha_entity_renamer/status']=()=>Promise.reject(new Error('offline'));
  await f.card._refreshMigrationProgress();assert.match(f.card.shadowRoot.getElementById('migrationJournal').textContent,/unavailable/);
  assert.doesNotMatch(f.card.shadowRoot.getElementById('migrationJournal').textContent,/1 \/ 2/);
  pending.reject(new Error('uncertain'));await first;
});

test('server inventory rediscovers a lost card without storage and never authorizes a write',async t=>{
  const f=await fixture(t);const storage=f.dom.window.Storage.prototype;const original=storage.setItem;
  storage.setItem=()=>{throw new Error('blocked');};t.after(()=>{storage.setItem=original;});
  f.handlers['ha_entity_renamer/status']=()=>({operations:[{operation_id:'old_qa',fingerprint,status:'prepared',direction:'forward'}]});
  await f.card._findMigrationRecovery();const button=f.card.shadowRoot.querySelector('[data-recovery-index]');
  assert.match(button.textContent,/old_qa.*prepared/);button.click();
  assert.equal(f.card._migration.stage,'unknown');assert.equal(f.card._migration.restoreRevoked,true);
  assert.equal(f.card.shadowRoot.getElementById('reviewMigrationRestore').disabled,true);
  assert.ok(f.card.shadowRoot.querySelector('.apply-result.migration-pending'));assert.equal(writes(f).length,0);
});

test('recovered complete operation needs fresh restore confirmation after inspection',async t=>{
  const f=await fixture(t);f.handlers['ha_entity_renamer/status']=()=>({operations:[{operation_id:'review_qa',fingerprint,status:'unknown',direction:'forward'}]});
  f.entries[0].entity_id='sensor.b';
  f.handlers['ha_entity_renamer/recovery/inspect']=()=>({operation_id:'review_qa',fingerprint,state:'recoverable_complete',eligible:true,restore_attempt_available:true,
    frozen_review:{changes:f.review.changes,dependencies:f.review.dependencies,identities:[['entry-a','mqtt','a','dev']]}});
  await f.card._findMigrationRecovery();f.card.shadowRoot.querySelector('[data-recovery-index]').click();await f.card._inspectMigration();
  assert.equal(f.card._migration.stage,'unknown');assert.match(f.card.shadowRoot.textContent,/Admitted original review/);
  assert.match(f.card.shadowRoot.textContent,/template.yaml/);assert.equal(f.card.shadowRoot.getElementById('reviewMigrationRestore').disabled,false);
  f.card._reviewMigrationRestore();assert.equal(f.card._migration.restoreConsent,true);
  assert.equal(writes(f).length,0);assert.equal(restores(f).length,0);
});

test('late inventory response cannot populate a different account',async t=>{
  const f=await fixture(t);const pending=gate();f.handlers['ha_entity_renamer/status']=()=>pending.promise;
  const read=f.card._findMigrationRecovery();f.card.hass={...f.hass,user:{id:'other-admin',is_admin:true}};
  pending.resolve({operations:[{operation_id:'old_qa',fingerprint,status:'unknown'}]});await read;
  assert.doesNotMatch(f.card.shadowRoot.textContent,/old_qa/);assert.equal(f.card._migration,null);
});

test('unavailable browser storage does not block ordinary reviewed apply',async t=>{
  const f=await fixture(t);await f.card._prepareMigration();const storage=f.dom.window.Storage.prototype;const original=storage.setItem;
  storage.setItem=()=>{throw new Error('blocked');};t.after(()=>{storage.setItem=original;});
  await f.card._applyMigration();assert.equal(writes(f).length,1);
});

test('recovered legacy or consumed attempt never offers another restore',async t=>{
  for(const available of [false,undefined]) {
    const f=await fixture(t);f.entries[0].entity_id='sensor.b';
    f.handlers['ha_entity_renamer/status']=()=>({operations:[{operation_id:'review_qa',fingerprint,status:'applied',direction:'forward'}]});
    f.handlers['ha_entity_renamer/recovery/inspect']=()=>({operation_id:'review_qa',fingerprint,state:'applied',eligible:true,restore_attempt_available:available,
      frozen_review:{changes:f.review.changes,dependencies:f.review.dependencies,identities:[['entry-a','mqtt','a','dev']]}});
    await f.card._findMigrationRecovery();f.card.shadowRoot.querySelector('[data-recovery-index]').click();await f.card._inspectMigration();
    assert.equal(f.card.shadowRoot.getElementById('reviewMigrationRestore').disabled,true);
    f.card._reviewMigrationRestore();await f.card._restoreMigration();assert.equal(restores(f).length,0);
  }
});

test('recreated card can finish lost restore ACK only with server terminal proof and original identity',async t=>{
  const f=await fixture(t);f.handlers['ha_entity_renamer/status']=()=>({operations:[{operation_id:'review_qa',fingerprint,status:'rolled_back',direction:'rollback'}]});
  f.handlers['ha_entity_renamer/recovery/inspect']=()=>({operation_id:'review_qa',fingerprint,state:'rolled_back',reason:'restored_verified',eligible:false,
    frozen_review:{changes:f.review.changes,dependencies:f.review.dependencies,identities:[['entry-a','mqtt','a','dev']]}});
  await f.card._findMigrationRecovery();f.card.shadowRoot.querySelector('[data-recovery-index]').click();await f.card._inspectMigration();
  assert.equal(f.card._migration.stage,'restored');assert.ok(f.card.shadowRoot.getElementById('finishMigration'));
  f.card._finishMigration();assert.equal(f.card._migration,null);assert.equal(restores(f).length,0);
});

test('restored server proof cannot finish when original registry identity changed',async t=>{
  const f=await fixture(t);f.entries[0].unique_id='foreign';
  f.handlers['ha_entity_renamer/status']=()=>({operations:[{operation_id:'review_qa',fingerprint,status:'rolled_back',direction:'rollback'}]});
  f.handlers['ha_entity_renamer/recovery/inspect']=()=>({operation_id:'review_qa',fingerprint,state:'rolled_back',reason:'restored_verified',eligible:false,
    frozen_review:{changes:f.review.changes,dependencies:f.review.dependencies,identities:[['entry-a','mqtt','a','dev']]}});
  await f.card._findMigrationRecovery();f.card.shadowRoot.querySelector('[data-recovery-index]').click();await f.card._inspectMigration();
  assert.equal(f.card._migration.stage,'unknown');assert.equal(f.card.shadowRoot.getElementById('finishMigration'),null);
});

test('only server-proven prewrite refusal finishes and preserves the queue',async t=>{
  const f=await fixture(t);await f.card._prepareMigration();
  f.handlers['ha_entity_renamer/apply']=()=>Promise.reject(new Error('prewrite'));
  f.handlers['ha_entity_renamer/recovery/inspect']=()=>({operation_id:'review_qa',fingerprint,state:'refused',reason:'prewrite_refused',eligible:false});
  await f.card._applyMigration();assert.equal(f.card._migration.stage,'refused');
  assert.match(f.card.shadowRoot.textContent,/confirmed refusal before applying/);
  f.card._finishMigration();assert.equal(f.card._migration,null);assert.equal(f.card._renameQueue.length,1);assert.equal(writes(f).length,1);
});

test('replacement requires fresh admission and separate consent before inverse',async t=>{
  const f=await fixture(t);await f.card._prepareMigration();const pending=gate();f.handlers['ha_entity_renamer/apply']=()=>pending.promise;
  f.handlers['ha_entity_renamer/status']=()=>({operations:[{operation_id:'review_qa',fingerprint,status:'unknown',direction:'forward'}]});
  const first=f.card._applyMigration();await tick();f.card.remove();
  const card=f.dom.window.document.createElement('ha-entity-renamer');card.setConfig({show_support:false});f.dom.window.document.body.append(card);card.hass=f.hass;await tick();
  // A unique same-session operation is handed over directly, without inventory.
  assert.equal(card._migration?.operation_id,'review_qa');assert.equal(card._migration.fingerprint,fingerprint);
  assert.equal(card._migration.busy,true);assert.equal(card._migration.restoreRevoked,true);
  assert.equal(card.shadowRoot.getElementById('reviewMigrationRestore').disabled,true);
  await card._inspectMigration();await card._restoreMigration();
  assert.equal(f.requests.filter(r=>r.type==='ha_entity_renamer/recovery/inspect').length,0);
  assert.equal(restores(f).length,0);assert.equal(writes(f).length,1);
  pending.reject(new Error('late apply response'));await first;await tick();
  assert.equal(card._migration.busy,false);assert.equal(card._migration.inspection,undefined);
  assert.equal(card.shadowRoot.getElementById('reviewMigrationRestore').disabled,true);
  f.entries[0].entity_id='sensor.b';
  f.handlers['ha_entity_renamer/recovery/inspect']=()=>({operation_id:'review_qa',fingerprint,state:'recoverable_complete',eligible:true,restore_attempt_available:true,
    frozen_review:{changes:f.review.changes,dependencies:f.review.dependencies,identities:[['entry-a','mqtt','a','dev']]}});
  await card._inspectMigration();assert.equal(f.requests.filter(r=>r.type==='ha_entity_renamer/recovery/inspect').length,1);
  assert.equal(card.shadowRoot.getElementById('reviewMigrationRestore').disabled,false);
  assert.ok(!card._migration.restoreConsent);await card._restoreMigration();assert.equal(restores(f).length,0);
  card._reviewMigrationRestore();assert.equal(card._migration.restoreConsent,true);assert.equal(restores(f).length,0);
  f.handlers['ha_entity_renamer/recovery/restore']=()=>{f.entries[0].entity_id='sensor.a';finishedInspection(f);return restoreAck();};
  await card._restoreMigration();await card._restoreMigration();assert.equal(restores(f).length,1);
  assert.equal(writes(f).length,1);
});

test('foreign refusal and unproven restored journal cannot clear the operation',async t=>{
  const f=await fixture(t);await f.card._prepareMigration();
  f.handlers['ha_entity_renamer/apply']=()=>Promise.reject(new Error('lost response'));
  f.handlers['ha_entity_renamer/status']=()=>({operations:[{operation_id:'review_qa',fingerprint,status:'rolled_back'}]});
  f.handlers['ha_entity_renamer/recovery/inspect']=()=>({operation_id:'other',fingerprint,state:'refused',reason:'prewrite_refused'});
  await f.card._applyMigration();assert.equal(f.card._migration.stage,'unknown');assert.equal(f.card.shadowRoot.getElementById('finishMigration'),null);
  f.handlers['ha_entity_renamer/recovery/inspect']=()=>({operation_id:'review_qa',fingerprint,state:'UNKNOWN',reason:'restored_unproven'});
  await f.card._inspectMigration();assert.equal(f.card._migration.stage,'unknown');assert.equal(f.card.shadowRoot.getElementById('finishMigration'),null);
});

for (const state of ['applied','rolled_back']) test('finishing recovered '+state+' preserves unrelated queued entity, device name and impact',async t=>{
  const f=await fixture(t);f.entries[0].entity_id=state==='applied'?'sensor.b':'sensor.a';
  f.entries.push({id:'entry-c',entity_id:'sensor.c',device_id:'dev',unique_id:'c',platform:'mqtt',name:null});
  f.card._entities=f.entries.map(row=>({...row}));
  f.card._renameQueue=[{oldId:'sensor.c',newId:'sensor.d'}];f.card._deviceRenameQueue={dev:'Unrelated device name'};
  f.card._impactResults={'sensor.c':{automations:[],scripts:[],dashboards:[],scenes:[]}};const impact=f.card._impactResults;
  f.card._showRenameConfirmation();assert.ok(f.card._confirmation);assert.equal(f.card._confirmDialogOpen,true);
  f.handlers['ha_entity_renamer/status']=()=>({operations:[{operation_id:'review_qa',fingerprint,status:state,direction:state==='applied'?'forward':'rollback'}]});
  f.handlers['ha_entity_renamer/recovery/inspect']=()=>({operation_id:'review_qa',fingerprint,state,
    ...(state==='applied'?{eligible:true,restore_attempt_available:true}:{eligible:false,reason:'restored_verified'}),
    frozen_review:{changes:f.review.changes,dependencies:f.review.dependencies,identities:[['entry-a','mqtt','a','dev']]}});
  await f.card._findMigrationRecovery();f.card._entityEditor={oldId:'sensor.c'};
  f.card.shadowRoot.querySelector('[data-recovery-index]').click();
  assert.equal(f.card._confirmation,null);assert.equal(f.card._confirmDialogOpen,false);assert.equal(f.card._entityEditor,null);
  await f.card._inspectMigration();assert.equal(f.card._migration.stage,state==='applied'?'applied':'restored');
  assert.match(f.card.shadowRoot.getElementById('finishMigration').textContent,/Finish operation/);
  f.card._finishMigration();assert.equal(f.card._migration,null);
  assert.deepEqual(JSON.parse(JSON.stringify(f.card._renameQueue)),[{oldId:'sensor.c',newId:'sensor.d'}]);
  assert.deepEqual(JSON.parse(JSON.stringify(f.card._deviceRenameQueue)),{dev:'Unrelated device name'});
  assert.equal(f.card._impactResults,impact);assert.equal(writes(f).length,0);assert.equal(restores(f).length,0);
});

test('recovered operation shows changing journal while inspection waits and clears a failed read',async t=>{
  const f=await fixture(t);const inspect=gate();let state='prepared';
  const row=()=>({operation_id:'review_qa',fingerprint,status:state,direction:'forward',phases:[{adapter:'registry',phase:state==='prepared'?'pending':'intent'}],progress:{total_steps:2,verified_steps:0,undone_steps:0}});
  f.handlers['ha_entity_renamer/status']=()=>({operations:[row()]});
  f.handlers['ha_entity_renamer/recovery/inspect']=()=>inspect.promise;
  await f.card._findMigrationRecovery();f.card.shadowRoot.querySelector('[data-recovery-index]').click();
  assert.match(f.card.shadowRoot.getElementById('migrationJournal').textContent,/prepared/);
  const pending=f.card._inspectMigration();await tick();
  assert.match(f.card.shadowRoot.getElementById('migrationJournal').textContent,/pending/);
  state='applying';await f.card._refreshMigrationProgress();
  assert.match(f.card.shadowRoot.getElementById('migrationJournal').textContent,/applying/);
  assert.match(f.card.shadowRoot.getElementById('migrationJournal').textContent,/intent/);
  assert.doesNotMatch(f.card.shadowRoot.textContent,/Partial or unknown outcome|Apply observation: UNKNOWN/);
  assert.equal(f.card.shadowRoot.getElementById('reviewMigrationRestore').disabled,true);
  assert.equal(f.card.shadowRoot.getElementById('applyMigration'),null);
  f.handlers['ha_entity_renamer/status']=()=>Promise.reject(new Error('offline'));
  await f.card._refreshMigrationProgress();assert.match(f.card.shadowRoot.getElementById('migrationJournal').textContent,/unavailable/);
  assert.doesNotMatch(f.card.shadowRoot.getElementById('migrationJournal').textContent,/intent/);
  inspect.resolve({operation_id:'review_qa',fingerprint,state:'refused',reason:'prewrite_refused'});await pending;
  assert.equal(f.card._migration.stage,'refused');assert.equal(writes(f).length,0);
  f.card._finishMigration();assert.equal(f.card._migration,null);assert.equal(f.card._renameQueue.length,1);
});

test('late recovered status and inspection cannot update after disconnect or role change',async t=>{
  for(const change of ['disconnect','role']) {
    const f=await fixture(t),inspect=gate(),status=gate();let calls=0;
    f.handlers['ha_entity_renamer/status']=()=>++calls===1?{operations:[{operation_id:'review_qa',fingerprint,status:'prepared'}]}:status.promise;
    f.handlers['ha_entity_renamer/recovery/inspect']=()=>inspect.promise;
    await f.card._findMigrationRecovery();f.card.shadowRoot.querySelector('[data-recovery-index]').click();
    const pending=f.card._inspectMigration();await tick();
    if(change==='disconnect') f.card.remove(); else f.card.hass={...f.hass,user:{id:'admin-a',is_admin:false}};
    status.resolve({operations:[{operation_id:'review_qa',fingerprint,status:'applied'}]});
    inspect.resolve({operation_id:'review_qa',fingerprint,state:'refused',reason:'prewrite_refused'});await pending;await tick();
    assert.notEqual(f.card._migration?.stage,'refused');assert.notEqual(f.card._migration?.journal?.status,'applied');
    assert.equal(writes(f).length,0);
  }
});

test('first apply status starts once and a pending read cannot overlap within its observation',async t=>{
  const f=await fixture(t);await f.card._prepareMigration();const apply=gate(),status=gate();
  f.handlers['ha_entity_renamer/apply']=()=>apply.promise;
  f.handlers['ha_entity_renamer/status']=()=>status.promise;
  const operation=f.card._applyMigration();await tick();
  assert.equal(f.requests.filter(m=>m.type==='ha_entity_renamer/status').length,1);
  await f.card._refreshMigrationProgress();
  assert.equal(f.requests.filter(m=>m.type==='ha_entity_renamer/status').length,1);
  status.resolve({operations:[{operation_id:'review_qa',fingerprint,status:'applying',phases:[{adapter:'registry',phase:'intent'}]}]});await tick();
  assert.match(f.card.shadowRoot.getElementById('migrationJournal').textContent,/intent/);
  f.handlers['ha_entity_renamer/status']=()=>({operations:[{operation_id:'review_qa',fingerprint,status:'unknown'}]});
  apply.reject(new Error('uncertain'));await operation;
  assert.equal(f.card._migration.journal.status,'unknown');assert.equal(writes(f).length,1);
});

for (const order of ['old-first','new-first','reattach','repeat']) test('pending recovered inspection survives card lifecycle: '+order,async t=>{
  const f=await fixture(t),inspect=gate();
  f.handlers['ha_entity_renamer/status']=()=>({operations:[{operation_id:'review_qa',fingerprint,status:'applied',progress:{total_steps:2,verified_steps:2,undone_steps:0}}]});
  f.handlers['ha_entity_renamer/recovery/inspect']=()=>inspect.promise;
  await f.card._findMigrationRecovery();f.card.shadowRoot.querySelector('[data-recovery-index]').click();
  const oldRead=f.card._inspectMigration();await tick();
  const make=()=>{const c=f.dom.window.document.createElement('ha-entity-renamer');c.setConfig({show_support:false});f.dom.window.document.body.append(c);c.hass=f.hass;return c;};
  let card;if(order==='new-first') {card=make();f.card.remove();} else {f.card.remove();card=order==='reattach'?f.card:make();if(order==='reattach')f.dom.window.document.body.append(card);}
  if(order==='repeat'){card.remove();f.dom.window.document.body.append(card);card.remove();f.dom.window.document.body.append(card);}
  await tick();assert.equal(card._migration?.operation_id,'review_qa');assert.equal(card._migration.busy,true);
  assert.match(card.shadowRoot.getElementById('migrationJournal').textContent,/2 \/ 2/);
  assert.equal(card.shadowRoot.getElementById('reviewMigrationRestore').disabled,true);
  await card._inspectMigration();assert.equal(f.requests.filter(r=>r.type==='ha_entity_renamer/recovery/inspect').length,1);
  inspect.resolve({operation_id:'review_qa',fingerprint,state:'refused',reason:'prewrite_refused'});await oldRead;await tick();
  assert.equal(card._migration.busy,false);assert.equal(card._migration.stage,'unknown');assert.equal(card._migration.inspection,undefined);
  assert.equal(card.shadowRoot.getElementById('reviewMigrationRestore').disabled,true);assert.equal(writes(f).length,0);assert.equal(restores(f).length,0);
});

for (const change of ['account','role','connection','config']) test('recovery handoff is fenced by '+change,async t=>{
  const f=await fixture(t),inspect=gate();f.handlers['ha_entity_renamer/recovery/inspect']=()=>inspect.promise;
  f.handlers['ha_entity_renamer/status']=()=>({operations:[{operation_id:'review_qa',fingerprint,status:'applied'}]});
  await f.card._findMigrationRecovery();f.card.shadowRoot.querySelector('[data-recovery-index]').click();const oldRead=f.card._inspectMigration();await tick();f.card.remove();
  const card=f.dom.window.document.createElement('ha-entity-renamer');card.setConfig(change==='config'?{show_support:true}:{show_support:false});f.dom.window.document.body.append(card);
  const connection=new f.dom.window.EventTarget();connection.connected=true;
  card.hass=change==='account'?{...f.hass,user:{id:'other',is_admin:true}}:change==='role'?{...f.hass,user:{id:'admin-a',is_admin:false}}:change==='connection'?{...f.hass,connection}:f.hass;
  await tick();assert.equal(card._migration,null);assert.doesNotMatch(card.shadowRoot.textContent,/review_qa/);
  inspect.resolve({operation_id:'review_qa',fingerprint,state:'refused',reason:'prewrite_refused'});await oldRead;assert.equal(writes(f).length,0);
});

test('multiple replacement cards require explicit inventory choice and never guess',async t=>{
  const f=await fixture(t),inspect=gate();f.handlers['ha_entity_renamer/recovery/inspect']=()=>inspect.promise;
  f.handlers['ha_entity_renamer/status']=()=>({operations:[{operation_id:'review_qa',fingerprint,status:'applied'}]});
  await f.card._findMigrationRecovery();f.card.shadowRoot.querySelector('[data-recovery-index]').click();const oldRead=f.card._inspectMigration();await tick();
  const make=()=>{const c=f.dom.window.document.createElement('ha-entity-renamer');c.setConfig({show_support:false});f.dom.window.document.body.append(c);c.hass=f.hass;return c;};
  const a=make(),b=make();f.card.remove();await tick();assert.equal(a._migration,null);assert.equal(b._migration,null);
  await a._findMigrationRecovery();assert.match(a.shadowRoot.querySelector('[data-recovery-index]').textContent,/review_qa/);
  a.shadowRoot.querySelector('[data-recovery-index]').click();assert.equal(a._migration.busy,true);
  await a._inspectMigration();assert.equal(f.requests.filter(r=>r.type==='ha_entity_renamer/recovery/inspect').length,1);
  inspect.resolve({operation_id:'review_qa',fingerprint,state:'refused',reason:'prewrite_refused'});await oldRead;await tick();
  assert.equal(a._migration.busy,false);assert.equal(a._migration.inspection,undefined);assert.equal(writes(f).length,0);
});

for(const missing of ['user','id','role']) test('unresolved '+missing+' hides pending identity and same admin can recover without consent',async t=>{
  const f=await fixture(t),inspect=gate();f.handlers['ha_entity_renamer/recovery/inspect']=()=>inspect.promise;
  f.handlers['ha_entity_renamer/status']=()=>({operations:[{operation_id:'review_qa',fingerprint,status:'applied'}]});
  await f.card._findMigrationRecovery();f.card.shadowRoot.querySelector('[data-recovery-index]').click();const read=f.card._inspectMigration();await tick();
  const user=missing==='user'?undefined:missing==='id'?{id:'',is_admin:true}:{id:'admin-a'};
  f.card.hass={...f.hass,user};assert.equal(f.card._migration,null);assert.doesNotMatch(f.card.shadowRoot.textContent,/review_qa/);
  f.card.hass={...f.hass};await tick();assert.equal(f.card._migration?.operation_id,'review_qa');assert.equal(f.card._migration.busy,true);
  assert.equal(f.card.shadowRoot.getElementById('reviewMigrationRestore').disabled,true);
  await f.card._inspectMigration();assert.equal(f.requests.filter(r=>r.type==='ha_entity_renamer/recovery/inspect').length,1);
  inspect.resolve({operation_id:'review_qa',fingerprint,state:'refused',reason:'prewrite_refused'});await read;await tick();
  assert.equal(f.card._migration.busy,false);assert.equal(f.card._migration.inspection,undefined);assert.equal(writes(f).length,0);
});

test('a definite role revocation after unresolved authority discards suspended identity',async t=>{
  const f=await fixture(t),inspect=gate();f.handlers['ha_entity_renamer/recovery/inspect']=()=>inspect.promise;
  f.handlers['ha_entity_renamer/status']=()=>({operations:[{operation_id:'review_qa',fingerprint,status:'applied'}]});
  await f.card._findMigrationRecovery();f.card.shadowRoot.querySelector('[data-recovery-index]').click();const read=f.card._inspectMigration();await tick();
  f.card.hass={...f.hass,user:{id:'admin-a'}};f.card.hass={...f.hass,user:{id:'admin-a',is_admin:false}};
  f.card.hass={...f.hass};await tick();assert.equal(f.card._migration,null);
  inspect.resolve({operation_id:'review_qa',fingerprint,state:'refused',reason:'prewrite_refused'});await read;assert.equal(writes(f).length,0);
});


test('inventory enables on initial connection ready and reconnect without another render',async t=>{
  const f=await fixture(t,'en',false);
  const button=()=>f.card.shadowRoot.getElementById('findMigrationRecovery');
  assert.equal(f.card._migration,null);assert.equal(button().disabled,true);
  f.connection.dispatchEvent(new f.dom.window.Event('ready'));assert.equal(button().disabled,true);
  f.connection.connected=true;f.connection.dispatchEvent(new f.dom.window.Event('ready'));
  assert.equal(button().disabled,false);
  button().click();await tick();assert.ok(f.card.shadowRoot.querySelector('[data-recovery-index]'));
  f.connection.connected=false;f.connection.dispatchEvent(new f.dom.window.Event('disconnected'));
  assert.equal(button().disabled,true);
  f.connection.connected=true;f.connection.dispatchEvent(new f.dom.window.Event('ready'));
  assert.equal(button().disabled,false);assert.equal(writes(f).length,0);assert.equal(restores(f).length,0);
});

test('online enables inventory only when connection and admin authority are current',async t=>{
  const f=await fixture(t);const button=()=>f.card.shadowRoot.getElementById('findMigrationRecovery');
  Object.defineProperty(f.dom.window.navigator,'onLine',{configurable:true,value:false});
  f.dom.window.dispatchEvent(new f.dom.window.Event('offline'));assert.equal(button().disabled,true);
  Object.defineProperty(f.dom.window.navigator,'onLine',{configurable:true,value:true});
  f.dom.window.dispatchEvent(new f.dom.window.Event('online'));assert.equal(button().disabled,false);
  f.connection.connected=false;f.connection.dispatchEvent(new f.dom.window.Event('disconnected'));
  f.dom.window.dispatchEvent(new f.dom.window.Event('online'));assert.equal(button().disabled,true);
  f.card.hass={...f.hass,user:{id:'admin-a',is_admin:false}};
  f.connection.connected=true;f.connection.dispatchEvent(new f.dom.window.Event('ready'));
  assert.equal(button(),null);assert.equal(writes(f).length,0);assert.equal(restores(f).length,0);
});

for (const transition of ['remount','authority','connection']) for (const finish of ['before-return','after-return']) test('invalidated inspection has an honest terminal and requires a new explicit check: '+transition+' '+finish,async t=>{
  const f=await fixture(t),pending=gate();f.handlers['ha_entity_renamer/recovery/inspect']=()=>pending.promise;
  f.handlers['ha_entity_renamer/status']=()=>({operations:[{operation_id:'review_qa',fingerprint,status:'unknown',direction:'forward',progress:{total_steps:2,verified_steps:2,undone_steps:0}}]});
  await f.card._findMigrationRecovery();f.card.shadowRoot.querySelector('[data-recovery-index]').click();
  const old=f.card._inspectMigration();await tick();let card=f.card;
  if(transition==='remount') f.card.remove();
  if(transition==='authority') f.card.hass={...f.hass,user:{id:'admin-a'}};
  if(transition==='connection') {f.connection.connected=false;f.connection.dispatchEvent(new f.dom.window.Event('disconnected'));}
  const resume=()=>{
    if(transition==='remount') {card=f.dom.window.document.createElement('ha-entity-renamer');card.setConfig({show_support:false});f.dom.window.document.body.append(card);card.hass=f.hass;}
    if(transition==='authority') f.card.hass={...f.hass};
    if(transition==='connection') {f.connection.connected=true;f.connection.dispatchEvent(new f.dom.window.Event('ready'));}
  };
  if(finish==='after-return') {resume();await tick();assert.equal(card._migration.busy,true);await card._inspectMigration();assert.equal(f.requests.filter(r=>r.type==='ha_entity_renamer/recovery/inspect').length,1);}
  pending.resolve({operation_id:'review_qa',fingerprint,state:'refused',reason:'prewrite_refused'});await old;
  if(finish==='before-return') resume();
  await tick();assert.equal(card._migration.busy,false);assert.equal(card._migration.inspection,undefined);
  assert.match(card.shadowRoot.textContent,/previous inspection result cannot be used after the card or authority changed/i);
  assert.doesNotMatch(card.shadowRoot.textContent,/Previous operation selected\. Check status/);
  assert.equal(card.shadowRoot.getElementById('reviewMigrationRestore').disabled,true);
  assert.equal(card.shadowRoot.getElementById('inspectMigration').disabled,false);
  assert.equal(writes(f).length,0);assert.equal(restores(f).length,0);
  f.entries[0].entity_id='sensor.b';f.handlers['ha_entity_renamer/recovery/inspect']=()=>({operation_id:'review_qa',fingerprint,state:'recoverable_complete',eligible:true,restore_attempt_available:true,
    frozen_review:{changes:f.review.changes,dependencies:f.review.dependencies,identities:[['entry-a','mqtt','a','dev']]}});
  await card._inspectMigration();assert.equal(f.requests.filter(r=>r.type==='ha_entity_renamer/recovery/inspect').length,2);
  assert.equal(card._migration.registryMatches,true);assert.equal(card.shadowRoot.getElementById('reviewMigrationRestore').disabled,false);
  assert.ok(!card._migration.restoreConsent);await card._restoreMigration();assert.equal(restores(f).length,0);
  card._reviewMigrationRestore();assert.equal(card._migration.restoreConsent,true);assert.equal(restores(f).length,0);
});

for(const failure of ['rejected','mismatched','registry']) test('completed inspection explains '+failure+' without treating it as a selected operation',async t=>{
  const f=await fixture(t);f.handlers['ha_entity_renamer/status']=()=>({operations:[{operation_id:'review_qa',fingerprint,status:'applied'}]});
  f.handlers['ha_entity_renamer/recovery/inspect']=()=>failure==='rejected'?Promise.reject(new Error('offline')):failure==='mismatched'?{operation_id:'other',fingerprint,state:'applied'}:{operation_id:'review_qa',fingerprint,state:'applied'};
  if(failure==='registry') f.handlers['config/entity_registry/list']=()=>Promise.reject(new Error('offline'));
  await f.card._findMigrationRecovery();f.card.shadowRoot.querySelector('[data-recovery-index]').click();await f.card._inspectMigration();
  const text=f.card.shadowRoot.textContent;
  assert.match(text,failure==='registry'?/fresh registry read failed/i:/Inspection failed or returned a mismatched response/i);
  assert.doesNotMatch(text,/Previous operation selected\. Check status/);
  assert.equal(f.card.shadowRoot.getElementById('reviewMigrationRestore').disabled,true);
  assert.equal(f.card.shadowRoot.getElementById('inspectMigration').disabled,false);assert.equal(writes(f).length,0);
});

test('connection replacement drains old inspection before any fresh check and exposes no old result',async t=>{
  const f=await fixture(t),pending=gate();f.handlers['ha_entity_renamer/recovery/inspect']=()=>pending.promise;
  f.handlers['ha_entity_renamer/status']=()=>({operations:[{operation_id:'review_qa',fingerprint,status:'applied'}]});
  await f.card._findMigrationRecovery();f.card.shadowRoot.querySelector('[data-recovery-index]').click();const old=f.card._inspectMigration();await tick();
  const connection=new f.dom.window.EventTarget();connection.connected=true;f.card.hass={...f.hass,connection};await tick();
  assert.equal(f.card._migration.busy,true);assert.equal(f.card.shadowRoot.getElementById('inspectMigration').disabled,true);
  await f.card._inspectMigration();assert.equal(f.requests.filter(r=>r.type==='ha_entity_renamer/recovery/inspect').length,1);
  pending.resolve({operation_id:'review_qa',fingerprint,state:'refused',reason:'prewrite_refused'});await old;await tick();
  assert.equal(f.card._migration.busy,false);assert.equal(f.card._migration.inspection,undefined);
  assert.match(f.card.shadowRoot.textContent,/previous inspection result cannot be used after the card or authority changed/i);
  assert.equal(f.card.shadowRoot.getElementById('inspectMigration').disabled,false);
  assert.equal(f.card.shadowRoot.getElementById('reviewMigrationRestore').disabled,true);assert.equal(writes(f).length,0);
});

test('completed inspection is explicitly invalidated when its card is later replaced',async t=>{
  const f=await fixture(t);f.entries[0].entity_id='sensor.b';
  f.handlers['ha_entity_renamer/status']=()=>({operations:[{operation_id:'review_qa',fingerprint,status:'unknown',direction:'forward'}]});
  f.handlers['ha_entity_renamer/recovery/inspect']=()=>({operation_id:'review_qa',fingerprint,state:'recoverable_complete',eligible:true,restore_attempt_available:true,
    frozen_review:{changes:f.review.changes,dependencies:f.review.dependencies,identities:[['entry-a','mqtt','a','dev']]}});
  await f.card._findMigrationRecovery();f.card.shadowRoot.querySelector('[data-recovery-index]').click();await f.card._inspectMigration();
  assert.equal(f.card.shadowRoot.getElementById('reviewMigrationRestore').disabled,false);f.card._reviewMigrationRestore();assert.equal(f.card._migration.restoreConsent,true);
  f.card.remove();const card=f.dom.window.document.createElement('ha-entity-renamer');card.setConfig({show_support:false});f.dom.window.document.body.append(card);card.hass=f.hass;await tick();
  assert.match(card.shadowRoot.textContent,/previous inspection result cannot be used after the card or authority changed/i);
  assert.equal(card._migration.inspection,undefined);assert.ok(!card._migration.restoreConsent);
  assert.equal(card.shadowRoot.getElementById('reviewMigrationRestore').disabled,true);assert.equal(restores(f).length,0);
});

// A wide changes table must stay reachable inside the confirmation's focus loop.
test('forward and inverse review keyboard loop reaches the named changes scroll region without writing',async t=>{
  for(const inverse of [false,true]) {
    const f=await fixture(t,inverse?'pl':'en');
    await f.card._prepareMigration();
    if(inverse) {await f.card._applyMigration();f.card._reviewMigrationRestore();}
    const root=f.card.shadowRoot,dialog=root.querySelector('[role="dialog"]');
    const cancel=root.getElementById(inverse?'cancelMigrationRestore':'cancelRenameDialog');
    const apply=root.getElementById(inverse?'restoreMigration':'applyMigration');
    const region=dialog.querySelector('[role="region"]');
    assert.ok(region,'server changes table needs a focusable scroll region');
    assert.equal(region.tabIndex,0);
    assert.equal(root.getElementById(region.getAttribute('aria-labelledby')).textContent,inverse?'Zmiany serwera':'Server changes');
    assert.equal(region.querySelectorAll('tbody tr').length,1);
    assert.equal(root.activeElement,cancel);
    const key=(key,shiftKey=false)=>root.activeElement.dispatchEvent(new f.dom.window.KeyboardEvent('keydown',{key,shiftKey,bubbles:true}));
    apply.focus();key('Tab');assert.equal(root.activeElement,region);
    key('Tab',true);assert.equal(root.activeElement,apply);
    key('Tab');key('Tab');assert.equal(root.activeElement,dialog.querySelector('summary'));
    key('Tab',true);assert.equal(root.activeElement,region);
    const beforeWrites=writes(f).length;
    key('Escape');assert.equal(writes(f).length,beforeWrites);assert.equal(restores(f).length,0);
    assert.equal(f.card._renameQueue.length,1);
    assert.equal(root.activeElement.id,inverse?'reviewMigrationRestore':'executeRenames');
  }
});

test('Script definition key is distinct from references in forward and inverse review',async t=>{
  const f=await fixture(t,'en');
  f.review.dependencies.files[2].references.push({kind:'script_key',before:'worker',after:'new_worker',occurrences:1});
  f.review.dependencies.files[2].edit_count=2;
  await f.card._prepareMigration();
  let dialog=f.card.shadowRoot.querySelector('[role="dialog"]');
  let rows=[...dialog.querySelectorAll('details li')].map(row=>row.textContent);
  assert.ok(rows.some(row=>/Script key:.*worker.*new_worker.*1 definition/.test(row)));
  assert.ok(rows.some(row=>/sensor\.a.*sensor\.b.*1 references/.test(row)));
  await f.card._applyMigration();
  f.card._reviewMigrationRestore();
  dialog=f.card.shadowRoot.querySelector('[role="dialog"]');
  rows=[...dialog.querySelectorAll('details li')].map(row=>row.textContent);
  assert.ok(rows.some(row=>/Script key:.*new_worker.*worker.*1 definition/.test(row)));
  assert.equal(restores(f).length,0);
});

test('unknown dependency kind and invalid Script key cannot be reviewed',async t=>{
  for (const ref of [
    {kind:'foreign',before:'worker',after:'new_worker',occurrences:1},
    {kind:'script_key',before:'<worker>',after:'new_worker',occurrences:1},
  ]) {
    const f=await fixture(t);f.review.dependencies.files[2].references.push(ref);
    await f.card._prepareMigration();
    assert.equal(f.card._migration.stage,'refused');
    assert.equal(writes(f).length,0);
  }
});

test('numeric Template entry dependency is explicit in forward and inverse review', async t => {
  const f=await fixture(t);
  f.review.dependencies={domains:['template'],yaml_file_count:0,template_consumer_count:1,files:[],
    config_entries:[{entity_id:'sensor.consumer',before:'sensor.a',after:'sensor.b'}]};
  await f.card._prepareMigration();
  assert.equal(f.card._migration.stage,'review');
  let text=f.card.shadowRoot.querySelector('[role="dialog"]').textContent;
  assert.match(text,/sensor.consumer/); assert.match(text,/sensor.a\s*→\s*sensor.b/);
  f.handlers['ha_entity_renamer/recovery/inspect']=()=>({operation_id:'review_qa',fingerprint,state:'applied',eligible:true,restore_attempt_available:true});
  await f.card._applyMigration();
  f.card._reviewMigrationRestore();
  text=f.card.shadowRoot.querySelector('[role="dialog"]').textContent;
  assert.match(text,/sensor.consumer/); assert.match(text,/sensor.b\s*→\s*sensor.a/);
});

test('Powercalc group reference is visible in forward and inverse review', async t => {
  const f=await fixture(t,'en');
  f.review.dependencies={domains:['powercalc'],yaml_file_count:0,template_consumer_count:0,files:[],
    config_entries:[{kind:'powercalc',entity_id:'sensor.old_group',before:'sensor.old_group',after:'sensor.new_group'}]};
  await f.card._prepareMigration();
  assert.equal(f.card._migration.stage,'review');
  let text=f.card.shadowRoot.querySelector('[role="dialog"]').textContent;
  assert.match(text,/Powercalc group/); assert.match(text,/sensor.old_group\s*→\s*sensor.new_group/);
  f.handlers['ha_entity_renamer/recovery/inspect']=()=>({operation_id:'review_qa',fingerprint,state:'applied',eligible:true,restore_attempt_available:true});
  await f.card._applyMigration();
  f.card._reviewMigrationRestore();
  text=f.card.shadowRoot.querySelector('[role="dialog"]').textContent;
  assert.match(text,/Powercalc group/); assert.match(text,/sensor.new_group\s*→\s*sensor.old_group/);
});

test('unsupported Template dependency explains the specific refusal without offering apply', async t => {
  for (const [code, expected] of [['unsupported_template_reference', /Template reference could not be safely interpreted/i],
                                  ['unsupported_template_family', /Template form is not supported/i],
                                  ['unsupported_powercalc_scope', /Powercalc is loaded/i],
                                  ['unsupported_automation_scope', /Automation state or configuration could not be safely verified/i]]) {
    const f=await fixture(t);
    f.handlers['ha_entity_renamer/prepare']=()=>Promise.reject({code});
    await f.card._prepareMigration();
    assert.equal(f.card._migration.stage,'refused');
    assert.match(f.card.shadowRoot.textContent, expected);
    assert.equal(f.card.shadowRoot.getElementById('applyMigration'),null);
  }
});
