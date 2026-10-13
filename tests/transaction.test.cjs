const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { JSDOM } = require('jsdom');

function fixture(t) {
  const dom = new JSDOM('', { runScripts: 'dangerously', url: 'http://localhost/' });
  t.after(() => dom.window.close());
  dom.window.eval(readFileSync(require('node:path').join(__dirname, '../ha-entity-renamer.js'), 'utf8'));
  const card = dom.window.document.createElement('ha-entity-renamer');
  dom.window.document.body.append(card);
  const entries = ['a', 'b'].map(id => ({ id: 'entry-' + id, entity_id: 'sensor.' + id, device_id: 'dev', unique_id: id, platform: 'mqtt' }));
  const writes = [];
  const devices = [{id:'dev',name:'QA',name_by_user:null}];
  const connection = new dom.window.EventTarget(); connection.connected = true;
  const hass = { language: 'en', connection, user: { id: 'admin-a', is_admin: true }, states: {}, callWS: async msg => {
    if (msg.type.endsWith('/update')) { writes.push(msg);if(msg.entity_id){const row=entries.find(x=>x.entity_id===msg.entity_id);if(row){if(msg.new_entity_id)row.entity_id=msg.new_entity_id;if(msg.name)row.name=msg.name;}}if(msg.device_id){const row=devices.find(x=>x.id===msg.device_id);if(row)row.name_by_user=msg.name_by_user;}return {}; }
    if (msg.type === 'config/entity_registry/list') return entries.map(row=>({...row}));
    if (msg.type === 'config/device_registry/list') return devices.map(row=>({...row}));
    if (msg.type === 'config/entity_registry/get_automatic_entity_ids') return Object.fromEntries(msg.entity_ids.map(id => [id, id + '_auto']));
    if (msg.type === 'lovelace/dashboards/list') return [];
    return {};
  } };
  card._hass = hass; card._entities = entries; card._devices = devices; card._deviceEntities = { dev: entries };
  card._bindRenameExportEvents();
  function confirm() { card._showRenameConfirmation(); return card._executeRenames(true); }
  return { card, hass, entries, writes, confirm, dom };
}
function gate() { let release; const promise = new Promise(resolve => { release = resolve; }); return { promise, release }; }

function openEntityEditor(card) {
  card._expandedDevices.add('dev'); card.render();
  card.shadowRoot.querySelector('[data-add-single="sensor.a"]').click();
  assert.ok(card.shadowRoot.getElementById('entityEditObjectId'), 'manual ID field is available without a browser prompt');
}

test('inline manual editor queues literal ID and name, then one confirmed transaction', async t => {
  const { card, writes } = fixture(t); openEntityEditor(card);
  card.shadowRoot.getElementById('entityEditObjectId').value = 'qa_changed';
  card.shadowRoot.getElementById('entityEditName').value = '<img src=x onerror=alert(1)> QA';
  card.shadowRoot.getElementById('queueEntityEdit').click();
  assert.equal(writes.length, 0); assert.equal(card._renameQueue.length, 1);
  card._showRenameConfirmation();
  assert.match(card.shadowRoot.querySelector('.confirm-dialog').textContent, /<img src=x onerror=alert\(1\)> QA/);
  assert.equal(card.shadowRoot.querySelector('.confirm-dialog img'), null);
  await card._executeRenames(true);
  assert.equal(writes.length, 1); assert.equal(writes[0].new_entity_id, 'sensor.qa_changed');
  assert.equal(writes[0].name, '<img src=x onerror=alert(1)> QA');
});

test('inline editor preserves draft and keyboard focus across locale change, cancel writes nothing', t => {
  const {card,hass,writes}=fixture(t); openEntityEditor(card);
  const field=card.shadowRoot.getElementById('entityEditName'); field.value='Draft QA'; field.focus(); field.setSelectionRange(2,5);
  card.hass={...hass,language:'pl'};
  assert.equal(card.shadowRoot.getElementById('entityEditName').value,'Draft QA');
  assert.equal(card.shadowRoot.activeElement.id,'entityEditName'); assert.equal(card.shadowRoot.activeElement.selectionStart,2);
  card.shadowRoot.getElementById('cancelEntityEdit').click();
  assert.equal(card.shadowRoot.getElementById('entityEditName'),null); assert.equal(card._renameQueue.length,0); assert.equal(writes.length,0);
});

