// Run the shipped browser script in a small DOM harness; no network or model calls.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
function setup() {
  const nodes = new Map();
  function element() { return {value:'', textContent:'', innerHTML:'', hidden:false, disabled:false, children:[],
    classList:{toggle(){}}, setAttribute(k,v){this[k]=v}, removeAttribute(k){delete this[k]}, addEventListener(){},
    append(...children){this.children.push(...children)}, replaceChildren(...children){this.children=children},
    focus(){this.focused=true}, scrollIntoView(){} }; }
  const get = selector => { if(!nodes.has(selector))nodes.set(selector,element());return nodes.get(selector); };
  const example = element();example.textContent = 'How many collisions in Orange County in 2019?';
  const calls=[];
  const context = vm.createContext({document:{querySelector:get,querySelectorAll:()=>[example],createElement:element},
    localStorage:{getItem:()=>null,setItem(){}},AbortController,AbortSignal,TextDecoder,
    fetch:async (url,options)=>{calls.push({url,options});return url==='/api/status'
      ? {json:async()=>({ready:true})} : {ok:false,status:401,json:async()=>({detail:'Enter the access code to ask a question.'})};}});
  vm.runInContext(fs.readFileSync('hosted/static/app.js','utf8'),context);
  return {get,example,calls,run:code=>vm.runInContext(code,context)};
}
test('examples prepare a fresh question without sending or inheriting a prior scope',()=>{
  const ui=setup();ui.run("setContext({status:'answered',question:'How many collisions in 2020?'})");
  ui.example.onclick();
  assert.equal(ui.get('#question').value,ui.example.textContent);
  assert.equal(ui.get('#question').focused,true);
  assert.equal(ui.calls.some(x=>x.url==='/api/chat'),false);
  assert.equal(ui.run('previousReceipt'),null);
});
test('invalid access code opens the field and keeps the question available to retry',async()=>{
  const ui=setup();ui.get('#access-code').value='wrong';ui.get('#access').open=false;
  ui.get('#question').value='How many collisions?';await ui.run("ask('How many collisions?')");
  assert.equal(ui.get('#access').open,true);
  assert.equal(ui.get('#access-code').focused,true);
  assert.equal(ui.get('#access-code')['aria-invalid'],'true');
  assert.equal(ui.get('#question').value,'How many collisions?');
  assert.equal(ui.get('#send').disabled,false);
  assert.equal(ui.get('#answer').innerHTML,'');
});
test('limitations clear follow-up scope and do not use the answer color',()=>{
  const ui=setup();ui.run("setContext({status:'answered',resolved_question:'How many collisions in 2020?'})");
  assert.equal(ui.get('#followup-context').hidden,false);
  ui.run("setContext({status:'unsupported',question:'Why?'})");
  assert.equal(ui.get('#followup-context').hidden,true);
  ui.run("verdict(checkedLabel('unsupported'), false, 'unsupported')");
  assert.match(ui.get('#verdict').children[0].className,/limitation/);
});
test('history has an empty state and an accurate count',()=>{
  const ui=setup();
  assert.equal(ui.get('#history-count').textContent,'0 saved');
  assert.match(ui.get('#history').children[0].textContent,/No saved questions/);
});
