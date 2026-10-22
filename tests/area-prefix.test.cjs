const {test}=require('node:test');
const assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
function fixture() {
 const dom=new JSDOM('',{runScripts:'dangerously',url:'http://qa.invalid/',virtualConsole:new VirtualConsole()});
 dom.window.eval(require('node:fs').readFileSync(require('node:path').join(__dirname,'../ha-entity-renamer.js'),'utf8'));
 const card=dom.window.document.createElement('ha-entity-renamer');dom.window.document.body.append(card);
 const calls=[];
 card._hass={user:{id:'qa',is_admin:true},language:'en',states:{},callWS:async msg=>{
  calls.push(msg.type);assert.equal(msg.type,'config/area_registry/list');
  return [{area_id:'bath',name:'Łazienka'},{area_id:'living',name:'Salon'}];
 }};
 card._devices=[{id:'d',name:'QA',area_id:'living'}];card._selectedDevice='d';
 card._entities=[{entity_id:'sensor.temperature',device_id:'d',area_id:'bath'},
  {entity_id:'sensor.humidity',device_id:'d'}, {entity_id:'sensor.salon_power',device_id:'d'},
  {entity_id:'sensor.missing',device_id:'d',area_id:'missing'}];
 card._deviceEntities={d:card._entities};return {dom,card,calls};
}
test('area prefix respects entity override, device fallback and existing prefix, without writes',async()=>{
 const {dom,card,calls}=fixture();try {
  await card._addAreaPrefix();
  assert.deepEqual(Array.from(card._renameQueue,row=>[row.oldId,row.newId]),[
   ['sensor.temperature','sensor.lazienka_temperature'],['sensor.humidity','sensor.salon_humidity']]);
  assert.deepEqual(calls,['config/area_registry/list']);
  assert.ok(card._renameQueue.every(row=>row.source==='area'));
 }finally{dom.window.close();}
});
test('area suggestions preserve authored queue entries and do not invent missing areas',async()=>{
 const {dom,card}=fixture();try {
  card._addToQueue('sensor.temperature','sensor.authored');card._devices[0].area_id=null;
  await card._addAreaPrefix();assert.equal(card._renameQueue.length,1);assert.equal(card._renameQueue[0].newId,'sensor.authored');
 }finally{dom.window.close();}
});
test('late area response is discarded after account or selected device changes',async()=>{
 for(const change of [c=>c._hass={...c._hass,user:{id:'other',is_admin:true}},c=>c._selectedDevice=null]){
  const {dom,card}=fixture();try {
   let resolve;card._hass.callWS=()=>new Promise(r=>resolve=r);
   const request=card._addAreaPrefix();change(card);resolve([{area_id:'living',name:'Salon'},{area_id:'bath',name:'Łazienka'}]);await request;
   assert.equal(card._renameQueue.length,0);
  }finally{dom.window.close();}
 }
});

test('Clear invalidates an outstanding area proposal request',async()=>{
 const {dom,card}=fixture();try {
  card._addToQueue('sensor.temperature','sensor.authored');
  let resolve;card._hass.callWS=()=>new Promise(r=>resolve=r);
  const pending=card._addAreaPrefix();card._clearQueue();
  resolve([{area_id:'living',name:'Salon'},{area_id:'bath',name:'Łazienka'}]);await pending;
  assert.equal(card._renameQueue.length,0,'Clear must remain effective');
 }finally{dom.window.close();}
});
test('impact analysis supersedes area lookup without a false added confirmation',async()=>{
 for(const finishImpactFirst of [false,true]) {
  const {dom,card}=fixture();try {
   card._addToQueue('sensor.temperature','sensor.authored');
   let areaResolve,impactResolve;
   card._hass.callWS=msg=>{
    if(msg.type==='config/area_registry/list')return new Promise(r=>areaResolve=r);
    if(msg.type==='search/related')return new Promise(r=>impactResolve=r);
    return Promise.resolve(msg.type==='lovelace/dashboards/list'?[]:{});
   };
   const area=card._addAreaPrefix();const impact=card._analyzeImpact();
   if(finishImpactFirst){impactResolve({});await impact;}
   areaResolve([{area_id:'living',name:'Salon'}]);await area;
   assert.equal(card._renameQueue.length,1);
   assert.doesNotMatch(card._message?.text||'',/Area proposals added/);
   if(!finishImpactFirst){impactResolve({});await impact;}
  }finally{dom.window.close();}
 }
});
