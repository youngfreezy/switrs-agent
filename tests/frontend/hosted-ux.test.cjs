// Run the shipped browser script in a small DOM harness; no network or model calls.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
function setup({chat, suggest, rank} = {}) {
  const nodes = new Map(); let focused = null;
  function element() { return {value:'', textContent:'', innerHTML:'', get hidden(){return this._hidden??false}, set hidden(value){this._hidden=value;if(value && focused===this)focused=null}, disabled:false, children:[], listeners:{}, selectionStart:0, selectionEnd:0,
    setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end},
    classList:{toggle(){}}, setAttribute(k,v){this[k]=v}, removeAttribute(k){delete this[k]}, addEventListener(type,fn){(this.listeners[type]??=[]).push(fn)},
    append(...children){this.children.push(...children)}, replaceChildren(...children){this.children=children},
    focus(){this.focused=true;focused=this}, scrollIntoView(){} }; }
  const get = selector => { if(!nodes.has(selector))nodes.set(selector,element());return nodes.get(selector); };
  const example = element();example.textContent = 'How many collisions occurred in Orange County in 2019?';
  const calls=[], edits=[], timers=new Map(); let timerId=0;
  get('#access-code').value='test-code';
  const fixtureSuggestion = ({prefix, previous}) => {
    const options = [example.textContent, 'How many collisions in Orange County in 2019?',
      ...(previous ? ['And in 2020?'] : [])];
    const compact=prefix.trimStart().replace(/\s+/g,' ').toLowerCase();
    const match=options.find(x=>x.toLowerCase().startsWith(compact));
    return match ? prefix+match.slice(compact.length) : '';
  };
  const context = vm.createContext({document:{get activeElement(){return focused},execCommand(command,unused,text){edits.push({command,text});focused.value+=text;focused.setSelectionRange(focused.value.length,focused.value.length);return true},querySelector:get,querySelectorAll:()=>[example],createElement:element},
    setTimeout(fn){const id=++timerId;timers.set(id,fn);return id},clearTimeout(id){timers.delete(id)},
    localStorage:{getItem:()=>null,setItem(){}},AbortController,AbortSignal,TextDecoder,
    fetch:async (url,options)=>{calls.push({url,options});
      if(url==='/api/suggestions') {
        const body=JSON.parse(options.body);
        return {ok:true,json:async()=>({draft:body.draft,source:'jev',suggestions:rank ? await rank(body) : []})};
      }
      if(url==='/api/completions') {
        const body=JSON.parse(options.body);
        const completion=await (suggest || fixtureSuggestion)(body);
        return {ok:true,json:async()=>({prefix:body.prefix,completion})};
      }
      return url==='/api/status'
      ? {json:async()=>({ready:true})} : chat ? chat(options) : {ok:false,status:401,json:async()=>({detail:'Enter the access code to ask a question.'})};}});
  if(fs.existsSync('hosted/static/activity.js'))vm.runInContext(fs.readFileSync('hosted/static/activity.js','utf8'),context);
  if(fs.existsSync('hosted/static/completion.js'))vm.runInContext(fs.readFileSync('hosted/static/completion.js','utf8'),context);
  if(fs.existsSync('hosted/static/suggestions.js'))vm.runInContext(fs.readFileSync('hosted/static/suggestions.js','utf8'),context);
  vm.runInContext(fs.readFileSync('hosted/static/app.js','utf8'),context);
  return {get,example,calls,edits,flush:async()=>{const batch=[...timers.values()];timers.clear();for(const fn of batch)await fn()},run:code=>vm.runInContext(code,context)};
}
test('examples prepare a fresh question without sending or inheriting a prior scope',async()=>{
  const ui=setup();ui.run("setContext({status:'answered',question:'How many collisions in 2020?'})");
  ui.example.onclick();
  assert.equal(ui.get('#question').value,ui.example.textContent);
  assert.equal(ui.get('#question').focused,true);
  assert.equal(ui.calls.some(x=>x.url==='/api/chat'),false);
  assert.equal(ui.run('previousReceipt'),null);
});

