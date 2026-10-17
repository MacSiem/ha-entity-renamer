const assert = require('node:assert/strict');
const { test } = require('node:test');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { JSDOM } = require('jsdom');

// Catch registry-order rendering, surprise navigation and stale queue affordances.
function automaticView() {
  const dom = new JSDOM('', { runScripts: 'dangerously', url: 'http://localhost/' });
  dom.window.eval(readFileSync(join(__dirname, '..', 'ha-entity-renamer.js'), 'utf8'));
  const card = dom.window.document.createElement('ha-entity-renamer');
  dom.window.document.body.append(card);
  card._hass = { language: 'en', user: { id: 'admin', is_admin: true }, states: {} };
  card._devices = [{ id: 'z', name: 'Alpha', name_by_user: 'Zulu' }, { id: 'a', name: 'Alpha' }];
  card._deviceEntities = {};
  card._automaticRows = [
    { oldId: 'sensor.z1', newId: 'sensor.new_z1', deviceId: 'z' },
    { oldId: 'sensor.a1', newId: 'sensor.new_a1', deviceId: 'a' },
    { oldId: 'sensor.a2', newId: 'sensor.new_a2', deviceId: 'a' },
    { oldId: 'sensor.conflict', newId: 'sensor.occupied', deviceId: 'a', conflict: true },
  ];
  card._automaticLoaded = true;
  card._activeTab = 'automatic';
  card.render();
  return { dom, card, root: card.shadowRoot };
}

test('automatic devices follow displayed names rather than registry entity order', () => {
  const {dom, root} = automaticView();
  try {
    assert.deepEqual(Array.from(root.querySelectorAll('[data-auto-group] > summary .device-name'), el => el.textContent.trim()), ['Alpha', 'Zulu']);
  } finally { dom.window.close(); }
});

for (const action of ['entity', 'device', 'all']) test(`automatic ${action} queue keeps the current tab and expanded device`, () => {
  const {dom, card, root} = automaticView();
  try {
    const group = Array.from(root.querySelectorAll('.automatic-tab details')).find(el => el.textContent.includes('sensor.a1'));
    group.open = true;
    const selector = action === 'entity' ? '[data-auto-entity="sensor.a1"]' : action === 'device' ? '[data-auto-device="a"]' : '#queueAllAutomatic';
    root.querySelector(selector).click();
    assert.equal(card._activeTab, 'automatic');
    assert.ok(root.querySelector('.automatic-tab'));
    assert.equal(Array.from(root.querySelectorAll('.automatic-tab details')).find(el => el.textContent.includes('sensor.a1')).open, true);
    assert.equal(card._renameQueue.length, {entity: 1, device: 2, all: 3}[action]);
  } finally { dom.window.close(); }
});

test('queued entities and devices show status, avoid duplicates and become available after removal', () => {
  const {dom, card, root} = automaticView();
  try {
    root.querySelector('[data-auto-entity="sensor.a1"]').click();
    assert.equal(root.querySelector('[data-auto-entity="sensor.a1"]'), null);
    const row = root.querySelector('[data-remove-queue="sensor.a1"]').closest('.rename-preview-row');
    assert.match(row.querySelector('.queued-badge').textContent, /In Queue/i);
    assert.equal(row.querySelectorAll('button').length, 1);
    assert.equal(root.querySelector('[data-auto-device="a"]').disabled, false, 'one eligible entity remains');
    root.querySelector('[data-auto-device="a"]').click();
    assert.equal(card._renameQueue.length, 2);
    assert.equal(root.querySelector('[data-auto-device="a"]').disabled, true);
    assert.match(root.querySelector('[data-auto-device="a"]').textContent, /In Queue/i);
    card._queueAutomaticIds('a');
    assert.equal(card._renameQueue.length, 2);
    card._removeFromQueue('sensor.a1');
    assert.equal(root.querySelector('[data-auto-entity="sensor.a1"]').disabled, false);
    assert.equal(root.querySelector('[data-auto-device="a"]').disabled, false);
    root.querySelector('#queueAllAutomatic').click();
    assert.equal(card._renameQueue.length, 3);
    assert.equal(root.querySelector('#queueAllAutomatic').disabled, true);
    card._clearQueue();
    assert.equal(root.querySelector('#queueAllAutomatic').disabled, false);
    assert.equal(root.querySelectorAll('[data-auto-entity]:disabled').length, 0);
    assert.equal(card._renameQueue.length, 0);
  } finally { dom.window.close(); }
});

test('queued automatic status follows Polish labels and never enables conflicts', () => {
  const {dom, card, root} = automaticView();
  try {
    card._lang = 'pl';
    card._queueAutomaticIds();
    assert.match(root.querySelector('[data-remove-queue="sensor.a1"]').closest('.rename-preview-row').textContent, /W kolejce/);
    assert.equal(root.querySelector('[data-auto-entity="sensor.conflict"]'), null);
    assert.equal(card._renameQueue.length, 3);
  } finally { dom.window.close(); }
});

