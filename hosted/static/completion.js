// Suggestions draft questions only. The answer still uses the checked agent flow.
function setupCompletion() {
  const input = $('#question'), panel = $('#question-suggestion');
  const ghost = $('#question-ghost'), button = $('#complete-question'), status = $('#completion-status');
  let dismissedValue = null, composing = false, timer = null, inflight = null;
  let version = 0, snapshot = null, suggestion = '';
  function eligible() {
    return !busy && !composing && document.activeElement === input &&
      input.value.trim().length >= 4 && input.value.length <= 1000 &&
      dismissedValue !== input.value && !/[\r\n?]/.test(input.value) &&
      input.selectionStart === input.value.length && input.selectionEnd === input.value.length;
  }
  function same(current) {
    return current && current.prefix === input.value && current.previous === previousReceipt &&
      current.code === $('#access-code').value;
  }
  function paint(text = '', message = '') {
    panel.hidden = ghost.hidden = !text;
    $('#completion-prefix').textContent = text ? input.value : '';
    $('#completion-text').textContent = text.slice(input.value.length);
    $('#completion-description').textContent = text ? 'AI suggestion: ' + text + '. Press Tab to complete or Escape to dismiss.' : '';
    button.setAttribute('aria-label', 'Complete question: ' + text);
    status.textContent = text ? '' : message;
    status.hidden = !status.textContent;
    ghost.scrollTop = input.scrollTop;
    ghost.scrollLeft = input.scrollLeft;
  }
  function cancel() {
    version++;
    clearTimeout(timer); timer = null;
    inflight?.abort(); inflight = null;
    snapshot = null; suggestion = '';
  }
  async function request(current, revision) {
    timer = null;
    if (!same(current) || !eligible() || revision !== version) return;
    const controller = new AbortController(); inflight = controller;
    paint('', 'Suggesting…');
    try {
      const response = await fetch('/api/completions', {
        method:'POST', headers:{'Content-Type':'application/json', Authorization:'Bearer ' + current.code},
        body:JSON.stringify({prefix:current.prefix, previous:current.previous}),
        signal:AbortSignal.any([controller.signal, AbortSignal.timeout(7000)])
      });
      if (!response.ok) throw new Error('Suggestion unavailable');
      const result = await response.json();
      if (revision !== version || !same(current) || !eligible()) return;
      const text = result.completion;
      suggestion = result.prefix === current.prefix && typeof text === 'string' &&
        text.startsWith(current.prefix) && text.length > current.prefix.length &&
        text.length <= current.prefix.length + 240 && text.endsWith('?') &&
        !/[\r\n<>`]/.test(text) ? text : '';
      paint(suggestion);
    } catch {
      if (revision === version && same(current) && eligible()) paint('', 'Suggestions unavailable · keep typing');
    } finally {
      if (revision === version) inflight = null;
    }
  }
  function refresh() {
    if (!eligible()) { cancel(); paint(); return; }
    const code = $('#access-code').value;
    if (!code) { cancel(); paint('', 'Enter your access code for AI suggestions'); return; }
    if (same(snapshot)) {
      if (suggestion) paint(suggestion);
      return;
    }
    // Keep a useful continuation while the user types toward it, without another request.
    if (suggestion && snapshot?.previous === previousReceipt && snapshot.code === code &&
        suggestion.startsWith(input.value) && suggestion.length > input.value.length) {
      snapshot = {...snapshot, prefix:input.value}; paint(suggestion); return;
    }
    cancel();
    snapshot = {prefix:input.value, previous:previousReceipt, code};
    const current = snapshot, revision = version;
    paint();
    timer = setTimeout(() => request(current, revision), 450);
  }
  function accept() {
    if (!eligible() || !same(snapshot) || !suggestion || panel.hidden) return false;
    // Preserve native Undo/Redo. Without this command, Tab keeps normal navigation.
    if (!document.execCommand?.('insertText', false, suggestion.slice(input.value.length))) return false;
    dismissedValue = input.value; refresh();
    return true;
  }
  input.addEventListener('input', event => {
    dismissedValue = event.inputType === 'historyUndo' ? input.value : null;
    refresh();
  });
  for (const type of ['focus', 'blur', 'click', 'keyup', 'select', 'scroll']) input.addEventListener(type, refresh);
  $('#access-code').addEventListener('input', refresh);
  input.addEventListener('compositionstart', () => { composing = true; refresh(); });
  input.addEventListener('compositionend', () => { composing = false; refresh(); });
  input.addEventListener('keydown', event => {
    if (event.isComposing || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return;
    if (event.key === 'Escape' && (!panel.hidden || timer !== null || inflight)) {
      dismissedValue = input.value; refresh(); event.preventDefault();
    } else if (event.key === 'Tab' && accept()) event.preventDefault();
  });
  button.addEventListener('pointerdown', event => event.preventDefault());
  button.onclick = accept;
  refresh();
  return {refresh};
}
