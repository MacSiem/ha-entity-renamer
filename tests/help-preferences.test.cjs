const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { JSDOM, VirtualConsole } = require('jsdom');
function fixture() {
  const dom = new JSDOM('', {runScripts:'dangerously',url:'http://qa.invalid/',virtualConsole:new VirtualConsole()});
  dom.window.eval(readFileSync(require('node:path').join(__dirname,'../ha-entity-renamer.js'),'utf8'));
  const card = dom.window.document.createElement('ha-entity-renamer');
  card._hass={user:{id:'first',is_admin:true},language:'en',themes:{},states:{}};
  card.setConfig({show_support:false});
  dom.window.document.body.append(card);
  return {dom,card,help:()=>card.shadowRoot.querySelector('details.renamer-help')};
}
test('help is collapsed on narrow cards, retained through renders and hidden by config', () => {
  const f=fixture();try {
    assert.ok(f.help(),'native keyboard-accessible details');
    f.card._syncHelpWidth(390);assert.equal(f.help().open,false);
    f.help().querySelector('summary').click();assert.equal(f.help().open,true,'explicit reading remains possible');
    f.card.render();assert.equal(f.help().open,true,'ordinary data render preserves reading');
    f.card._syncHelpWidth(320);assert.equal(f.help().open,false,'narrow resize collapses help');
    f.card.setConfig({show_help:false});assert.equal(f.help(),null);
  } finally {f.dom.window.close();}
});
test('first wide opening and remembered closure are scoped to the HA account', () => {
  const f=fixture();try {
    f.card._syncHelpWidth(900);assert.equal(f.help().open,true);
    f.help().querySelector('summary').click();assert.equal(f.help().open,false);
    f.card.render();f.card._syncHelpWidth(900);assert.equal(f.help().open,false);
    f.card._hass={...f.card._hass,user:{id:'second',is_admin:true}};
    f.card.render();f.card._syncHelpWidth(900);assert.equal(f.help().open,true,'another account has independent help');
    f.card._hass={...f.card._hass,user:{id:'first',is_admin:true}};
    f.card.render();f.card._syncHelpWidth(900);assert.equal(f.help().open,false);
    f.card._helpState=null;f.card.render();f.card._syncHelpWidth(900);
    assert.equal(f.help().open,false,'closure survives a fresh component state');
  } finally {f.dom.window.close();}
});
