# Certification contract in version 0.2

Version 0.1 accepted model-written prose when its numbers matched result cells. That allowed correct arithmetic over the wrong population, fabricated expressions, and unsupported nonnumeric assertions. Version 0.2 certifies only server-compiled metrics and server-rendered answers.

## Client migration

Bind the original user question through the host as before. Call `begin_question`, then `prepare_answer(turn_id)`. Copy the returned `answer` and `claims` into `submit_answer` without editing them. Require `ok: true` and `certified: true`, verify the request identity, and publish `final_answer` unchanged. Failed submissions revoke previous certificates for that turn.

`prepare_answer` accepts only a turn ID. It reads the host-bound question, not the model's interpretation. The compiler consumes the entire question. An extra condition cannot be dropped to fit a known metric. Raw SQL and prose remain available for exploration but cannot supply a certificate.

Preparation returns `answered`, `no_matching_records`, `unsupported`, or `needs_clarification`. Execution errors return an error with `execution_failed`; they never become a count of zero. Unsupported requests receive a server-owned limitation or clarification. A successful refusal is distinct from an answered measurement.

Reopened web chats use the same publication rules. A stored badge alone is insufficient: the latest submission must have a valid certificate for the original question and the same final answer. Legacy, edited, revoked, and interrupted submissions appear as unverified working notes. Reading a chat does not alter the saved history.

## Evidence and metric definitions

Count queries use the collision table directly or filter it with `EXISTS`. Fatalities are the sum of collision-level recorded fatality counts; missing, negative, fractional, or nonfinite values prevent a complete certified total. Integer counts retain exact precision in both prose and evidence. County ranking excludes missing county labels and reports all leading ties. A party is not automatically a vehicle, and fatal crashes are not fatality totals.

Whole-year and COVID-period requests must fit the reconciled snapshot's date bounds. Partial coverage does not become a full-period count. Missing or invalid coverage metadata prevents calendar-period certification.

Parked-vehicle shares disclose primary involvement, party type, and preceding movement as separate definitions. Cellphone shares use at-fault driver parties with recorded binary values as the denominator and report excluded unknowns. Belt shares use recorded passenger-car driver/passenger victims, both equipment fields, and mutually exclusive recorded belt status. These victim shares do not represent all occupant risk. COVID comparisons describe calendar and April periods and calculate the direction from results; they cannot establish a causal effect.

Every numerical assertion records its approved metric, population, operation, result-cell references, and computed value. Non-numerical ranking and direction wording is rendered by the corresponding metric implementation. Queries, returned rows, and final assertions are retained in the trace.

## Tests and extensibility

`test_certification.py` tests publication, request binding, refusals, and tampering. `test_certified_metrics.py` provides independent known answers for supported examples. `test_certification_attacks.py` adds invalid-data cases and randomized data checked against independent Python calculations. `test_certified_dataset.py` runs the 11 known examples against the real snapshot without a model. `test_chat_history_certification.py` tests saved-history migration and submission ordering. `test_review7_grain.py` tests direct and derived joins, including scalar lookup attacks. `test_review7_eval_exit.py` exercises evaluator process exit codes. `test_cli_exit_status.py` checks that uncertified CLI runs fail while certified answers and limitations succeed.

The older arithmetic/parser regressions now call `Toolkit.check_calculation`. They retain their numerical expectations but explicitly receive `certified: false` and no final answer. Certification has separate positive and negative tests; the change does not disable the previous regression checks.

To add a supported question, define its metric and population, match the complete request, compile its SQL, render its assertions, and add independent fixtures and adversarial variants. Do not add keyword detection that discards unmatched conditions. Before release on a changed snapshot, rerun full-data reconciliation and live-model checks. The 2026-10-19 validation on the pinned snapshot passed 479 tests and all 11 live example questions.