test('related suggestions supplement starters and prepare an explicit fresh question without submitting',async()=>{
  const question='How many fatalities in Orange County in 2019?';
  const ui=setup({rank:()=>[question]});
  ui.run("setContext({signature:'signed',status:'answered',question:'How many collisions in Orange County in 2019?'})");
  await ui.flush();
  assert.equal(ui.get('#related').hidden,false);
  assert.equal(ui.get('#examples').hidden,false);
  const button=ui.get('#related-questions').children[0];
  assert.equal(button.textContent,question);button.onclick();
  assert.equal(ui.get('#question').value,question);
  assert.equal(ui.run('previousReceipt'),null);
  assert.equal(ui.calls.some(x=>x.url==='/api/chat'),false);
  assert.equal(ui.get('#question').focused,true);
});

test('related requests debounce edits and discard late results after new question, auth or busy changes',async()=>{
  let release;const ui=setup({rank:()=>new Promise(r=>release=r)});
  editQuestion(ui,'fat');editQuestion(ui,'fatalities');
  assert.equal(ui.calls.some(x=>x.url==='/api/suggestions'),false);
  let pending=ui.flush();await new Promise(r=>setImmediate(r));
  ui.get('#new').onclick();release(['How many fatalities in California?']);await pending;
  assert.equal(ui.get('#related').hidden,true);
  editQuestion(ui,'fatalities');pending=ui.flush();await new Promise(r=>setImmediate(r));
  ui.run('setBusy(true)');release(['How many fatalities in California?']);await pending;
  assert.equal(ui.get('#related').hidden,true);
  ui.run('setBusy(false)');pending=ui.flush();await new Promise(r=>setImmediate(r));
  ui.get('#access-code').value='changed';
  for(const fn of ui.get('#access-code').listeners.input)fn({});
  release(['How many fatalities in California?']);await pending;
  assert.equal(ui.get('#related').hidden,true);
});

