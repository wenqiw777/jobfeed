# SQLite core, claims, and run contracts

**Status:** Task 0 core slice; frozen for implementation subject to the blocking
tests in section 9

**Scope:** `JobStore` core (20 operations), evaluation claims (8 operations),
evaluation batch/preview (5 operations), and new run leases (3 operations)

**Out of scope:** SQLite production code, schema implementation, migration,
status/apply/ops/views/performance capabilities

**Parent source of truth:**
`docs/plans/2026-08-11-jobfeed-sqlite-cutover-impact-plan.md`

**Purpose:** Preserve observable PostgreSQL behavior while replacing the adapter,
and make the new cross-process run lease behavior precise enough to test before
implementation.

## 1. Contract precedence and notation

The SQLite implementation must follow this order when evidence conflicts:

1. The approved Phase 10 plan, especially its explicit SQLite representation,
   run-lease, concurrency, and acceptance rules.
2. This method-level contract.
3. Existing contract/integration tests.
4. Current `PostgresStore` behavior for cases not yet asserted by tests.

An ambiguity found at level 3 or 4 is not permission to choose convenient SQLite
behavior. It is a Task 0 test gap and remains blocking where section 9 says so.

Notation used in the matrices:

- **R:** one read statement; no explicit transaction owned by the method.
- **W:** one write statement; statement atomicity only.
- **TX:** all listed writes commit or roll back together.
- **IMMEDIATE:** SQLite `BEGIN IMMEDIATE`; external I/O is forbidden inside it.
- **No-op:** zero matching rows returns normally without mutation.
- Store identities are decimal integer database keys exposed as `str`.
- Unless a row says otherwise, a malformed `job_id` currently fails during
  `int(job_id)` conversion with `ValueError`; a missing numeric row is `None` for
  getters and a no-op for guarded updates. Representative read/write/claim and
  strict-batch evidence is in `test_core_store_input_contract.py`; only
  `claim_stage_a_by_ids` deliberately drops malformed members.
- The target adapter must normalize persisted datetimes to UTC ISO-8601 text with
  six microseconds and `Z`. Read DTOs still receive aware `datetime` values.
- Stage B JSON is decoded to Python objects on read. Target writes use canonical
  UTF-8 JSON: sorted keys, compact separators, `ensure_ascii=false`, and no
  NaN/Infinity. PostgreSQL JSONB does not preserve caller whitespace or key order,
  so parity is the canonical byte fingerprint of the decoded value, not raw
  `jsonb::text` equality. Invalid JSONB syntax is rejected atomically; invalid
  JSON stored in SQLite TEXT must raise `JSONDecodeError` on hydration rather
  than being coerced or silently dropped.

The existing port declarations are
`src/jobfeed/ports/store.py:17-246` and
`src/jobfeed/ports/store_claims.py:14-168`, plus the evaluation subset of
`src/jobfeed/ports/store_ext.py:17-96`. The implementation evidence is in
`src/jobfeed/adapters/store/postgres.py:129-656`,
`src/jobfeed/adapters/store/postgres.py:958-1622`, and
`src/jobfeed/adapters/store/postgres.py:1651-3440`.

Table references use these short names only to keep the matrices readable:
`store.py`, `store_claims.py`, and `store_ext.py` mean the full port paths above;
`postgres.py` means `src/jobfeed/adapters/store/postgres.py`;
`test_store_contract.py` means `tests/contract/test_store_contract.py`; and all
other test basenames mean their explicitly named `tests/integration/` or
`tests/store/` file.

## 2. Shared data, validation, and failure rules

### 2.1 Jobs and evaluations

- `jobs(platform, canonical_id)` is the job natural key and remains unique
  (`migrations/versions/0001_initial_schema.py:21-67`).
- `evaluations.job_id` is unique and references an existing job
  (`migrations/versions/0001_initial_schema.py:72-113`).
- Score and enum constraints remain enforced. Domain construction already rejects
  Stage A/Fit scores outside `0..100` and ML gate scores outside `0..1`
  (`src/jobfeed/domain/models.py:71-160`, `191-195`).
- Evaluation claim status values include `in_progress`; retryable error rows stop
  being eligible at `MAX_STAGE_RETRIES`. Evaluation claim TTL is one hour
  (`postgres.py:963-973`; migration `0004`:18-39).
- Every pending/claim path orders jobs by
  `discovered_at DESC, id DESC`. The second key is mandatory and deterministic.
- Every Stage A pending/claim path excludes `closed_at IS NOT NULL`.
- `quality_bands=None` and an empty set both mean no quality filter.
- `max_days=None` means no freshness cutoff; otherwise cutoff uses the method's
  application-supplied UTC `now` and `jobs.discovered_at`.
- Corpus values are exactly `unrated`, `failed`, and `all`. Other strings raise
  `ValueError` before database I/O (`postgres.py:1006-1029`, `1178-1214`).

### 2.2 Read hydration

- A job row becomes `JobPosting`; its numeric key becomes `str`, nullable
  timestamps/text remain `None`, and non-null `jd_quality` becomes `QualityBand`
  (`postgres.py:129-154`). Unknown quality values therefore fail hydration.
