# Triage Priority Snapshot Performance Plan

Status: **IMPLEMENTED AND VERIFIED on 2026-09-03.** All six tasks are complete
in the local working tree; no commit, push, or merge has been performed.

## Outcome

Triage returns the first 100 already-ranked jobs without loading or parsing job
descriptions and without launching a second full-corpus request. Eligibility,
compensation, company evidence, queue tier, and priority are computed outside
the HTTP read path and stored in a disposable one-row-per-job read model.

## Confirmed decisions

- Keep SQLite and the existing Jobfeed run lifecycle; do not add Redis, Celery,
  Elasticsearch, or a second service.
- Use a normal projection table rather than a native materialized view because
  the ranking rules are Python domain logic and Jobfeed must support SQLite and
  PostgreSQL consistently.
- Scan completion performs one full Priority Score refresh after enrichment.
- Each daily evaluation starts one full Priority Score rebuild at the same time.
  The two jobs run independently and concurrently; neither waits for the other.
- A Priority Score rebuild never calls an LLM, never evaluates Evidence Fit,
  and never waits for Evidence Fit evaluation. It only reads the Evidence Fit
  already persisted for each job and evaluates the deterministic priority
  formula.
- Evidence Fit is reused when its JD, resume, and prompt inputs are unchanged.
  There is no daily Evidence Fit refresh requirement.
- Continue serving existing rows during a rebuild. Publish recalculated rows in
  short guarded batches rather than deleting and replacing the whole table.
  A failed rebuild leaves all previous rows readable and can resume/retry the
  unfinished jobs.
- Use the indexed stored order with bounded offset pagination. Do not stream rows
  before global order is known and do not use a background exact request.
- Do not add a startup timer, daily scheduler, cron job, or background daemon.
- Newly discovered jobs remain visible as Pending until their projection row is
  computed; a failed refresh must preserve the previous usable snapshot.

## Freshness contract

Freshness is intentionally day-granular. It must not decay continuously by
minutes or hours.

For the rebuild's captured UTC ranking date `D` and the posting's UTC date `P`:

```text
age_days = max(0, D - P)  # whole calendar dates, not elapsed hours

age_days <= 1   -> 100
age_days <= 3   -> 85
age_days <= 7   -> 65
age_days <= 14  -> 40
age_days >= 15  -> 10
```

Consequences:

- A job's Freshness score cannot change five minutes later.
- All jobs use one captured `ranking_date` for a full rebuild, so a long rebuild
  cannot straddle two dates and produce mixed freshness results.
- Daily evaluation is the user-triggered daily refresh boundary. Starting it
  launches the independent full Priority Score rebuild; there is no additional
  midnight timer or 24-hour staleness check.
- `posted_at` remains the final tie-breaker after `queue_tier` and
  `priority_score`; its precise timestamp does not change the Freshness
  component.

## Evaluation and Priority concurrency

The two workloads begin together:

```text
daily evaluation start
  |-- Evidence Fit evaluation (slow, model calls)
  `-- full Priority Score rebuild (fast, deterministic, no model calls)
```

The full Priority Score rebuild uses whatever persisted Evidence Fit exists
when each job is calculated. It does not rerun or modify Evidence Fit.

If evaluation creates the first Evidence Fit for a job, or legitimately replaces
one because its JD/resume/prompt fingerprint changed, that job's priority input
has changed. The system may then recompute **only that job's arithmetic Priority
Score**. This is not another Evidence Fit run and not another full rebuild. If
Evidence Fit did not change, no per-job Priority Score write occurs.

Put another way: the daily full Priority rebuild still processes every job to
refresh day-based Freshness. During the concurrently running evaluation, only a
job whose Evidence Fit is newly created or actually changed receives an extra
single-job/batch Priority Score update. A job that reuses its existing Evidence
Fit does not receive that redundant extra update.

Concurrent writes for the same job use the priority-input timestamp/fingerprint:
an older calculation cannot overwrite a score computed from newer inputs. There
is no stage barrier, replay queue, or evaluation-completion rebuild.

Therefore:

- Priority and Evidence Fit work in parallel from the start.
- Evaluation completion does not trigger or wait for a full Priority rebuild.
- The only time-based full refresh is the one started alongside the daily
  evaluation (plus scan completion when the corpus changes).
- Minute-level passage alone never triggers any refresh.

## Read model contract

Add a disposable `job_priority_snapshot` table keyed by `job_id` containing:

- eligibility status, reason, evidence, and enrollment eligibility;
- in-scope/display-representative flags for Triage visibility and dedupe;
- queue tier and total priority;
- compensation midpoint/percentile, company strength, freshness, New Grad
  clarity, Evidence Fit, and role-direction component scores;
- posting/discovery sort timestamps, policy version, input fingerprint, and
  `computed_at`.

Each row carries `computed_at`, `policy_version`, and the timestamp/fingerprint
of the priority inputs used to calculate it. Full rebuilds and changed-input
updates both use short batch upserts. An upsert must not replace a row calculated
from newer priority inputs. A failed transaction leaves the previous rows
readable; it never clears the table first.

The source tables remain authoritative. Snapshot rows can be regenerated and
must never own application status or user decisions.

The primary index follows the actual queue query:

```text
(in_scope, display_representative, blocked_rank, queue_tier, pending_rank,
 priority_score DESC, posted_sort_at DESC, job_id DESC)
