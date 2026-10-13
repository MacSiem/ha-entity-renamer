const SOURCE="ha-entity-renamer.js";
const TAG="ha-entity-renamer";
const NAV=".tabs";
const ATTR="data-tab";
const STATE="_activeTab";
const DRAFT_ID="deviceName";
// Source-only regression proposal. Owner executes on frozen source through FIFO.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {JSDOM,VirtualConsole}=require('jsdom');
function fixture(){
 const dom=new JSDOM('<body><button id="outside">Outside</button></body>',{runScripts:'outside-only',pretendToBeVisual:true,url:'http://synthetic.invalid/',virtualConsole:new VirtualConsole()});
 const observers=[];
 dom.window.ResizeObserver=class{constructor(callback){this.callback=callback;this.targets=new Set();observers.push(this);}observe(target){this.targets.add(target);}disconnect(){this.targets.clear();}};
 dom.window.eval(readFileSync(SOURCE,'utf8'));
 const card=dom.window.document.createElement(TAG);dom.window.document.body.append(card);
 const calls=[];
 card._hass={language:'en',user:{id:'synthetic-admin',is_admin:true},states:{},themes:{},callWS:async message=>{calls.push(message);return [];}};card._lang='en';
 card._config={show_support:false};card._devices=[{id:'synthetic-device',name:'Synthetic device'}];card._entities=[{entity_id:'sensor.synthetic',device_id:'synthetic-device',unique_id:'synthetic'}];card._deviceEntities={'synthetic-device':card._entities};
 card.render();
 const native=()=>[...card.shadowRoot.querySelectorAll(NAV+' button['+ATTR+']')];
 const picker=()=>card.shadowRoot.getElementById('ha-tools-tab-picker');
 const resize=width=>{const nav=card.shadowRoot.querySelector(NAV);const target=nav.parentElement;
  const observer=observers.find(o=>o.targets.has(target));assert.ok(observer,'actual navigation container is observed');
  observer.callback([{target,contentRect:{width}}]);};
 const choose=value=>{const select=picker();select.value=value;select.dispatchEvent(new dom.window.Event('change',{bubbles:true}));};
 return {dom,card,calls,observers,native,picker,resize,choose};
}
test('all current navigation options reuse real handlers, selected panes and labels',()=>{
 const f=fixture();try{
  const expected=['devices','automatic','queue','log'];
  assert.deepEqual(f.native().map(b=>b.getAttribute(ATTR)),expected);
  const select=f.picker();assert.ok(select);assert.ok(select.labels.length,'native associated label');
  assert.deepEqual([...select.options].map(o=>o.value),expected);
  assert.deepEqual([...select.options].map(o=>o.textContent),f.native().map(b=>b.getAttribute('aria-label')||b.textContent.trim()));
  const reads=f.calls.length;
  for(const value of expected){f.choose(value);assert.equal(f.card[STATE],value);assert.equal(f.picker().value,value);assert.equal(f.native().find(b=>b.getAttribute(ATTR)===value).classList.contains('active'),true);}
  const invalid=f.dom.window.document.createElement('option');invalid.value='foreign';f.picker().append(invalid);
  f.choose('foreign');assert.equal(f.card[STATE],expected.at(-1));assert.equal(f.picker().value,expected.at(-1));
  assert.equal(f.calls.length,reads,'navigation does not introduce API reads or writes');
 }finally{f.card.remove();f.dom.window.close();}
});
test('available navigation width transfers focus, retaining drafts and caret without rebuilding content',()=>{
 const f=fixture();try{
  const reads=f.calls.length,first=f.native()[0];first.focus();f.resize(260);
  assert.equal(f.card.shadowRoot.querySelector(NAV).hidden,true);assert.equal(f.card.shadowRoot.activeElement,f.picker());
  f.resize(320);assert.equal(f.card.shadowRoot.querySelector(NAV).hidden,false);assert.equal(f.card.shadowRoot.activeElement,f.native()[0]);
  f.native().find(b=>b.getAttribute(ATTR)==='devices').click();f.card.shadowRoot.querySelector('[data-device-id="synthetic-device"]').click();
  const input=f.card.shadowRoot.getElementById(DRAFT_ID);input.value='synthetic unsubmitted draft';input.focus();input.setSelectionRange(2,8,'backward');
  f.resize(260);f.resize(900);
  assert.equal(f.card.shadowRoot.getElementById(DRAFT_ID),input);assert.equal(f.card.shadowRoot.activeElement,input);
  assert.equal(input.value,'synthetic unsubmitted draft');assert.equal(input.selectionStart,2);assert.equal(input.selectionEnd,8);assert.equal(input.selectionDirection,'backward');
  f.card.render();const restored=f.card.shadowRoot.getElementById(DRAFT_ID);
  assert.equal(restored.value,'synthetic unsubmitted draft');assert.equal(f.card.shadowRoot.activeElement,restored);assert.equal(restored.selectionStart,2);assert.equal(restored.selectionEnd,8);assert.equal(restored.selectionDirection,'backward');
  assert.equal(f.calls.length,reads);
  f.resize(260);f.picker().focus();f.card._lang='pl';f.card._hass.language='pl';f.card.render();
  assert.equal(f.card.shadowRoot.activeElement,f.picker());assert.equal(f.picker().labels[0].textContent.trim(),'Zakładka');
  assert.deepEqual([...f.picker().options].map(o=>o.textContent),f.native().map(b=>b.getAttribute('aria-label')||b.textContent.trim()));
  f.dom.window.document.getElementById('outside').focus();f.card.render();assert.equal(f.dom.window.document.activeElement.id,'outside');
 }finally{f.card.remove();assert.equal(f.observers.some(o=>o.targets.size),false,'all resize observers clean up on detach');f.dom.window.close();}
});
test('active picker follows native navigation and no detached containers remain observed after rerenders',()=>{
 const f=fixture();try{
  f.resize(260);f.choose(['devices','automatic','queue','log'][1]);f.native()[2].click();assert.equal(f.picker().value,['devices','automatic','queue','log'][2]);
  const current=f.card.shadowRoot.querySelector(NAV).parentElement;
  for(const observer of f.observers)for(const target of observer.targets)assert.ok(target===f.card||target===current);
  f.card._renameQueue=[{oldId:'sensor.synthetic',newId:'sensor.revised'}];f.card._activeTab='queue';f.card._hass.user.is_admin=false;f.card.render();assert.deepEqual([...f.picker().options].map(o=>o.value),f.native().map(b=>b.getAttribute(ATTR)));assert.equal(f.card.shadowRoot.getElementById('executeRenames')?.disabled,true);
 }finally{f.card.remove();assert.equal(f.observers.some(o=>o.targets.size),false);f.dom.window.close();}
});
test('every visible form field has a native label or accessible name, and editor preserves unknown config',()=>{
 const f=fixture();try{
  for(const input of f.card.shadowRoot.querySelectorAll('input,select,textarea'))assert.ok(input.labels?.length||input.getAttribute('aria-label')||input.getAttribute('aria-labelledby'),input.outerHTML);
  const editor=f.dom.window.document.createElement(TAG+'-editor');editor.setConfig({title:'Synthetic',unknown_setting:{keep:true}});f.dom.window.document.body.append(editor);
  const input=editor.shadowRoot.getElementById('cf_title');assert.ok(input.labels.length||input.getAttribute('aria-label'));
  let changed;editor.addEventListener('config-changed',e=>changed=e.detail.config);input.value='Revised';input.dispatchEvent(new f.dom.window.Event('input',{bubbles:true}));
  assert.equal(changed.title,'Revised');assert.equal(changed.unknown_setting.keep,true);
 }finally{f.card.remove();f.dom.window.close();}
});