- Stage A is present only when `stage_a_status == "completed"`.
- Stage B is present only when status is `completed`, verdict is non-null, and the
  fit JSON is an object with a non-null `score_0_100`. Otherwise `stage_b=None`;
  usable completed fit JSON may still populate `stage_b_blocks`
  (`postgres.py:316-562`).
- Legacy Stage B JSON strings are decoded. Legacy severity names are mapped and an
  unknown severity raises `ValueError` (`postgres.py:356-420`, `565-610`).

### 2.3 Store lifecycle and database failures

- `connect()` and `close()` are idempotent for repeated calls. A failed open is
  propagated; a failed close is propagated after the store has been marked closed
  (`postgres.py:1676-1699`).
- Any operation before a successful `connect()` raises `RuntimeError`
  (`postgres.py:1701-1713`).
- Integrity, lock-timeout, disk, and driver errors are not converted into business
  success. SQLite may translate backend exceptions into a stable store-level
  error later, but must not turn them into `None`, `[]`, or a silent no-op.
- SQLite write contention follows the parent plan: 5-second `busy_timeout`, at
  most 3 jittered retries, and no more than 15 seconds total wait.

## 3. Core job and evaluation matrix (17 operations)

| Method and evidence | Input → output/error | Transaction and idempotency | Ordering, NULL, JSON, time, concurrency |
|---|---|---|---|
| `connect` (`store.py:27`; `postgres.py:1676-1692`) | No input → `None`; open failure propagates without publishing partial state | Lifecycle operation; repeated open is a no-op | SQLite must check version `>=3.35`, set every connection's PRAGMAs/functions, and not create a second logical store connection on repeat. PostgreSQL lifecycle evidence: `test_postgres_store_lifecycle.py`. |
| `close` (`store.py:31`; `postgres.py:1694-1699`) | No input → `None`; close failure propagates after detaching the pool | Repeated close is a no-op | After close, operations fail as not connected. Close-before-open, repeated close, and failed-close evidence: `test_postgres_store_lifecycle.py`. |
| `save_job` (`store.py:35-44`; `postgres.py:2188-2293`) | `JobPosting` → `SaveJobResult(job_id, inserted, updated)`; natural-key insert/update is atomic | **IMMEDIATE/TX** in SQLite. Same natural key never creates a second row. Replay reports `updated=True`; it is state-idempotent only for identical input | Incoming equal/higher-quality JD wins; lower quality preserves stored JD/provenance. `posted_at`/`enriched_at` preserve stored value when incoming is null. `discovered_at`, URL/title/company/location/norms take incoming values. Incoming JD clears `closed_at` and `enrich_error`; without JD, an existing closure wins over a later closure and an incoming non-null error wins over the stored error. A winning JD or title change clears five ML-gate verdict columns; a losing lower-quality JD does not. Two independent processes racing the same natural key return exactly one truthful insert and one update against one row (`test_store_process_concurrency.py`). Other evidence: `test_store_contract.py:230-305`; `test_store_pg_behaviors.py:218-239,522-608`; `tests/store/test_save_job_closed_at.py:28-104`. |
| `get_job` (`store.py:46-55`; `postgres.py:2295-2307`) | Decimal `job_id` → `JobPosting | None`; malformed ID raises `ValueError` | **R**, repeatable observation | Hydration follows section 2.2. Missing numeric row is `None`. Evidence: `test_store_contract.py:230-243,331-334`; closed fields at `test_save_job_closed_at.py:176-202`. |
| `list_jobs` (`store.py:57-66`; `postgres.py:2309-2324`) | `limit=100` → at most `limit` jobs | **R** | Strict order `discovered_at DESC, id DESC`; nullable fields hydrate unchanged. Evidence: `test_store_contract.py:307-328`, including equal-time tie-break. |
| `job_exists` (`store.py:68-78`; `postgres.py:2326-2343`) | Exact `platform`, `canonical_id` → `bool` | **R** | Exact, case-sensitive natural-key match; no NULL inputs in the port contract. It has no production call site, but remains a supported port behavior. Evidence: `test_store_contract.py:316-328`. |
| `save_stage_a` (`store.py:80-87`; `postgres.py:2405-2468`) | Existing decimal job ID + valid `StageAResult` → `None`; malformed ID/constraint/FK error propagates | **TX**: evaluation upsert plus optional `new→scored` status/history transition. Replay updates result but preserves first `stage_a_at`; only first eligible transition appends history | Sets status `completed`, clears error, preserves Stage B except reopening `skipped_below_threshold` to null. If job status is `new`, atomically transition to `scored`; later statuses remain. No external work in TX. Evidence: `test_store_contract.py:345-359`; `test_stage_a_status_store.py:79-169`, including rollback injection; SQL-column lock test `test_evaluation_persistence.py:268-311`. |
| `save_stage_a_error` (`store.py:89-96`; `postgres.py:2470-2490`) | Existing job ID + error text → `None`; malformed/FK errors propagate | **W**; deliberately non-idempotent: each call increments `stage_a_error_count` | Sets status `error`, stores last error, advances `updated_at`; does not advance workflow status. Evidence: `test_store_contract.py:385-392`; `test_stage_a_status_store.py:121-130`; retry cap `test_store_pg_behaviors.py:359-371`. |
| `save_stage_b` (`store.py:98-105`; `postgres.py:2492-2536`) | Existing job ID + valid `StageBResult` → `None`; malformed/FK/JSON/constraint errors propagate | **W**; replay overwrites result, clears error, preserves first `stage_b_at` | Stores flat fields plus 4 structured JSON blocks. If `raw_blocks` is absent, deterministic blocks are derived. JSON including Unicode round-trips to the same Python shape and canonical semantic bytes. PostgreSQL rejects syntactically invalid JSON without damaging the prior value; TEXT decode raises `JSONDecodeError`. Evidence: `test_stage_b_json_contract.py`; `test_store_contract.py:405-458`; `test_store_pg_behaviors.py:373-425`; `test_evaluation_persistence.py:268-325`. |
| `save_stage_b_error` (`store.py:107-114`; `postgres.py:2538-2558`) | Existing job ID + error → `None` | **W**; deliberately non-idempotent error count | Sets status `error`, last error, increments count and time. Evidence is only `test_store_contract.py:395-402`; repeated-count and missing-row coverage are missing. |
| `mark_stage_b_skipped` (`store.py:116-122`; `postgres.py:2560-2575`) | Decimal job ID → `None`; malformed raises; absent evaluation is no-op | **W**, idempotent | Sets `skipped_below_threshold` and `updated_at` unless already `completed`; it never erases a completed result. Evidence: `test_store_contract.py:363-383,1295-1303`. |
| `load_pending_stage_a` (`store.py:124-143`; `postgres.py:2676-2705`) | Filters → `list[JobPosting]`; invalid corpus raises `ValueError` | **R**, non-claiming | Uses shared filters and retry cap; excludes closed rows; order is recency + ID. `unrated`: no eval/null/error; `failed`: error only; `all`: no corpus status restriction (so it can include completed/fresh in-progress rows). Evidence: `test_store_contract.py:1278-1293`; `test_store_pg_behaviors.py:303-371`. Closed-row direct coverage exists only through gate/claim tests, so a direct loader assertion is missing. |
| `load_pending_stage_b` (`store.py:145-162`; `postgres.py:2865-2891`) | `limit`, optional freshness/threshold → jobs | **R**, non-claiming | Requires Stage A completed; Stage B null/error and under retry cap; threshold applies before limit; order recency + ID. It intentionally does not reclaim `in_progress`; claim/preview methods own stale behavior. Evidence: `test_store_contract.py:1295-1341`. |
| `list_evaluated_jobs` (`store.py:164-173`; `postgres.py:3005-3036`) | `limit` → joined evaluations | **R** | Inner join means only rows with an evaluation record; order `jobs.discovered_at DESC, jobs.id DESC`. Stage result NULL behavior follows section 2.2. Evidence: `test_store_contract.py:405-458,1435-1449`; identity join: `test_store_pg_behaviors.py:427-439`. |
| `get_evaluation` (`store.py:175-184`; `postgres.py:3038-3070`) | Decimal job ID → `JobEvaluation | None`; malformed raises | **R** | Missing job → `None`. Existing unevaluated job → `JobEvaluation(job, None, None)`, not `None`, because of left join. Stage B defensive NULL/JSON behavior follows section 2.2. Evidence: `test_store_contract.py:1400-1421` and `420-458`. |
| `top_evaluated_jobs` (`store.py:186-201`; `postgres.py:3096-3138`) | `min_score=0`, `limit=100` → completed Stage B evaluations | **R** | Filters Stage B status `completed` and Stage A score `>= min_score`; stable order is `stage_a_score DESC, discovered_at DESC, id DESC`. Evidence: `test_store_contract.py:1499-1555`; SQL-shape lock: `test_evaluation_persistence.py:332-338`. |
| `save_ml_gate_result` (`store.py:203-214`; `postgres.py:3266-3303`) | Decimal job ID + valid `MLGateResult` → `None`; malformed ID/domain validation error; missing row is no-op | **W**; last write wins; same replay changes `ml_gate_at`, so not time-idempotent | Optional list fields are JSON text or NULL; current empty lists collapse to NULL. Boolean/nullable features preserve Python types on hydration. Evidence: `test_store_contract.py:953-1023`; DB score check `test_store_pg_behaviors.py:508-520`; locked column test `test_evaluation_persistence.py:267-298`. Canonical JSON byte equality has no existing test. |

