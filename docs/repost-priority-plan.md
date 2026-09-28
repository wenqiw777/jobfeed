# Repost recognition, evaluation exclusion, and Triage ordering

Status: Implementation and local API/UI/DB checks completed. Live signed-in LinkedIn recognition verification is blocked by browser read timeouts; not released.

## Agreed behavior

- Confirmed reposts are retained but excluded from new model scoring, including SDE gate, seniority model, Stage A and Stage B.
- Existing scores and user decisions remain; do not clear or recompute scores merely because a repost is detected.
- Time ordering: reposts last within the same local calendar day.
- Score ordering: descending 10-point bands; ordinary/unknown first, confirmed repost last within each band; actual score orders each subgroup. Top band is 90–100, followed by 80–89 etc.; null scores last.
- Unknown repost status is eligible, and must not be presented as confirmed original.
- Repeated discovery, age, and a new source ID are not sufficient repost evidence.
- No new hash fields, computations, validations, or dependencies.

## Confirmed day semantics

The user selected option 1: first discovery in Jobfeed, bucketed in America/Detroit. Change Triage time ordering from posted_at to discovered_at and label it First seen. Do not refresh first discovery when scanning an existing ID or observing a repost. Within each local day, confirmed reposts sort after ordinary/unknown records; retain exact discovery time as the subgroup ordering key.

## Tasks and evidence

1. Validate acquisition against real LinkedIn cards and detail pages. Extract only date/repost evidence scoped to the exact job card; exclude recommended-job cards. Preserve fixtures for explicit repost, explicit original wording, absent field, and embedded references. Verify against visible page text. If the search response lacks sufficient evidence, use bounded detail lookup and leave failures unknown. Do not infer an undocumented API field. This is the first verifiable result and prerequisite for recognition implementation.
2. Add nullable is_repost, repost_evidence (short exact source text), repost_observed_at to the posting model and persisted stores. NULL means unknown. Preserve posted_at and discovered_at semantics. Align SQLite/Postgres schema, serializers, canonical schema metadata and API DTOs. A missing observation must not erase a confirmed repost; affirmative repost evidence is sticky for the same source identity. Verify migration on a test DB and direct readback after upsert.
3. Extend the extension discovery payload and map_board_job to carry repost evidence for new and reused jobs. Existing complete JDs can remain cached, but fresh discovery evidence must still merge into the old row. Test repeated same-ID scan: one job row, updated not inserted, unchanged first discovery and evaluations, newly persisted repost marker.
4. Apply a shared eligibility predicate to Stage A candidate/gate/claim paths and independent Stage B paths, including retries and explicit-ID normal evaluation. Recheck persisted repost status immediately before model dispatch to handle a scan updating the flag after candidates were loaded. Already dispatched calls cannot be cancelled retroactively. Record a distinct repost skip reason; do not set a job-wide hard_filter that would hide previously scored rows from Triage. Verify zero model invocations for confirmed repost, including a Stage-A-completed/Stage-B-pending record, and eligibility for unknown.
5. Add Triage-specific ordering before pagination and use it consistently in regular SQL, service fallback, Postgres and priority-snapshot paths actually serving Triage. Do not change Library's ordering implicitly. Keep repost-last within a group for both sort directions. Score bands never cross because of repost status. Time grouping follows the resolved day semantics. Refresh/invalidate materialized priority snapshots when repost evidence changes. Verify ordering across page boundaries, timezone midnight/DST boundaries, equal scores, 100, missing scores and existing user status.
6. Display Reposted on existing scored rows and Skipped scoring: repost in Library/run reporting for unscored records. Verify real browser flows and capture pre-merge screenshots; user screenshot approval remains required for merge. Do not introduce a blanket age filter.

## Historical data and rollout boundaries

Historical rows start unknown, not original. The saved 12-row browser audit is diagnostic evidence, not permission to silently relabel all 209. Any historical backfill must read explicit per-job evidence, report coverage, and persist bounded changes with a backup and direct readback. Rebuilding sort projections does not call scoring models. Automatic cross-source/new-ID repost classification is out of scope without an explicit identity/evidence rule. No commit, push, merge or release in this design task.

## Verification discipline

Behavior changes are test-first: record failing and passing focused checks. Complete the actual browser/DB checks appropriate to each boundary. A frontend-only reorder or only blocking Stage A is incomplete. Keep progress and evidence here during execution.

## Implementation evidence (2026-09-20)

- Implemented nullable source observation fields, migration 0014, SQLite additive repair, sticky upserts, PostgreSQL parity and API list fields. First discovery and existing evaluations remain intact.
- Source discovery now parses explicit card-local rendered Reposted age labels (including Default-state/RSC references and split text), with bounded title-header HTML fallback. It excludes other job cards and recommendation sections. Fresh observations merge into cached JD payloads and checkpoint reuse. Missing/failed observations remain unknown. No historical blanket backfill was performed.
- Stage A gate/claim and independent Stage B query/claim/preview exclude confirmed reposts. Gate, seniority, Stage A and Stage B dispatch boundaries recheck stored evidence. Calls already sent are not cancelled. Dispatch skips have a distinct log reason, and unscored Library rows show `Skipped scoring: repost`; this change does not add a new aggregate run counter.
- Triage uses explicit `triage_*` sort names, preserving Library semantics. SQLite, PostgreSQL, in-memory fallback and priority snapshot paths implement the same day/band ordering before pagination. Snapshot ordering reads the live observation, so a repost update needs no rebuild or new projection identity. Provisional recency-only pages are disabled for these sorts to prevent misleading initial order.
- UI: First seen is rendered in America/Detroit; existing scored reposts keep scores and show Reposted. Native browser pass used an isolated five-job SQLite database with the real API and built UI on port 7668, not production data. Verified time demotion, score ascending/descending, and Library skip reason. Captured screenshots in the task; fixed clipped badge found during this pass.
- Test-first checks: missing posting fields and sort keys failed initially; dispatch and source/cache tests failed before guards/parsing; frontend had 3 expected failures before the new header/query/badge wiring. Passing regression command covered 217 Python tests; extension suite 29 passed; frontend 21 passed; TypeScript and Vite build passed. Separate real PostgreSQL container test applied migrations and verified sticky upsert, preserved score, claims, and pagination (1 passed). Existing-database SQLite upgrade and direct SQL readback passed.
- Outstanding verification: LinkedIn's live signed-in HTML/RSC capture could not be completed because browser DOM/CDP reads repeatedly timed out. Therefore acquisition is implemented conservatively and tested against controlled fixtures, but live recognition coverage is not yet proven. The prior 12-row visual audit is not being represented as a parser fixture or complete backfill.
- Delivery boundary: source changes remain in the existing checkout. No commit, push, merge, production-service restart, extension reload, or production-data backfill was performed. Screenshot approval and live acquisition verification remain pre-release requirements.

## Current verification — 2026-09-21

Read-only live verification is recorded in artifacts/repost-verification-20260921/report.md. Ninety persisted confirmed reposts have no scores; the latest evaluation completed with Stage A 122 / Stage B 54 / errors 0. Four real API sorts over 113 Rippling records (five pages per sort) have zero violations. Fourteen focused regression tests pass, including zero-call dispatch and scored-repost sorting. Browser verification confirms the current localhost:5173 UI sends triage sort parameters and shows the actual Rippling repost as skipped scoring. No scored confirmed repost exists in production, so that demotion remains verified with controlled test data. Found a delivery discrepancy: port 7654 serves an older static frontend; its backend supports the new sorts, but that static UI is not current. No release performed.