test('detaching stops actual registered browser and connection event callbacks',()=>{
 const f=fixture();try {
  const connection=new f.dom.window.EventTarget();connection.connected=true;f.card._hass.connection=connection;f.card._bindRenameExportEvents();
  f.card.remove();const revision=f.card._exportRevision,epoch=f.card._readEpoch;
  f.dom.window.dispatchEvent(new f.dom.window.Event('offline'));connection.dispatchEvent(new f.dom.window.Event('disconnected'));
  assert.equal(f.card._exportRevision,revision,'detached export must not react to browser or old connection');assert.equal(f.card._readEpoch,epoch);
 }finally{f.card.remove();f.dom.window.close();}
});


test('device headers support Enter and Space through original expand/select behavior and retain focus',()=>{
 const f=fixture();try{const header=()=>f.card.shadowRoot.querySelector('.device-header');header().focus();header().dispatchEvent(new f.dom.window.KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));
  assert.equal(f.card._selectedDevice,'synthetic-device');assert.equal(f.card._expandedDevices.has('synthetic-device'),true);assert.equal(header().tagName,'BUTTON');assert.equal(header().getAttribute('role'),'button');assert.equal(header().tabIndex,0);assert.equal(header().getAttribute('aria-expanded'),'true');assert.equal(f.card.shadowRoot.activeElement,header());
  header().dispatchEvent(new f.dom.window.KeyboardEvent('keydown',{key:' ',bubbles:true,cancelable:true,repeat:true}));assert.equal(f.card._expandedDevices.has('synthetic-device'),true);
  f.card._lang='pl';f.card.render();assert.equal(f.card.shadowRoot.activeElement,header());assert.match(header().getAttribute('aria-label'),/Synthetic device/);
  header().dispatchEvent(new f.dom.window.KeyboardEvent('keydown',{key:' ',bubbles:true,cancelable:true}));assert.equal(f.card._selectedDevice,null);assert.equal(header().getAttribute('aria-expanded'),'false');assert.equal(f.card.shadowRoot.activeElement,header());assert.equal(f.calls.length,0);
 }finally{f.card.remove();f.dom.window.close()}
});