test('unavailable or malformed related suggestions leave starter buttons and Tab completion usable',async()=>{
  const ui=setup({rank:()=>{throw new Error('offline')}});
  await typeQuestion(ui,'How many');
  assert.equal(ui.get('#related').hidden,true);
  assert.equal(ui.get('#examples').hidden,false);
  assert.equal(key(ui,'Tab').prevented,true);
  const invalid=setup({rank:()=>['<script>bad</script>',42,'No question mark']});
  await typeQuestion(invalid,'fatalities');
  assert.equal(invalid.get('#related').hidden,true);
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
test('limitations clear follow-up scope and do not use the answer color',async()=>{
  const ui=setup();ui.run("setContext({status:'answered',resolved_question:'How many collisions in 2020?'})");
  assert.equal(ui.get('#followup-context').hidden,false);
  ui.run("setContext({status:'unsupported',question:'Why?'})");
  assert.equal(ui.get('#followup-context').hidden,true);
  ui.run("verdict(checkedLabel('unsupported'), false, 'unsupported')");
  assert.match(ui.get('#verdict').children[0].className,/limitation/);
});
test('history has an empty state and an accurate count',async()=>{
  const ui=setup();
  assert.equal(ui.get('#history-count').textContent,'0 saved');
  assert.match(ui.get('#history').children[0].textContent,/No saved questions/);
});

function editQuestion(ui, value, cursor=value.length) {
  const input=ui.get('#question');input.focus();input.value=value;input.setSelectionRange(cursor,cursor);
  for(const fn of input.listeners.input || [])fn({});
  for(const fn of input.listeners.focus || [])fn({});
}
async function typeQuestion(ui,value,cursor=value.length) { editQuestion(ui,value,cursor);await ui.flush(); }
function key(ui,key,extra={}) {
  const event={key,prevented:false,preventDefault(){this.prevented=true},...extra};
  for(const fn of ui.get('#question').listeners.keydown || [])fn(event);
  return event;
}
test('empty input keeps normal Tab navigation and does not insert an arbitrary question',async()=>{
  const ui=setup();await typeQuestion(ui,'');
  assert.equal(ui.get('#question-suggestion').hidden,true);
  assert.equal(key(ui,'Tab').prevented,false);
  assert.equal(ui.get('#question').value,'');
});
test('completion appends a matching suffix while preserving typed capitalization',async()=>{
  const ui=setup();await typeQuestion(ui,'how many coll');key(ui,'Tab');
  assert.equal(ui.get('#question').value,'how many collisions occurred in Orange County in 2019?');
});
test('unmatched, selected, middle-of-text, shifted and composing input keep normal Tab behavior',async()=>{
  const ui=setup();await typeQuestion(ui,'Unknown question');assert.equal(key(ui,'Tab').prevented,false);
  await typeQuestion(ui,'How many',3);assert.equal(key(ui,'Tab').prevented,false);
  await typeQuestion(ui,'How many');ui.get('#question').selectionStart=0;assert.equal(key(ui,'Tab').prevented,false);
  await typeQuestion(ui,'How many');assert.equal(key(ui,'Tab',{shiftKey:true}).prevented,false);
  assert.equal(key(ui,'Tab',{isComposing:true}).prevented,false);
  assert.equal(key(ui,'Tab',{ctrlKey:true}).prevented,false);
});
test('Escape dismisses completion until text changes; busy state hides it',async()=>{
  const ui=setup();await typeQuestion(ui,'How many');key(ui,'Escape');
  assert.equal(ui.get('#question-suggestion').hidden,true);
  assert.equal(key(ui,'Tab').prevented,false);
  await typeQuestion(ui,'How many c');assert.equal(ui.get('#question-suggestion').hidden,false);
  ui.run('setBusy(true)');assert.equal(ui.get('#question-suggestion').hidden,true);
  assert.equal(key(ui,'Tab').prevented,false);
});
test('follow-up completion requires an active checked answer and never reuses stale context',async()=>{
  const ui=setup();await typeQuestion(ui,'And in');assert.equal(key(ui,'Tab').prevented,false);
  ui.run("setContext({status:'answered',question:'How many collisions in 2019?'})");
  await typeQuestion(ui,'And in');assert.equal(key(ui,'Tab').prevented,true);assert.equal(ui.get('#question').value,'And in 2020?');
  ui.run("setContext({status:'unsupported'})");await typeQuestion(ui,'And in');assert.equal(key(ui,'Tab').prevented,false);
});

test('loading replaces the composer with the submitted question and one cancel control',async()=>{
  const ui=setup();ui.get('#question').value='How many collisions?';
  ui.run("resetResult('How many collisions?');setBusy(true)");
  assert.equal(ui.get('#question-form').hidden,true);
  assert.equal(ui.get('#examples').hidden,true);
  assert.equal(ui.get('#activity').hidden,false);
  assert.equal(ui.get('#pending-question').textContent,'How many collisions?');
  assert.equal(ui.get('#result').hidden,true);
  assert.equal(ui.get('#progress').hidden,true);
  assert.equal(ui.get('#stop').hidden,false);
  ui.run('setBusy(false)');
  assert.equal(ui.get('#activity').hidden,true);
  assert.equal(ui.get('#question-form').hidden,false);
  assert.equal(ui.get('#examples').hidden,false);
});
test('streamed tool events update progress without exposing model prose as an answer',async()=>{
  const ui=setup();ui.run("setBusy(true);showEvent({kind:'tool_call',tool:'run_sql',id:'q',input:{}},new Map())");
  assert.equal(ui.get('#activity-title').textContent,'Querying the records');
  assert.match(ui.get('#activity-log').children.at(-1).textContent,/read-only query/);
  ui.run("showEvent({kind:'text',text:'Unverified: 999 crashes'},new Map())");
  assert.equal(ui.get('#answer').innerHTML,'');
  assert.equal(ui.get('#activity-log').children.some(x=>x.textContent.includes('999')),false);
  ui.run("showEvent({kind:'tool_call',tool:'submit_answer',id:'s',input:{}},new Map())");
  assert.equal(ui.get('#activity-title').textContent,'Checking the answer');
});
test('a certificate stays hidden until the stream closes, then loading ends',async()=>{
  let stream;const ui=setup({chat:async()=>({ok:true,body:new ReadableStream({start(c){stream=c}})})});
  ui.get('#access-code').value='demo';ui.get('#question').value='Question';
  const pending=ui.run("ask('Question')");await new Promise(resolve=>setImmediate(resolve));
  stream.enqueue(new TextEncoder().encode('data: '+JSON.stringify({kind:'certified',status:'answered',final_answer:'Checked: 42.',receipt:{status:'answered',question:'Question',answer:'Checked: 42.'}})+'\n\n'));
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(ui.get('#answer').innerHTML,'');assert.equal(ui.get('#activity').hidden,false);
  stream.close();await pending;
  assert.match(ui.get('#answer').innerHTML,/42/);
  assert.equal(ui.get('#activity').hidden,true);assert.equal(ui.get('#result').hidden,false);
  assert.equal(ui.get('#examples').hidden,false);
});
test('cancelling restores the editable question and never publishes a result',async()=>{
  const ui=setup({chat:({signal})=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(Object.assign(new Error('Stopped'),{name:'AbortError'}))))});
  ui.get('#access-code').value='demo';ui.get('#question').value='Keep my question';
  const pending=ui.run("ask('Keep my question')");ui.get('#stop').onclick();await pending;
  assert.equal(ui.get('#activity').hidden,true);assert.equal(ui.get('#question-form').hidden,false);
  assert.equal(ui.get('#question').value,'Keep my question');assert.equal(ui.get('#answer').innerHTML,'');
  assert.match(ui.get('#progress').textContent,/Stopped/);
  assert.equal(ui.get('#examples').hidden,false);
});

