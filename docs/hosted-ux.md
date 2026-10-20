# Hosted UX design and checks

The visual reference is V2 Software, https://v2software.ai/ (reviewed October 20, 2026), not Fareez’s personal portfolio. The palette matches the live site's Tailwind tokens: navy ink `#12203a`, cream paper `#f6f5f1`, rule `#dcd9d0`, secondary text `#4a5163`, green `#1f6b45`, success background/foreground `#e3f0e8`/`#17543a`, and warning background/foreground `#fbebd2`/`#7a4508`. The light hero uses V2's 36px ledger grid (`#e9e6de`/`#eeebe4`), serif headings and navy buttons. This app uses local system font fallbacks (Georgia, Arial and monospace), with no external font or script dependency.

The primary flow is question → access code if needed → checked answer → follow-up. The question and access controls precede history in both document order and mobile layout. Examples prepare an editable, independent question without sending a request. A prior answer’s verified scope appears under the composer only when follow-ups are supported. A limitation has a distinct neutral badge and clears that scope. Invalid credentials reopen and focus the access field without losing the question.

The textarea uses a separate LLM to suggest a continuation after 450 ms without typing. Requests require the access code and contain only the current draft and active signed receipt. The server supplies dataset coverage, county vocabulary, supported query forms and the last authenticated answer. Suggestions are draft questions, never checked answers. The reserved hint row displays “Suggesting…” while waiting, with no composer layout shift.

Tab appends the visible suggestion without submitting; Escape cancels both visible and pending suggestions until the text changes. Undo restores the draft. A second Tab, Shift+Tab, an unmatched prefix, a selection or a caret away from the end keeps normal navigation. Suggestions also have a touch/click button. Changes to text, context, access code, focus, busy state or IME composition invalidate outstanding responses. Continuing to type a matching visible suffix reuses it without another model call. Empty, complete, multiline or over-1,000-character drafts do not request suggestions. Provider failure leaves manual typing and submission available.

The presentation preserves the complete server-certified answer, scope, caveats, evidence and receipt verification. Raw model notes and tool results remain behind an explicitly unverified investigation disclosure. No numerical answer is derived or rewritten in the browser.

## Streaming and loading states

During a request, replace the composer with a compact submitted-question card, animated thinking dots, one Stop control and a live progress feed. The Understand / Query / Verify highlight follows actual tool calls; it does not imply a percentage or a completed stage. Display only known, plain-language descriptions of tool activity, never model prose as a pending answer. Show at most three recent updates and respect reduced-motion preferences.

On clean completion, place the checked result above the restored composer. Cancellation and errors restore the editable question and clear the loader; the app never publishes a partial certificate. A saved-result load uses the same card with an authentication message and no Stop control. Focus moves from the disappearing composer to the activity heading, then to the result unless the user moved elsewhere. Keep the V2 palette and preserve keyboard autocomplete.

## Verification

Run the behavior regressions with `node --test tests/frontend/hosted-ux.test.cjs`, check syntax with `node --check hosted/static/app.js`, and run the service regressions with `uv run pytest -q`.

Browser acceptance: inspect 320, 768, 1024 and 1440 px widths for overflow; verify starter selection, missing/invalid access, keyboard entry, a real checked answer, a year follow-up, saved-answer authentication, cancellation and an unsupported question. Keep live query results consistent with the SQLite oracle. The narrow layout must put the composer ahead of history, preserve visible labels and focus, and keep evidence readable without page-wide horizontal scrolling.
