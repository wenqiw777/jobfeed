# JD content display deduplication

Status: prior local sample verification complete, but expanded real-source
acceptance now exposes 7 false-negative scenarios and an order-dependence bug.
Cross-source acceptance is NOT complete. Running service on 7654 unchanged.
No shipping authorization.

## Outcome and boundaries

Results should show one representative of near-identical JD content across
source IDs, sources and locations. Preserve every original posting and URL.
Do not change exact-ID enrichment reuse, evaluation claims, workflow mutations,
or add hashes. No commit, merge or release is authorized.

## Confirmed causes

- HEAD uses company/title soft clustering. The current uncommitted change in
  domain/dedupe.py makes external identity override that key for every caller,
  including display. The September 18 scan plan requires exact IDs for browser
  enrichment targets; that rule has leaked into display grouping.
- September 3 investigation already recorded the location-only content rule;
  the historical implementation compared company/title, never JD similarity.
- Current Results API returns 467381 and 467382 with different LinkedIn IDs,
  but identical 6,802-character JD bodies. Both company values are Unknown.
- SQLite list queries currently replace JD text with NULL, so a pure comparator
  change alone cannot fix the actual Results path.

## Independently verifiable tasks

1. Separate display content grouping from exact identity clustering. Test
   cross-source identical bodies, location-only differences, near-identical
   bodies, distinct companies, teams, levels and requirements, empty/short JDs,
   and unchanged exact-ID enrichment grouping. Record failing then passing tests.
2. Supply content to the display path, including in-flight candidates; fold
   before pagination and provisional display. Verify SQLite-backed service
   responses and totals, plus representative/status behavior.
3. Replay the real Amazon sample read-only and inspect the browser flow and
   screenshot before claiming app-level completion. Record performance and
   limitations. No destructive record consolidation.

## Matching policy readiness

99.95% was given as an example; optional threshold clarification received no
answer during implementation. Starting assumption is >=99.5% word similarity
after formatting/location normalization,
with matching role titles and guards for employer and substantive requirement
changes. Similarity alone must not collapse distinct teams or levels.
Missing content is not proof of duplication across different native IDs.

## Risks / open findings

- Source summaries may omit large sections; do not claim they are near-identical
  without evidence. Lower similarity and semantic summaries are out of scope.
- Unknown company values require conservative matching and remain a separate
  data quality issue.
- Existing unrelated uncommitted work must be preserved.

## Implementation and current evidence

Expanded user-requested acceptance is recorded in
`artifacts/jd-cross-source-audit/REPORT.md`: 19 real scenarios, 12 pass / 7 fail;
one additional controlled order-dependence failure. Earlier 77 green checks
below do not establish complete cross-source coverage. Repair remains open.

- Separate display-only content folding preserves exact identity clustering
  for enrichment/evaluation. Native posting records and URLs are untouched.
- SQLite includes JD text only when the service opts in for dedupe; ordinary
  page reads retain their existing lightweight contract. In-flight lookup
  additionally retrieves same-title candidates and confirms body equivalence.
- Provisional pages fold local duplicates; cross-status reconciliation remains
  in the subsequent exact request, as before.
- Red: initial content tests 6 failed / 1 passed; SQLite service tests 2 failed.
  Additional red checks caught C++/C# normalization and tiny team differences.
- Green: 77 checks spanning display, native identity, jobs view service,
  SQLite integration/contracts, legacy dedupe and priority projection.
  Focused Ruff, mypy (four implementation modules) and git diff --check pass.
- Read-only current Results replay: old content fold disabled gives 2,564 rows;
  final rules give 2,241, or 323 fewer displayed rows. This is a rule effect,
  not a manually adjudicated precision measurement of all 323 suppressions.
  Amazon Leo records 467381/467382 fold to 467382. Original bodies are exactly
  equal (6,802 characters); neither posting is deleted.
- Existing compiled UI tested in Chrome through an isolated GET-only preview
  on 7656, reading the real DB via SQLite mode=ro. Final screenshot shows
  2,241 postings. The normal 7654 worker was not restarted or hot-modified.
- Timing: baseline replay 4.943 seconds; first content replay 8.446 seconds;
  final preview API 13.652 seconds while browser requests also ran. These are
  observations, not a controlled latency benchmark. Further optimization is
  advisable before broadening the candidate/title matching policy.
- PostgreSQL query branch updated consistently, but no live PostgreSQL run
  performed. SQLite is the verified active backend.
- Existing detail twin links and workflow write expansion remain native-ID
  based; source records are retained in Library. This change does not implement
  a new content-group expansion UI or group-wide Ignore/Wait writes.
- Evidence: artifacts/jd-display-dedupe-audit.json plus test output and Chrome
  screenshot in the task. No commit, push, merge, migration or DB cleanup.
