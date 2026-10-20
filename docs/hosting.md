# Hosted SWITRS

The `switrs-agent` Vercel project is separate from Grasp. Its interface uses Grasp's paper palette, serif headings, forest-green controls and evidence-first reading layout. The original local CLI and UI remain available.

## Runtime boundaries

- `hosted/app.py` owns HTTP validation, access control, streaming and cleanup.
- `hosted/runtime.py` supplies Claude through Vercel AI Gateway. It uses the project's OIDC identity; no desktop Claude login is deployed.
- `switrs_agent` owns the bound question, SQL guards, metric compiler, dataset integrity and certification. Both UIs use `publication.py` to decide what may be published.
- `hosted/receipts.py` signs published answers with a separate server secret. Browser history is never accepted as metric evidence. Saved answers are authenticated before display and labelled as historical.
- `hosted/static` owns presentation and browser-local history. Each question is independent; include its county, year and measure. No shared server chat list or trace endpoint exists.

The model has up to eight turns and 4,096 output tokens per turn. A request has a 240-second application deadline and a 300-second Vercel limit. Model errors, lost submissions and interrupted responses cannot publish a checked answer. These are usage bounds, not a guaranteed dollar cap. The private access code prevents anonymous model usage; use AI Gateway budgets for an account-wide spending cap.

## Dataset deployment

The full, reconciled 1.26 GB DuckDB snapshot is compressed in a private Vercel Blob store. The build downloads and expands it, verifies its exact size and SHA-256 against `hosted/dataset_meta.json`, then packages it with the Python function. The runtime verifies the file again before answering. It never downloads or trusts arbitrary model-selected data.

This requires Vercel Fluid compute and large-function support (`VERCEL_SUPPORT_LARGE_FUNCTIONS=1`). No metrics were replaced with cached answers or aggregates. Python deployments and their timestamps may change; the content hash remains authoritative.

The uploaded archive is 579 MB. Building and deploying consumes Blob storage/transfer and Vercel function resources. The database is not committed to Git. Local data is excluded from CLI source uploads; the build retrieves the pinned archive.

## Configuration

| Variable | Purpose |
| --- | --- |
| `SWITRS_DATASET_URL` | Private Blob URL for the pinned compressed snapshot |
| `BLOB_READ_WRITE_TOKEN` | Downloads the snapshot during builds; supplied by its Vercel integration |
| `SWITRS_ACCESS_KEY` | Private shared code required by `/api/chat` |
| `SWITRS_RECEIPT_KEY` | Independent signing secret, never sent to browsers |
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

For rollback, promote the previous known-good Vercel deployment. Keep its pinned dataset archive and signing key available. A data update requires full reconciliation, a new digest-named archive and an updated committed manifest before deployment. Do not overwrite an existing snapshot.

Vercel injects the runtime token into each request, not the process environment. The controller passes it directly to the agent factory without mutating shared environment variables. See [Vercel system environment variables](https://vercel.com/docs/environment-variables/system-environment-variables#vercel_oidc_token).
