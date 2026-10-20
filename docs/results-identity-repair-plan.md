# Results identity repair

## Scope and acceptance

Repair observed posting identity so mirrors and city-specific advertisements for
one official requisition share a canonical parent. Preserve source rows, Applied
and application history, explicit status conflicts, distinct official requisitions,
and user decisions to keep jobs separate. No new hashes, schema, or UI changes.
Deployment and production reconciliation were authorized by the user with “部署”.

## Implementation

- [x] Recognize tenant-scoped Avature RegisterMethod folderId, SmartRecruiters jobs,
  Synchrony TalentBrew job IDs, and known PwC/Serco Phenom paths. Unwrap the observed
  DoubleClick destination once, without network requests or generic URL guesses.
- [x] Let the same LinkedIn posting tolerate city labels after official-ID conflict
  checks and company/title compatibility. Different official IDs still block joins.
- [x] Replace the >50-source content-match bailout with batches in SQLite and
  PostgreSQL. Ambiguous candidates still require review; exact identities use
  indexed lookups. No bulk merge on title or JD alone was introduced.
- [x] Exercise destination matching, rescan idempotence, application retention,
  manual separation, explicit status conflicts, and differing requisitions.
- [x] Produce a read-only historical candidate report from a consistent Mini export.
- [ ] Deploy and reconcile production (authorized; in progress).

## Evidence

Initial targeted run: 11 failed, 22 passed. Failures reproduced missing destination
parsers, LinkedIn city aliases, and the >50-source matching cutoff.

Current targeted SQLite/domain/application suite: 190 passed. A 60-source historical
group was reconciled in 20-source pages twice on a disposable SQLite database:
one parent, all 60 source rows retained, Applied retained, PRAGMA quick_check=ok.

PostgreSQL compatibility suite: 15 passed, including the new destination and
>50-source content matching regression. Initial fixture startup lacked `alembic`
on PATH; rerun with the virtualenv bin directory passed.

Source type checking passed for the four changed production modules. All nine
changed/new code and test files passed lint and format checks; git diff --check
passed.

Live Epic folderId=30063 source URLs and city labels were replayed in an isolated
local SQLite database: 145 parents became one, all 145 source rows survived, two
backfill passes were idempotent, and PRAGMA quick_check returned ok. This is not a
production reconciliation; see `epic-replay.json`.

Artifacts: `artifacts/results-identity-repair/live-sources.json` and `review.json`.
The export was read-only, transaction-consistent, captured 2026-10-19 23:23 EDT,
and limited to the affected providers and known LinkedIn examples. It contains
1,133 source rows. The report identifies 28 shared-ID groups (401 group memberships),
27 with no detected static blocker and one SmartRecruiters group with conflicting
requisition IDs. These are candidates, not final approval or a live Results count.
Active evaluations and intervening changes must be checked before any actual apply.

## Findings and dispositions

- Existing source identifiers cannot be trusted to contain newly supported IDs;
  reuse `backfill_real_job_identifiers` on an isolated snapshot before live apply.
- Preserve the SmartRecruiters conflicting-ID group for review; do not merge it.
- The 50-merge transaction safety bound remains; the content candidate-count cutoff
  was removed. Large shared-ID history converges through paged backfill.
- The original checkout has unrelated changes; they are excluded from this work.
- No production data, code, service configuration, or scheduled worker was changed.
