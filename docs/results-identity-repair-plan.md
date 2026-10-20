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
- [x] Deploy and reconcile production (authorized by “部署”).

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
- Production deployment and scoped reconciliation completed after authorization;
  original code/database backed up before changing either.


## Production deployment evidence

- Deployed the four tested identity modules after byte-for-byte baseline checks
  against Mini; preserved all unrelated local and Mini changes.
- Paused writers and backed up code plus the entire SQLite database to Mini:
  `data/backups/results-identity-20261020T033511Z/`.
- Rehearsal on the full backup and the production transaction produced identical
  counts: 166,062 -> 165,690 canonical parents (372 removed duplicate parents),
  178,402 source rows retained, 397 application rows retained, 228,301 status-history
  rows retained, and both interview rows retained. Existing application content
  and explicit workflow decisions were checked before commit. Scope was limited
  to 27 reviewed shared-ID groups; the conflicting group was excluded.
- SQLite quick_check and foreign_key_check passed. Deployed sources match tested
  local bytes. API, DB, Redis healthy. Chrome extension loaded live Triage normally.
- Live Results: Initi8 has zero remaining results, with matching source pairs
  sharing Applied parents; Synchrony API Engineer I - Credit has one result.
  Epic folderId=30063 now has one parent, retaining its original May discovery
  date, so it falls outside the configured recent-posting Results window.
- Restored scheduler and paused background process. Restoring an idle legacy
  intermediary worker triggered RunAtLoad; stopped it immediately through the
  run API and launchd. Comparison against the pre-deploy backup found zero source
  URL, apply URL, JD, or enrichment timestamp changes. Active runs returned empty.
- Initial remote CI: browser tests passed; quality gate found an existing typed
  JobSpy method monkeypatch error. Added an explicit Any cast for that dynamic
  assignment, with no runtime behavior change. Eight patch tests and full-source
  mypy passed locally. Remote CI rerun is tracked separately.