test('focus leaves the disappearing Stop button for the result',async()=>{
  const ui=setup();ui.run("resetResult('Question');setBusy(true)");ui.get('#stop').focus();
  ui.run("verdict('No checked answer',true);setBusy(false)");
  assert.equal(ui.run('document.activeElement'),ui.get('#result'));
});

test('completion is shown only while editing and clears on blur',async()=>{
  const ui=setup();assert.equal(ui.get('#question-suggestion').hidden,true);
  await typeQuestion(ui,'How many');assert.equal(ui.get('#question-suggestion').hidden,false);
  ui.get('#send').focus();
  for(const fn of ui.get('#question').listeners.blur || [])fn({});
  assert.equal(ui.get('#question-suggestion').hidden,true);
});
test('completion uses a native editing command for undo and never submits',async()=>{
  const ui=setup();await typeQuestion(ui,'How many coll');key(ui,'Tab');
  assert.deepEqual(ui.edits,[{command:'insertText',text:'isions occurred in Orange County in 2019?'}]);
  assert.equal(key(ui,'Tab').prevented,false);
  assert.equal(ui.calls.some(x=>x.url==='/api/chat'),false);
});
test('spacing and a shorter common phrasing preserve the typed prefix',async()=>{
  const ui=setup();await typeQuestion(ui,'  how  many collisions in');key(ui,'Tab');
  assert.equal(ui.get('#question').value,'  how  many collisions in Orange County in 2019?');
});

