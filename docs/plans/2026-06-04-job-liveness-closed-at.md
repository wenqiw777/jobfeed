# Job Liveness: `closed_at` + Workday JD Recovery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (1) Fix the Workday JD fetch so open postings' JD is recovered (two-step cookie + CSRF flow), and (2) mark a posting `closed` when Workday's own `postingAvailable=false` flag says so (or any vendor returns HTTP 404/410), so closed reqs leave the funnel immediately instead of waiting out the passive freshness window.

**Architecture:** Hexagonal (domain → ports → services → adapters → cli). A new nullable `jobs.closed_at` column is the liveness signal; existing `jobs.enrich_error` records the reason. The Workday adapter's fetch is rewritten to GET the HTML (seed session cookies + read `token`/`postingAvailable`), then GET the CXS API with the CSRF header — returning JD, a closed signal, or a transient miss. SpeedyApply propagates the closed signal onto the `JobPosting`; `save_job` persists it via an `ON CONFLICT` rule **orthogonal to the quality-monotonic JD gate** (earliest-closed wins; recovered JD clears it). Conservative one-time backfill for stale stock. **Candidate-selection consumer is deferred to Phase 5** (out of scope here).

**Tech Stack:** Python 3, httpx (+ `respx` for mocked HTTP tests — already a dependency), asyncpg, Alembic (`migrations/`, head `0004`), Click, pytest (`postgres`/`live` markers). Spec: `docs/specs/2026-06-04-job-liveness-closed-at-design.md`.

**Worktree/env:** branch `worktree-job-liveness-closed-at`; venv `.venv`; **TEST DB = `jobfeed_test`** (`PGTEST_DSN="postgresql://jobfeed:jobfeed_dev@localhost:5432/jobfeed_test"`) — NEVER `jobfeed_dev` (conftest drops the schema per test). Migrations: `alembic -c migrations/alembic.ini`.

---

## Reference: Workday fetch outcomes → posting effect (authoritative)

| Workday fetch outcome                          | `jd_text` | `closed_at` | `enrich_error`                       | `enrich_source`        |
|------------------------------------------------|-----------|-------------|--------------------------------------|------------------------|
| HTML config `postingAvailable: false`          | ""        | `now`       | `closed:posting-unavailable:workday` | `speedyapply-error`    |
| HTTP 404 / 410 (HTML or CXS)                   | ""        | `now`       | `gone:<code>:workday`                | `speedyapply-error`    |
| `postingAvailable: true` + CXS 200 with JD     | `<jd>`    | NULL        | NULL                                 | `speedyapply-workday`  |
| Transient (timeout / 5xx / other non-2xx)      | ""        | unchanged   | unchanged                            | `speedyapply-error`    |

The CSRF call sends headers `X-CALYPSO-CSRF-TOKEN: <token>`, `Accept: application/json`, `Referer: <apply_url>`, on the SAME client that fetched the HTML (cookies carried). A bare 403 is NOT a standalone closed trigger.

---

### Task 1: Schema migration — add `jobs.closed_at`

**Files:**
- Create: `migrations/versions/0005_add_jobs_closed_at.py`
- Test: `tests/integration/test_migration_closed_at.py`

**What to build:** Alembic revision `0005`, `down_revision = "0004"`. `upgrade()` adds nullable `closed_at TIMESTAMP WITH TIME ZONE` to `jobs` (no default, no backfill). `downgrade()` drops it. Match the style of `migrations/versions/0004_*.py` and `0002_*.py`.

**Acceptance criteria:**
- [ ] `alembic -c migrations/alembic.ini upgrade head` adds `jobs.closed_at` (nullable, tz-aware); `downgrade -1` removes it
- [ ] Revision chains off `0004`, single new head (`alembic ... heads` shows one)
- [ ] `@pytest.mark.postgres` test asserts the column present + nullable on a migrated DB (read an existing `tests/integration/` test for the DSN/connection pattern first)
- [ ] All tests pass, committed

---

### Task 2: Domain model — `JobPosting.closed_at` + `JobPosting.enrich_error`

**Files:**
- Modify: `src/jobfeed/domain/models.py`
- Test: `tests/unit/test_models.py` (extend; create if absent)

**What to build:** Add `closed_at: datetime | None = None` and `enrich_error: str | None = None` to the `JobPosting` dataclass. Data carriers only; pure stdlib.

**Acceptance criteria:**
- [ ] `JobPosting(...)` defaults both to `None` when omitted; both accept `datetime`/`str` respectively
- [ ] `domain/` boundary test still passes (no non-stdlib import added)
- [ ] All tests pass, committed

---

### Task 3: Store — persist + hydrate `closed_at`/`enrich_error` in `save_job`

**Files:**
- Modify: `src/jobfeed/adapters/store/postgres.py` (`save_job` INSERT + `ON CONFLICT` SET; `_job_from_record`)
- Test: `tests/store/test_save_job_closed_at.py`

