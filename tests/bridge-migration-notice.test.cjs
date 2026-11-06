const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {JSDOM,VirtualConsole}=require('jsdom');
function fixture(){
 const dom=new JSDOM('',{runScripts:'dangerously',url:'http://qa.invalid/',pretendToBeVisual:true,virtualConsole:new VirtualConsole()});
 const storage=dom.window.localStorage;
 storage.setItem('ha-tools-entity-renamer-history','[{"oldId":"sensor.legacy"}]');
 storage.setItem('ha-tools-entity-renamer-history-v2-existing','preserve-exact');
 dom.window.eval(readFileSync(join(__dirname,'../ha-entity-renamer.js'),'utf8'));
 const card=dom.window.document.createElement('ha-entity-renamer');
 card._hass={user:{id:'first',is_admin:true},language:'en',themes:{},states:{}};
 card.setConfig({title:'Original',show_help:false,unknown:{retained:true}});dom.window.document.body.append(card);
 return {dom,card,storage,root:card.shadowRoot};
}
test('notice preserves existing data, queue and configuration, scopes dismissal and reopens',()=>{
 const f=fixture();try{
  f.card._renameQueue=[{oldId:'sensor.a',newId:'sensor.b'}];f.card.render();
  let notice=f.root.querySelector('.bridge-notice');assert.equal(notice.getAttribute('role'),'region');
  assert.equal(notice.getAttribute('aria-labelledby'),'bridgeNoticeTitle');
  assert.match(notice.textContent,/do not remove your current installation now/);
  assert.match(notice.textContent,/full Home Assistant backup/);
  const link=notice.querySelector('a');assert.equal(link.href,'https://github.com/MacSiem/ha-entity-renamer/blob/main/MIGRATION.md');assert.equal(link.rel,'noopener noreferrer');
  f.root.querySelector('#bridgeNoticeDismiss').click();assert.equal(f.root.querySelector('.bridge-notice'),null);
  assert.equal(f.root.activeElement.id,'bridgeNoticeOpen');assert.equal(f.card._renameQueue.length,1);
  assert.equal(f.card._config.unknown.retained,true);assert.equal(f.storage.getItem('ha-tools-entity-renamer-history'),'[{"oldId":"sensor.legacy"}]');
  assert.equal(f.storage.getItem('ha-tools-entity-renamer-history-v2-existing'),'preserve-exact');
  f.card._bridgeNotice=null;f.card.render();assert.ok(f.root.querySelector('#bridgeNoticeOpen'));
  f.card._hass={...f.card._hass,user:{id:'second',is_admin:true}};f.card.render();assert.ok(f.root.querySelector('.bridge-notice'));
  f.card._hass={...f.card._hass,user:{id:'first',is_admin:true}};f.card.render();assert.ok(f.root.querySelector('#bridgeNoticeOpen'));
  f.root.querySelector('#bridgeNoticeOpen').click();assert.ok(f.root.querySelector('.bridge-notice'));assert.equal(f.root.activeElement.id,'bridgeNoticeDismiss');
  f.card._bridgeNotice=null;f.card.render();assert.ok(f.root.querySelector('.bridge-notice'));
 }finally{f.dom.window.close();}
});
test('Polish notice follows language and remains independent of help settings',()=>{
 const f=fixture();try{
  f.card._lang='pl';f.card.render();const text=f.root.querySelector('.bridge-notice').textContent;
  assert.match(text,/nie usuwaj teraz obecnej instalacji/);assert.match(text,/pełną kopię zapasową Home Assistant/);assert.match(text,/jednorazowej migracji/);
  assert.ok(f.root.querySelector('#bridgeNoticeDismiss'));assert.equal(f.root.querySelector('.renamer-help'),null);
  f.card._hass={...f.card._hass,user:null};f.card.render();assert.equal(f.root.querySelector('.bridge-notice'),null);
 }finally{f.dom.window.close();}
});