test('inline editor closes when the authenticated session changes', t => {
  const {card,hass,writes}=fixture(t); openEntityEditor(card);
  card.hass={...hass,user:{id:'admin-b',is_admin:true}};
  assert.equal(card.shadowRoot.getElementById('queueEntityEdit'),null); assert.equal(card._renameQueue.length,0); assert.equal(writes.length,0);
});

test('apply action counts device-only and mixed queues accurately', t => {
  const {card}=fixture(t); card._deviceRenameQueue={dev:'New QA'}; card.setActiveTab('queue');
  assert.match(card.shadowRoot.getElementById('executeRenames').textContent, /\(1\)/);
  card._addToQueue('sensor.a','sensor.new_a');
  assert.match(card.shadowRoot.getElementById('executeRenames').textContent, /\(2\)/);
});

test('Polish friendly-only queue describes the unchanged ID in Polish', t => {
  const {card,hass}=fixture(t); card.hass={...hass,language:'pl'};
  card._addToQueue('sensor.a','sensor.a','QA nazwa'); card.setActiveTab('queue');
  assert.doesNotMatch(card.shadowRoot.querySelector('.queue-list').textContent,/no entity_id change/);
  assert.match(card.shadowRoot.querySelector('.queue-list').textContent,/Bez zmiany entity_id/);
});

test('ID and friendly name are one registry transaction, failed item retries safely', async t => {
  const { card, hass, writes, confirm } = fixture(t);
  const call = hass.callWS; let fail = true;
  hass.callWS = async msg => { if (msg.type.endsWith('/update') && fail) { fail = false; throw new Error('controlled failure'); } return call(msg); };
  card._addToQueue('sensor.a', 'sensor.renamed', 'Friendly QA');
  await confirm(); assert.equal(card._renameQueue.length, 1); assert.equal(writes.length, 0);
  await confirm(); assert.equal(writes.length, 1);
  assert.equal(writes[0].entity_id, 'sensor.a'); assert.equal(writes[0].new_entity_id, 'sensor.renamed'); assert.equal(writes[0].name, 'Friendly QA');
});

for (const kind of ['account', 'role', 'connection']) test('pending apply cancels after ' + kind + ' change', async t => {
  const { card, hass, writes, confirm } = fixture(t); const wait = gate(); const call = hass.callWS;
  hass.callWS = async msg => msg.type === 'search/related' ? wait.promise : call(msg);
  card._addToQueue('sensor.a', 'sensor.new_a'); const applying = confirm();
  await new Promise(resolve => setImmediate(resolve));
  card.hass = { ...hass, user: kind === 'account' ? { id: 'admin-b', is_admin: true } : kind === 'role' ? { id: 'admin-a', is_admin: false } : hass.user, connection: kind === 'connection' ? {} : hass.connection };
  wait.release({}); await applying; assert.equal(writes.length, 0); assert.equal(card._renameQueue.length, kind === 'connection' ? 1 : 0);
});

test('confirmed queue cannot acquire unconfirmed edits during impact await', async t => {
  const { card, hass, writes, confirm } = fixture(t); const wait = gate(); const call = hass.callWS;
  hass.callWS = async msg => msg.type === 'search/related' ? wait.promise : call(msg);
  card._addToQueue('sensor.a', 'sensor.new_a'); const applying = confirm();
  await new Promise(resolve => setImmediate(resolve));
  card._addToQueue('sensor.b', 'sensor.unconfirmed'); card._removeFromQueue('sensor.a'); card._clearQueue();
  wait.release({}); await applying;
  assert.deepEqual(writes.map(x => x.entity_id), ['sensor.a']);
});

test('replacement source during impact scan blocks automatic rename', async t => {
  const { card, hass, entries, writes, confirm } = fixture(t); const wait = gate(); const call = hass.callWS;
  await card._loadAutomaticIds(); card._queueAutomaticIds(null, 'sensor.a');
  hass.callWS = async msg => msg.type === 'search/related' ? wait.promise : call(msg);
  const applying = confirm(); await new Promise(resolve => setImmediate(resolve));
  entries[0] = { ...entries[0], id: 'replacement-entry', platform: 'another-integration' };
  wait.release({}); await applying; assert.equal(writes.length, 0); assert.equal(card._renameQueue.length, 1);
});

