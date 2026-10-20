# Live agent challenges against SQLite

Run the live Claude agent, MCP server, and certificate publisher, then compare every
published numerical claim with independently written queries against the original
SQLite file:

```sh
uv run python evals/run_live_challenges.py --source /path/to/switrs.sqlite
```

The runner uses the configured `SWITRS_MODEL` (or `--model`), the existing Claude
subscription authentication, a default $1 model budget and 240-second timeout per
question. It checks billing before starting. `--only count_fronted,fatal_motorcycle`
selects cases. Expected measurements and oracle SQL are never supplied to the model.
Built-in model tools, other MCP servers, and user settings are disabled.

`evals/live_challenges.json` contains questions, independent SQLite SQL, exact count
checks, and explicit floating-point tolerances for percentages. The runner verifies
the source hash against the reconciliation manifest before querying. Every published
certificate must match the question and analytical file hash. Refusing a supported
question fails. Publishing numbers for a limitation fails. Unexpected numerical
claims also fail. Raw model wording cannot replace the server's certified answer.

Each run saves `outputs/evals/live-*/report.json` and per-question MCP traces. Reports
include source and analytical hashes, the tested commit and dirty-state marker,
questions, SQL, expected values, certificates, raw model output, costs, and timings.
The checked-in cases are regression challenges, not a held-out reliability benchmark.
Passing them does not establish that arbitrary English questions are supported.

`tests/test_live_paraphrases.py` covers the false refusals found in the initial live
run, county/year preservation, ranking ties, and adversarial nearby forms.
`tests/test_live_challenges.py` checks the oracle grader itself, including NaN,
unchecked claims, false refusals, and integer counts above floating-point precision.