## 4. Evaluation-claim matrix (8 operations)

All claims keep paid LLM work outside their database transaction. SQLite replaces
`FOR UPDATE SKIP LOCKED` with candidate selection plus guarded mutation inside one
short **IMMEDIATE** transaction. The returned rows are exactly those successfully
guarded and mutated, not the preselected candidates.

| Method and evidence | Input → output/error | Transaction and idempotency | Eligibility, order, NULL/time, concurrency |
|---|---|---|---|
| `claim_pending_stage_a` (`store_claims.py:31-50`; `postgres.py:2707-2736`) | Stage A filters → claimed jobs; invalid corpus raises | **IMMEDIATE/TX**; non-idempotent claim. A fresh second claimant gets none | Under retry cap, open jobs, optional quality/freshness. `unrated` takes null/error plus stale `in_progress` only if score is null or error exists; `failed` takes error plus stale in-progress with error; `all` takes anything except fresh in-progress. Stale cutoff is strictly `updated_at < now-1h`. Order recency + ID. Evidence: `test_services.py:423-455`; independent-process exclusion/stale-boundary tests: `test_store_process_concurrency.py`. |
| `preview_claimable_stage_a` (`store_claims.py:52-71`; `postgres.py:2738-2767`) | Same filters → jobs that a claim could take now | **R**, must not mutate | Same corpus/stale/retry/closed/order semantics as broad claim. It is advisory: a later real claim may lose a race. Direct uncontended parity and non-mutation evidence covers all corpora at `test_gate_candidates_query.py:584-620`. |
| `load_gate_candidates` (`store_claims.py:73-100`; `postgres.py:2769-2820`) | Filters, gate flag, optional `(datetime,id)` cursor → `GateCandidate` page | **R**, must not create/update evaluation rows | Shared Stage A claim eligibility. When `exclude_gate_failed=True`, `fail` is excluded but NULL/pass survive. `unrated` additionally excludes completed rows and nonblank normalized twins of any completed cluster; `all` and `failed` do not. Cursor is strict tuple `<` in recency/ID order. Evidence: `test_gate_candidates_query.py:205-389,521-710`, including equal-time multi-page continuity. |
| `claim_stage_a_by_ids` (`store_claims.py:102-123`; `postgres.py:2822-2863`) | String ID list + filters → eligible claimed subset | **IMMEDIATE/TX**. Empty/all-malformed input is a no-query no-op. Duplicate/replayed fresh IDs cannot be reclaimed | Non-numeric IDs are silently dropped; numeric IDs still pass the same closed/corpus/retry/stale/quality/freshness guards. Results are recency + ID, not caller input order. Evidence: `test_gate_candidates_query.py:360-487`. Duplicate IDs and two-process overlap need tests. |
| `claim_pending_stage_b` (`store_claims.py:125-142`; `postgres.py:2893-2919`) | `limit`, optional freshness/Stage A threshold → claimed jobs | **IMMEDIATE/TX**; non-idempotent claim | Requires Stage A completed and under Stage B retry cap. Takes Stage B null/error, or stale `in_progress` only when verdict is null; strict one-hour cutoff. Order recency + ID. Evidence: `test_services.py:487-508,534-559`; independent-process exclusion/stale-boundary tests: `test_store_process_concurrency.py`. |
| `release_stage_a_claim` (`store_claims.py:144-150`; `postgres.py:2921-2939`) | Decimal job ID → `None`; malformed raises; missing/not-in-progress is no-op | **W**, idempotent after first release | Only `in_progress` changes. Restore `error` if error text exists, else `completed` if score exists, else NULL; update time. Evidence is embedded in `test_services.py:423-455`; each restoration branch lacks its own contract test. |
| `release_stage_b_claim` (`store_claims.py:152-158`; `postgres.py:2941-2959`) | Decimal job ID → `None`; malformed raises; missing/not-in-progress is no-op | **W**, idempotent after first release | Restore `error` if error text exists, else `completed` if verdict exists, else NULL; update time. Evidence is embedded in `test_services.py:487-508`; restoration branches lack direct tests. |
| `refresh_stage_b_claim` (`store_claims.py:160-166`; `postgres.py:2961-2974`) | Decimal job ID → `None`; malformed raises; missing/not-in-progress is no-op | **W**; repeat refresh only advances time | Only an active `in_progress` Stage B row is refreshed. Service refreshes immediately and then every 1,800 seconds, keeping the one-hour claim alive (`_evaluate_claims.py:17,236-270`). A background refresh error is suppressed after the LLM returns. Evidence: `test_services.py:1218-1281`. There is no return value telling the owner it lost the claim; preserve current behavior for this job-level lease. |

