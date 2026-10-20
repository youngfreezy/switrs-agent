// Run the shipped browser script in a small DOM harness; no network or model calls.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
function setup() {
  const nodes = new Map();
  function element() { return {value:'', textContent:'', innerHTML:'', hidden:false, disabled:false, children:[], listeners:{}, selectionStart:0, selectionEnd:0,
    setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end},
    classList:{toggle(){}}, setAttribute(k,v){this[k]=v}, removeAttribute(k){delete this[k]}, addEventListener(type,fn){(this.listeners[type]??=[]).push(fn)},
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

function typeQuestion(ui, value, cursor=value.length) {
  const input=ui.get('#question');input.value=value;input.setSelectionRange(cursor,cursor);
  for(const fn of input.listeners.input || [])fn({});
  for(const fn of input.listeners.focus || [])fn({});
}
function key(ui,key,extra={}) {
  const event={key,prevented:false,preventDefault(){this.prevented=true},...extra};
  for(const fn of ui.get('#question').listeners.keydown || [])fn(event);
  return event;
}
test('Tab fills the empty textarea with its visible starter without submitting',()=>{
  const ui=setup();typeQuestion(ui,'');
  assert.equal(ui.get('#question-suggestion').hidden,false);
  assert.equal(key(ui,'Tab').prevented,true);
  assert.equal(ui.get('#question').value,ui.example.textContent);
  assert.equal(ui.calls.some(x=>x.url==='/api/chat'),false);
  assert.equal(key(ui,'Tab').prevented,false);
});
test('completion appends a matching suffix while preserving typed capitalization',()=>{
  const ui=setup();typeQuestion(ui,'how many coll');key(ui,'Tab');
  assert.equal(ui.get('#question').value,'how many collisions in Orange County in 2019?');
});
test('unmatched, selected, middle-of-text, shifted and composing input keep normal Tab behavior',()=>{
  const ui=setup();typeQuestion(ui,'Unknown question');assert.equal(key(ui,'Tab').prevented,false);
  typeQuestion(ui,'How many',3);assert.equal(key(ui,'Tab').prevented,false);
  typeQuestion(ui,'How many');ui.get('#question').selectionStart=0;assert.equal(key(ui,'Tab').prevented,false);
  typeQuestion(ui,'How many');assert.equal(key(ui,'Tab',{shiftKey:true}).prevented,false);
  assert.equal(key(ui,'Tab',{isComposing:true}).prevented,false);
  assert.equal(key(ui,'Tab',{ctrlKey:true}).prevented,false);
});
test('Escape dismisses completion until text changes; busy state hides it',()=>{
  const ui=setup();typeQuestion(ui,'How many');key(ui,'Escape');
  assert.equal(ui.get('#question-suggestion').hidden,true);
  assert.equal(key(ui,'Tab').prevented,false);
  typeQuestion(ui,'How many c');assert.equal(ui.get('#question-suggestion').hidden,false);
  ui.run('setBusy(true)');assert.equal(ui.get('#question-suggestion').hidden,true);
  assert.equal(key(ui,'Tab').prevented,false);
});
test('follow-up completion requires an active checked answer and never reuses stale context',()=>{
  const ui=setup();typeQuestion(ui,'And in');assert.equal(key(ui,'Tab').prevented,false);
  ui.run("setContext({status:'answered',question:'How many collisions in 2019?'})");
  typeQuestion(ui,'And in');assert.equal(key(ui,'Tab').prevented,true);assert.equal(ui.get('#question').value,'And in 2020?');
  ui.run("setContext({status:'unsupported'})");typeQuestion(ui,'And in');assert.equal(key(ui,'Tab').prevented,false);
});