test('clear removes both device and entity proposals and friendly-only confirmation is exact', t => {
  const { card } = fixture(t);
  card._addToQueue('sensor.a', 'sensor.a', '<img src=x onerror=alert(1)> QA');
  card._deviceRenameQueue = { dev: 'New device' }; card._showRenameConfirmation();
  const dialog = card.shadowRoot.querySelector('.confirm-dialog');
  assert.match(dialog.textContent, /Entities to rename: 1/); assert.match(dialog.textContent, /<img src=x onerror=alert\(1\)> QA/); assert.equal(dialog.querySelector('img'), null);
  card._clearQueue(); assert.equal(card._renameQueue.length, 0); assert.equal(Object.keys(card._deviceRenameQueue).length, 0);
});

test('second confirm cannot duplicate an active transaction', async t => {
  const { card, hass, writes, confirm } = fixture(t); const wait = gate(); const call = hass.callWS;
  hass.callWS = async msg => msg.type === 'search/related' ? wait.promise : call(msg);
  card._addToQueue('sensor.a', 'sensor.new_a'); const applying = confirm();
  await new Promise(resolve => setImmediate(resolve)); const duplicate = card._executeRenames(true);
  wait.release({}); await Promise.all([applying, duplicate]); assert.equal(writes.length, 1);
});

for (const kind of ['automatic', 'registry', 'impact']) test('late ' + kind + ' read cannot repopulate a changed session', async t => {
  const { card, hass } = fixture(t); const wait = gate(); const call = hass.callWS;
  const type = kind === 'automatic' ? 'config/entity_registry/get_automatic_entity_ids' : kind === 'registry' ? 'config/entity_registry/list' : 'search/related';
  hass.callWS = async msg => msg.type === type ? wait.promise : call(msg);
  if (kind === 'impact') card._addToQueue('sensor.a', 'sensor.new_a');
  const reading = kind === 'automatic' ? card._loadAutomaticIds() : kind === 'registry' ? card._loadData() : card._analyzeImpact();
  await new Promise(resolve => setImmediate(resolve));
  card.hass = { ...hass, user: { id: 'admin-b', is_admin: true }, callWS: call };
  wait.release(kind === 'automatic' ? {'sensor.a':'sensor.new_a','sensor.b':'sensor.new_b'} : kind === 'registry' ? [{entity_id:'sensor.private_old_session'}] : {});
  await reading;
  assert.equal(card._automaticLoaded, false); assert.equal(card._impactResults, null);
  assert.equal(card._entities.some(e => e.entity_id === 'sensor.private_old_session'), false);
});

test('device removal is frozen while the confirmed transaction awaits impact', async t => {
  const { card, hass, confirm } = fixture(t); const wait = gate(); const call = hass.callWS;
  card._addToQueue('sensor.a', 'sensor.new_a'); card._deviceRenameQueue = {dev:'QA new device'};
  hass.callWS = async msg => msg.type === 'search/related' ? wait.promise : call(msg);
  const applying = confirm(); await new Promise(resolve => setImmediate(resolve));
  card.shadowRoot.querySelector('[data-remove-dev-queue]').click();
  const retained = card._deviceRenameQueue.dev; wait.release({}); await applying;
  assert.equal(retained, 'QA new device');
});

test('cancelled confirmation cannot authorize a later direct apply', async t => {
  const { card, writes } = fixture(t); card._addToQueue('sensor.a', 'sensor.new_a'); card._showRenameConfirmation();
  card.shadowRoot.getElementById('cancelRenameDialog').click(); await card._executeRenames(true);
  assert.equal(writes.length, 0); assert.equal(card._renameQueue.length, 1);
});

