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
const ready = (overrides = {}) => ({
  version: '5.0.0.dev0', migration_ready: false,
  reason: 'dependency_coverage_incomplete',
  recovery_required: false, operations: [], ...overrides
});

async function fixture({admin = true, language = 'en', showHelp = true} = {}) {
  const dom = new JSDOM('', {url: 'http://localhost/', runScripts: 'dangerously', pretendToBeVisual: true});
  dom.window.eval(readFileSync('ha-entity-renamer.js', 'utf8'));
  const card = dom.window.document.createElement('ha-entity-renamer');
  card.setConfig({show_support: false, show_help: showHelp});
  dom.window.document.body.append(card);
  const messages = [];
  let backend = async () => ready();
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
      throw Error('Unexpected callWS message: ' + message.type);
    }
  };
  card.hass = hass;
  await flush();
  return {dom, card, hass, messages, connection, setBackend: fn => {backend = fn;}};
}

async function checked(f) {
  const button = f.card.shadowRoot.getElementById('checkRenamerBackendStatus');
  assert.ok(button, 'admin must be offered explicit status-check button');
  button.click();
  await flush();
  return f.card.shadowRoot.getElementById('renamerBackendStatus');
}
function code(card) {
  return card.shadowRoot.getElementById('renamerBackendStatus')?.getAttribute('data-backend-state') || null;
}
function ws(f) {
  return f.messages.filter(m => m.type.startsWith('ha_entity_renamer/'));
}
function clean(f) {f.dom.window.close();}

test('admin sees opt-in readonly action, and the legacy card never auto-queries companion', async () => {
  const f = await fixture();
  try {
    assert.equal(ws(f).length, 0);
    assert.equal(code(f.card), 'not_checked');
    assert.equal(f.card.shadowRoot.getElementById('checkRenamerBackendStatus').getAttribute('type'), 'button');
    await checked(f);
    assert.deepEqual([...ws(f)].map(row => row.type), ['ha_entity_renamer/status']);
    assert.equal(ws(f)[0].type,'ha_entity_renamer/status');
    assert.deepEqual(Object.keys(ws(f)[0]),['type']);
    assert.equal(code(f.card), 'blocked');
    assert.equal(f.messages.some(m => m.type.endsWith('/update')), false);
  } finally { clean(f); }
});

test('server status is sanitized: raw operations and version fields never enter UI', async () => {
  const f = await fixture();
  try {
    f.setBackend(async () => ready({operations:[{operation_id:'PRIVATE-OP-123',status:'SECRET-STATE',direction:'forward'}],
      private_token:'DONT-RENDER', version:'<img src=x onerror=alert(1)>'}));
    await checked(f);
    assert.equal(code(f.card), 'blocked');
    const html = f.card.shadowRoot.innerHTML;
    for (const secret of ['PRIVATE-OP-123','SECRET-STATE','DONT-RENDER','<img src=x']) assert.equal(html.includes(secret),false,secret);
  } finally {clean(f);}
});

test('backend recovery required is distinct from ordinary blocked migration', async () => {
  const f = await fixture();
  try {
    f.setBackend(async () => ready({recovery_required:true, operations:[{operation_id:'redacted'}]}));
    await checked(f);
    assert.equal(code(f.card), 'recovery_required');
    assert.match(f.card.shadowRoot.getElementById('renamerBackendStatus').textContent, /recover/i);
  } finally {clean(f);}
});

test('malformed or migration_ready=true response is never presented as ready', async () => {
  const f = await fixture();
  try {
    f.setBackend(async () => ready({migration_ready:true, recovery_required:false}));
    await checked(f);
    assert.equal(code(f.card), 'unverified');
    f.setBackend(async () => ({version:'5.0.0.dev0', migration_ready:false, recovery_required:'no'}));
    await checked(f);
    assert.equal(code(f.card), 'unverified');
    assert.doesNotMatch(f.card.shadowRoot.innerHTML, /id="applyMigration"/);
  } finally {clean(f);}
});

test('missing backend reports unavailable without surfacing native error details', async () => {
  const f = await fixture();
  try {
    f.setBackend(async () => {throw Object.assign(new Error('Sensitive YAML path /private/configuration.yaml'),{code:'unavailable'});});
    await checked(f);
    assert.equal(code(f.card), 'unavailable');
    assert.doesNotMatch(f.card.shadowRoot.innerHTML, /Sensitive YAML|private\/configuration/);
    assert.equal(f.card._renameQueue.length,0);
  } finally {clean(f);}
});

