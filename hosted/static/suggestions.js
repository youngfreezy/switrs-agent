// Optional related questions. Starter prompts and inline completion work independently.
function setupSuggestions() {
  const input = $('#question'), panel = $('#related'), list = $('#related-questions');
  let timer = null, inflight = null, revision = 0, snapshot = null, composing = false;
  function same(current) {
    return current && current.draft === input.value && current.previous === previousReceipt &&
      current.code === $('#access-code').value;
  }
  function cancel() {
    revision++; clearTimeout(timer); timer = null; inflight?.abort(); inflight = null; snapshot = null;
    panel.hidden = true; list.replaceChildren();
  }
  async function request(current, version) {
    timer = null;
    if (version !== revision || !same(current) || busy) return;
    const controller = new AbortController(); inflight = controller;
    try {
      const response = await fetch('/api/suggestions', {
        method:'POST', headers:{'Content-Type':'application/json', Authorization:'Bearer ' + current.code},
        body:JSON.stringify({draft:current.draft, previous:current.previous}),
        signal:AbortSignal.any([controller.signal, AbortSignal.timeout(6000)])
      });
      if (!response.ok) return;
      const result = await response.json();
      if (version !== revision || !same(current) || busy || result.draft !== current.draft ||
          result.source !== 'jev' || !Array.isArray(result.suggestions)) return;
      const questions = [...new Set(result.suggestions)].filter(q => typeof q === 'string' &&
        q.length > 4 && q.length <= 300 && q.endsWith('?') && !/[\r\n<>`]/.test(q)).slice(0, 3);
      list.replaceChildren();
      for (const question of questions) {
        const button = document.createElement('button'); button.type = 'button'; button.textContent = question;
        button.onclick = () => {
          if (busy) return;
          // Each suggestion spells out its scope and starts independently of the old receipt.
          $('#new').onclick(); input.value = question; input.focus(); completion.refresh(); refresh();
        };
        list.append(button);
      }
      panel.hidden = !questions.length;
    } catch { /* Optional: keep the stable starter prompts and manual question flow. */ }
    finally { if (version === revision) inflight = null; }
  }
  function refresh() {
    if (busy || composing || !$('#access-code').value || input.value.length > 1000 ||
        /[\r\n]/.test(input.value) || (input.value.trim().length < 4 && !previousReceipt)) {
      cancel(); return;
    }
    if (same(snapshot)) return;
    cancel();
    snapshot = {draft:input.value, previous:previousReceipt, code:$('#access-code').value};
    const current = snapshot, version = revision;
    timer = setTimeout(() => request(current, version), 900);
  }
  input.addEventListener('input', refresh);
  input.addEventListener('compositionstart', () => { composing = true; refresh(); });
  input.addEventListener('compositionend', () => { composing = false; refresh(); });
  $('#access-code').addEventListener('input', refresh);
  refresh();
  return {refresh};
}