test('partial success retries only failed entity and device writes', async t => {
  const { card, hass, writes, confirm } = fixture(t); const call = hass.callWS; let failing = true;
  hass.callWS = async msg => {
    if (failing && (msg.entity_id === 'sensor.b' || msg.device_id === 'dev') && msg.type.endsWith('/update')) throw new Error('controlled rejected write');
    return call(msg);
  };
  card._addToQueue('sensor.a', 'sensor.new_a'); card._addToQueue('sensor.b', 'sensor.new_b'); card._deviceRenameQueue = {dev:'New QA'};
  await confirm(); assert.equal(card._lastApplyResult.ok, 1); assert.equal(card._lastApplyResult.total, 3);
  assert.deepEqual(Array.from(card._renameQueue, row=>row.oldId), ['sensor.b']); assert.equal(card._deviceRenameQueue.dev, 'New QA');
  failing = false; await confirm(); assert.equal(card._lastApplyResult.ok, 2); assert.equal(card._lastApplyResult.total, 2);
  assert.deepEqual(writes.map(msg=>msg.entity_id || msg.device_id), ['sensor.a','dev','sensor.b']);
  assert.equal(card._renameQueue.length,0); assert.equal(Object.keys(card._deviceRenameQueue).length,0);
});

test('impact lookup failure retains all proposals without registry writes', async t => {
  const {card,hass,writes,confirm}=fixture(t);const call=hass.callWS;
  hass.callWS=async msg=>{if(msg.type==='search/related') throw new Error('controlled lookup unavailable');return call(msg);};
  card._addToQueue('sensor.a','sensor.new_a');card._deviceRenameQueue={dev:'New QA'};
  await confirm();assert.equal(writes.length,0);assert.equal(card._renameQueue.length,1);assert.equal(card._deviceRenameQueue.dev,'New QA');
  assert.match(card._message.text,/no changes applied/);assert.equal(card._loading,false);
});

test('failed ID writes do not claim readable references need updating', async t => {
  const {card,hass,confirm}=fixture(t);const call=hass.callWS;
  hass.callWS=async msg=>{
    if(msg.type==='search/related')return {automation:['automation.qa_reference']};
    if(msg.type.endsWith('/update'))throw new Error('Invalid entity ID');
    return call(msg);
  };
  card._addToQueue('sensor.a','sensor.invalid id');await confirm();
  assert.doesNotMatch(card._message.text,/places require (update|review)/);
  assert.equal(card._renameQueue.length,1);
});

// Missing keyboard cancellation must never authorize registry writes.
test('Escape cancels confirmation, retains the proposal and cannot authorize apply', async t => {
  const { card, writes, dom } = fixture(t);
  card._addToQueue('sensor.a', 'sensor.new_a'); card.setActiveTab('queue');
  card.shadowRoot.getElementById('executeRenames').click();
  card.shadowRoot.getElementById('cancelRenameDialog').dispatchEvent(new dom.window.KeyboardEvent('keydown', {key: 'Escape', bubbles: true, cancelable: true}));
  assert.equal(card.shadowRoot.querySelector('.confirm-dialog'), null, 'Escape closes the visible confirmation');
  assert.equal(card._renameQueue.length, 1, 'cancellation keeps the local proposal');
  await card._executeRenames(true); assert.equal(writes.length, 0, 'cancelled confirmation cannot authorize direct apply');
});
test('confirmation defaults to Cancel and returns focus to Apply after cancellation', t => {
  const { card, dom } = fixture(t);
  card._addToQueue('sensor.a', 'sensor.new_a'); card.setActiveTab('queue');
  const trigger=card.shadowRoot.getElementById('executeRenames'); trigger.focus(); trigger.click();
  assert.equal(card.shadowRoot.activeElement.id, 'cancelRenameDialog');
  card.shadowRoot.getElementById('cancelRenameDialog').click();
  assert.equal(card.shadowRoot.activeElement.id, 'executeRenames');
});
test('confirmation keyboard stays on its two actions while the proposal is pending', t => {
  const { card, dom, writes } = fixture(t);
  card._addToQueue('sensor.a', 'sensor.new_a'); card.setActiveTab('queue'); card._showRenameConfirmation();
  const cancel=card.shadowRoot.getElementById('cancelRenameDialog'); cancel.focus();
  cancel.dispatchEvent(new dom.window.KeyboardEvent('keydown', {key:'Tab',shiftKey:true,bubbles:true,cancelable:true}));
  assert.equal(card.shadowRoot.activeElement.id,'confirmRenameApply');
  card.shadowRoot.getElementById('confirmRenameApply').dispatchEvent(new dom.window.KeyboardEvent('keydown', {key:'Tab',bubbles:true,cancelable:true}));
  assert.equal(card.shadowRoot.activeElement.id,'cancelRenameDialog'); assert.equal(writes.length,0);
});