## 5. Evaluation batch and preview matrix (5 operations)

| Method and evidence | Input → output/error | Transaction and idempotency | Eligibility, order, NULL/time, concurrency |
|---|---|---|---|
| `get_stage_a_scores` (`store_ext.py:19-29`; `postgres.py:3072-3094`) | ID list → mapping only for evaluation rows found; empty list → `{}`; any malformed ID aborts before SQL | **R**, no mutation | Values may be NULL. Missing jobs and jobs without evaluation rows are absent rather than mapped to NULL. Evidence: `test_store_contract.py:1449-1464`; strict malformed-batch behavior: `test_core_store_input_contract.py`. Duplicate-ID output naturally collapses to one mapping key. |
| `mark_stage_b_skipped_batch` (`store_ext.py:31-38`; `postgres.py:2577-2595`) | ID list → `None`; empty is no-op; any malformed ID aborts before SQL | **W** single statement; replay is state-idempotent but advances `updated_at` | Marks every matching evaluation except rows already `completed`; absent IDs and jobs without evaluations are no-ops. Direct mixed/empty/malformed-input atomicity evidence: `test_stage_b_threshold_contract.py`. |
| `mark_stage_b_below_threshold` (`store_ext.py:40-55`; `postgres.py:2597-2638`) | Threshold plus optional freshness → changed-row count | **W** single statement; second replay returns 0 | Stage A must be completed and score below threshold. Stage B must be NULL/error, or stale `in_progress` with NULL verdict; error retry cap applies. Fresh in-progress/completed/over-cap/out-of-freshness rows stay unchanged. Time is application UTC; stale is strict one-hour cutoff. Evidence: `test_store_contract.py:1466-1479`; all listed guard branches at `test_stage_b_threshold_contract.py`. |
| `reopen_stage_b_at_or_above_threshold` (`store_ext.py:57-72`; `postgres.py:2640-2674`) | Threshold plus optional freshness → changed-row count | **W** single statement; second replay returns 0 | Only Stage A completed, score at/above threshold, and Stage B exactly `skipped_below_threshold`; clears Stage B status and error. Evidence: `test_store_contract.py:1481-1498` and service threshold tests `test_services.py:875-989`. |
| `preview_pending_stage_b_after_threshold_sync` (`store_ext.py:76-94`; `postgres.py:2976-3003`) | Required threshold plus optional freshness/limit → jobs | **R**, must not mutate | Models the post-sync eligible set: Stage A completed/at threshold, Stage B NULL/error/skipped or stale in-progress with NULL verdict, under retry cap; order `discovered_at DESC, id DESC`. Evidence: `test_services.py:950-989`; direct non-mutation and ordered parity with `reopen` + `mark below` + claim at `test_stage_b_threshold_contract.py`. |

