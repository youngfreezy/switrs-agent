# Validation and development

[Project overview](../README.md) · [Usage](usage.md) · [Certification contract](certification.md)

## Run checks

```sh
uv run pytest -q                                            # guard, claim-checking, adversarial and evaluator tests
uv run python evals/run_evals.py --expected                 # recompute golden values from SQL, no model calls
uv run python evals/run_evals.py --freeze                   # approve changed golden values as the new baseline
uv run python evals/run_evals.py --agent claude --budget 2  # run the golden questions through the agent
uv run python scripts/reconcile.py --sqlite PATH            # SQLite vs DuckDB reconciliation
uv run python scripts/compare_engines.py --sqlite PATH      # same queries on both engines, results then timing
```

`evals/golden.yaml` holds 11 known questions covering counts, rankings, recorded equipment outcomes, period comparisons, and refusals. Cases also exercise join duplication, fatal-hour rankings, sparse cellphone data, and unavailable geography.

Number checks are sign-aware and require the entity in the same sentence. For traced runs, the last submission must have both `ok: true` and `certified: true`. Failed or missing answers and unknown `--only` selectors cause a nonzero exit status. The grader reads its submitted body, so server-added caveats and evidence cannot satisfy answer checks. Targeted checks also reject direct causal/bias verdicts and invented counts written as words. External answers without traces have unknown certification. If the golden SQL's values drift from `expected_v1.json`, the run stops until someone reviews the change and approves it with `--freeze`.

## Recorded results

The version 0.2 review on 2026-10-19 recorded:

- 479 tests passed with no skips, including all 11 example questions through the compiler on the real snapshot.
- The live `claude-opus-5-5` run passed 11/11 under the current grader. Five raw model outputs differed from their certificates; the publisher retained the unchanged server answers. The harness estimated $2.08 for the run.
- SQLite-to-DuckDB reconciliation passed with no conversion losses, duplicate keys, or orphan records. Recorded aggregates and frozen golden values match.
- The original 12-probe Model Context Protocol (MCP) harness accepted 9 unsupported answers on untouched upstream `b008b46`, and 0 after these changes. Both positive controls passed.
- New attacks cover large integers, invalid fatality values, unknown outcomes, zero denominators, ties, date coverage, tampering, and interrupted or stale certificates. Six randomized datasets are checked against independent Python calculations.

For traced live evaluations, `answer` contains the unchanged server-rendered certificate. The model's final text remains in `raw_answer`, with `output_matches_certificate` recording any drift. Only the submitted body is graded; server-added caveats cannot rescue an unsupported answer.

These 11 questions are known smoke examples. They are not a held-out generalization benchmark. Passing these tests does not prove universal correctness.

## Dataset reconciliation

The Kaggle SQLite file is the untouched source. It is only ever opened in immutable read-only mode. `scripts/build_duckdb.py` makes an analytical copy, and `scripts/reconcile.py` must pass before the tools will start. Reconciliation checks:

- non-NULL counts per column against the source (any value lost in conversion fails)
- numeric source domains before conversion, including exact integer affinity, binary flags, and nonnegative counts
- all source and copied column values compared in key order, with bounded Python memory; changed associations fail even if all marginal totals match
- key uniqueness and orphan rows
- aggregates by year, severity, county and injury

The result goes in `data/dataset_meta.json`, along with the source and analytical SHA-256 digests, the integrity version, and the DuckDB file's size and modification time. The tools hash the analytical file at startup and reject substitutions even when size and timestamp match. Reconcile again after this upgrade; older manifests do not contain the required checks. Record comparison scans and sorts the source and copy; allow additional reconciliation time and temporary space on the full dataset.

Run the new synthetic regressions without the large dataset or model credentials:

```sh
uv run pytest -q tests/test_dataset_integrity.py tests/test_snapshot_coverage.py tests/test_fatal_classification.py tests/test_integrity_followup.py
```

These tests preserve both sides of the contract: corrupted data cannot become a certified measurement, and supported questions on valid records still return independently checked answers.

Recorded engine comparisons returned matching aggregates. A nine-query comparison and a separate ten-query review both passed. SQLite took 86 to 111 seconds in total, against 1.35 to 1.5 seconds for DuckDB. These historical timings exclude an indexed SQLite copy and are not a performance guarantee.

## Execution limits

- read-only connection, external file access off, configuration locked
- 4 GB memory and 4 GB temp disk per process
- one 60-second deadline covering EXPLAIN, execution and fetch
- 200 rows and 150 KB per result
- 25 queries per question, 200 per session

## Known limits

- Certification supports the documented question forms, not unrestricted natural-language analytics. New forms require a metric definition, a full-question parser, and independent known-answer tests. Unsupported follow-ups require the user to restate the scope.
- Approved metrics and templates can still contain implementation bugs. Independent fixtures and adversarial tests reduce this risk; they do not establish universal correctness.
- The source snapshot contains police-reported collisions, not exposure or all road travel. Its final months are incomplete. Descriptive associations do not establish causes.
- A changed dataset requires fresh reconciliation and regression testing. Historical demo numbers and passing runs on a different snapshot are not substitutes.
- Exploration uses a conservative SQL guard. It can reject complex safe queries whose grain it cannot prove. A `data_derived` flag records expression dependencies, not truth or certifiability.
- Queries run in the server process. Public multi-user hosting needs process isolation and authentication; the HTTP MCP server has neither and shares one toolkit.
- Other MCP clients can omit the certification step or rewrite its output. The guarantee applies only to the exact server-rendered answer. Custom clients must require `certified: true`, the current request identity, and a successful final submission.

## Repository maintenance

The repository has a pinned dependency lockfile, regression tests, and package build metadata. It does not include a continuous integration workflow or a license file. Full-snapshot tests require a local copy of the dataset; model evaluations also require credentials.
