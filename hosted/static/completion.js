// Local question suggestions only; accepting one never calls the agent.
function setupCompletion() {
  const input = $('#question'), panel = $('#question-suggestion');
  const ghost = $('#question-ghost'), button = $('#complete-question');
  const starters = [...document.querySelectorAll('.example-grid button')]
    .flatMap(node => [node.textContent.trim(), node.textContent.trim().replace('occurred in', 'in')]);
  let dismissedValue = null, composing = false;
  function candidate() {
    if (busy || composing || document.activeElement !== input || !input.value.trim() ||
        dismissedValue === input.value || input.value.includes('\n') ||
        input.selectionStart !== input.value.length || input.selectionEnd !== input.value.length) return '';
    const options = previousReceipt ? [...starters, 'And in 2020?', 'What about Orange County?'] : starters;
    // Tolerate spacing without rewriting anything the user has typed.
    const escaped = input.value.trimStart().split(/\s+/)
      .map(word => word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('\\s+');
    const prefix = new RegExp('^' + escaped, 'i');
    for (const option of options) {
      const match = option.match(prefix);
      if (match && match[0].length < option.length) return input.value + option.slice(match[0].length);
    }
    return '';
  }
  function refresh() {
    const text = candidate(), suffix = text.slice(input.value.length);
    panel.hidden = ghost.hidden = !text;
    $('#completion-prefix').textContent = text ? input.value : '';
    $('#completion-text').textContent = suffix;
    $('#completion-description').textContent = text ? 'Suggestion: ' + text + '. Press Tab to complete or Escape to dismiss.' : '';
    button.setAttribute('aria-label', 'Complete question: ' + text);
    ghost.scrollTop = input.scrollTop;
    ghost.scrollLeft = input.scrollLeft;
  }
  function accept() {
    const text = candidate();
    if (!text || panel.hidden) return false;
    // Native insertText preserves Undo/Redo. Direct .value assignment clears history.
    // If a browser lacks this editing command, leave Tab as normal focus navigation.
    if (!document.execCommand?.('insertText', false, text.slice(input.value.length))) return false;
    refresh();
    return true;
  }
  input.addEventListener('input', event => {
    // Undo should restore the draft, not immediately offer to reinsert the same text.
    dismissedValue = event.inputType === 'historyUndo' ? input.value : null;
    refresh();
  });
  for (const type of ['focus', 'blur', 'click', 'keyup', 'select', 'scroll']) input.addEventListener(type, refresh);
  input.addEventListener('compositionstart', () => { composing = true; refresh(); });
  input.addEventListener('compositionend', () => { composing = false; refresh(); });
  input.addEventListener('keydown', event => {
    if (event.isComposing || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return;
    if (event.key === 'Escape' && !panel.hidden) {
      dismissedValue = input.value; refresh(); event.preventDefault();
    } else if (event.key === 'Tab' && accept()) event.preventDefault();
  });
  // Keep the textarea's selection while a pointer activates the completion control.
  button.addEventListener('pointerdown', event => event.preventDefault());
  button.onclick = accept;
  refresh();
  return {refresh};
}
