# Changelog

## Unreleased

### Fixed

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
