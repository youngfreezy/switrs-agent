# Contextual question suggestions

## Contract

The four starter questions remain visible whenever the composer is available.
LLM Tab completion continues independently. Jev adds up to three related questions
below the starters, preserving the starter buttons' position while ranking loads.
Buttons prepare an editable, fully scoped question; they never submit it or inherit
an invisible prior scope. All answer certification continues through the existing
question compiler, database and evidence checks.

`POST /api/suggestions` accepts `{draft: string, previous?: Receipt}`. Drafts are
single-line and at most 1,000 characters. The access code and prior receipt are
authenticated before ranking. The response is `{draft, suggestions: string[],
source: "jev" | "fallback"}`. Fallback returns an empty array; the static starter
questions remain usable. Extra request fields are rejected.

The server builds at most 16 candidate questions from approved metrics and recipes.
County and year are explicit. Unsupported or ambiguous years/counties produce no
candidates; fixed recipes never acquire unsupported county or year filters. Jev
receives only the draft, authenticated previous question, the existing code's
explicit place/year follow-up resolution, and approved candidates.
No prior numerical answer, receipt signature, browser history or dataset rows go
to the ranking model. Model-generated text is never returned as a suggestion.

## Provider and failure behavior

Use Vercel AI Gateway `POST /v1/evaluate` with `typesafe-ai/jev-1.14.0` and the
existing Gateway key or per-request Vercel OIDC identity. No TypeSafe key or new
dependency is required. One request scores all candidates with three concrete
relevance levels. A candidate needs at least 0.7 probability on the most relevant
level; valid scores determine order, with stable candidate order breaking ties.
Probabilities must be finite, bounded, normalized and consistent with the score.
Unknown IDs cannot introduce questions. The threshold controls optional prompt
display, never answer truth or query execution.

Client requests wait 900 ms after edits, skip empty drafts without context, and
invalidate responses after changes to text, active receipt, credentials, IME or
busy state. Identical state does not repeat a request. Nothing is stored in browser
history until the user submits and gets a checked answer. Provider failures remain
optional: four-second HTTP timeout, five-second route timeout, six-second browser
timeout, no retries, at most two ranking calls per worker, and cancellation on
disconnect. This is a per-worker bound, not an account-wide spending limit.

## Verification

Run `pytest tests/test_hosted_suggestions.py` and
`node --test tests/frontend/hosted-ux.test.cjs` for deterministic regression checks.
Run the full Python suite before shipping.

Live model checks incur a small Gateway charge:

```sh
python -m scripts.eval_suggestions --env-file .env.local --repeat 2 --output /tmp/jev-eval.json
```

The evaluation covers metric distinctions, topic fragments, county/year follow-ups,
paraphrases, unsupported scope, prompt injection and unrelated requests. Relevance
is measured separately from the hard guarantee that only approved candidates can
be selected. Also verify the hosted UI with real Jev, click a related question,
submit through the unchanged agent, and compare its result to SQLite. Inspect
320/768/1024/1440 px layouts, keyboard controls and continued starter availability.

Sources: [Gateway decision API](https://vercel.com/docs/ai-gateway/modalities/decision),
[TypeSafe scoring](https://docs.typesafe.ai/primitives/score),
[probabilities and confidence](https://docs.typesafe.ai/confidence).
