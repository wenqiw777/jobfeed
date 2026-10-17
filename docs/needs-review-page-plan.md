# Needs review decision page

Scope: local implementation and Chrome screenshot review only. No push, Mini deployment,
or production identity decisions until Wenqi confirms the screenshots.

- [x] Separate pending and decision-history page, source comparison, explicit merge target,
  keep-separate action, navigation count, loading/error/empty states.
- [x] Persist decisions atomically. Merge preserves source/history/application data,
  refuses active claims or incompatible workflow statuses; keep-separate survives refresh.
- [x] Identity ambiguity does not block scoring or hide scores. Existing application,
  archived-status, source-quality and spam protections remain effective.
- [x] Test-first backend/UI regression checks; real Chrome flow against isolated dev DB,
  direct DB verification and screenshots using copied United/Boeing examples.

Material choice: each candidate pair is a decision. Multiple possible duplicates may
produce multiple cards; choosing one merge does not silently merge other candidates.
History preserves the compared snapshots and chosen target.

Evidence (2026-10-16, America/Detroit):
- Initial backend tests failed because review methods did not exist (2 failures).
  Initial frontend test failed because the route did not exist.
- Final SQLite/API/OpenAPI focused checks: 10 passed. Includes separate decisions
  surviving identical-content source refresh, merge target/history, active claim
  rejection, applied/archived claim guards and stale/invalid HTTP decisions.
- PostgreSQL real-container decision tests: 2 passed (explicit target merge/separate).
- Tracked repository test baseline: 3364 passed, 19 skipped, 479 deselected,
  8 expected failures. New UI + existing frontend suite: 194 passed.
- Ruff and mypy pass; frontend lint, typecheck/build pass; OpenAPI/types regenerated.
- Chrome extension, localhost:5174 with Air API 17656: expanded original JD,
  cancelled merge, confirmed merge, kept pair separate, visited saved decisions,
  refreshed and verified persisted state. No Mini writes.
- Direct dev DB check: United target 158543 retained both sources; Boeing parents
  remained separate; 0 pending pairs and 2 decision snapshots; quick_check=ok.
- The test-only Air data was then restored and reseeded to two pending comparisons
  for Wenqi's review. Before/after DBs and public-source snapshots are retained in
  artifacts/needs-review-page. These are preview fixtures, not production decisions.
- Final screenshots: pending.jpg, merge-confirmation.jpg, history.jpg in that folder.
- Self-review: explicit target validation and transaction locking, score/history and
  application preservation, separation carry-forward on later merges, source URL
  scheme checks and stale decision rejection inspected. No open release blocker.

Finding dispositions:
- Two existing untracked semantic-label experiment tests fail against changed local
  sample data. Not changed by this task; tracked suite above passes.
- Default repository-root test discovery also traverses untracked data/ experiment
  dependencies. Verification was rerun against tracked test files.
- Browser console's existing Cloudscape list-key warning also appears in tests;
  interactions and persistence pass. Non-blocking; no unrelated UI cleanup.
- Existing unrelated changes in .gitignore, sqlite_schema.py,
  test_priority_schema_compatibility.py and vite.config.ts were preserved.
- Built web-ui/dist assets must be included with the eventual approved commit.

Wenqi approved shipping with 'push 部署同时'. Push and Mini deployment proceed
in parallel, with CI and live health/queue verification. Production review
decisions remain for Wenqi; preview decisions are not deployed.

Release evidence: fb6a7b7 pushed and Mini deployed concurrently on 2026-10-17 UTC.
CI run 38988365494 passed. Mini health DB/Redis ok; server running and scheduled
scan agent restored. Chrome verified /needs-review on port 7654. Live screenshot:
artifacts/needs-review-page/mini-live.jpg. No production decisions (history=0).
Deployment backup: data/maintenance/needs-review-20261017T005242Z/code.tar.
Compile smoke excludes existing macOS ._ metadata files; actual Python passes.

Post-deploy scope finding: live page shows 1036 historical candidate pairs, whereas
previous Results discussion concerned two jobs. Pending query currently includes
all non-self review pairs, without Results eligibility filtering. User flagged
this mismatch; do not represent the total as 1036 current scoring blockers.

Scope correction (2026-10-16 America/Detroit):
- Pending reads now require a current actionable review state and Results visibility
  (workflow, closed/application/spam, company/location/date policy). Ordinary
  identity_conflict candidate flags alone do not become manual tasks.
- Keep candidates of an eligible job available even if the candidate is historical.
  Sidebar counts distinct eligible jobs; page distinguishes jobs from comparisons.
- Red: historical-clear pair test returned 1 instead of 0. Green: 12 SQLite/API/
  OpenAPI tests, 2 real PostgreSQL tests including bound filter values; frontend
  suite 194 tests, lint/typecheck/build and mypy pass. Chrome preview shows 2 jobs.
- Read-only candidate code against Mini: 11 comparisons belonging to United 169159
  and Boeing 168542. No production decisions made. Screenshot scoped-preview.jpg.
- User explicitly authorized push and deployment together after verification.