// Regression: availability restoration cannot restore consent captured before loss.
for (const disruption of ['connection', 'browser', 'detach']) {
  function disrupt(f) {
    if (disruption === 'connection') {
      f.hass.connection.dispatchEvent(new f.dom.window.Event('disconnected'));
      f.hass.connection.dispatchEvent(new f.dom.window.Event('ready'));
    } else if (disruption === 'browser') {
      f.dom.window.dispatchEvent(new f.dom.window.Event('offline'));
      f.dom.window.dispatchEvent(new f.dom.window.Event('online'));
    } else { f.card.remove(); f.dom.window.document.body.append(f.card); }
  }
  test(disruption + ' recovery requires fresh confirmation', async t => {
    const f = fixture(t); f.card._addToQueue('sensor.a', 'sensor.new_a');
    f.card._showRenameConfirmation(); disrupt(f); await f.card._executeRenames(true);
    assert.equal(f.writes.length, 0);
    await f.confirm(); assert.equal(f.writes.length, 1);
  });
  test(disruption + ' recovery during impact cancels captured batch', async t => {
    const f = fixture(t), wait = gate(), call = f.hass.callWS;
    f.hass.callWS = msg => msg.type === 'search/related' ? wait.promise : call(msg);
    f.card._addToQueue('sensor.a', 'sensor.new_a'); const applying = f.confirm();
    await new Promise(resolve => setImmediate(resolve)); disrupt(f); wait.release({}); await applying;
    assert.equal(f.writes.length, 0); assert.equal(f.card._lastApplyResult, null);
    assert.equal(f.card._confirmation, null); assert.equal(f.card._loading, false);
  });
}

// Regression: a sent request stays uncertain in its original account only.
for (const entity of [true, false]) for (const change of ['account', 'role', 'unresolved']) for (const reject of [false, true]) {
  test((entity ? 'entity' : 'device') + ' late ' + (reject ? 'rejection' : 'ACK') + ' after ' + change + ' stays private and unknown', async t => {
    const f = fixture(t), sent = gate(), reply = gate(), call = f.hass.callWS;
    f.hass.callWS = async msg => {
      if (msg.type.endsWith('/update')) { sent.release(); await reply.promise; if (reject) throw new Error('PRIVATE old authority'); return call(msg); }
      return call(msg);
    };
    if (entity) { f.card._addToQueue('sensor.a', 'sensor.new_a', 'PRIVATE friendly'); f.card._addToQueue('sensor.b','sensor.new_b'); }
    else { f.card._deviceRenameQueue = {dev:'PRIVATE device'}; f.card._addToQueue('sensor.a','sensor.new_a'); }
    const applying = f.confirm(); await sent.promise;
    f.card.hass = {...f.hass, user: change === 'account' ? {id:'admin-b',is_admin:true} : change === 'role' ? {id:'admin-a',is_admin:false} : {id:'admin-a'}};
    reply.release(); await applying;
    assert.equal(f.card._entities.length,0); assert.equal(f.card._devices.length,0);
    assert.equal(f.card._renameQueue.length,0); assert.equal(Object.keys(f.card._deviceRenameQueue).length,0);
    assert.equal(f.card._renameLog.length,0); assert.equal(f.card._lastApplyResult,null);
    assert.doesNotMatch(f.card.shadowRoot.textContent,/PRIVATE|sensor\.new_a/);
    f.card.hass = {...f.hass,user:{id:'admin-a',is_admin:true}};
    assert.equal(f.card._renameLog.length,1);
    assert.equal(f.card._renameLog[0].writePhase,'attempted');
    const report=JSON.parse(f.card._buildRenameReport({draft:{format:'json',includePrivate:true}}).serialized);
    assert.equal(report.counts.unknown,1); assert.equal(report.counts.applied,0);
    assert.equal(f.writes.length,reject ? 0 : 1);
  });
}

