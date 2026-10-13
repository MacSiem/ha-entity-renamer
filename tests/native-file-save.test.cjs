const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {JSDOM}=require('jsdom');
function fixture(t,format='json'){
 const dom=new JSDOM('',{runScripts:'dangerously',url:'http://localhost/'});t.after(()=>dom.window.close());
 dom.window.eval(readFileSync(require('node:path').join(__dirname,'../ha-entity-renamer.js'),'utf8'));
 const card=dom.window.document.createElement('ha-entity-renamer');dom.window.document.body.append(card);
 const connection=new dom.window.EventTarget();connection.connected=true;
 card._hass={user:{id:'native-admin',is_admin:true},connection,language:'en',states:{},callWS:async()=>{throw Error('Saving must not access HA');}};card._bindRenameExportEvents();
 card._renameLog=[{oldId:'sensor.fixture_old',newId:'sensor.fixture_new',targetEntityId:'sensor.fixture_new',status:'ok',writePhase:'readback_confirmed',verification:'independent_registry_list_exact_fields',completedAt:'2026-10-13T17:00:00.000Z'}];
 const p=card._buildRenameReport({draft:{format,includePrivate:false}});
 const controller=new AbortController(),request={blob:new Blob([p.serialized]),serialized:p.serialized,filename:'native-report.'+format,format,signal:controller.signal};
 let anchors=0;dom.window.URL.createObjectURL=()=> 'blob:http://localhost/qa';dom.window.URL.revokeObjectURL=()=>{};dom.window.HTMLAnchorElement.prototype.click=function(){anchors++;};
 return {dom,card,request,controller,anchors:()=>anchors};
}
for(const format of ['json','csv','txt'])test(format+' native Save confirms exact file bytes only after close and readback',async t=>{
 const f=fixture(t,format),steps=[];let written;
 f.dom.window.showSaveFilePicker=async options=>{
  steps.push('picker');assert.equal(options.suggestedName,f.request.filename);
  return {name:f.request.filename,createWritable:async()=>({write:async blob=>{steps.push('write');written=await blob.text();},close:async()=>{steps.push('close');},abort:async()=>{steps.push('abort');}}),getFile:async()=>{steps.push('readback');return new Blob([written]);}};
 };
 const result=await f.card._downloadRenameReport(f.request);
 assert.equal(result.outcome,'saved');assert.equal(written,f.request.serialized);assert.deepEqual(steps,['picker','write','close','readback']);assert.equal(f.anchors(),0);
});
for(const failure of ['cancel','renamed','write-error','corrupt-readback','aborted-before-write'])test(failure+' never claims Saved or automatically starts a second download',async t=>{
 const f=fixture(t);let writes=0;
 f.dom.window.showSaveFilePicker=async ()=>{
  if(failure==='cancel')throw new DOMException('Cancelled','AbortError');
  if(failure==='aborted-before-write')f.controller.abort();
  return {name:failure==='renamed'?'unexpected.json':f.request.filename,createWritable:async()=>({write:async()=>{writes++;if(failure==='write-error')throw Error('controlled write failure');},close:async()=>{},abort:async()=>{}}),getFile:async()=>new Blob([failure==='corrupt-readback'?'different bytes':f.request.serialized])};
 };
 const result=await f.card._downloadRenameReport(f.request);assert.equal(result.outcome,'unknown');assert.equal(f.anchors(),0);
 if(['cancel','renamed','aborted-before-write'].includes(failure))assert.equal(writes,0);
});
test('unsupported native picker retains the existing initiated-only download',async t=>{
 const f=fixture(t);const result=await f.card._downloadRenameReport(f.request);assert.equal(result.outcome,'initiated');assert.equal(f.anchors(),1);
});