test('automatic ID preview separates null and collision, queues safe rows, rejects stale apply', async () => {
  const dom = new JSDOM('', { runScripts: 'dangerously', url: 'http://localhost/' });
  try {
    dom.window.eval(readFileSync(join(__dirname, '..', 'ha-entity-renamer.js'), 'utf8'));
    const card = dom.window.document.createElement('ha-entity-renamer');
    dom.window.document.body.append(card);
    const connection = new dom.window.EventTarget(); connection.connected = true;
    card._devices = [{ id: 'device1', name: 'Room' }];
    card._entities = [
      { entity_id: 'sensor.a', device_id: 'device1', unique_id: 'a' },
      { entity_id: 'sensor.b', device_id: 'device1', unique_id: 'b' },
      { entity_id: 'sensor.c', device_id: 'device1', unique_id: 'c' },
      { entity_id: 'sensor.occupied', device_id: 'device1', unique_id: 'occupied' },
    ];
    card._deviceEntities = { device1: card._entities };
    let currentTarget = 'sensor.new_a';
    card._hass = { language: 'en', connection, states: {}, user: { id: 'admin-a', is_admin: true }, callWS: async msg => {
      if (msg.type === 'config/entity_registry/list') return card._entities;
      if (msg.type === 'config/device_registry/list') return card._devices;
      if (msg.type === 'lovelace/dashboards/list') return [];
      if (msg.type === 'lovelace/config') return {};
      if (msg.type === 'config/entity_registry/get_automatic_entity_ids') {
        return Object.fromEntries(msg.entity_ids.map(id => [id, {
          'sensor.a': currentTarget, 'sensor.b': null,
          'sensor.c': 'sensor.occupied', 'sensor.occupied': 'sensor.occupied',
        }[id]]));
      }
      throw new Error(msg.type);
    } };
    card._bindRenameExportEvents();
    await card._loadAutomaticIds();
    assert.equal(card._automaticRows.length, 2);
    assert.deepEqual(Array.from(card._automaticNull, row => row.entity_id), ['sensor.b']);
    assert.equal(card._automaticRows.find(row => row.oldId === 'sensor.c').conflict, true);
    assert.match(card._renderAutomaticTab(), /sensor\.b/);
    card._queueAutomaticIds();
    assert.deepEqual(Array.from(card._renameQueue, row => row.oldId), ['sensor.a']);
    currentTarget = 'sensor.changed';
    const failures = await card._revalidateAutomaticQueue();
    assert.match(failures.get('sensor.a'), /changed since preview/);
    card._showRenameConfirmation();
    await card._executeRenames(true);
    assert.equal(card._renameQueue.length, 1);
    assert.equal(card._renameLog[0].status, 'error');
  } finally { dom.window.close(); }
});

test('large automatic preview batches HA requests and queues one device', async () => {
  const dom = new JSDOM('', { runScripts: 'dangerously', url: 'http://localhost/' });
  try {
    dom.window.eval(readFileSync(join(__dirname, '..', 'ha-entity-renamer.js'), 'utf8'));
    const card = dom.window.document.createElement('ha-entity-renamer');
    dom.window.document.body.append(card);
    const connection = new dom.window.EventTarget(); connection.connected = true;
    card._devices = [{ id: 'one', name: 'One' }, { id: 'two', name: 'Two' }];
    card._entities = Array.from({ length: 250 }, (_, i) => ({ entity_id: `sensor.old_${i}`, device_id: i < 125 ? 'one' : 'two', unique_id: String(i) }));
    card._deviceEntities = { one: card._entities.slice(0, 125), two: card._entities.slice(125) };
    const sizes = [];
    card._hass = { language: 'en', connection, user: {id: 'admin-a', is_admin: true}, states: {}, callWS: async msg => {
      if (msg.type !== 'config/entity_registry/get_automatic_entity_ids') throw new Error(msg.type);
      sizes.push(msg.entity_ids.length);
      return Object.fromEntries(msg.entity_ids.map(id => [id, id.replace('old_', 'new_')]));
    } };
    card._bindRenameExportEvents();
    await card._loadAutomaticIds();
    assert.deepEqual(sizes, [100, 100, 50]);
    assert.equal(card._automaticRows.length, 250);
    card._queueAutomaticIds('one');
    assert.equal(card._renameQueue.length, 125);
    assert.ok(card._renameQueue.every(row => row.deviceId === 'one'));
  } finally { dom.window.close(); }
});


test('large queue can be abandoned before keyboard traversal of hundreds of per-entity actions', () => {
  const dom = new JSDOM('', { runScripts: 'dangerously', url: 'http://localhost/' });
  try {
    dom.window.eval(readFileSync(join(__dirname, '..', 'ha-entity-renamer.js'), 'utf8'));
    const card = dom.window.document.createElement('ha-entity-renamer');
    dom.window.document.body.append(card);
    const connection = new dom.window.EventTarget(); connection.connected = true;
    card._hass = { language: 'en', connection, states: {}, user: { id: 'admin-a', is_admin: true }, callWS: () => {
      throw new Error('Clearing a preview must not write to Home Assistant');
    } };
    card._activeTab = 'queue';
    card._renameQueue = Array.from({ length: 881 }, (_, i) => ({ oldId: `sensor.old_${i}`, newId: `sensor.new_${i}` }));
    card.render();
    const queueStops = card.shadowRoot.querySelectorAll('.queue-list button, .queue-actions button');
    assert.equal(queueStops[0].id, 'clearQueue', 'Whole-queue cancellation must precede hundreds of individual remove actions');
    queueStops[0].click();
    assert.equal(card._renameQueue.length, 0);
    assert.match(card.shadowRoot.textContent, /Queue is empty/);
    assert.equal(card.shadowRoot.querySelectorAll('[data-remove-queue]').length, 0);
  } finally { dom.window.close(); }
});
