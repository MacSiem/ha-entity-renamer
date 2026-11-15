const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {JSDOM,VirtualConsole}=require('jsdom');
const id='1234394331',full='MacSiem/ha-entity-renamer';
function fixture(options={}) {
 const dom=new JSDOM('',{runScripts:'dangerously',url:'http://qa.invalid/',pretendToBeVisual:true,virtualConsole:new VirtualConsole()});
 const {window:w}=dom; w.confirm=()=>options.confirm!==false;
 w.eval(readFileSync(join(__dirname,'../ha-entity-renamer.js'),'utf8'));
 const calls=[];let repo={id,full_name:full,category:'plugin',installed:true,...options.repo};
 const connection={connected:true};
 const hass={user:{id:'synthetic',is_admin:options.admin!==false},connection,language:'en',themes:{},states:{},
  async callWS(msg){calls.push(msg.type);
   if(msg.type==='hacs/info')return {version:options.version||'2.0.5',startup:false,has_pending_tasks:!!options.pending,disabled_reason:null};
   if(msg.type==='hacs/repository/info'){if(!repo){const e=new Error('not found');e.code='repository_not_found';throw e;}const result={...repo};if(options.onRepoRead)await options.onRepoRead(result);return result;}
   if(msg.type==='hacs/repositories/remove'){if(options.removeFailure)throw Error('uncertain');if(options.hangRemove)await options.hangRemove;repo=null;return {};}
   if(msg.type==='hacs/repositories/add'){if(options.addFailure)throw Error('uncertain');repo={id,full_name:full,category:'integration',installed:false};return {};}
   return [];
  }};
 const card=w.document.createElement('ha-entity-renamer');card._hass=hass;card.setConfig({show_help:false});w.document.body.append(card);
 return {w,card,calls,connection,get repo(){return repo},close:()=>w.close()};
}
test('explicit switch checks HACS, unregisters then registers exact integration and reads back',async()=>{
 const f=fixture();try{await f.card._switchHacsRepository();assert.deepEqual(f.calls.filter(x=>x.startsWith('hacs/')),['hacs/info','hacs/repository/info','hacs/repositories/remove','hacs/repository/info','hacs/repositories/add','hacs/repository/info']);assert.equal(f.repo.category,'integration');assert.equal(f.card._hacsSwitchStatus,'done');assert.equal(f.w.localStorage.getItem(f.card._hacsMigrationKey()),'done');await f.card._switchHacsRepository();assert.equal(f.calls.filter(x=>x==='hacs/repositories/remove').length,1);}finally{f.close()}
});
test('no change without consent, admin, supported HACS and idle metadata queue',async()=>{
 for(const options of [{confirm:false},{admin:false},{version:'2.0.4'},{pending:true},{repo:{id:'foreign'}},{repo:{full_name:'Other/repo'}},{repo:{category:'theme'}},{repo:{installed:false}}]) {const f=fixture(options);try{await f.card._switchHacsRepository();assert.ok(!f.calls.includes('hacs/repositories/remove'));assert.ok(!f.calls.includes('hacs/repositories/add'));}finally{f.close()}}
});
test('uncertain effect persists and is never blindly retried after rerender or reload',async()=>{
 for(const options of [{removeFailure:true},{addFailure:true}]) {const f=fixture(options);try{await f.card._switchHacsRepository();assert.equal(f.card._hacsSwitchStatus,'stopped');assert.equal(f.w.localStorage.getItem(f.card._hacsMigrationKey()),'inflight');const before=f.calls.length;await f.card._switchHacsRepository();assert.equal(f.calls.length,before);assert.equal(f.card._hacsSwitchStatus,'stopped');}finally{f.close()}}
});

test('switch button and warning are present for administrators in English and Polish',()=>{
 const f=fixture();try{f.card.render();assert.ok(f.card.shadowRoot.querySelector('#hacsSwitch'));assert.match(f.card.shadowRoot.textContent,/Finish all HACS downloads/);f.card._lang='pl';f.card.render();assert.match(f.card.shadowRoot.textContent,/Przygotuj migrację w HACS/);assert.match(f.card.shadowRoot.textContent,/zakończ wszystkie pobierania/);}finally{f.close()}
});

test('a second card with delayed stale read cannot remove the newly registered integration',async()=>{
 let release;const delayed=new Promise(resolve=>{release=resolve});let reads=0, captured;const ready=new Promise(resolve=>{captured=resolve});
 const f=fixture({onRepoRead:async result=>{if(result.category==='plugin' && ++reads===2){captured();await delayed;}}});
 try{const other=f.w.document.createElement('ha-entity-renamer');other._hass=f.card._hass;other.setConfig({show_help:false});f.w.document.body.append(other);
  const first=f.card._switchHacsRepository(), second=other._switchHacsRepository();await ready;await first;assert.equal(f.repo.category,'integration');release();await second;
  assert.equal(f.calls.filter(x=>x==='hacs/repositories/remove').length,1);assert.equal(f.calls.filter(x=>x==='hacs/repositories/add').length,1);assert.equal(f.repo.category,'integration');
 }finally{release();f.close()}
});

test('timed-out remove remains uncertain and a late response never starts registration',async()=>{
 let release;const pending=new Promise(resolve=>{release=resolve});const f=fixture({hangRemove:pending});try{const original=f.w.setTimeout.bind(f.w);f.w.setTimeout=(fn,ms)=>original(fn,ms===15000?0:ms);await f.card._switchHacsRepository();assert.equal(f.card._hacsSwitchStatus,'stopped');assert.equal(f.w.localStorage.getItem(f.card._hacsMigrationKey()),'inflight');release();await new Promise(resolve=>setTimeout(resolve,10));assert.equal(f.calls.filter(x=>x==='hacs/repositories/add').length,0);await f.card._switchHacsRepository();assert.equal(f.calls.filter(x=>x==='hacs/repositories/remove').length,1);}finally{release();f.close()}
});