**What to build:** Extend `save_job`'s INSERT columns/VALUES with `closed_at` and `enrich_error`. Add two `ON CONFLICT ... DO UPDATE SET` clauses **independent of the existing quality-monotonic CASE** (do NOT change `jd_text`/`jd_quality`/`enrich_source`):

```
closed_at = CASE WHEN EXCLUDED.jd_text IS NOT NULL THEN NULL
                 ELSE COALESCE(jobs.closed_at, EXCLUDED.closed_at) END,
enrich_error = CASE WHEN EXCLUDED.jd_text IS NOT NULL THEN NULL
                    ELSE COALESCE(EXCLUDED.enrich_error, jobs.enrich_error) END
```

Hydrate both in `_job_from_record`.

**Acceptance criteria:**
- [ ] New insert with `closed_at`/`enrich_error` persists both; `get_job` round-trips them
- [ ] Re-upsert of an open row (NULL) with a closed posting (`jd_text=None`, `closed_at=t1`) sets `closed_at=t1`
- [ ] Second closed re-confirm (`closed_at=t2>t1`, `jd_text=None`) keeps EARLIEST `closed_at=t1`
- [ ] Re-upsert with non-NULL `jd_text` sets `closed_at=NULL` and `enrich_error=NULL` (self-heal)
- [ ] Quality-monotonic `jd_text`/`jd_quality` behavior unchanged (regression: lower-quality re-scan does NOT overwrite stored full JD)
- [ ] `_job_from_record` hydrates both new columns
- [ ] `@pytest.mark.postgres`; all tests pass, committed

---

### Task 4: Workday fetch — two-step cookie+CSRF flow (recover JD) + closed detection

**Files:**
- Modify: `src/jobfeed/adapters/sources/_ats_workday.py`
- Test: `tests/unit/test_ats_workday.py` (extend/create; mock HTTP with `respx`)