## 6. Pipeline-run matrix (3 existing operations)

`PipelineRun` fields and defaults are defined in
`src/jobfeed/domain/models_run.py:19-39`. Run IDs created by application services
are UUID4 strings (`src/jobfeed/services/runs.py:11-24`), but the current store
accepts arbitrary unique text for historical/tests compatibility.

| Method and evidence | Input → output/error | Transaction and idempotency | NULL/time/concurrency |
|---|---|---|---|
| `record_pipeline_run` (`store.py:218-224`; `postgres.py:3309-3343`) | Complete `PipelineRun` → `None`; duplicate `run_id`/constraint error propagates | **W**, insert-only and not idempotent | Persists all counters, status, start/finish time. `dry_run_preview` is intentionally not persisted. Current tests use non-UUID IDs, so UUID enforcement belongs to the new lease boundary, not this legacy method. Evidence: `test_store_contract.py:1111-1129`; `test_step_timings_store.py:37-57`. |
| `update_pipeline_run_status` (`store.py:226-235`; `postgres.py:3405-3440`) | `PipelineRun` → `None`; missing run is silent no-op | **W**; last snapshot wins | Replaces status, finish time, all counters, cost, errors. Current method has no terminal-state or ownership guard and is unsafe as the final authority after a lease takeover; leased paths must use `finalize_run_with_lease`. Evidence: `test_step_timings_store.py:59-85`. Missing-row and terminal-regression tests are absent. |
| `get_pipeline_run` (`store.py:237-245`; `postgres.py:3345-3360`) | Exact text run ID → `PipelineRun | None` | **R** | Hydrates all persisted fields; `dry_run_preview=[]`. Missing is `None`. Evidence: `test_store_contract.py:1111-1134`; `test_step_timings_store.py:37-85`. |

## 7. New run-lease contract (3 operations)

These operations form a new capability port. Their final signatures may use
dataclasses, but they must preserve the inputs, outcomes, and atomic boundaries
below. They do not change the one-hour per-job evaluation claim above.

### 7.1 Shared invariants

- `kind` is exactly `scan` or `evaluate`; the schema contains exactly those two
  permanent rows.
- Rows are created once with `generation=0` and nullable owner/run/heartbeat/expiry.
  They are never deleted and generation is never reset, including after finalize.
- `owner_id` and `run_id` supplied to lease methods must be canonical,
  non-reused UUID strings. Invalid UUID, unsupported kind, naive time, or
  `generation <= 0` raises `ValueError` before mutation.
- Heartbeat interval is 30 seconds; TTL is 180 seconds. `now` is application-
  supplied aware UTC time for deterministic tests; production callers pass current
  UTC time. An expiry is claimable when `expires_at <= now`.
- Fencing identity is the complete tuple
  `(kind, owner_id, run_id, generation)`. Matching only run or owner is forbidden.
- `generation` is a monotonic SQLite integer. Every successful start increments
  it exactly once, whether taking a free row or an expired row.
- In-process `asyncio.Lock` remains a fast same-process conflict response, but the
  database lease is the correctness boundary for Web/CLI process overlap.
- Evaluation dry-run is not a leased run: it does not claim jobs, spend paid LLM
  work, or persist `PipelineRun`, so it must not call `start_run_with_lease`.
  Web may retain an in-process UX lock and SSE preview state for dry-run only.
- Store startup recovery examines only occupied rows whose `expires_at <= now`.
  A matching `running` pipeline run becomes `failed`; owner/run/heartbeat/expiry
  are then cleared and generation is retained. Missing or already-terminal old
  runs only cause the expired lease fields to clear. Unexpired rows are untouched.

### 7.2 `start_run_with_lease`

Conceptual signature:

