# Changelog

## Unreleased

- Resolve explicit year/county follow-ups from authenticated prior question scopes, preserving all other filters and displaying the resolved question. Distinguish checked limitations from numerical answers in the hosted UI.
- Reject malformed Unicode receipt signatures normally instead of returning HTTP 500.

- Accept additional whole-question paraphrases for reported traffic accidents, fatal motorcycle counts, calendar years, known-status cellphone shares, and leading fatal-crash hours. Preserve all filters and reject nearby population changes.

- Add a separate Vercel app with a Grasp-inspired interface, Claude via AI Gateway, and the full verified dataset packaged with the function.
- Authenticate saved answers with server signatures and keep question history in each browser.
- Add hosted HTTP regressions and live SQLite comparison checks.

## Unreleased

### Fixed

- Answer scoped count paraphrases, reordered county/year clauses, and calendar-year county rankings. Preserve every condition and ranking tie. Support equivalent cellphone-share, drinking-driver, and fatal-hour wording without changing metric definitions.
- Add live Claude challenges checked against independently queried SQLite values, with source/dataset hashes, false-refusal checks, and regressions for combined filters and unverified conversation reuse.

- Preserve useful coded-fatal answers when known fatal records coexist with unknown severity. State unknown counts and exclusions; do not convert an unknown population without observed fatal codes into zero. Unknown codes are not counted as known severity/fatality conflicts.

- Encode the SQLite source URI so reserved filename characters cannot make reconciliation hash one source but compare another.

- Use `(case_id, id)` for victim identity; the real snapshot legitimately repeats victim IDs across collisions. Duplicate identities within a collision still fail reconciliation.

- Verify an explicitly supplied database connection's file against the manifest rather than validating a different file selected by the environment.
- Reject incomplete motorcycle eligibility and orphaned case bookkeeping records found during the follow-up adversarial pass.
- Block complete fatal-crash measurements when the requested population has unknown severity. Counts, hour rankings, and period comparisons disclose conflicts with fatality counts and explicitly measure records coded fatal. Other valid counts remain answerable.
- Use the selected snapshot's coverage in answerability checks, refusals, context, and answer footers. Updated snapshots can answer newly available years. Static catalog statistics are no longer presented as measurements of the active snapshot.
- Reject invalid source numeric domains before conversion and reconciliation; fractional or non-binary motorcycle flags can no longer be rounded into positive observations.
- Compare complete source and analytical records by key, rejecting changed associations, missing/duplicate keys, and altered values even when marginal totals match.
- Verify the analytical file's SHA-256 before serving answers, including when a replacement preserves file size and modification time. Certificates include the analytical digest. Run reconciliation again to upgrade older manifests.

## 0.2.0

### Changed

- Certification now requires `prepare_answer` followed by an unchanged `submit_answer`. Approved server metrics and templates determine the result. Custom clients must require `certified: true`; arbitrary SQL and model prose cannot certify answers.
- Unsupported question forms and unresolved follow-ups ask for clarification. The CLI suppresses unchecked fallback answers.
- Traces retain every returned evidence row rather than only a preview.

### Fixed

- Return a failing `switrs ask` exit code when no answer is certified, including normal model stops and failed or pending revisions.

- Reject party-level and collision-level totals multiplied by child joins, including derived queries and scalar lookups.
- Return a failing evaluation exit code for failed or missing answers and reject invalid question selectors.
- Reject legacy arithmetic-only successes as evaluation certificates.
- Revalidate saved chat badges against the latest matching submission. Legacy or revoked answers remain visible as unverified working notes; saved history is preserved.
- Preserve exact integer counts, reject invalid individual fatality values, disclose unknown injury outcomes, and check requested calendar periods against reconciled coverage.

See [the client migration and metric definitions](docs/certification.md).