**What to build:** Rewrite `_ats_workday.fetch_jd` from the bare CXS GET to the two-step flow, returning a result that distinguishes **JD recovered / confirmed-closed / transient** (use a small dataclass like `WorkdayFetch(jd_text: str, is_closed: bool, reason: str | None)`, OR raise a typed `WorkdayClosedError` for the closed case — implementer's choice; keep it importable & ≤300 lines, complexity ≤10):

1. GET the apply HTML (existing `client`, `User-Agent`, follow redirects). HTTP 404/410 → closed, `reason="gone:<code>:workday"`.
2. Parse the inline config: `postingAvailable: (true|false)` and `token: "<uuid>"` (regex on the HTML; the fields appear as `postingAvailable: true,` and `token: "<36-char-uuid>",`).
3. `postingAvailable=false` → closed, `reason="closed:posting-unavailable:workday"` (no CXS call).
4. `postingAvailable=true` → GET the CXS URL (existing `_build_cxs_url`) on the SAME client with headers `X-CALYPSO-CSRF-TOKEN: <token>`, `Accept: application/json`, `Referer: <apply_url>`. CXS 200 → parse `jobPostingInfo.jobDescription` (HTML → plain text via the existing `html_to_text`) → JD. CXS 404/410 → closed `reason="gone:<code>:workday"`. Other non-2xx → transient (no JD, not closed).

Use a **per-fetch cookie context** (do not pollute the shared client's cookie jar across tenants). Keep `_build_cxs_url` unchanged.

**Acceptance criteria:**
- [ ] Happy path (mocked HTML with `postingAvailable: true` + `token`, mocked CXS 200 with `jobPostingInfo.jobDescription`) returns JD text; the CXS request carried `X-CALYPSO-CSRF-TOKEN` + the HTML cookies
- [ ] `postingAvailable: false` HTML → result is closed (`reason` = `closed:posting-unavailable:workday`), NO CXS request made, no JD
- [ ] HTML 404 → closed `gone:404:workday`; CXS 410 → closed `gone:410:workday`
- [ ] Transient (CXS 500, or timeout) → not closed, no JD (reason None)
- [ ] Both Workday host shapes (`*.myworkdayjobs.com` and `*.myworkdaysite.com`) still resolve via `_build_cxs_url`
- [ ] `_ats_workday.py` ≤300 lines, complexity ≤10; all tests pass, committed

---

### Task 5: Producer wiring — propagate Workday closed signal to the `JobPosting`

**Files:**
- Modify: `src/jobfeed/adapters/sources/_speedyapply_routing.py` (carry the closed result out of `route_and_fetch`)
- Modify: `src/jobfeed/adapters/sources/speedyapply.py` (`_route` + the `JobPosting` builder stamp `closed_at`/`enrich_error`)
- Test: `tests/unit/test_speedyapply_closed.py`

**What to build:** Thread Task 4's three-way Workday outcome through routing into the built `JobPosting` per the **Reference table** above. `route_and_fetch` currently returns `(jd_text, enrich_source)`; extend it (add `closed_at`/`enrich_error`, or return a small result object) so `SpeedyApplySource`'s posting builder can stamp them. On a recovered JD → `enrich_source="speedyapply-workday"`, `closed_at=None`. On closed → `closed_at=now`, `enrich_error` per table, `enrich_source="speedyapply-error"`. On transient → unchanged (`speedyapply-error`, `closed_at=None`). Non-Workday vendors: a per-job HTTP 404/410 (e.g. greenhouse single-job fetch) also yields `closed_at=now`, `enrich_error="gone:<code>:<vendor>"`; a 403/other stays transient. Keep files ≤300 lines, complexity ≤10.

**Acceptance criteria:**
- [ ] Workday `postingAvailable=false` outcome → posting `closed_at` set, `enrich_error="closed:posting-unavailable:workday"`, `enrich_source="speedyapply-error"`, `jd_text` None
- [ ] Workday JD recovered → posting `jd_text` set, `closed_at=None`, `enrich_error=None`, `enrich_source="speedyapply-workday"`
- [ ] HTTP 404 (any vendor) → `closed_at` set, `enrich_error="gone:404:<vendor>"`
- [ ] Transient (timeout/5xx) → `closed_at=None`, `enrich_error=None`, `enrich_source="speedyapply-error"`
- [ ] Existing SpeedyApply tests still pass (greenhouse/ashby/lever/unrouted paths unchanged)
- [ ] All tests pass, committed

---

### Task 6: Backfill — conservative stale-no-JD marking (maintenance command)

**Files:**
- Modify: `src/jobfeed/ports/store_ops.py` (add `mark_stale_jobs_closed` to `StoreOpsMixin`)
- Modify: `src/jobfeed/adapters/store/postgres.py` (implement)
- Create: `src/jobfeed/cli/maintenance.py` (Click command `mark-stale-closed`)
- Modify: `src/jobfeed/cli/__init__.py` (register via `cli.add_command(...)`)
- Test: `tests/store/test_mark_stale_closed.py`

**What to build:** Store op `mark_stale_jobs_closed(*, older_than_days: int, dry_run: bool) -> int` targeting `(jd_quality IS NULL OR jd_quality IN ('missing','abandoned')) AND discovered_at < now() - (older_than_days || ' days')::interval AND closed_at IS NULL`. `dry_run=True` → return matching count, write nothing; `dry_run=False` → set `closed_at=now()`, `enrich_error='backfill:stale-no-jd'`, return count updated. Thin Click command `mark-stale-closed` (`--older-than-days` default 30, dry-run default, `--apply` to write) prints the count. No network. Single-level loops; CLI thin (no business logic).

**Acceptance criteria:**
- [ ] Marks a 40-day-old `jd_quality='missing'` `closed_at IS NULL` row: `closed_at` set + `enrich_error='backfill:stale-no-jd'`
- [ ] Leaves untouched: a fresh (5-day) row, a stale row WITH `jd_quality='full'`, an already-`closed_at` row
- [ ] `dry_run=True` (default) writes nothing, returns would-affect count; `--apply` writes, returns updated count
- [ ] `--apply` twice is idempotent (second run affects 0)
- [ ] `mark-stale-closed` registered + prints count; `@pytest.mark.postgres`; committed

---

### Task 7: Verification

**Files:** none (verification only).

**What to verify:**
1. `make quality` (browser-free) green.
2. `PGTEST_DSN=...jobfeed_test pytest -m 'postgres and not browser' -o "addopts="` green (Tasks 1, 3, 6).
3. Architecture boundaries, ≤300 lines (store exempt), complexity ≤10 green.
4. Optional manual `@pytest.mark.live` Workday smoke (1–2 real URLs: one open → JD, one `postingAvailable=false` → closed).
5. Consumer absent here; present as a requirement in the Phase 5 plan (Task 5).

**Acceptance criteria:**
- [ ] `make quality` green
- [ ] `pytest -m 'postgres and not browser'` green
- [ ] No new lint/mypy/complexity/line-count/boundary violations
- [ ] Final state committed on `worktree-job-liveness-closed-at`

---

## Self-review notes
- **Spec coverage:** storage (T1+T2+T3), Workday fetch fix/JD recovery (T4), closed-signal producer wiring (T5), backfill (T6), tests per task + T7. Consumer intentionally absent (Phase 5).
- **`postingAvailable` is the authoritative closed signal** (T4/T5), replacing HTTP-403 guessing; 403 alone never marks closed.
- **Type consistency:** `closed_at: datetime | None`, `enrich_error: str | None` identical across T2/T3/T5; `WorkdayFetch(jd_text, is_closed, reason)` (or typed exception) is T4's contract consumed by T5; `mark_stale_jobs_closed(*, older_than_days: int, dry_run: bool) -> int` identical in T6 port/adapter/CLI.
- **No-network backfill** (T6) is pure SQL; **no-browser / no-evasion** throughout (plain httpx two-step).