test('unregistered WebSocket command means backend not installed, not migration ready', async () => {
  const f = await fixture();
  try {
    f.setBackend(async () => {throw Object.assign(new Error('Unknown command.'),{code:'unknown_command'});});
    await checked(f);
    assert.equal(code(f.card), 'unavailable');
    assert.equal(ws(f).length,1);
  } finally {clean(f);}
});

test('authorization failures are visible only as sanitized denial states', async () => {
  const f = await fixture();
  try {
    f.setBackend(async () => {throw Object.assign(new Error('private admin account details'),{code:'unauthorized'});});
    await checked(f);
    assert.equal(code(f.card), 'unauthorized');
    assert.doesNotMatch(f.card.shadowRoot.innerHTML, /private admin account/);
  } finally {clean(f);}
});

test('unknown transport error is a safe uncertainty, never an empty migration result', async () => {
  const f = await fixture();
  try {
    f.setBackend(async () => {throw Error('secrets or transport disconnected');});
    await checked(f);
    assert.equal(code(f.card), 'unverified');
    assert.doesNotMatch(f.card.shadowRoot.innerHTML, /secrets or transport/);
  } finally {clean(f);}
});

test('household and unresolved admin cannot read the backend, including direct method call', async () => {
  const f = await fixture({admin:false});
  try {
    assert.equal(f.card.shadowRoot.getElementById('checkRenamerBackendStatus'), null);
    await f.card._checkBackendStatus();
    assert.equal(ws(f).length, 0);
    f.card.hass = {...f.hass, user:{id:'admin-a',is_admin:undefined}};
    await f.card._checkBackendStatus();
    assert.equal(ws(f).length, 0);
  } finally {clean(f);}
});

test('account role revocation discards late privileged status and purges the old projection', async () => {
  const f = await fixture();
  try {
    const waiting=deferred(); f.setBackend(()=>waiting.promise);
    f.card.shadowRoot.getElementById('checkRenamerBackendStatus').click();
    assert.equal(code(f.card),'checking');
    f.card.hass={...f.hass,user:{id:'admin-a',is_admin:false}};
    waiting.resolve(ready({operations:[{operation_id:'SECRET-STALE'}]}));
    await flush();
    assert.equal(f.card.shadowRoot.getElementById('renamerBackendStatus'),null);
    assert.equal(f.card._backendStatus,null);
    assert.doesNotMatch(f.card.shadowRoot.innerHTML,/SECRET-STALE/);
  } finally {clean(f);}
});

test('a switched administrator account cannot inherit a previous account status', async () => {
  const f = await fixture();
  try {
    const pending=deferred(); f.setBackend(()=>pending.promise);
    f.card.shadowRoot.getElementById('checkRenamerBackendStatus').click();
    f.card.hass={...f.hass,user:{id:'admin-b',is_admin:true}};
    pending.resolve(ready({recovery_required:true,operations:[{operation_id:'old-private-data'}]}));
    await flush();
    assert.equal(code(f.card),'not_checked');
    assert.equal(f.card._backendStatus,null);
    assert.doesNotMatch(f.card.shadowRoot.innerHTML,/old-private-data/);
  } finally {clean(f);}
});

test('connection replacement discards late result and requires a new explicit read', async () => {
  const f = await fixture();
  try {
    const pending=deferred();f.setBackend(()=>pending.promise);
    f.card.shadowRoot.getElementById('checkRenamerBackendStatus').click();
    const second=new f.dom.window.EventTarget();second.connected=true;
    f.card.hass={...f.hass,connection:second};
    pending.resolve(ready({recovery_required:true}));
    await flush();
    assert.equal(code(f.card),'not_checked');
    assert.equal(ws(f).length,1);
  } finally {clean(f);}
});

test('disconnect and DOM detachment cancel pending read without a hidden retry', async () => {
  const f = await fixture();
  try {
    const pending=deferred();f.setBackend(()=>pending.promise);
    f.card.shadowRoot.getElementById('checkRenamerBackendStatus').click();
    f.connection.connected=false;
    f.connection.dispatchEvent(new f.dom.window.Event('disconnected'));
    f.card.remove();
    pending.resolve(ready({recovery_required:true}));
    await flush();
    assert.equal(f.card._backendStatus,null);
    assert.equal(ws(f).length,1);
  } finally {clean(f);}
});

