const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {JSDOM}=require('jsdom');
const payload='<img src=x onerror=alert(1)> " \' &';

function fixture(t){
  const dom=new JSDOM('',{runScripts:'dangerously',url:'http://localhost/'});
  t.after(()=>dom.window.close());
  dom.window.eval(readFileSync(join(__dirname,'../ha-entity-renamer.js'),'utf8'));
  const card=dom.window.document.createElement('ha-entity-renamer');dom.window.document.body.append(card);
  const did='device-'+payload,oldId='sensor.old_'+payload,newId='sensor.new_'+payload;
  const entry={id:'entry-qa',entity_id:oldId,device_id:did,name:payload,original_name:payload,unique_id:'qa',platform:'qa'};
  card._hass={user:{id:'qa',is_admin:true},states:{}};
  card._devices=[{id:did,name:payload}];card._entities=[entry];card._deviceEntities={[did]:[entry]};
  return {card,did,oldId,newId};
}
function literalOnly(card){
  assert.equal(card.shadowRoot.querySelector('img,svg,script,[onerror],[onload]'),null,'untrusted strings never become active DOM');
  assert.ok(card.shadowRoot.textContent.includes(payload));
}

test('registry names and identifiers remain literal in device rows and input attributes',t=>{
  const {card,did,oldId}=fixture(t);card._expandedDevices.add(did);card._selectedDevice=did;card.render();
  literalOnly(card);
  assert.equal(card.shadowRoot.querySelector('[data-add-single]').dataset.addSingle,oldId);
  assert.equal(card.shadowRoot.querySelector('[data-device-id]').dataset.deviceId,did);
  assert.equal(card.shadowRoot.getElementById('deviceName').value,payload);
});

test('queue, device changes, impact references and persisted history render hostile strings as text',t=>{
  const {card,did,oldId,newId}=fixture(t);
  const impact={automations:[payload],scripts:[payload],dashboards:[payload],scenes:[payload]};
  card._renameQueue=[{oldId,newId,newName:payload}];card._deviceRenameQueue={[did]:payload};card._impactResults={[oldId]:impact};
  card.setActiveTab('queue');literalOnly(card);
  assert.equal(card.shadowRoot.querySelector('[data-remove-queue]').dataset.removeQueue,oldId);
  assert.equal(card.shadowRoot.querySelector('[data-remove-dev-queue]').dataset.removeDevQueue,did);
  assert.equal(card.shadowRoot.querySelector('.new').textContent,payload);
  card._renameLog=[{time:payload,oldId,newId,status:'error',error:payload,impact}];
  card.setActiveTab('log');literalOnly(card);
  assert.ok(card.shadowRoot.querySelector('.log-entry').textContent.includes(newId));
});

test('inline editor preserves hostile names and IDs as input values without creating HTML',t=>{
  const {card,oldId}=fixture(t);card._showEntityEditor(oldId);literalOnly(card);
  assert.equal(card.shadowRoot.getElementById('entityEditObjectId').value,oldId.split('.')[1]);
  assert.equal(card.shadowRoot.getElementById('entityEditName').value,payload);
});
