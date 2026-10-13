const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {JSDOM}=require('jsdom');
function fixture(t,scenario){
 const dom=new JSDOM('',{runScripts:'dangerously',url:'http://localhost/'});t.after(()=>dom.window.close());
 dom.window.eval(readFileSync(require('node:path').join(__dirname,'../ha-entity-renamer.js'),'utf8'));
 const card=dom.window.document.createElement('ha-entity-renamer');dom.window.document.body.append(card);
 const connection=new dom.window.EventTarget();connection.connected=true;
 const entry={id:'coverage-fixture-entry',entity_id:'sensor.coverage_old',device_id:'coverage-device',unique_id:'coverage-unique',platform:'fixture',name:null};
 const devices=[{id:'coverage-device',name:'Coverage fixture',name_by_user:null}];let writes=0;
 const hass={language:'en',user:{id:'coverage-admin',is_admin:true},connection,states:{},callWS:async msg=>{
  if(msg.type==='config/entity_registry/list')return [{...entry}];
  if(msg.type==='config/device_registry/list')return devices.map(x=>({...x}));
  if(msg.type==='search/related')return {automation:[],script:[],scene:[]};
  if(msg.type==='lovelace/dashboards/list'){
   if(scenario==='list-unavailable')throw Error('Controlled dashboard list unavailable');
   return scenario==='partial-hit'?[{url_path:'known',title:'Known fixture'},{url_path:'denied',title:'Denied fixture'}]:[];
  }
  if(msg.type==='lovelace/config'){
   if(scenario==='configs-unavailable'||scenario==='partial-hit'&&msg.url_path==='denied')throw Error('Controlled config unavailable');
   return {views:[{cards:scenario==='partial-hit'&&msg.url_path==='known'?[{type:'entities',entities:['sensor.coverage_old']}]:[]}]};
  }
  if(msg.type==='config/entity_registry/update'){
   assert.equal(msg.entity_id,'sensor.coverage_old');entry.entity_id=msg.new_entity_id;writes++;return {...entry};
  }
  throw Error('Unexpected request '+msg.type);
 }};
 card._hass=hass;card._entities=[{...entry}];card._devices=devices;card._deviceEntities={'coverage-device':card._entities};card._renameLog=[];card._bindRenameExportEvents();
 card._addToQueue('sensor.coverage_old','sensor.coverage_new');
 return {card,writes:()=>writes};
}
for(const scenario of ['list-unavailable','configs-unavailable'])test(scenario+' is unavailable rather than zero in the completed JSON/CSV report',async t=>{
 const f=fixture(t,scenario);f.card._showRenameConfirmation();await f.card._executeRenames(true);assert.equal(f.writes(),1);
 const report=JSON.parse(f.card._buildRenameReport({draft:{format:'json',includePrivate:false}}).serialized);
 assert.equal(report.rows[0].references.dashboards,null);assert.equal(report.rows[0].dashboardCoverage,'unavailable');
 const csv=f.card._buildRenameReport({draft:{format:'csv',includePrivate:false}}).serialized;assert.match(csv,/unavailable/);
});
test('partial dashboard reads retain one observed hit and disclose partial coverage',async t=>{
 const f=fixture(t,'partial-hit');f.card._showRenameConfirmation();await f.card._executeRenames(true);assert.equal(f.writes(),1);
 const report=JSON.parse(f.card._buildRenameReport({draft:{format:'json',includePrivate:false}}).serialized);
 assert.equal(report.rows[0].references.dashboards,1);assert.equal(report.rows[0].dashboardCoverage,'partial');
 assert.match(f.card.shadowRoot.textContent,/partial|unavailable|incomplete/i);
});
test('complete readable dashboards can report known zero without hiding source coverage',async t=>{
 const f=fixture(t,'complete-empty');f.card._showRenameConfirmation();await f.card._executeRenames(true);
 const report=JSON.parse(f.card._buildRenameReport({draft:{format:'json',includePrivate:false}}).serialized);
 assert.equal(report.rows[0].references.dashboards,0);assert.equal(report.rows[0].dashboardCoverage,'complete');
});
test('impact preview with no readable dashboards keeps missing coverage visible and performs no rename',async t=>{
 const f=fixture(t,'configs-unavailable');await f.card._analyzeImpact();assert.equal(f.writes(),0);
 assert.match(f.card.shadowRoot.textContent,/dashboard.*(?:unavailable|not checked)|(?:unavailable|not checked).*dashboard/i);
});