```python
async def start_run_with_lease(
    run: PipelineRun, *, kind: str, owner_id: str, now: datetime
) -> int | None: ...
```

Contract:

- `run.run_id` is the lease `run_id`; input status must be `running` and
  `finished_at` must be NULL.
- **IMMEDIATE/TX** over the permanent lease row and new `pipeline_runs` insert.
- Free means `owner_id`, `run_id`, `heartbeat_at`, and `expires_at` are all NULL.
- If free or expired, increment generation, set UUID owner/run, set
  `heartbeat_at=now`, set `expires_at=now+180s`, insert the complete running
  `PipelineRun`, and return the new generation. Lease mutation and run insert
  either both commit or both roll back.
- If an unexpired owner exists, return `None` without mutation. Conflict is a
  normal outcome, not a retryable busy error.
- When taking an expired row whose old `run_id` exists and is still `running`, the
  same transaction marks that old run `failed`, sets `finished_at=now`, and uses a
  stable stale-lease recovery reason in logging. Missing/already-terminal old run
  is left unchanged.
- A duplicate `run_id`, failed insert, or injected failure rolls back the lease
  mutation and generation increment. There is no state where a committed lease
  refers to a run that was never inserted.

### 7.3 `renew_run_lease`

Conceptual signature:

```python
async def renew_run_lease(
    *, kind: str, owner_id: str, run_id: str, generation: int, now: datetime
) -> bool: ...
```

Contract:

- **IMMEDIATE/TX** guarded update on the full fencing identity.
- It succeeds only while `expires_at > now`; success sets
  `heartbeat_at=now`, `expires_at=now+180s`, and returns `True`.
- Missing, expired, finalized, or mismatched owner/run/generation returns `False`
  with no mutation. It never resurrects an expired lease.
- `False` means lease loss: the old owner stops scheduling new work and must not
  call legacy `update_pipeline_run_status` as a terminal fallback.

### 7.4 `finalize_run_with_lease`

Conceptual signature:

```python
async def finalize_run_with_lease(
    run: PipelineRun,
    *, kind: str, owner_id: str, generation: int, now: datetime
) -> bool: ...
```

Contract:

- `run.run_id` is the fencing `run_id`; final status must be `succeeded` or
  `failed`, and `finished_at` must be non-null and no later than the supplied
  `now`. Invalid terminal input raises `ValueError` before mutation.
- **IMMEDIATE/TX** atomically updates the matching run's complete terminal
  snapshot and clears lease owner/run/heartbeat/expiry fields.
- The update succeeds only when the full fencing identity matches and the lease
  is still unexpired. It returns `True`; generation remains unchanged.
- Missing/expired/finalized/mismatched fencing returns `False`; neither the run nor
  lease changes. In particular, an old owner cannot overwrite a new generation's
  run after takeover or after normal finalize followed by reacquire.
- Repeating a successful finalize returns `False`, making terminal release
  effect-idempotent without treating a stale token as authority.

### 7.5 Required lease tests

No current tests cover these methods because they do not exist. Task 2 must add:

1. schema seed and permanent-row/generation constraints;
2. free start, live conflict, expired takeover, and strictly increasing
   generation;
3. same owner/run with an old generation rejected after normal
   finalize→reacquire;
4. old owner rejected after expired takeover;
5. renew extends an active lease but cannot resurrect an expired one;
6. finalize atomically updates all run fields and clears owner fields while
   retaining generation;
7. injected failure between lease mutation and run insert rolls back both; a
   committed start always has both lease and `running` run;
8. two OS processes racing at least 100 rounds: exactly one winner per kind,
   with scan and evaluate independently acquirable;
9. injected failure between terminal run update and lease clear rolls back both;
10. startup recovery ignores unexpired leases, recovers only expired occupied
    leases, fails only matching running runs, clears ownership fields, and retains
    generation;
11. CLI and Web evaluation dry-run never call start/renew/finalize and create no
    lease or `pipeline_runs` row, while real scan/evaluate paths do.

## 8. Current call-path consequences

- Scan service currently records a run before concurrent source fetch, then saves
  jobs in short per-job calls (`src/jobfeed/services/scan.py:40-82,171-196`).
- Evaluate service records a run before the funnel, atomically claims jobs, performs
  concurrent LLM calls outside transactions, and then writes results
  (`src/jobfeed/services/evaluate.py:60-107,116-168,217-299`).
- Current Web `RunManager` only uses process-local locks and may insert/update a
  run during finalization (`run_manager.py:45-161,200-212`). Task 2 must replace
  the separate record step with atomic `start_run_with_lease` before any Web or
  CLI work starts and must use fenced finalize.
- Current startup recovery marks every listed `running` row failed without TTL or
  ownership (`run_manager.py:278-297`). That behavior must be replaced; retaining
  it would violate the lease contract.
- One shared orchestration helper must serve CLI and Web. It calls
  `start_run_with_lease`, starts the 30-second heartbeat before scheduling any
  fetch/LLM/external task, stops scheduling work on renewal failure, and uses only
  fenced finalize. Separate CLI/Web lease sequences are forbidden because they
  can drift at the crash boundaries.
