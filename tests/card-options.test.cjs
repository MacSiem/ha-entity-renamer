const {test}=require('node:test');
const assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
function fixture() {
 const dom=new JSDOM('',{runScripts:'dangerously',url:'http://qa.invalid/',virtualConsole:new VirtualConsole()});
 dom.window.eval(require('node:fs').readFileSync(require('node:path').join(__dirname,'../ha-entity-renamer.js'),'utf8'));
 const card=dom.window.document.createElement('ha-entity-renamer');
 card._hass={user:{id:'qa',is_admin:true},language:'en',themes:{},states:{}};
 dom.window.document.body.append(card);
 return {dom,card};
}
test('configured tabs and default are validated without resetting navigation on equal echoes',()=>{
 const {dom,card}=fixture();try {
  const config={tabs:['log','devices','invalid','log'],default_tab:'log',show_help:false};
  card.setConfig(config);
  const tabs=()=>Array.from(card.shadowRoot.querySelectorAll('.tabs [data-tab]'),n=>n.dataset.tab);
  assert.deepEqual(tabs(),['devices','log']);assert.equal(card._activeTab,'log');
  card.setActiveTab('devices');card.setConfig({...config});assert.equal(card._activeTab,'devices');
  card.setConfig({tabs:['automatic'],default_tab:'invalid'});assert.equal(card._activeTab,'automatic');
  card.setConfig({tabs:[],default_tab:'invalid'});assert.equal(card._activeTab,'devices');assert.equal(tabs().length,4);
  card.setActiveTab('<invalid>');assert.equal(card._activeTab,'devices');
 }finally{dom.window.close();}
});
test('a restricted navigation never strands a pending queue',()=>{
 const {dom,card}=fixture();try {
  card.setConfig({tabs:['devices'],show_help:false});
  card._renameQueue=[{oldId:'sensor.before',newId:'sensor.after'}];card.render();
  card.shadowRoot.querySelector('#openQueue').click();
  assert.equal(card._activeTab,'queue');assert.ok(card.shadowRoot.querySelector('[data-tab="queue"]'));
 }finally{dom.window.close();}
});
test('visual options preserve unknown fields, focused title and synchronous echoes',()=>{
 const {dom}=fixture();try {
  const editor=dom.window.document.createElement('ha-entity-renamer-editor');
  editor.setConfig({title:'Original',future:{keep:true}});dom.window.document.body.append(editor);
  let received;
  editor.addEventListener('config-changed',e=>{received=e.detail.config;editor.setConfig({...received});});
  const root=editor.shadowRoot,title=root.querySelector('#cf_title');
  for(const id of ['show_help','show_support']) {
   const input=root.querySelector('#cf_'+id);assert.ok(input);input.click();assert.equal(received[id],false);
  }
  const log=root.querySelector('[data-config-tab="log"]');log.click();assert.ok(!received.tabs.includes('log'));
  const select=root.querySelector('#cf_default_tab');select.value='automatic';select.dispatchEvent(new dom.window.Event('change'));
  assert.equal(received.default_tab,'automatic');assert.equal(received.future.keep,true);
  title.focus();title.setSelectionRange(2,4);editor.setConfig({...received});
  assert.equal(root.querySelector('#cf_title'),title);assert.equal(root.activeElement,title);assert.equal(title.selectionStart,2);
 }finally{dom.window.close();}
});
