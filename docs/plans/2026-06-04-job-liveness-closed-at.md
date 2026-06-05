# Job Liveness: `closed_at` Marking — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mark a job posting `closed` the moment a per-job JD fetch returns a definitive "not-here" HTTP status (404 / 410 / 403), so closed reqs stop being scoring candidates without waiting out the passive `discovered_at` freshness window.

**Architecture:** Same hexagonal structure (domain → ports → services → adapters → cli). A new nullable `jobs.closed_at` column is the liveness signal; the existing `jobs.enrich_error` column (currently unwritten by `save_job`) records the reason. SpeedyApply's JD-fetch error handler reads `ATSFetchError.status_code`/`.vendor` and stamps the signal onto the `JobPosting`; `save_job` persists it via an `ON CONFLICT` rule that is **orthogonal to the quality-monotonic JD gate** (earliest-confirmed-gone wins; any successful re-enrich clears it). A conservative one-time backfill marks stale stock. **The candidate-selection consumer is deferred to Phase 5** (its plan already carries the `closed_at IS NULL` requirement) and is OUT OF SCOPE here.

**Tech Stack:** Python 3, asyncpg, Alembic (`migrations/`, head `0004`), Click CLI, pytest (`postgres` marker for PG-backed tests). Spec: `docs/specs/2026-06-04-job-liveness-closed-at-design.md`.

**Worktree/env:** branch `worktree-job-liveness-closed-at`; dev/test DB is the shared docker `jobfeed-postgres-1` (`postgresql://jobfeed:jobfeed_dev@localhost:5432/jobfeed_dev`); migrations run via `alembic -c migrations/alembic.ini`.

---

## Reference: trigger → effect mapping (authoritative)

| JD fetch outcome                         | `closed_at` | `enrich_error`               |
|------------------------------------------|-------------|------------------------------|
| `ATSFetchError.status_code` ∈ {404, 410} | `now`       | `gone:<code>:<vendor>`       |
| `ATSFetchError.status_code` == 403       | `now`       | `unreachable:403:<vendor>`   |
| `ATSFetchError.status_code` None or 5xx  | unchanged   | unchanged                    |
| Success (non-empty `jd_text`)            | **NULL**    | **NULL** (cleared)           |

`enrich_source` stays `speedyapply-error` on any `ATSFetchError` (unchanged from today). `<vendor>` comes from `ATSFetchError.vendor`.

---

### Task 1: Schema migration — add `jobs.closed_at`

**Files:**
- Create: `migrations/versions/0005_add_jobs_closed_at.py`
- Test: `tests/integration/test_migration_closed_at.py`

**What to build:**
An Alembic revision `0005` with `down_revision = "0004"`. `upgrade()` adds a nullable `closed_at TIMESTAMP WITH TIME ZONE` column to `jobs` (no default, no backfill in the migration). `downgrade()` drops it. Follow the style of existing revisions in `migrations/versions/`.

**Acceptance criteria:**
- [ ] `alembic -c migrations/alembic.ini upgrade head` adds `jobs.closed_at` (nullable, tz-aware); `alembic ... downgrade -1` removes it
- [ ] Revision chains off `0004` and is the new single head (`alembic ... heads` shows one head)
- [ ] `@pytest.mark.postgres` test upgrades, asserts the column exists + is nullable, downgrades, asserts it is gone
- [ ] All tests pass, committed

---

### Task 2: Domain model — `JobPosting.closed_at` + `JobPosting.enrich_error`

**Files:**
- Modify: `src/jobfeed/domain/models.py`
- Test: `tests/unit/test_models.py` (extend; create if absent)

**What to build:**
Add two optional fields to the `JobPosting` dataclass: `closed_at: datetime | None = None` and `enrich_error: str | None = None`. Pure stdlib; no new imports beyond `datetime` (already used). No behavior — data carriers only.

