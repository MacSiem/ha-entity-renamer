const assert = require('node:assert/strict');
const {test} = require('node:test');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {JSDOM} = require('jsdom');

function fixture() {
  const dom = new JSDOM('', {runScripts:'dangerously', url:'http://localhost/'});
  dom.window.eval(readFileSync(join(__dirname,'../ha-entity-renamer.js'),'utf8'));
  const card = dom.window.document.createElement('ha-entity-renamer');
  dom.window.document.body.append(card);
  const connection = new dom.window.EventTarget(); connection.connected = true;
  const now = Date.now()/1000;
  const devices = [{id:'ten',name:'Lampa 10',created_at:0},{id:'two',name:'Lampa 2',created_at:now-3600},{id:'bath',name:'Łazienka'},{id:'kitchen',name:'Kuchnia'}];
  const entities = devices.map((d,i)=>({entity_id:`sensor.old_${i}`,device_id:d.id,unique_id:String(i),created_at:d.created_at}));
  card._lang='pl';
  card._hass={language:'pl',connection,user:{id:'admin',is_admin:true},states:{},callWS:async msg=>{
    if(msg.type==='config/device_registry/list')return devices;
    if(msg.type==='config/entity_registry/list')return entities;
    throw new Error('Unexpected registry write/read: '+msg.type);
  }};
  card._bindRenameExportEvents();
  return {dom,card,root:card.shadowRoot,devices,entities};
}

test('all views use the HA natural order, including numeric device names',async()=>{
  const {dom,card,root,entities}=fixture();
  try {
    await card._loadData();card.render();
    assert.deepEqual(Array.from(root.querySelectorAll('.device-name'),el=>el.textContent.trim()),['Kuchnia','Lampa 2','Lampa 10','Łazienka']);
    card._renameQueue=entities.map(e=>({oldId:e.entity_id,newId:e.entity_id+'_new',entity:e}));
    const original=Array.from(card._renameQueue,r=>r.oldId);
    card._activeTab='queue';card.render();
    assert.deepEqual(Array.from(root.querySelectorAll('[data-queue-group]'),el=>el.dataset.queueGroup),['kitchen','two','ten','bath']);
    assert.deepEqual(Array.from(card._renameQueue,r=>r.oldId),original,'view sorting must not reorder transactions');
  } finally {dom.window.close();}
});

test('Devices show an immediate queued marker and group count with an accessible Remove action',async()=>{
  const {dom,card,root}=fixture();
  try {
    await card._loadData();card._expandedDevices.add('two');card._addToQueue('sensor.old_1','sensor.new_1');
    assert.match(root.querySelector('[data-device-id="two"]').textContent,/1 w kolejce/);
    const row=root.querySelector('[data-remove-queue="sensor.old_1"]').closest('.entity-row');
    assert.match(row.textContent,/✓ W kolejce/);
    assert.match(row.querySelector('button').textContent,/Usuń/);
    row.querySelector('button').click();
    assert.match(root.querySelector('[data-device-id="two"]').textContent,/0 w kolejce/);
  } finally {dom.window.close();}
});

test('Automatic search and chips constrain Queue all to visible eligible proposals only',async()=>{
  const {dom,card,root,entities}=fixture();
  try {
    await card._loadData();card._activeTab='automatic';card._automaticLoaded=true;
    card._automaticRows=entities.map(e=>({oldId:e.entity_id,newId:e.entity_id+'_new',deviceId:e.device_id}));card.render();
    const search=root.querySelector('#automaticSearch');assert.ok(search,'Automatic IDs has its own search');
    search.value='Lampa 2';search.dispatchEvent(new dom.window.Event('input',{bubbles:true}));
    assert.match(root.querySelector('#queueAllAutomatic').textContent,/1/);
    root.querySelector('#queueAllAutomatic').click();
    assert.deepEqual(Array.from(card._renameQueue,r=>r.oldId),['sensor.old_1']);
    root.querySelector('[data-list-filter="queued"]').click();
    assert.equal(card._renameQueue.length,1,'filtering has no queue side effect');
    assert.equal(root.querySelectorAll('[data-auto-group]').length,1);
  } finally {dom.window.close();}
});

test('New filter excludes unknown and old dates; newest sort uses registry timestamps',async()=>{
  const {dom,card,root}=fixture();
  try {
    await card._loadData();card.render();
    root.querySelector('[data-list-filter="new"]').click();
    assert.deepEqual(Array.from(root.querySelectorAll('.device-name'),el=>el.textContent.trim()),['Lampa 2']);
    assert.match(root.textContent,/Nowe/);
    root.querySelector('[data-list-filter="all"]').click();
    const sort=root.querySelector('#listSort');sort.value='newest';sort.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
    assert.equal(root.querySelector('.device-name').textContent.trim(),'Lampa 2');
  } finally {dom.window.close();}
});

test('Undo restores exact proposal and insertion position, but Clear invalidates it',async()=>{
  const {dom,card,root}=fixture();
  try {
    await card._loadData();
    card._addToQueue('sensor.old_0','sensor.new_0');card._addToQueue('sensor.old_1','sensor.new_1');card._addToQueue('sensor.old_2','sensor.new_2');
    const original=card._renameQueue[1];card._removeFromQueue(original.oldId);
    const undo=root.querySelector('#undoQueueRemoval');assert.ok(undo,'undo is visible outside Queue');undo.click();
    assert.equal(card._renameQueue[1],original);
    card._removeFromQueue(original.oldId);card._clearQueue();
    assert.equal(root.querySelector('#undoQueueRemoval'),null);
  } finally {dom.window.close();}
});

