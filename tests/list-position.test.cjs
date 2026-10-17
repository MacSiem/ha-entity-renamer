const assert = require('node:assert/strict');
const {test} = require('node:test');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {JSDOM} = require('jsdom');
function fixture(language='en') {
 const dom=new JSDOM('',{runScripts:'dangerously',url:'http://localhost/'});
 dom.window.eval(readFileSync(join(__dirname,'../ha-entity-renamer.js'),'utf8'));
 const card=dom.window.document.createElement('ha-entity-renamer');dom.window.document.body.append(card);
 card._lang=language;card._hass={language,user:{id:'admin',is_admin:true},states:{},config:{time_zone:'Pacific/Auckland'}};
 card._devices=[{id:'one',name:'QA Device'}];
 card._entities=[1,2,3].map(i=>({entity_id:'sensor.original_'+i,device_id:'one',unique_id:String(i),original_name:'Reading '+i}));
 card._deviceEntities={one:card._entities};card._automaticRows=card._entities.map(e=>({oldId:e.entity_id,newId:e.entity_id+'_automatic',deviceId:'one'}));card._automaticLoaded=true;
 card.render();return {dom,card,root:card.shadowRoot};
}
test('expanding a device retains the list scroll position and focused device',()=>{
 const {dom,root}=fixture();
 try {
  const list=root.querySelector('.device-list');list.scrollTop=2362;
  const header=root.querySelector('[data-device-id="one"]');header.focus();header.click();
  assert.equal(root.querySelector('.device-list').scrollTop,2362);
  assert.equal(root.activeElement?.getAttribute('data-device-id'),'one');
  assert.equal(root.activeElement?.getAttribute('aria-expanded'),'true');
 } finally {dom.window.close();}
});
test('automatic summary keeps its native toggle and never enters the Devices handler',()=>{
 const {dom,card,root}=fixture();
 try {
  card._activeTab='automatic';card.render();const summary=root.querySelector('[data-auto-group] summary');summary.focus();summary.click();
  assert.equal(root.querySelector('[data-auto-group]').open,true);
  assert.equal(root.contains(summary),true,'native details toggling does not replace the list');
  assert.equal(card._expandedDevices.size,0);
 } finally {dom.window.close();}
});
for(const language of ['en','pl']) test(`automatic queued status is text with one Remove action and focus stays on that entity (${language})`,()=>{
 const {dom,card,root}=fixture(language);
 try {
  card._activeTab='automatic';card.render();root.querySelector('[data-auto-group]').open=true;
  const queue=root.querySelector('[data-auto-entity="sensor.original_1"]');queue.focus();queue.click();
  const row=root.querySelector('[data-remove-queue="sensor.original_1"]').closest('.rename-preview-row');
  assert.equal(row.querySelectorAll('button').length,1,'queued status is not a second button');
  assert.match(row.textContent,language==='pl'?/W kolejce/:/In Queue/);
  assert.equal(root.activeElement?.getAttribute('data-remove-queue'),'sensor.original_1','focus follows the same entity action');
  assert.equal(root.querySelector('[data-auto-group]').open,true);
  root.activeElement.click();assert.equal(card._renameQueue.length,0);
  assert.equal(root.activeElement?.getAttribute('data-auto-entity'),'sensor.original_1','Remove returns focus to Queue for the same entity');
 } finally {dom.window.close();}
});
test('automatic and Devices headers use the same readable entity count',()=>{
 const {dom,card,root}=fixture();
 try {
  const devicesText=root.querySelector('[data-device-id="one"]').textContent.replace(/\s+/g,' ').trim();
  card._activeTab='automatic';card.render();const automaticText=root.querySelector('[data-auto-group] summary').textContent.replace(/\s+/g,' ').trim();
  assert.match(devicesText,/3 entities.*0 in queue/);assert.match(automaticText,/3 entities.*0 in queue/);
 } finally {dom.window.close();}
});
for(const server of [false,true]) test(`history uses ${server?'explicit server':'default system'} zone and System date language`,()=>{
 const {dom,card,root}=fixture('pl');
 try {
  Object.defineProperty(dom.window.navigator,'language',{value:'en-US',configurable:true});
  card._hass.locale={date_format:'system',time_format:'24',...(server?{time_zone:'server'}:{})};
  const completedAt='2026-01-01T00:30:00Z';card._renameLog=[{time:'00:30',completedAt,oldId:'sensor.old',newId:'sensor.new',status:'ok',writePhase:'readback'}];card._activeTab='log';card.render();
  const zone=server?'Pacific/Auckland':new Intl.DateTimeFormat().resolvedOptions().timeZone;
  assert.equal(root.querySelector('.log-time').textContent,new Intl.DateTimeFormat('en-US',{timeZone:zone,dateStyle:'medium',timeStyle:'short',hourCycle:'h23'}).format(new Date(completedAt)));
  assert.match(root.querySelector('.log-entry details').textContent,/2026-01-01T00:30:00Z/);
  assert.ok(root.querySelector('.log-entry details').textContent.includes(zone),'details identify the displayed zone');
 } finally {dom.window.close();}
});