test('Undo dismisses completion until the next edit and multiline drafts are left alone',async()=>{
  const ui=setup();await typeQuestion(ui,'How many coll');key(ui,'Tab');
  const input=ui.get('#question');input.value='How many coll';input.setSelectionRange(13,13);
  for(const fn of input.listeners.input)fn({inputType:'historyUndo'});
  assert.equal(ui.get('#question-suggestion').hidden,true);
  assert.equal(key(ui,'Tab').prevented,false);
  await typeQuestion(ui,'How many colli');assert.equal(ui.get('#question-suggestion').hidden,false);
  await typeQuestion(ui,'How many\ncollisions');assert.equal(key(ui,'Tab').prevented,false);
});
test('IME composition and unsupported native editing never replace the draft',async()=>{
  const ui=setup();await typeQuestion(ui,'How many coll');
  for(const fn of ui.get('#question').listeners.compositionstart)fn({});
  assert.equal(key(ui,'Tab').prevented,false);
  for(const fn of ui.get('#question').listeners.compositionend)fn({});
  ui.run('document.execCommand = () => false');
  assert.equal(key(ui,'Tab').prevented,false);
  assert.equal(ui.get('#question').value,'How many coll');
});
test('regex punctuation is treated as literal question text',async()=>{
  const ui=setup();await typeQuestion(ui,'How.*');
  assert.equal(key(ui,'Tab').prevented,false);
  assert.equal(ui.get('#question').value,'How.*');
});


test('LLM requests are debounced and include only the active receipt',async()=>{
  const ui=setup({suggest:({prefix})=>prefix+' crashes in Alameda County in 2018?'});
  ui.run("setContext({signature:'signed',status:'answered',question:'Prior question',answer:'Checked'})");
  editQuestion(ui,'How many');editQuestion(ui,'How many fatal');
  assert.equal(ui.calls.filter(x=>x.url==='/api/completions').length,0);
  assert.equal(key(ui,'Tab').prevented,false);
  await ui.flush();key(ui,'Tab');
  assert.equal(ui.get('#question').value,'How many fatal crashes in Alameda County in 2018?');
  const requests=ui.calls.filter(x=>x.url==='/api/completions');assert.equal(requests.length,1);
  assert.equal(JSON.parse(requests[0].options.body).previous.signature,'signed');
  assert.equal(ui.calls.some(x=>x.url==='/api/chat'),false);
});
test('late responses cannot overwrite a newer draft or a changed context',async()=>{
  let release;const ui=setup({suggest:()=>new Promise(r=>release=r)});
  editQuestion(ui,'How many fatal');const pending=ui.flush();
  editQuestion(ui,'How many motorcycle');
  release('How many fatal crashes?');await pending;
  assert.equal(ui.get('#question-suggestion').hidden,true);
  assert.equal(ui.get('#question').value,'How many motorcycle');
  const next=ui.flush();ui.run("setContext({signature:'different',status:'answered',question:'Another scope'})");
  release('How many motorcycle crashes?');await next;
  assert.equal(ui.get('#question-suggestion').hidden,true);
});
test('Escape while waiting cancels the suggestion without trapping Tab',async()=>{
  let release;const ui=setup({suggest:()=>new Promise(r=>release=r)});
  editQuestion(ui,'How many');const pending=ui.flush();key(ui,'Escape');
  release('How many collisions?');await pending;
  assert.equal(ui.get('#question-suggestion').hidden,true);
  assert.equal(key(ui,'Tab').prevented,false);
});
test('no credentials or a failed suggestion never prevents asking manually',async()=>{
  const ui=setup({suggest:()=>{throw new Error('Unavailable')}});
  ui.get('#access-code').value='';await typeQuestion(ui,'How many');
  assert.equal(ui.calls.some(x=>x.url==='/api/completions'),false);
  ui.get('#access-code').value='code';await typeQuestion(ui,'How many collisions');
  assert.equal(key(ui,'Tab').prevented,false);
  assert.equal(ui.get('#send').disabled,false);
  assert.equal(ui.get('#question').value,'How many collisions');
});