test('same-session state and locale changes retain only sanitized status without new requests', async () => {
  const f = await fixture();
  try {
    await checked(f);
    const count=ws(f).length;
    f.card.hass={...f.hass,states:{'sensor.qa':{state:'2'}},language:'pl'};
    assert.equal(code(f.card),'blocked');
    assert.match(f.card.shadowRoot.getElementById('renamerBackendStatus').textContent,/niegotow|zalezn|migracj|zależn|gotow/i);
    assert.equal(ws(f).length,count);
  } finally {clean(f);}
});

test('show_help=false leaves legacy card and network unchanged', async () => {
  const f = await fixture({showHelp:false});
  try {
    assert.equal(f.card.shadowRoot.getElementById('checkRenamerBackendStatus'),null);
    assert.equal(ws(f).length,0);
    assert.ok(f.card.shadowRoot.getElementById('searchInput'));
  } finally {clean(f);}
});

test('rapid repeated checks coalesce into one pending read', async () => {
  const f = await fixture();
  try {
    const pending=deferred(); f.setBackend(()=>pending.promise);
    const p=f.card._checkBackendStatus();
    assert.equal(code(f.card),'checking');
    const p2=f.card._checkBackendStatus();
    await flush();
    assert.equal(ws(f).length,1);
    pending.resolve(ready());
    await Promise.all([p,p2]);
    assert.equal(code(f.card),'blocked');
  } finally {clean(f);}
});

// Regression: a delayed diagnostic response must not interrupt another task.
for (const outcome of ['success', 'error']) {
  test(`delayed status ${outcome} preserves the open export and its active filename draft`, async () => {
    const f = await fixture();
    try {
      const pending = deferred(); f.setBackend(() => pending.promise);
      f.card.shadowRoot.getElementById('checkRenamerBackendStatus').click();
      f.card.setActiveTab('log');
      f.card.shadowRoot.getElementById('exportRenameLog').click();
      await flush();
      const host = f.card.shadowRoot.querySelector('[data-ha-tools-export]');
      assert.ok(host, 'real export dialog opens while status is pending');
      const dialog = host.shadowRoot;
      const filename = dialog.querySelector('[data-export="filename"]');
      filename.value = 'my-reviewed-history.json';
      filename.focus(); filename.setSelectionRange(3, 11);
      if (outcome === 'success') pending.resolve(ready());
      else pending.reject({code: 'unavailable'});
      await flush();
      assert.equal(code(f.card), outcome === 'success' ? 'blocked' : 'unavailable');
      assert.equal(host.isConnected, true, 'status must not cancel an independent export');
      assert.equal(f.card.shadowRoot.activeElement, host);
      assert.equal(dialog.activeElement, filename, 'filename editing keeps keyboard focus');
      assert.equal(filename.value, 'my-reviewed-history.json');
      assert.equal(filename.selectionStart, 3);
      assert.equal(filename.selectionEnd, 11);
      assert.equal(f.card.shadowRoot.getElementById('checkRenamerBackendStatus').disabled, false);
    } finally { clean(f); }
  });

  test(`delayed status ${outcome} preserves an active card field and its selection`, async () => {
    const f = await fixture();
    try {
      const pending = deferred(); f.setBackend(() => pending.promise);
      f.card.shadowRoot.getElementById('checkRenamerBackendStatus').click();
      const field = f.card.shadowRoot.getElementById('searchInput');
      field.value = 'unfinished search';
      field.focus(); field.setSelectionRange(2, 7);
      if (outcome === 'success') pending.resolve(ready());
      else pending.reject({code: 'unavailable'});
      await flush();
      assert.equal(code(f.card), outcome === 'success' ? 'blocked' : 'unavailable');
      assert.equal(f.card.shadowRoot.activeElement, field, 'status must not steal keyboard focus');
      assert.equal(field.value, 'unfinished search');
      assert.equal(field.selectionStart, 2);
      assert.equal(field.selectionEnd, 7);
    } finally { clean(f); }
  });
}
