const {test}=require('node:test');
const assert=require('node:assert/strict');
const {createUiV3}=require('../shared/ha-tools-ui.js');
const {JSDOM}=require('jsdom');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
test('primary foreground remains readable for HA theme colors and both fallback themes',()=>{
  const ui=createUiV3();
  for(const dark of [false,true])for(const color of ['', '#3B82F6','#818cf8','#ff9800','#fff','#000','#777777']){
    const result=ui.primaryColors(color,{dark});
    assert.ok(result.contrast>=4.5,`${color} dark=${dark}: ${result.contrast}`);
  }
});
test('ordinary HA theme update applies the same readable primary colors to the connected card',()=>{
  const dom=new JSDOM('',{runScripts:'dangerously',url:'http://localhost/'});
  try{
    dom.window.eval(readFileSync(join(__dirname,'../ha-entity-renamer.js'),'utf8'));
    const card=dom.window.document.createElement('ha-entity-renamer');dom.window.document.body.append(card);
    card.style.setProperty('--primary-color','#818cf8');
    card.hass={language:'en',themes:{darkMode:true},states:{}};
    const expected=createUiV3().primaryColors('#818cf8',{dark:true});
    assert.equal(card.style.getPropertyValue('--hat-primary-fill-computed'),expected.fill);
    assert.equal(card.style.getPropertyValue('--hat-on-primary-computed'),expected.foreground);
    card.style.setProperty('--primary-color','#2563eb');card.hass={language:'en',themes:{darkMode:false},states:{}};
    assert.equal(card.style.getPropertyValue('--hat-on-primary-computed'),createUiV3().primaryColors('#2563eb').foreground);
  }finally{dom.window.close();}
});