```

Pages use `limit <= 100` plus the existing offset contract in this stored order.

## Tasks and acceptance evidence

- [x] **1. Persisted projection contract and store operations**
  - Result: additive SQLite/PostgreSQL schema, domain row model, small guarded
    batch upsert, state read, and indexed page query. Full and incremental
    publishers now share the same short-batch path; there is no all-table
    `DELETE`. `priority_input_updated_at` conditionally rejects a late result
    calculated from older source inputs.
  - Evidence: the focused behavior check first failed because full replace
    deleted the guarded row, then passed after removal of the delete path.
    Contract coverage also proves a failed batch preserves old rows,
    `EXPLAIN QUERY PLAN` uses the priority index, and the page read does not
    select `jd_text`.
  - Return to design only if the canonical schema cannot add the disposable
    table without destructive migration.

- [x] **2. Projection builder** (depends on 1)
  - Result: existing eligibility/company/pay/priority functions build snapshot
    rows outside requests. Full rebuild computes the pay cohort once; Evidence
    Fit batch refresh preserves the most recent cohort/freshness inputs.
  - Evidence: the UTC-date boundary check first returned `85` for a posting on
    the preceding UTC date, then returned `100` after changing Freshness from
    elapsed hours to whole calendar-day buckets. 65 focused
    schema/store/builder/ranking tests passed. Contract
    coverage proves rollback preserves old rows, `limit <= 100`, the page query
    omits `jd_text`, and `EXPLAIN QUERY PLAN` uses
    `idx_job_priority_snapshot_page`. Builder tests prove strict tier order,
    neutral unknowns, and one-ID Evidence Fit refresh leaves other rows byte-for-
    byte unchanged. Ruff passed; strict mypy passed for the new store/builder and
    PostgreSQL adapter. Final review exposed two pre-existing Alembic parent
    names that did not match their real revision IDs; those links were corrected.
    `alembic heads` now resolves to the single `0012` head, and verbose history
    proves one continuous chain from `0001` through `0012`.
  - Return to design only if a ranking input cannot be reconstructed from the
    authoritative tables/cache.

- [x] **3. Refresh lifecycle** (depends on 2)
  - Result: web scan completion performs one full refresh after enrichment. A
    daily evaluation starts one deterministic full Priority Score rebuild in
    parallel, using one captured calendar date. Evidence Fit evaluation never
    waits for it and it never waits for Evidence Fit. Only jobs whose persisted
    Evidence Fit input actually changes receive a cheap single-job/batch Priority
    Score update. Evaluation completion launches no rebuild. Formula/company-
    baseline changes and explicit maintenance may also request a full rebuild.
    No startup timer or separate scheduler is added.
  - Evidence: the lifecycle red checks first showed that `EvaluateService`
    owned an unjoined background task, CLI could close its store before a
    zero-work rebuild finished, Web had no shutdown drain, and equal input
    watermarks allowed an older computation to win. Green checks prove the
    service now owns only per-job Stage B refresh; CLI and Web own the concurrent
    full rebuild, projection failures are logged without failing evaluation,
    Web shutdown cancels/drains before store close, and both store contracts use
    `(priority_input_updated_at, computed_at)` ordering. CLI/web scan still
    refresh after enrichment while containing failures.
    Existing Stage B claim/fingerprint invalidation remains the changed-input
    seam: reused Evidence Fit never reaches `save_stage_b`, so it causes no
    incremental priority write. Builder/store tests additionally prove
    day-granular Freshness, no Evidence Fit/LLM dependency in the builder, and
    protection against older priority input overwriting newer input.

- [x] **4. Indexed Triage API and pagination** (depends on 1-3)
  - Result: priority-sorted Triage reads only the projection plus display fields,
    returns at most 100 rows with bounded offset pagination, and never invokes runtime
    eligibility, compensation, company matching, dedupe, or a 10,000-row fetch.
  - Evidence: the service routing check first failed by returning an empty
    10k-corpus slice, then passed through the direct snapshot port. 29 focused
    backend tests passed. The store contract proves the SQL omits `jd_text`,
    `EXPLAIN QUERY PLAN` uses `idx_job_priority_snapshot_page`, live
    Applied/Ignored status removes a row immediately.
    The service spy proves no ordinary jobs query, runtime decorator, or twin
    lookup runs on the canonical path. Canonical limits above 100 are rejected.
    A 100,000-row synthetic SQLite measurement recorded a 0.66 ms cold first
    page and 28.48 ms cold exact count (29.15 ms combined); warm medians were
    0.18 ms and 31.05 ms. Both plans used the covering priority index with no
    temporary sort. Existing fold tests explicitly require an in-flight twin
    to suppress its queue sibling. Whether ignoring the stored representative
    should instead promote a queued sibling has no current product contract and
    remains a non-blocking follow-up rather than adding a transition write path.
    A follow-up red check showed the four alternate Results sorts still entered
    the legacy 10,000-row path. The passing check now proves `priority_desc`,
    `posted_asc`, `posted_desc`, `score_asc`, and `score_desc` all call the
    snapshot port directly. SQL uses a fixed whitelist of snapshot columns;
    blocked/queue/pending tiers remain the leading keys and the selected posted
    or Evidence Fit component sorts only within a tier, with a stable job-id tie.
    The final COUNT hot-path check first exposed a 2.72 s real-database count
    because it joined `jobs` only to test `closed_at`. Closed jobs are now marked
    `in_scope=false` and excluded from representative selection during projection
    builds, so an open twin remains visible. Page reads no longer test
    `jobs.closed_at` and exact COUNT joins only snapshot to live status. On the
    current SQLite database (52,264 matching rows), a read-only measurement was
    132.73 ms cold and 14.09 ms warm median; its plan used the covering priority
    index plus the status primary key. No real snapshot rebuild was run.
  - Return to design only if existing decision semantics cannot be applied while
    walking the priority index.

- [x] **5. Frontend cutover** (depends on 4)
  - Result: Triage issues one first-page request, keeps the current page while a
    requested offset page loads, and does not fire an exact background request
    or speculative prefetch. Results pages contain up to 100 rows; select-all
    walks bounded pages of at most 100.
  - Evidence: the frontend checks first observed 50-row offsets and two
    fast/exact requests, then passed after the single-query cutover. 21 focused
    Vitest tests prove one first-page request, `limit=100`/`offset=100`,
    `keepPreviousData`, and bounded select-all pages. ESLint and TypeScript pass.

- [x] **6. Backfill and performance verification** (depends on 1-5)
  - Result: built the initial disposable snapshot from current data without
    changing jobs, evaluations, or user decisions, then verified the actual UI.
  - Evidence: the final snapshot contains 116,438 rows and 52,264 currently
    visible Results. The default Results API measured 803.57 ms on the first
    request and 149.35/146.12/147.10 ms warm. Warm health checks measured
    9.15-22.55 ms. The browser showed 52,264 postings with strict Priority order;
    the leading sample included NVIDIA New Grad, Palantir Forward Deployed
    Software Engineer New Grad, GM Early Career, Retell AI New Grad, Software
    Engineer I, and Junior roles. Browser console logs contained no application
    errors.
  - Verification: 2,122 backend tests passed; Ruff check, Ruff format check,
    strict mypy, `git diff --check`, and the production frontend build passed.
    All 16 Triage tests passed, including the one-request/no-fast-companion
    contract. The full frontend suite passed 159/160 in the resource-constrained
    single-worker run; its only five-second timeout passed immediately when
    rerun alone (1.79 s), confirming test contention rather than a product
    failure. A real-browser screenshot was captured after the final build.
  - Independent finish review found one deploy blocker in the pre-existing
    Alembic parent names. After the two-link correction, re-review reported
    `NO BUG FOUND`; its focused suite passed 184 tests with 30 deselected and
    confirmed the single `0012` migration head.

## Acceptance thresholds

- First Triage API page: under 500 ms warm and under 1 second cold on the current
  local database.
- Health endpoint remains under 250 ms while a full snapshot rebuild runs.
- One page load sends one jobs-list request; no automatic exact/full request.
- No jobs-list query reads 10,000 JD bodies or performs request-time ranking.
- First 100 Results are all selected by strict queue tier and stored priority;
  internships cannot precede eligible Primary jobs.
- A refresh failure leaves the previous snapshot readable.

## Baseline evidence

- Current exact Triage request reads and decorates 10,000 rows and previously
  completed in 30.6 seconds.
- The first snapshot COUNT implementation still joined `jobs` for `closed_at`
  and took 2.72 seconds on the current database. Moving that input into the
  offline projection reduced the read-only exact COUNT to 132.73 ms cold and
  14.09 ms warm median without rebuilding production data.
- Under overlapping exact requests, both a 10-second fast request and a
  45-second exact request timed out; the worker reached about 83% CPU.
- The database is already in WAL mode. WAL permits read/write concurrency but
  cannot prevent request-thread CPU work from blocking the application.

## Handoff

The first verifiable result is an indexed snapshot store that can return a page
without `jd_text`. The deliberately excluded work is new infrastructure and
streaming unsorted results. Completion requires current database timing and a
real browser network trace, not unit tests alone. A destructive schema migration
or inability to preserve the previous snapshot would invalidate this plan.

## Review findings dispositions

- **Task ownership and stale-write guard — accepted and fixed.** Full evaluation
  rebuilds are owned by the CLI command or Web `RunManager`, not by a detached
  `EvaluateService` task. CLI joins the concurrent rebuild before closing its
  store; Web shutdown cancels and drains its independent rebuild before closing
  the store. Equal input watermarks now use `computed_at` as the second guard so
  reverse completion order keeps the newer computation. Per-job Stage B refresh
  remains unchanged. The final focused suite passed 157 tests (17 deselected),
  with Ruff, format check, focused mypy, and diff-check clean.
- **Startup missing/stale rebuild — not adopted by explicit product decision.**
  There is no startup timer or scheduler; scan/evaluation lifecycle hooks own
  rebuilds.
- **One-transaction full-table replace — not adopted.** It conflicts with the
  approved short-batch, recoverable publication contract; batch upserts remain.
- **Ignored-twin promotion — deferred.** No product contract currently requires
  promoting a queued sibling when the stored representative becomes Ignored, so
  this change does not add a transition write path.

## Follow-up: daily evaluation scope

- [x] Persist exact inserted job IDs for both the latest scan and the local
  calendar day. Web and CLI scans use the same state-backed helper; a new day
  resets the daily bucket.
- [x] Default Evaluation to `today`, retain explicit `latest_scan` and `backlog`
  choices, and expose exact counts before the user starts a run.
- [x] Label live progress from the selected scope. A daily run reports its exact
  input size and no longer looks like a latest-scan run.
- [x] Restore today's existing scan history into state: 4,710 daily insertions;
  the latest scan contains 126. No evaluation was started.
- Verification: 56 focused backend tests, Ruff, focused mypy, 35 focused UI
  tests, ESLint, TypeScript, OpenAPI drift, and the production frontend build
  passed.

## Follow-up: Evidence Fit-only evaluation

- [x] The normal web evaluation no longer exposes or runs the legacy Quick
  Score/Detailed Review pair. It sends the internal compatibility value
  `stage=b`, whose current parser and prompt produce Evidence Fit.
- [x] Evidence Fit claiming no longer requires a completed legacy Stage A row;
  newly inserted jobs with no evaluation row can be claimed directly and
  atomically.
- [x] Active-run UI uses `Prepare scope → Evidence Fit → Complete`. Legacy
  storage columns remain intact to avoid a destructive migration.
- [x] Web, service, and CLI defaults all run Evidence Fit only. Current Runs,
  job detail, Insights, Performance, and Settings surfaces no longer describe
  the active workflow as Quick Score or Detailed Review; old Stage A counters
  are explicitly labeled as legacy history.
- Verification: the red checks first proved that the API defaulted to `both`,
  the dialog exposed both old stages, and an Evidence Fit-only claim could not
  select a new job. The passing focused suites cover API defaults, direct SQLite
  claiming, legacy threshold compatibility, and the current Runs UI. The final
  UI pass covered 75 tests; ESLint, TypeScript, Ruff, 82 focused backend tests,
  the production build, and a real-browser Runs check all passed.

## Follow-up: Applied and Ignored snapshot reads

- [x] Applied and Ignored now use the same bounded priority-snapshot API as
  Results, including stored eligibility/priority fields, dedupe semantics,
  supported within-tier sorts, 100-row pagination, and live workflow statuses.
- [x] Decided tabs drive SQLite reads from `idx_job_status_status` before joining
  the priority snapshot. This avoids scanning the 52,264-row Results corpus to
  find the much smaller Applied/Ignored sets; Results retains its covering
  priority-index plan.
- Verification: red routing/UI checks first proved both tabs still used the
  legacy path and omitted `dedupe=true`. A second red query-plan check exposed
  the full priority-index scan for Applied. The passing contract pins the live
  status index as the decided-tab driver. 141 related backend tests, 22 frontend
  tests, Ruff, strict mypy, and TypeScript pass. On the current database the
  Applied decision group contains 93 visible rows and Ignored contains 1,609;
  direct status-driven page reads completed in 18 ms and 192 ms respectively
  while another evaluation process was actively using the same SQLite database.
  After replacing a reload worker wedged behind stale proxy connections, warm
  live API requests completed in 5.4 ms for Applied and 10.3 ms for Ignored.
  The independent browser pass showed both exact totals, rendered Jobs tables,
  and no console errors.
