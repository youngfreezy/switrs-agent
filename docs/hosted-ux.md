# Hosted UX design and checks

The visual reference is V2 Software, https://v2software.ai/ (reviewed October 20, 2026), not Fareez’s personal portfolio. The palette matches the live site's Tailwind tokens: navy ink `#12203a`, cream paper `#f6f5f1`, rule `#dcd9d0`, secondary text `#4a5163`, green `#1f6b45`, success background/foreground `#e3f0e8`/`#17543a`, and warning background/foreground `#fbebd2`/`#7a4508`. The light hero uses V2's 36px ledger grid (`#e9e6de`/`#eeebe4`), serif headings and navy buttons. This app uses local system font fallbacks (Georgia, Arial and monospace), with no external font or script dependency.

The primary flow is question → access code if needed → checked answer → follow-up. The question and access controls precede history in both document order and mobile layout. Examples prepare an editable, independent question without sending a request. A prior answer’s verified scope appears under the composer only when follow-ups are supported. A limitation has a distinct neutral badge and clears that scope. Invalid credentials reopen and focus the access field without losing the question.

The presentation preserves the complete server-certified answer, scope, caveats, evidence and receipt verification. Raw model notes and tool results remain behind an explicitly unverified investigation disclosure. No numerical answer is derived or rewritten in the browser.

## Verification

Run the behavior regressions with `node --test tests/frontend/hosted-ux.test.cjs`, check syntax with `node --check hosted/static/app.js`, and run the service regressions with `uv run pytest -q`.

Browser acceptance: inspect 320, 768, 1024 and 1440 px widths for overflow; verify starter selection, missing/invalid access, keyboard entry, a real checked answer, a year follow-up, saved-answer authentication, cancellation and an unsupported question. Keep live query results consistent with the SQLite oracle. The narrow layout must put the composer ahead of history, preserve visible labels and focus, and keep evidence readable without page-wide horizontal scrolling.