**Acceptance criteria:**
- [ ] `JobPosting(...)` defaults both fields to `None` when omitted
- [ ] Both fields accept and round-trip a `datetime` / `str` respectively
- [ ] `domain/` boundary test still passes (no non-stdlib import added)
- [ ] All tests pass, committed

---

### Task 3: Store — persist + hydrate `closed_at`/`enrich_error` in `save_job`

**Files:**
- Modify: `src/jobfeed/adapters/store/postgres.py` (`save_job` INSERT + `ON CONFLICT` SET; `_job_from_record`)
- Test: `tests/store/test_save_job_closed_at.py`

**What to build:**
Extend `save_job`'s `INSERT` column list + `VALUES` to include `closed_at` and `enrich_error` from the `JobPosting`. Add two `ON CONFLICT ... DO UPDATE SET` clauses that are **independent of the existing quality-monotonic `CASE`** for `jd_text`/`jd_quality`/`enrich_source` (do NOT alter those):

```
closed_at = CASE WHEN EXCLUDED.jd_text IS NOT NULL THEN NULL
                 ELSE COALESCE(jobs.closed_at, EXCLUDED.closed_at) END,
enrich_error = CASE WHEN EXCLUDED.jd_text IS NOT NULL THEN NULL
                    ELSE COALESCE(EXCLUDED.enrich_error, jobs.enrich_error) END
```

Hydrate both columns in `_job_from_record` (`closed_at=r["closed_at"]`, `enrich_error=r["enrich_error"]`).

**Acceptance criteria:**
- [ ] Inserting a new posting with `closed_at`/`enrich_error` set persists both; `get_job` round-trips them
- [ ] Re-upserting a stored open row (had `closed_at=NULL`) with a gone posting (`jd_text=None`, `closed_at=t1`) sets `closed_at=t1`
- [ ] A second gone re-confirm (`closed_at=t2`, `t2>t1`, still `jd_text=None`) keeps the **earliest** `closed_at=t1` (COALESCE-of-existing)
- [ ] Re-upserting with a posting carrying non-NULL `jd_text` sets `closed_at=NULL` and `enrich_error=NULL` (self-heal)
- [ ] The quality-monotonic `jd_text`/`jd_quality` behavior is unchanged (regression: a lower-quality re-scan still does NOT overwrite a stored full JD)
- [ ] `_job_from_record` hydrates both new columns
- [ ] `@pytest.mark.postgres`; all tests pass, committed

---

### Task 4: Producer — SpeedyApply status-code → closed signal

**Files:**
- Modify: `src/jobfeed/adapters/sources/speedyapply.py` (`_route` error handler + the `JobPosting` builder that consumes its result)
- Test: `tests/unit/test_speedyapply_closed.py`

**What to build:**
In `SpeedyApplySource`'s `except ATSFetchError` path, read `exc.status_code` and `exc.vendor` and produce a closed signal per the **Reference mapping table** above, then build the `JobPosting` with `closed_at` and `enrich_error` set accordingly. The `_route` result currently carries `(jd_text, enrich_source)`; extend it (e.g. add `closed_at`/`enrich_error`, or return a small result object — implementer's choice) so the posting builder can stamp them. On a successful fetch, `closed_at`/`enrich_error` are `None` (the success path is unchanged otherwise). Do NOT change `enrich_source` (stays `speedyapply-error` on failure). Keep functions within complexity ≤10 and the file ≤300 lines (split a helper if needed).

**Acceptance criteria:**
- [ ] A workday JD fetch raising `ATSFetchError(status_code=404, vendor="workday")` → posting has `closed_at` set (non-None) and `enrich_error="gone:404:workday"`
- [ ] `status_code=410` → `enrich_error="gone:410:<vendor>"`, `closed_at` set
- [ ] `status_code=403` → `enrich_error="unreachable:403:<vendor>"`, `closed_at` set
- [ ] `status_code=None` (network/timeout) AND `status_code=503` → `closed_at` stays `None`, `enrich_error` stays `None`
- [ ] A successful fetch (non-empty `jd_text`) → `closed_at=None`, `enrich_error=None`, `enrich_source="speedyapply-<vendor>"` (unchanged success behavior)
- [ ] Existing SpeedyApply tests still pass; `enrich_source` on failure is still `speedyapply-error`
- [ ] All tests pass, committed

