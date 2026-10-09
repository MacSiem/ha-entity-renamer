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
  const hass = { language: 'en', connection: {}, user: { id: 'admin-a', is_admin: true }, states: {}, callWS: async msg => {
    if (msg.type.endsWith('/update')) { writes.push(msg); return {}; }
    if (msg.type === 'config/entity_registry/list') return entries;
    if (msg.type === 'config/device_registry/list') return [{ id: 'dev', name: 'QA' }];
    if (msg.type === 'config/entity_registry/get_automatic_entity_ids') return Object.fromEntries(msg.entity_ids.map(id => [id, id + '_auto']));
    if (msg.type === 'lovelace/dashboards/list') return [];
    return {};
  } };
  card._hass = hass; card._entities = entries; card._devices = [{ id: 'dev', name: 'QA' }]; card._deviceEntities = { dev: entries };
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
  wait.release({}); await applying; assert.equal(writes.length, 0); assert.equal(card._renameQueue.length, 1);
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
  card.hass = { ...hass, user: { id: 'admin-b', is_admin: true } };
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