- Evaluate dry-run bypasses that persistent orchestration helper and retains the
  existing read-only service behavior. Web may still serialize preview UX and
  broadcast in-memory SSE snapshots, but the call-path test must assert that no
  lease operation, legacy `record_pipeline_run`, or pipeline row occurs.
- Job-level Stage B heartbeat currently runs every 1,800 seconds against a
  one-hour claim TTL. The new run-level heartbeat is separate and runs every
  30 seconds.

## 9. Blocking test and decision ledger

The matrix freezes 33 existing methods plus 3 new methods. This ledger records
both resolved and open gaps; every **OPEN** row is blocking before the SQLite core
slice can claim behavioral parity:

| ID | Gap | Required disposition |
|---|---|---|
| C01 | `top_evaluated_jobs` equal-score tie-break | **RESOLVED:** stable score/recency/ID order implemented and behavior plus SQL shape tested. |
| C02 | `list_jobs` and `list_evaluated_jobs` tie-breaks | **RESOLVED:** fixed equal-timestamp contract tests added. |
| C03 | PostgreSQL lifecycle and operation-before-connect behavior lacked direct contracts | **RESOLVED for the golden backend:** direct tests cover repeated connect/close, close-before-open, failed open/close, and disconnected operations. SQLite per-connection PRAGMA setup remains a Task 2 adapter acceptance test. |
| C04 | `save_job` lacked a true concurrent same-natural-key and truthful outcome test | **RESOLVED:** two independently spawned OS processes prove exactly one insert, one truthful update, one shared ID, and one persisted row. |
| C05 | `save_stage_a` rollback between evaluation upsert and status/history transition | **RESOLVED:** injected status failure proves no evaluation, status, or history partial commit. |
| C06 | Stage B canonical JSON bytes and malformed stored JSON behavior were not frozen | **RESOLVED:** Unicode/raw blocks round-trip through JSONB to identical canonical semantic bytes; invalid JSONB writes fail atomically; corrupt TEXT hydration raises `JSONDecodeError`. |
| C07 | `save_stage_b_error` repeat-count and release Stage A/B restoration branches are incomplete | Add per-branch direct contract tests, including missing and non-in-progress no-op. |
| C08 | `preview_claimable_stage_a` equality/non-mutation coverage | **RESOLVED:** direct immediate-claim parity covers `unrated`, `failed`, and `all`, including fresh/stale claims. |
| C09 | Gate-candidate keyset cursor page continuity | **RESOLVED:** equal-timestamp multi-page test proves strict ID order with no gaps or duplicates. |
| C10 | Stage A/B claims were tested sequentially, not with independent processes | **RESOLVED for PostgreSQL:** two independently spawned worker processes prove disjoint Stage A and Stage B paid-work claims, strict fresh/stale ownership around one hour, and a reader observing committed state without waiting on an open writer transaction. |
| C11 | `update_pipeline_run_status` can regress terminal runs and silently no-ops for missing IDs | Legacy behavior may remain for non-leased compatibility, but all production terminal paths must prove they use fenced finalize. Add a call-path test that stale owners cannot reach legacy finalization. |
| C12 | All run-lease behaviors are new | Add the 11 cases in section 7.5 before implementation is accepted. |
| C13 | Invalid ID, negative limit, naive datetime, and backend exception behavior was unevenly specified/tested | **PARTIAL:** representative malformed single/batch ID tests now freeze strict `ValueError` behavior and the `claim_stage_a_by_ids` skip exception. Target-port negative-limit and new lease aware-datetime tests remain open for Task 2. |
| C14 | Evaluation batch/preview methods had partial or incidental coverage | **RESOLVED:** direct tests cover skip-batch atomic validation, threshold stale/fresh/retry/freshness guards, and ordered non-mutating Stage B preview parity with actual sync plus claim. |

Non-blocking production-call observation: `job_exists` and
`top_evaluated_jobs` have no direct `src/` call site today. They remain in this
contract because they are public port methods with tests. The approved target retires
them only after those tests move to the replacement capability; the SQLite
implementation must not silently omit their behavior during migration.

## 10. Public capability disposition

The behavior contract and the future public API are different questions. These
recommendations target the audited project-wide goal of exactly 78 public capability
operations without hiding atomic behavior:

- **RETAIN:** remains a first-class public capability operation.
- **MERGE:** behavior remains, but becomes a typed outcome/mode of the named shared
  operation; the old method may be a temporary wrapper.
- **WRAPPER:** no independent SQL implementation; compatibility delegates to the
  named shared behavior and is removed after call-site migration.
- **RETIRE:** remove only after static/runtime caller audit and replacement tests;
  it remains implemented during the compatibility window.

