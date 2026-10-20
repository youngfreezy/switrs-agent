"use strict";
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function md(src){
  const lines = esc(src).split("\n"); let html = "", i = 0;
  const inline = t => t.replace(/`([^`]+)`/g,"<code>$1</code>").replace(/\*\*([^*]+)\*\*/g,"<b>$1</b>").replace(/(^|\W)\*([^*\n]+)\*(?=\W|$)/g,"$1<i>$2</i>").replace(/(^|\W)_([^_\n]+)_(?=\W|$)/g,"$1<i>$2</i>");
  while(i < lines.length){
    const l = lines[i];
    if(l.startsWith("```")){ let b=[]; i++; while(i<lines.length && !lines[i].startsWith("```")) b.push(lines[i++]); i++; html += `<pre>${b.join("\n")}</pre>`; continue; }
    if(/^\|.*\|\s*$/.test(l) && i+1<lines.length && /^\|[\s:|-]+\|\s*$/.test(lines[i+1])){
      const row = r => r.trim().replace(/^\||\|$/g,"").split("|").map(c=>inline(c.trim()));
      let t = "<table><thead><tr>"+row(l).map(c=>`<th>${c}</th>`).join("")+"</tr></thead><tbody>"; i+=2;
      while(i<lines.length && /^\|.*\|\s*$/.test(lines[i])) t += "<tr>"+row(lines[i++]).map(c=>`<td>${c}</td>`).join("")+"</tr>";
      html += t+"</tbody></table>"; continue; }
    const h = l.match(/^(#{1,4})\s+(.*)/); if(h){ html += `<h3>${inline(h[2])}</h3>`; i++; continue; }
    if(/^\s*([-*]|\d+\.)\s+/.test(l)){ const ordered = /^\s*\d+\./.test(l); let items=[];
      while(i<lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) items.push(inline(lines[i++].replace(/^\s*([-*]|\d+\.)\s+/,"")));
      html += (ordered?"<ol>":"<ul>")+items.map(x=>`<li>${x}</li>`).join("")+(ordered?"</ol>":"</ul>"); continue; }
    if(!l.trim()){ i++; continue; }
    let p=[]; while(i<lines.length && lines[i].trim() && !/^(#{1,4}\s|```|\s*([-*]|\d+\.)\s|\|)/.test(lines[i])) p.push(inline(lines[i++]));
    if(p.length) html += `<p>${p.join("<br>")}</p>`; else { html += `<p>${inline(l)}</p>`; i++; }
  }
  return html;
}

