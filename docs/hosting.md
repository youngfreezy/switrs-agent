# Hosted SWITRS

The `switrs-agent` Vercel project is separate from Grasp. Its interface uses Grasp's paper palette, serif headings, forest-green controls and evidence-first reading layout. The original local CLI and UI remain available.

## Runtime boundaries

- `hosted/app.py` owns HTTP validation, access control, streaming and cleanup.
- `hosted/runtime.py` supplies Claude through Vercel AI Gateway. It uses the project's OIDC identity; no desktop Claude login is deployed.
- `switrs_agent` owns the bound question, SQL guards, metric compiler, dataset integrity and certification. Both UIs use `publication.py` to decide what may be published.
- `hosted/receipts.py` signs published answers with a separate server secret. Browser history is never accepted as metric evidence. Saved answers are authenticated before display and labelled as historical.
- `hosted/static` owns presentation and browser-local history. A complete question never inherits filters. A follow-up such as “And in 2020?” or “What about Orange County?” may replace one explicit scope in the selected prior question. No shared server chat list or trace endpoint exists.

The model has up to eight turns and 4,096 output tokens per turn. A request has a 240-second application deadline and a 300-second Vercel limit. Model errors, lost submissions and interrupted responses cannot publish a checked answer. These are usage bounds, not a guaranteed dollar cap. The private access code prevents anonymous model usage; use AI Gateway budgets for an account-wide spending cap.

## Dataset deployment

The full, reconciled 1.26 GB DuckDB snapshot is compressed in a private Vercel Blob store. The build downloads and expands it, verifies its exact size and SHA-256 against `hosted/dataset_meta.json`, then packages it with the Python function. The runtime verifies the file again before answering. It never downloads or trusts arbitrary model-selected data.

Interrupted transfers, HTTP 429, and server errors get at most three attempts. Each attempt starts from byte zero and must pass the same integrity checks. Digest mismatches and permanent HTTP failures stop immediately. Failed attempts remove their temporary file and cannot replace the previous verified copy or publish a new deployment.

This requires Vercel Fluid compute and large-function support (`VERCEL_SUPPORT_LARGE_FUNCTIONS=1`). No metrics were replaced with cached answers or aggregates. Python deployments and their timestamps may change; the content hash remains authoritative.

The uploaded archive is 579 MB. Building and deploying consumes Blob storage/transfer and Vercel function resources. The database is not committed to Git. Local data is excluded from CLI source uploads; the build retrieves the pinned archive.

