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