| Current/new operation | Disposition | Target shared behavior and reason |
|---|---|---|
| `connect` | RETAIN | Store lifecycle boundary, including expired run-lease recovery. |
| `close` | RETAIN | Store lifecycle boundary. |
| `save_job` | RETAIN | Distinct natural-key, quality-aware atomic upsert. |
| `get_job` | RETAIN | Canonical job detail lookup. |
| `list_jobs` | RETAIN | Public recency listing; implementation shares the jobs query builder. |
| `job_exists` | RETIRE | No production caller; natural-key upsert already answers insert/update. |
| `save_stage_a` | RETAIN | Typed Stage A success write has its own result and atomic job-status transition; it may share only private SQL/mapping helpers with the error path. |
| `save_stage_a_error` | RETAIN | Typed, deliberately non-idempotent failure-count operation; a generic success/error sum command would obscure retry semantics. |
| `save_stage_b` | RETAIN | Typed Stage B success write owns structured result persistence and first-completion time; it may share only private SQL/mapping helpers. |
| `save_stage_b_error` | RETAIN | Typed, deliberately non-idempotent Stage B failure-count operation. |
| `mark_stage_b_skipped` | RETAIN | Typed single-job skip remains public; implementation may share a private set-based helper with the batch operation. |
| `load_pending_stage_a` | RETAIN | Dry-run and funnel paths require non-claiming Stage A eligibility. |
| `load_pending_stage_b` | RETAIN | Its eligibility excludes `skipped` and stale `in_progress`; share only an internal predicate builder with preview, never delegate to preview semantics. |
| `list_evaluated_jobs` | RETAIN | Digest read shape differs from job list/detail. |
| `get_evaluation` | RETAIN | Canonical evaluation detail lookup. |
| `top_evaluated_jobs` | RETIRE | No production caller; a scored sort belongs in the shared jobs/evaluations query. |
| `save_ml_gate_result` | RETAIN | Distinct persisted gate decision and features. |
| `record_pipeline_run` | RETIRE | Production real-run start moves to atomic `start_run_with_lease`; dry-run persists no run. Keep only as a PG compatibility wrapper until call-site migration. |
| `update_pipeline_run_status` | RETIRE | Production terminal writes move to fenced finalize. Keep only as a PG compatibility wrapper until call-site migration. |
| `get_pipeline_run` | RETAIN | Run detail/SSE lookup. |
| `claim_pending_stage_a` | RETAIN | Live non-gate Stage A claim path with distinct corpus semantics. |
| `preview_claimable_stage_a` | RETIRE | No production caller; shared Stage A eligibility can provide preview mode. |
| `load_gate_candidates` | RETAIN | Distinct gate state, twin suppression, and keyset behavior. |
| `claim_stage_a_by_ids` | RETAIN | Atomic paid-work claim after funnel selection. |
| `claim_pending_stage_b` | RETAIN | Atomic Stage B paid-work claim. |
| `release_stage_a_claim` | MERGE | Typed `release_evaluation_claim(stage, job_id)` with Stage A restoration rules. |
| `release_stage_b_claim` | MERGE | Same shared release command with Stage B restoration rules. |
| `refresh_stage_b_claim` | RETAIN | Stage B job-level heartbeat has distinct active-claim semantics. |
| `get_stage_a_scores` | RETAIN | Efficient batch input for Stage B prompts. |
| `mark_stage_b_skipped_batch` | RETIRE | No production caller; keep its set-based SQL as a private helper for the retained single-ID command until tests migrate. |
| `mark_stage_b_below_threshold` | MERGE | One `sync_stage_b_threshold` transaction returns skipped/reopened counts. |
| `reopen_stage_b_at_or_above_threshold` | MERGE | Paired half of `sync_stage_b_threshold`; current service always calls the pair. |
| `preview_pending_stage_b_after_threshold_sync` | RETAIN | Read-only post-sync view shares eligibility builder but not mutation. |
| `start_run_with_lease` | RETAIN | Atomic lease acquisition plus running-run insert. |
| `renew_run_lease` | RETAIN | Full-token heartbeat/fencing command. |
| `finalize_run_with_lease` | RETAIN | Atomic terminal snapshot plus lease release. |

This slice therefore freezes 36 observable behaviors and maps them to **28 final
public operations**: 26 retained operations plus the two typed merged operations
`release_evaluation_claim` and `sync_stage_b_threshold`. Six old methods retire
after call-site/test migration; temporary PostgreSQL wrappers are not part of the
final SQLite port. This is the core contribution to the exact 78-operation
project target; it does not delete behavior or replace typed operations with a
generic executor.

`sync_stage_b_threshold` is one intentional behavior tightening, not a cosmetic
wrapper: the current PostgreSQL service commits the skip and reopen halves
separately, while the target commits both or neither. Before replacing those two
methods, a PostgreSQL characterization must show the current split behavior and a
target-port failure-injection test must prove rollback of both halves. No other
`MERGE` row may silently change its transaction boundary.

## 11. Acceptance criteria for this slice

This document is ready for Task 0 review when:

1. all 20 `JobStore` core methods, all 8 claim methods, all 5 evaluation
   batch/preview methods, and all 3 new run-lease methods appear exactly once in
   a matrix or detailed contract;
2. every method records inputs/output/errors, mutation/transaction boundary,
   idempotency, and applicable order/NULL/JSON/time/concurrency semantics;
3. every existing behavior cites its port, implementation, and at least one test
   where evidence exists;
4. every missing or ambiguous behavior is visible in section 9 rather than guessed;
5. no production code, tests, dependency lockfile, or parent plan is changed by
   this slice.

This is one part of parent Task 0. The full Task 0 gate also requires the other
capability matrices, PostgreSQL snapshot/benchmark evidence, rollback contract,
and independent review required by the approved plan.
