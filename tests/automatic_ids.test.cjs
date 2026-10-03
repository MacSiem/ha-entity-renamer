const assert = require('node:assert/strict');
const { test } = require('node:test');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { JSDOM } = require('jsdom');

test('automatic ID preview separates null and collision, queues safe rows, rejects stale apply', async () => {
  const dom = new JSDOM('', { runScripts: 'dangerously', url: 'http://localhost/' });
  try {
    dom.window.eval(readFileSync(join(__dirname, '..', 'ha-entity-renamer.js'), 'utf8'));
    const card = dom.window.document.createElement('ha-entity-renamer');
    card._devices = [{ id: 'device1', name: 'Room' }];
    card._entities = [
      { entity_id: 'sensor.a', device_id: 'device1', unique_id: 'a' },
      { entity_id: 'sensor.b', device_id: 'device1', unique_id: 'b' },
      { entity_id: 'sensor.c', device_id: 'device1', unique_id: 'c' },
      { entity_id: 'sensor.occupied', device_id: 'device1', unique_id: 'occupied' },
    ];
    card._deviceEntities = { device1: card._entities };
    let currentTarget = 'sensor.new_a';
    card._hass = { language: 'en', states: {}, user: { is_admin: true }, callWS: async msg => {
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
    card._devices = [{ id: 'one', name: 'One' }, { id: 'two', name: 'Two' }];
    card._entities = Array.from({ length: 250 }, (_, i) => ({ entity_id: `sensor.old_${i}`, device_id: i < 125 ? 'one' : 'two', unique_id: String(i) }));
    card._deviceEntities = { one: card._entities.slice(0, 125), two: card._entities.slice(125) };
    const sizes = [];
    card._hass = { language: 'en', states: {}, callWS: async msg => {
      if (msg.type !== 'config/entity_registry/get_automatic_entity_ids') throw new Error(msg.type);
      sizes.push(msg.entity_ids.length);
      return Object.fromEntries(msg.entity_ids.map(id => [id, id.replace('old_', 'new_')]));
    } };
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
    card._hass = { language: 'en', states: {}, user: { is_admin: true }, callWS: () => {
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