DuckDB spills large query intermediates into a private system temporary directory, not beside the read-only deployed database. Connections to the same database share that directory for the process lifetime. Temporary storage does not enable database writes or external-file access; memory and temporary-disk caps still apply. The spill regression test forces an external sort with a blocked adjacent directory and confirms that the source database remains unchanged. See [DuckDB temporary-directory configuration](https://duckdb.org/docs/lts/configuration/pragmas#temp-directory-for-spilling-data-to-disk).

The Vercel entry point currently caps DuckDB at 1 GB of memory, two threads, and zero disk spill. The parked-vehicle recipe uses membership filters over eligible party IDs so it fits those limits. A separate-process snapshot regression checks all three percentages and certification under that exact budget; ordinary tests with the local 4 GB default do not establish hosted query viability.

## Configuration

| Variable | Purpose |
| --- | --- |
| `SWITRS_DATASET_URL` | Private Blob URL for the pinned compressed snapshot |
| `BLOB_READ_WRITE_TOKEN` | Downloads the snapshot during builds; supplied by its Vercel integration |
| `SWITRS_ACCESS_KEY` | Private shared code required by `/api/chat` and `/api/completions` |
| `SWITRS_RECEIPT_KEY` | Independent signing secret, never sent to browsers |
| `SWITRS_COMPLETION_MODEL` | Autocomplete model; defaults to `anthropic/claude-haiku-4.5` |
| `SWITRS_MODEL` | Gateway model ID, currently `anthropic/claude-opus-5-5` |
| `x-vercel-oidc-token` request header | Vercel-managed runtime identity; local development can use `VERCEL_OIDC_TOKEN` |
| `VERCEL_SUPPORT_LARGE_FUNCTIONS` | Set to `1` for the full dataset package |

Use separate high-entropy access and receipt keys. Rotating the access code does not invalidate receipts; rotating the receipt key does. The app returns 503 if either key is missing. Never put either secret in source, a public environment variable, a URL, or browser storage.

## Validate and deploy

```sh
uv run pytest -q
node --check hosted/static/app.js
vercel --prod
SWITRS_ACCESS_KEY=... uv run python evals/run_hosted_challenges.py \
  --url https://your-project.vercel.app \
  --source /path/to/switrs.sqlite \
  --only count_fronted,fatal_motorcycle,cellphone_control,injection,extra_filter
```

The live evaluator hashes the SQLite source, computes independent oracle queries, checks every certified claim and verifies the saved-answer signature. It does not send expected answers to the model. Check desktop and mobile layouts, a valid answer, an unsupported question, stop/error recovery and saved-history verification in a real browser.

### Follow-up contract

The browser sends the selected answer's signed receipt as `previous`. The server authenticates it before running an agent. `hosted/followups.py` consumes the whole follow-up and may replace exactly one year or county in a previously parsed count or ranking. It preserves the metric and every other filter. County replacements for rankings, extra conditions, ambiguous references, and unsupported parent questions receive clarification. A complete new question uses only its own scope.

The agent and metric compiler receive the deterministic resolved question. The UI shows that question beside the original follow-up. Version 2 receipts sign both questions, the answer, and its outcome status, so the next follow-up can use the resolved scope without trusting browser edits or model-written summaries. Existing version 1 receipts still authenticate. Prior numerical answers are never evidence: each follow-up queries the verified snapshot again.

New question, failed requests, stopped requests, and limitations clear the active context. Selecting an answered history record authenticates and restores that record's context. Limitations receive “Checked limitation” wording in both the badge and answer footer.

Reproduce the hosted adversarial and follow-up suites with `evals/run_hosted_challenges.py --cases evals/hosted_adversarial.json` and `--cases evals/hosted_followups.json`, plus the URL and SQLite source arguments above. Run the full follow-up suite so each named predecessor exists. The suite includes signed-context tampering and preserves independent SQLite SQL for each expected value.

For rollback, promote the previous known-good Vercel deployment. Keep its pinned dataset archive and signing key available. A data update requires full reconciliation, a new digest-named archive and an updated committed manifest before deployment. Do not overwrite an existing snapshot.

Vercel injects the runtime token into each request, not the process environment. The controller passes it directly to the agent factory without mutating shared environment variables. See [Vercel system environment variables](https://vercel.com/docs/environment-variables/system-environment-variables#vercel_oidc_token).


### LLM autocomplete contract

`POST /api/completions` accepts `{ "prefix": "How many fatal motorcycle cra", "previous": null }` with the same Bearer access code as chat. The prefix is preserved byte-for-byte, is 4–1,000 characters, and must be a single line. `previous` may be an existing signed receipt; forged receipts return 422. Only answered or no-matching-records receipts supply context. The model receives the resolved prior question and at most 2,000 characters of its checked answer, plus the deployed manifest's coverage, complete years, county vocabulary and supported forms. It never receives access codes, signing secrets, browser history or SQL tools.

The response is `{ "prefix": "…", "completion": "…" }`. An empty completion means no suggestion. The provider returns a structured suffix through `complete_question`; the server appends it to the exact draft and validates the complete question against the existing count compiler or descriptive recipes. At most 240 characters may be appended. Unsupported filters, unknown counties, partial/out-of-range whole years, markup, extra prose and truncated model output are discarded. A suggestion cannot issue a certificate or run a query. Only explicit submission enters the normal question-binding and certification flow.

Each worker allows two autocomplete requests independently of chat, without queuing excess requests. The provider has a five-second timeout, no retries and 256 output tokens; the route has a six-second deadline. Capacity exhaustion and provider failure return an empty suggestion. These worker limits and browser debounce are not an account-wide rate limiter; retain AI Gateway budgets for spending control. The default fast model uses the existing per-request Vercel OIDC identity, with no additional client credentials. See [Gateway Anthropic Messages API](https://vercel.com/docs/ai-gateway/sdks-and-apis/anthropic-messages-api).
