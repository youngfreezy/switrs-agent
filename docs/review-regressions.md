# Reproducing the certification fixes

The five review failures now have file-backed or in-memory regression tests. The tests use tiny datasets with known answers, so they run without a Kaggle download or model credentials.

```sh
uv sync --frozen
uv run pytest -q tests/test_dataset_integrity.py tests/test_snapshot_coverage.py tests/test_fatal_classification.py tests/test_integrity_followup.py
```

## Original failures and required outcomes

| Case | Reproduction | Required outcome |
| --- | --- | --- |
| R1: changed record associations | Swap counties between records while preserving county totals, severity totals, and total fatalities. The source has two Orange County deaths; the corrupted copy has zero. | Reconciliation fails on complete records. The corrupted copy cannot start a serving toolkit. The unchanged source/copy pair still certifies two deaths. |
| R2: unknown severity becomes zero | Replace all severity codes with NULL while retaining positive fatality counts. Ask for fatal collision counts, hour rankings, or period comparisons. | Return `data_quality_blocked` with the unknown count. Never certify zero fatal collisions. General collision and recorded fatality totals remain available when their own inputs are valid. |
| R3: substituted analytical file | Replace the approved DuckDB file with a same-sized file and restore its timestamp. | Startup rejects the changed SHA-256. The certificate for an unchanged file includes its analytical digest. |
| R4: contradictory coverage | Reconcile a 2019–2020 fixture, then ask about 2021 or today. Also test a newer snapshot that includes 2022. | Classification, refusal, context, and footer use the selected manifest. The older fixture refuses 2021; the newer fixture can answer supported 2022 questions. |
| R5: fractional category becomes positive | Store `0.6` in the SQLite INTEGER-affinity motorcycle flag, which a BIGINT cast can round to one. | Reject the source before conversion. Matching invalid values in both source and copy do not bypass reconciliation. |

The ingestion tests also cover changed flags, changed victim-parent associations, physical row reordering, invalid binary values, empty-string normalization, and exact counts above `2**53`.

## Second adversarial pass

The follow-up suite rejects missing or invalid motorcycle eligibility instead of reporting a complete zero. It rejects NULL party keys, duplicate `(case_id, id)` victim identities, and orphan case bookkeeping rows. Victim IDs can repeat across different collisions in the real source; a positive regression preserves those valid records. It also checks that an explicitly supplied database connection is verified against the manifest; an environment variable pointing to an approved file cannot validate another connection.

A source filename containing `#` previously made the reconciler fingerprint that file but open the path before `#`. The regression demonstrates false approval against a different source; URI encoding now binds both operations to the selected file. A valid filename containing `#` still passes.

Ten question variants add unsupported conditions or instructions, including omitted pedestrian exclusions, vehicle color, Nevada, exposure rates, and demands to certify invented numbers. They must yield a limitation or clarification with no measurement claims. Supported county, year, fatal, motorcycle, and fatality questions retain independently known fixture answers.

The end-to-end test exercises source reconciliation, a read-only file-backed database, MCP calls, preparation, and final publication. It verifies both a useful measurement and a snapshot-specific refusal.

## What a passing test establishes

A certified limitation is not a measured answer. Clients must inspect `status` as well as `certified`, and publish the exact final response. Dataset files and manifests remain trusted deployment inputs: the digest catches substituted bytes at startup, but does not authenticate an administrator-edited manifest.

See [validation](validation.md) for full-snapshot checks and [the certification contract](certification.md) for supported metrics and client requirements. These regressions are concrete counterexamples and controls, not a proof that all future questions or datasets are correct.
