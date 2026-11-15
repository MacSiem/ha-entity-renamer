const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {JSDOM}=require('jsdom');
function fixture(t,scenario){
 const dom=new JSDOM('',{runScripts:'dangerously',url:'http://localhost/'});t.after(()=>dom.window.close());
 dom.window.eval(readFileSync(require('node:path').join(__dirname,'../custom_components/ha_entity_renamer/www/ha-entity-renamer.js'),'utf8'));
 const card=dom.window.document.createElement('ha-entity-renamer');dom.window.document.body.append(card);
 const connection=new dom.window.EventTarget();connection.connected=true;
 const entry={id:'coverage-fixture-entry',entity_id:'sensor.coverage_old',device_id:'coverage-device',unique_id:'coverage-unique',platform:'fixture',name:null};
 const devices=[{id:'coverage-device',name:'Coverage fixture',name_by_user:null}];let writes=0;
 const fingerprint='d'.repeat(64), operation_id='coverage_review';
 const dashboards=['partial-hit','default-hit'].includes(scenario)?[{route:scenario==='default-hit'?null:'private-dashboard-route-qa',references:[{before:'sensor.coverage_old',after:'sensor.coverage_new',occurrences:1}]}]:[];
 const review={operation_id,fingerprint,changes:[{entity_id:'sensor.coverage_old',fields:{entity_id:{before:'sensor.coverage_old',after:'sensor.coverage_new'}}}],dependencies:{domains:dashboards.length?['dashboard']:[],dashboards,files:[],yaml_file_count:0,template_consumer_count:0,coverage:[{source:'dashboard',status:scenario==='complete-empty'?'COMPLETE':'UNKNOWN',reason:'',blockers:[]}]}};
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
  if(msg.type==='ha_entity_renamer/prepare')return review;
  if(msg.type==='ha_entity_renamer/apply'){entry.entity_id='sensor.coverage_new';writes++;return {operation_id,fingerprint,state:'applied'};}
  if(msg.type==='ha_entity_renamer/status')return {operations:[]};
  if(msg.type==='ha_entity_renamer/recovery/inspect')return {operation_id,fingerprint,state:'applied',eligible:true};
  throw Error('Unexpected request '+msg.type);
 }};
 card._hass=hass;card._entities=[{...entry}];card._devices=devices;card._deviceEntities={'coverage-device':card._entities};card._renameLog=[];card._bindRenameExportEvents();
 card._addToQueue('sensor.coverage_old','sensor.coverage_new');
 async function apply(){card._showRenameConfirmation();await card._executeRenames(true);assert.equal(card._migration.stage,'review');card.shadowRoot.getElementById('applyMigration').click();card.shadowRoot.getElementById('continueWriteWarning').click();await new Promise(resolve=>setImmediate(resolve));await new Promise(resolve=>setImmediate(resolve));}
 return {card,writes:()=>writes,apply};
}
for(const scenario of ['list-unavailable','configs-unavailable'])test(scenario+' is unavailable rather than zero in the completed JSON/CSV report',async t=>{
 const f=fixture(t,scenario);await f.apply();assert.equal(f.writes(),1);
 const report=JSON.parse(f.card._buildRenameReport({draft:{format:'json',includePrivate:false}}).serialized);
 assert.equal(report.rows[0].references.dashboards,null);assert.equal(report.rows[0].dashboardCoverage,'unavailable');
 const csv=f.card._buildRenameReport({draft:{format:'csv',includePrivate:false}}).serialized;assert.match(csv,/unavailable/);
});
test('partial dashboard reads retain one observed hit and disclose partial coverage',async t=>{
 const f=fixture(t,'partial-hit');await f.apply();assert.equal(f.writes(),1);
 const report=JSON.parse(f.card._buildRenameReport({draft:{format:'json',includePrivate:false}}).serialized);
 assert.equal(report.rows[0].references.dashboards,1);assert.equal(report.rows[0].dashboardCoverage,'partial');
 assert.deepEqual(Array.from(f.card._renameLog[0].impact.dashboards),['private-dashboard-route-qa']);
 assert.ok(!JSON.stringify(report).includes('private-dashboard-route-qa'));
 f.card._activeTab='log';f.card.render();assert.match(f.card.shadowRoot.textContent,/private-dashboard-route-qa/);
 assert.match(f.card.shadowRoot.textContent,/partial|unavailable|incomplete/i);
});
test('complete readable dashboards can report known zero without hiding source coverage',async t=>{
 const f=fixture(t,'complete-empty');await f.apply();
 const report=JSON.parse(f.card._buildRenameReport({draft:{format:'json',includePrivate:false}}).serialized);
 assert.equal(report.rows[0].references.dashboards,0);assert.equal(report.rows[0].dashboardCoverage,'complete');
});
test('impact preview with no readable dashboards keeps missing coverage visible and performs no rename',async t=>{
 const f=fixture(t,'configs-unavailable');await f.card._analyzeImpact();assert.equal(f.writes(),0);
 assert.match(f.card.shadowRoot.textContent,/dashboard.*(?:unavailable|not checked)|(?:unavailable|not checked).*dashboard/i);
});

test('default dashboard uses a meaningful local history label',async t=>{
 const f=fixture(t,'default-hit');await f.apply();
 assert.deepEqual(Array.from(f.card._renameLog[0].impact.dashboards),['Default dashboard']);
});