test('manually replacing automatic proposal clears automatic authority and keeps manual provenance',async()=>{
  const {dom,card,root,entities}=fixture();
  try {
    await card._loadData();card._automaticRows=[{oldId:entities[0].entity_id,newId:'sensor.auto',deviceId:'ten'}];
    card._queueAutomaticIds();card._addToQueue('sensor.old_0','sensor.manual');
    assert.equal(card._renameQueue[0].automatic,false);
    card._activeTab='queue';card.render();assert.match(root.querySelector('.queue-list').textContent,/Ręcznie/);
  } finally {dom.window.close();}
});

test('queue bar includes device renames and navigates without applying',async()=>{
  const {dom,card,root}=fixture();
  try {
    await card._loadData();card._deviceRenameQueue={two:'New name'};card.render();
    const button=root.querySelector('#openQueue');assert.ok(button);assert.match(button.parentElement.textContent,/1/);
    button.click();assert.equal(card._activeTab,'queue');assert.equal(card._confirmDialogOpen,false);
  } finally {dom.window.close();}
});

test('undo expiry removes only its notice and preserves the current card and focused draft',async()=>{
  const {dom,card,root}=fixture();
  try {
    await card._loadData();let expire;
    dom.window.setTimeout=callback=>{expire=callback;return 1;};
    card._addToQueue('sensor.old_0','sensor.new_0');card._removeFromQueue('sensor.old_0');
    const surface=root.querySelector('.card'),search=root.querySelector('#searchInput');search.focus();
    expire();
    assert.equal(root.querySelector('.card'),surface,'expiry must not replace the surface or cancel a separate export');
    assert.equal(root.activeElement,search);
    assert.equal(root.querySelector('#undoQueueRemoval'),null);
  } finally {dom.window.close();}
});

test('unknown automatic IDs respect search and conflict filters',async()=>{
  const {dom,card,root,entities}=fixture();
  try {
    await card._loadData();card._automaticLoaded=true;card._activeTab='automatic';
    card._automaticNull=[entities[0]];card.render();
    assert.match(root.textContent,/sensor.old_0/);
    root.querySelector('#automaticSearch').value='no matching device';
    root.querySelector('#automaticSearch').dispatchEvent(new dom.window.Event('input',{bubbles:true}));
    assert.doesNotMatch(root.textContent,/sensor.old_0/);
    root.querySelector('#automaticSearch').value='';
    root.querySelector('#automaticSearch').dispatchEvent(new dom.window.Event('input',{bubbles:true}));
    root.querySelector('[data-list-filter="conflicts"]').click();
    assert.doesNotMatch(root.textContent,/sensor.old_0/);
  } finally {dom.window.close();}
});

test('Polish registry counts use the right plural forms',async()=>{
  const {dom,card,root}=fixture();
  try {
    await card._loadData();card.render();
    assert.match(root.querySelector('.subtitle').textContent,/4 urządzenia.*4 encje/);
    assert.match(root.querySelector('[data-device-id="two"]').textContent,/1 encja/);
    card._devices.push({id:'extra',name:'Extra'});card._entities.push({entity_id:'sensor.extra',device_id:'extra'});card.render();
    assert.match(root.querySelector('.subtitle').textContent,/5 urządzeń.*5 encji/);
  } finally {dom.window.close();}
});

test('history respects explicit HA server zone and retains ISO only in details',()=>{
  const {dom,card,root}=fixture();
  try {
    card._hass.config={time_zone:'Pacific/Auckland'};
    card._hass.locale={time_zone:'server',time_format:'24'};
    const completedAt='2026-01-01T00:30:00Z';
    card._renameLog=[{time:'00:30',completedAt,oldId:'sensor.a',newId:'sensor.b',status:'ok',writePhase:'readback'}];
    card._activeTab='log';card.render();
    assert.equal(root.querySelector('.log-time').textContent,new Intl.DateTimeFormat('pl',{timeZone:'Pacific/Auckland',dateStyle:'medium',timeStyle:'short'}).format(new Date(completedAt)));
    assert.match(root.querySelector('.log-entry details').textContent,/2026-01-01T00:30:00Z/);
  } finally {dom.window.close();}
});

test('device-name proposals share their device group and support exact-position undo',async()=>{
  const {dom,card,root}=fixture();
  try {
    await card._loadData();card._deviceRenameQueue={ten:'Ten changed',two:'Two changed'};
    card._addToQueue('sensor.old_1','sensor.new_1');card._activeTab='queue';card.render();
    const group=root.querySelector('[data-queue-group="two"]');assert.ok(group);
    assert.match(group.textContent,/Two changed/);assert.match(group.textContent,/sensor.old_1/);
    root.querySelector('[data-remove-dev-queue="ten"]').click();
    assert.deepEqual(Object.keys(card._deviceRenameQueue),['two']);
    root.querySelector('#undoQueueRemoval').click();
    assert.deepEqual(Object.entries(card._deviceRenameQueue),[['ten','Ten changed'],['two','Two changed']]);
  } finally {dom.window.close();}
});