for (const entity of [true,false]) for (const reject of [false,true]) for (const change of ['account','role','disconnect']) test((entity?'entity':'device') + ' ' + change + ' during final readback ' + (reject?'rejection':'resolve') + ' cannot publish final results', async t => {
  const f=fixture(t), started=gate(), reply=gate(), call=f.hass.callWS;
  f.hass.callWS=async msg=>{if(msg.type==='config/entity_registry/list' && f.writes.length){started.release();const rows=await reply.promise;if(reject)throw new Error('PRIVATE readback failure');return rows;}return call(msg);};
  if(entity)f.card._addToQueue('sensor.a','sensor.new_a');else f.card._deviceRenameQueue={dev:'PRIVATE new device'};const applying=f.confirm();await started.promise;
  if(change==='disconnect') { f.hass.connection.dispatchEvent(new f.dom.window.Event('disconnected')); f.hass.connection.dispatchEvent(new f.dom.window.Event('ready')); }
  else f.card.hass={...f.hass,callWS:call,user:change==='account'?{id:'admin-b',is_admin:true}:{id:'admin-a',is_admin:false}};
  reply.release(f.entries.map(row=>({...row})));await applying;await new Promise(resolve=>setImmediate(resolve));
  assert.equal(f.card._lastApplyResult,null);assert.equal(f.card._applyOperation,null);assert.equal(f.card._loading,false);
  assert.doesNotMatch(f.card._message?.text || '',/Confirmed changes:/);
  if(change!=='disconnect') assert.equal(f.card._renameLog.length,0);
  else assert.equal(f.card._renameLog[0].writePhase,'acknowledged');
});

test('a sent request records attempt intent before transport and completion only after its reply', async t=>{
 const f=fixture(t),sent=gate(),reply=gate(),call=f.hass.callWS;
 f.hass.callWS=async msg=>{
  if(msg.type==='config/entity_registry/update'){sent.release();await reply.promise;}
  return call(msg);
 };
 f.card._addToQueue('sensor.a','sensor.new_a');const applying=f.confirm();await sent.promise;
 try {
  const pending=f.card._renameLog[0];
  assert.equal(pending.completedAt,undefined);
  assert.match(pending.attemptedAt,/^\d{4}-\d{2}-\d{2}T.*Z$/);
  const before=JSON.parse(f.card._buildRenameReport({draft:{format:'json',includePrivate:false}}).serialized);
  assert.equal(before.rows[0].timestampScope,'utc_attempt_intent');assert.equal(before.counts.applied,0);
 } finally {reply.release();await applying;}
 const after=JSON.parse(f.card._buildRenameReport({draft:{format:'json',includePrivate:false}}).serialized);
 assert.equal(after.rows[0].timestampScope,'utc_completion');assert.equal(after.counts.applied,1);
});

test('a new resolved account gets permitted registries after the cancelled old write releases its lock', async t => {
 const f=fixture(t),writeGate=gate(),original=f.hass.callWS;let started=false;
 f.hass.callWS=async message=>{if(message.type==='config/entity_registry/update'){started=true;f.writes.push(message);await writeGate.promise;return {};}return original(message);};
 f.card._renameQueue=[{oldId:'sensor.a',newId:'sensor.qa_cancelled_old'}];const pending=f.confirm();
 for(let i=0;i<20&&!started;i++)await new Promise(resolve=>setImmediate(resolve));assert.equal(started,true);
 const nextReads=[];
 f.card.hass={...f.hass,user:{id:'admin-b',is_admin:true},callWS:async message=>{nextReads.push(message);return message.type==='config/device_registry/list'?[{id:'fresh-device',name:'Fresh device'}]:[{entity_id:'sensor.fresh_account',device_id:'fresh-device'}];}};
 assert.equal(nextReads.length,0,'old unresolved write must retain its lock');writeGate.release();await pending;await new Promise(resolve=>setImmediate(resolve));
 assert.equal(f.card._entities[0]?.entity_id,'sensor.fresh_account');assert.equal(f.card._devices[0]?.id,'fresh-device');
 assert.equal(nextReads.length,2);assert.equal(nextReads.filter(x=>x.type.endsWith('/update')).length,0);assert.equal(f.writes.length,1);
 assert.equal(f.card._renameLog.length,0);assert.equal(f.card._confirmDialogOpen,false);assert.equal(f.card._applyOperation,null);
});