const storageKey = 'switrs-questions-v1';
const activity = createActivity();
let busy = false, controller = null, history = [], previousReceipt = null;
try { const saved = JSON.parse(localStorage.getItem(storageKey) || '[]'); if (Array.isArray(saved)) history = saved.slice(0, 50); } catch {}
function setBusy(value, saved = false) {
  const moveFocus = !value && [$('#activity-title'), $('#stop')].includes(document.activeElement);
  busy = value; $('#send').disabled = value; $('#new').disabled = value; $('#clear').disabled = value;
  $('#stop').hidden = !value; $('#question').readOnly = value;
  $('#question-form').setAttribute('aria-busy', String(value));
  $('#question-form').hidden = value; $('#access').hidden = value;
  $('#examples').hidden = value;
  $('#new').hidden = value; $('#progress').hidden = value;
  $('#ask-title').textContent = value ? 'Following the evidence' : $('#result').hidden ? 'What would you like to know?' : 'Your result';
  if (value) {
    $('#result').hidden = true; activity.start(saved);
  } else {
    activity.finish();
    if (moveFocus) ($('#result').hidden ? $('#question') : $('#result')).focus({preventScroll:true});
  }
  completion.refresh();
}
function resetResult(question) {
  $('#result').hidden = true;
  $('#pending-question').textContent = question;
  $('#result-question').textContent = question; $('#verdict').replaceChildren();
  $('#resolved-question').textContent = '';
  $('#answer').replaceChildren(); $('#trace').replaceChildren(); $('#evidence').open = false; $('#evidence').hidden = false;
}
function verdict(text, failure = false, status = 'answered') {
  const limited = !['answered', 'no_matching_records'].includes(status);
  const node = document.createElement('p'); node.className = 'badge' + (failure ? ' failure' : limited ? ' limitation' : '');
  node.textContent = text; $('#verdict').replaceChildren(node); $('#result').hidden = false;
}
function setContext(receipt) {
  if (receipt?.status && !['answered', 'no_matching_records'].includes(receipt.status)) receipt = null;
  previousReceipt = receipt;
  $('#followup-context').hidden = !receipt;
  $('#followup-context').textContent = receipt
    ? 'Follow-ups refer to: ' + (receipt.resolved_question || receipt.question) + ' Try “And in 2020?” or change the county.'
    : '';
  completion.refresh();
}
function showResolution(question, receipt) {
  $('#resolved-question').textContent = receipt.resolved_question && receipt.resolved_question !== question
    ? 'Resolved question: ' + receipt.resolved_question : '';
}
function checkedLabel(status) {
  return ['answered', 'no_matching_records'].includes(status)
    ? 'Checked answer · metric, scope & source verified'
    : 'Checked limitation · no complete answer certified';
}
function saveHistory() {
  try { localStorage.setItem(storageKey, JSON.stringify(history.slice(0, 50))); } catch { $('#progress').textContent = 'This browser could not save your history. The answer is still available below.'; }
  renderHistory();
}
function renderHistory() {
  $('#history').replaceChildren();
  const records = history.filter(record => record && typeof record.question === 'string');
  $('#history-count').textContent = records.length + ' saved';
  if (!records.length) {
    const empty = document.createElement('p'); empty.className = 'muted';
    empty.textContent = 'No saved questions yet. Your checked results will appear here.';
    $('#history').append(empty);
  }
  records.forEach(record => {
    const button = document.createElement('button'); button.type = 'button'; button.textContent = record.question;
    button.onclick = async () => {
      if (busy) return;
      setContext(null); resetResult(record.question); $('#question').value = record.question; $('#progress').textContent = 'Authenticating the saved answer…';
      setBusy(true, true); $('#stop').hidden = true; $('#evidence').hidden = true;
      try {
        const response = await fetch('/api/verify', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(record.receipt), signal:AbortSignal.timeout(15000)});
        const checked = response.ok && (await response.json()).valid === true;
        if (!checked || record.receipt.question !== record.question) throw new Error('Saved answer could not be authenticated. Ask the question again.');
        $('#answer').innerHTML = md(record.receipt.answer);
        showResolution(record.question, record.receipt);
        verdict(record.receipt.status ? 'Saved · ' + checkedLabel(record.receipt.status) : 'Authenticated saved answer · checked when originally produced', false, record.receipt.status || 'answered');
        setContext(record.receipt);
        $('#progress').textContent = 'Saved answer. Run the question again for a new investigation.';
      } catch (error) { verdict('Saved answer is unverified', true); $('#progress').textContent = error.message; }
      finally { setBusy(false); }
    };
    $('#history').append(button);
  });
}
function showEvent(event, cards) {
  activity.event(event);
  if (event.kind === 'status') $('#progress').textContent = event.text;
  else if (event.kind === 'text') {
    const note = document.createElement('p'); note.className = 'working-note'; note.textContent = event.text;
    $('#trace').append(note);
  } else if (event.kind === 'tool_call') {
    const card = document.createElement('details'), title = document.createElement('summary'), input = document.createElement('pre');
    title.textContent = event.tool; input.textContent = JSON.stringify(event.input, null, 2);
    card.append(title, input); cards.set(event.id, card); $('#trace').append(card);
    $('#progress').textContent = 'Checking the question, querying the records and verifying the result…';
  } else if (event.kind === 'tool_result') {
    const output = document.createElement('pre'); output.textContent = JSON.stringify(event.result, null, 2);
    (cards.get(event.id) || $('#trace')).append(output);
  } else if (event.kind === 'done' && event.error) {
    $('#progress').textContent = typeof event.error === 'string' ? event.error : 'The request failed. Please try again.';
  }
}
async function ask(question) {
  if (busy) return;
  if (!$('#access-code').value.trim()) {
    $('#access').open = true; $('#access-code').focus();
    $('#progress').textContent = 'Enter the private access code under Agent access, then ask again.'; return;
  }
  $('#access-code').removeAttribute('aria-invalid');
  const previous = previousReceipt;
  setContext(null); resetResult(question); setBusy(true); $('#progress').textContent = 'Connecting to the agent…';
  controller = new AbortController(); let finalEvent = null; const cards = new Map();
  try {
    const response = await fetch('/api/chat', {method:'POST', signal:controller.signal,
      headers:{'Content-Type':'application/json', Authorization:'Bearer ' + $('#access-code').value.trim()},
      body:JSON.stringify({message:question, ...(previous ? {previous} : {})})});
    if (!response.ok) {
      if (response.status === 401) {
        $('#access').hidden = false; $('#access').open = true; $('#access-code').setAttribute('aria-invalid', 'true'); $('#access-code').focus();
      }
      const detail = await response.json().catch(() => ({}));
      throw new Error(typeof detail.detail === 'string' ? detail.detail : 'The request could not start. Please try again.');
    }
    $('#access').open = false;
    const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = '';
    while (true) {
      const {value, done} = await reader.read(); if (done) break;
      buffer += decoder.decode(value, {stream:true});
      let split;
      while ((split = buffer.indexOf('\n\n')) >= 0) {
        const part = buffer.slice(0, split); buffer = buffer.slice(split + 2);
        if (!part.startsWith('data: ')) continue;
        const event = JSON.parse(part.slice(6));
        if (event.kind === 'certified' || event.kind === 'uncertified') finalEvent = event;
        else showEvent(event, cards);
      }
    }
    // Only publish after the response ends cleanly and the server supplies its verdict.
    if (finalEvent?.kind !== 'certified' || !finalEvent.receipt) throw new Error('No answer passed verification in this turn. The investigation notes remain unverified.');
    $('#answer').innerHTML = md(finalEvent.final_answer);
    showResolution(question, finalEvent.receipt);
    verdict(checkedLabel(finalEvent.status), false, finalEvent.status);
    setContext(finalEvent.receipt);
    $('#progress').textContent = '';
    history.unshift({question, receipt:finalEvent.receipt}); history = history.slice(0, 50); saveHistory();
  } catch (error) {
    verdict('No checked answer', true);
    $('#progress').textContent = error.name === 'AbortError' ? 'Stopped. No answer from this turn was published.' : error.message;
  } finally { controller = null; setBusy(false); }
}
$('#question-form').onsubmit = event => { event.preventDefault(); const question = $('#question').value.trim(); if (question) ask(question); };
$('#question').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && !event.ctrlKey && !event.altKey && !event.metaKey) { event.preventDefault(); $('#question-form').requestSubmit(); } });
$('#stop').onclick = () => controller?.abort();
$('#new').onclick = () => { if (busy) return; setContext(null); $('#result').hidden = true; $('#ask-title').textContent = 'What would you like to know?'; $('#examples').hidden = false; $('#question').value = ''; $('#progress').textContent = ''; completion.refresh(); $('#question').focus(); };
$('#clear').onclick = () => { if (!busy) { history = []; saveHistory(); } };
document.querySelectorAll('.example-grid button').forEach(button => { button.onclick = () => {
  if (busy) return;
  $('#new').onclick();
  $('#question').value = button.textContent;
  completion.refresh();
}; });
$('#access-code').addEventListener('input', () => $('#access-code').removeAttribute('aria-invalid'));
const completion = setupCompletion();
renderHistory();
fetch('/api/status').then(response => response.json()).then(status => {
  $('#service-status').textContent = status.ready ? 'Hosted agent configured. Your code is required to run it.' : 'The hosted agent is being configured.';
}).catch(() => { $('#service-status').textContent = 'The service is unavailable. Please try again shortly.'; });