---

### Task 5: Backfill — conservative stale-no-JD marking (maintenance command)

**Files:**
- Modify: `src/jobfeed/ports/store_ops.py` (add `mark_stale_jobs_closed` to `StoreOpsMixin`)
- Modify: `src/jobfeed/adapters/store/postgres.py` (implement it)
- Create: `src/jobfeed/cli/maintenance.py` (Click command `mark-stale-closed`)
- Modify: `src/jobfeed/cli/__init__.py` (register the command via `cli.add_command(...)`)
- Test: `tests/store/test_mark_stale_closed.py`

**What to build:**
A store op `mark_stale_jobs_closed(*, older_than_days: int, dry_run: bool) -> int` that targets rows where `(jd_quality IS NULL OR jd_quality IN ('missing','abandoned')) AND discovered_at < now() - (older_than_days || ' days')::interval AND closed_at IS NULL`. When `dry_run=True` it returns the matching count and writes nothing; when `dry_run=False` it sets `closed_at = now()`, `enrich_error = 'backfill:stale-no-jd'` on those rows and returns the count updated. A thin Click command `mark-stale-closed` (default `--older-than-days 30`, dry-run by default, `--apply` to write) calls it and prints the count. No network. Single-level loops only; keep the CLI thin (no business logic) per layering rules.

**Acceptance criteria:**
- [ ] Marks a stale (`discovered_at` 40d old) row with `jd_quality='missing'` and `closed_at IS NULL`: `closed_at` set + `enrich_error='backfill:stale-no-jd'`
- [ ] Leaves untouched: a fresh row (`discovered_at` 5d old), a stale row WITH usable JD (`jd_quality='full'`), and an already-`closed_at` row
- [ ] `dry_run=True` (default) writes nothing and returns the would-affect count; `--apply` writes and returns the updated count
- [ ] Running `--apply` twice is idempotent (second run affects 0 rows — they now have `closed_at` set)
- [ ] `mark-stale-closed` CLI command is registered and prints the count; `@pytest.mark.postgres`; committed

---

### Task 6: Verification

**Files:** none new (verification only).

**What to verify:**
1. `make quality` (lint + ruff format + mypy + unit/contract, browser-free) passes.
2. `pytest -m 'postgres and not browser' -o "addopts="` passes (Tasks 1, 3, 5 PG tests) against the shared dev DB / testcontainers.
3. Architecture boundaries (`test_architecture_boundaries.py`), per-file ≤300 lines (store layer exempt), complexity ≤10 — all green.
4. Confirm the consumer is absent here and present as a requirement in `docs/plans/2026-06-04-jobfeed-rewrite-phase5-eval-pipeline-ml-gate.md` (Task 5).

**Acceptance criteria:**
- [ ] `make quality` green
- [ ] `pytest -m 'postgres and not browser'` green
- [ ] No new lint/mypy/complexity/line-count/boundary violations
- [ ] Final state committed on `worktree-job-liveness-closed-at`

---

## Self-review notes

- **Spec coverage:** storage (T1 column + T2 model + T3 persist/hydrate), trigger/producer (T4), upsert orthogonality + reversibility + earliest-wins (T3), backfill (T5), tests (each task + T6). Consumer intentionally absent (Phase 5).
- **403 decision:** encoded in the T4 mapping table (`unreachable:403`), distinct from `gone:404/410`, matching the spec's confidence split.
- **Type consistency:** `closed_at: datetime | None`, `enrich_error: str | None` used identically in T2 (model), T3 (persist/hydrate), T4 (producer); store op `mark_stale_jobs_closed(*, older_than_days: int, dry_run: bool) -> int` named identically in T5 port + adapter + CLI.
- **No-network guarantee:** backfill (T5) is pure SQL; no re-probe.
