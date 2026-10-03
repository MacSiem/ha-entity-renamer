const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { JSDOM } = require('jsdom');
async function fixture(language = 'en', admin = true) {
 const dom = new JSDOM('', { runScripts: 'dangerously', pretendToBeVisual: true, url: 'http://localhost/' });
 dom.window.eval(readFileSync('ha-entity-renamer.js', 'utf8'));
 const card = dom.window.document.createElement('ha-entity-renamer'); card.setConfig({ show_support: false }); dom.window.document.body.append(card);
 const requests = [], devices = [{ id: 'qa-device', name: 'QA device' }], entities = [{ entity_id: 'sensor.qa_old', device_id: 'qa-device', unique_id: 'qa' }];
 const hass = { language, user: { is_admin: admin }, themes: {}, states: {}, callWS: async message => {
  requests.push(message); if (message.type === 'config/device_registry/list') return devices;
  if (message.type === 'config/entity_registry/list') return entities;
  throw new Error('Fixture rejects '+message.type);
 } };
 card.hass = hass; await new Promise(resolve => setImmediate(resolve));
 return { card, dom, hass, requests };
}
test('ordinary language change translates two independent cards without reloading registries', async () => {
 const a = await fixture(), b = await fixture('pl');
 try {
  a.card.hass = { ...a.hass, language: 'pl' };
  assert.match(a.card.shadowRoot.getElementById('searchInput').placeholder, /Szukaj/);
  b.card.hass = { ...b.hass, language: 'en' };
  assert.match(b.card.shadowRoot.getElementById('searchInput').placeholder, /Search/);
  assert.match(a.card.shadowRoot.getElementById('searchInput').placeholder, /Szukaj/);
  assert.equal(a.requests.length, 2); assert.equal(b.requests.length, 2);
 } finally { a.dom.window.close(); b.dom.window.close(); }
});
test('ordinary locale preserves unqueued device and prefix drafts, focus and backward selection', async () => {
 const f = await fixture();
 try {
  f.card._selectDevice('qa-device');
  const input = f.card.shadowRoot.getElementById('deviceName'); assert.ok(input);
  input.value = 'QA unsaved room'; f.card.shadowRoot.getElementById('prefixOld').value = 'qa_'; f.card.shadowRoot.getElementById('prefixNew').value = 'new_unsaved_';
  input.focus(); input.setSelectionRange(2, 8, 'backward'); f.card.hass = { ...f.hass, language: 'pl' };
  const current = f.card.shadowRoot.getElementById('deviceName'); assert.match(current.placeholder, /Nowa nazwa/);
  assert.equal(current.value, 'QA unsaved room'); assert.equal(f.card.shadowRoot.activeElement, current);
  assert.equal(current.selectionStart, 2); assert.equal(current.selectionEnd, 8); assert.equal(current.selectionDirection, 'backward');
  assert.equal(f.card.shadowRoot.getElementById('prefixOld').value, 'qa_'); assert.equal(f.card.shadowRoot.getElementById('prefixNew').value, 'new_unsaved_');
  assert.equal(Object.keys(f.card._deviceRenameQueue).length, 0); assert.equal(f.requests.length, 2);
 } finally { f.dom.window.close(); }
});
test('ordinary role loss closes confirmation and disables registry apply without executing it', async () => {
 const f = await fixture();
 try {
  f.card._renameQueue = [{ oldId: 'sensor.qa_old', newId: 'sensor.qa_new' }]; f.card._showRenameConfirmation(); assert.ok(f.card.shadowRoot.getElementById('confirmRenameApply'));
  f.card.hass = { ...f.hass, language: 'pl', user: { is_admin: false } };
  assert.equal(f.card.shadowRoot.getElementById('confirmRenameApply'), null);
  assert.equal(f.card.shadowRoot.getElementById('executeRenames').disabled, true); assert.match(f.card.shadowRoot.textContent, /administratora/);
  assert.equal(f.card._confirmDialogOpen, false); assert.equal(f.card._renameQueue.length, 1); assert.equal(f.requests.length, 2);
 } finally { f.dom.window.close(); }
});
test('unresolved role cannot confirm or send registry updates', async () => {
 const f = await fixture();
 try {
  f.card._renameQueue = [{ oldId: 'sensor.qa_old', newId: 'sensor.qa_new' }]; f.card._activeTab = 'queue'; f.card.hass = { ...f.hass, user: {} };
  f.card._showRenameConfirmation(); assert.equal(f.card._confirmDialogOpen, false);
  await f.card._executeRenames(true); assert.equal(f.requests.length, 2); assert.equal(f.card._renameQueue.length, 1);
  assert.equal(f.card.shadowRoot.getElementById('executeRenames').disabled, true);
 } finally { f.dom.window.close(); }
});
test('same-language and same-role state updates keep the live form DOM', async () => {
 const f = await fixture();
 try {
  const input = f.card.shadowRoot.getElementById('searchInput'); input.value = 'QA unsubmitted search'; input.focus();
  f.card.hass = { ...f.hass, states: { 'sensor.qa': { state: '2' } } };
  assert.equal(f.card.shadowRoot.getElementById('searchInput'), input); assert.equal(f.card.shadowRoot.activeElement, input); assert.equal(f.requests.length, 2);
 } finally { f.dom.window.close(); }
});
