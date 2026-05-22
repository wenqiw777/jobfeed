# Phase 1: Store + Domain Contract Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the JobStore Protocol to full production parity, build PostgresStore (asyncpg + Alembic), prove both SQLite and Postgres pass the same shared contract test suite, and verify legacy SQLite v16 data can import into the new schema with byte-level parity assertions.

**Architecture:** Same hexagonal structure from Phase 0. Phase 1 introduces a second store adapter (PostgresStore) behind the same `JobStore` Protocol. The shared contract test suite ensures both adapters have identical behavior. Alembic manages PG schema evolution. Legacy import is a CLI command that reads the old SQLite v16 and writes through the JobStore Protocol.

**Tech Stack additions:** asyncpg, alembic, testcontainers-python (CI only), psycopg2-binary (Alembic DDL runner)

**Spec reference:** `docs/specs/2026-05-20-jobfeed-rewrite-design.md` (Sections 4, 6, 19)

**Plan path:** `docs/plans/2026-05-21-jobfeed-rewrite-phase1-store-hardening.md`

**Prerequisite:** Phase 0 complete. Walking skeleton runs: MockSource → SQLiteStore → MockLLM → CLI scan/evaluate/digest.

**New variables in this phase:** 3 (PostgreSQL via Docker, Alembic migration framework, asyncpg driver). Legacy import adds one more risk variable (v16 schema compatibility).

**Environment isolation:** Phase 0's repo-local `.jobfeed-dev/` remains the default for SQLite. PostgreSQL runs in Docker Compose (service `postgres`) with a dedicated `jobfeed_dev` database. Phase 1 must still never read or write `~/.jobfeed/` unless a human explicitly passes a real user config path. The legacy import test fixture uses a checked-in anonymized SQLite file under `tests/fixtures/`, not the user's real database.

**Implementation repo:** `/Users/wenqiwang/wwq/jobfeed`. Do not implement Phase 1 tasks in the legacy repo `/Users/wenqiwang/wwq/job-apply`.

**Precedence:** This phase plan is the source of truth when it conflicts with the architecture spec. The architecture spec describes steady-state/cutover behavior.

**Commit strategy:** Commit each task separately with a task-sized conventional commit.

**Execution mode:** Run Phase 1 tasks sequentially.

---

## Async/Sync Boundary

Unchanged from Phase 0. All ports, adapters, and services are **async**. CLI is sync Click bridging via `asyncio.run()`. Store lifecycle: `await store.connect()` / `await store.close()`.

---

## File Map

```
jobfeed/                              # repo root
├── src/
│   └── jobfeed/
│       ├── domain/
│       │   ├── models.py             # MODIFY: add StatusTransition, ApplicationRecord,
│       │   │                         #   ResumeSnapshot, ResumeVariant, CompanyRecord,
│       │   │                         #   WorkflowAttention, CostEntry
│       │   └── status.py             # CREATE: ALLOWED_TRANSITIONS, STATUS_VALUES,
│       │                             #   validate_transition() — pure domain, no IO
│       │
│       ├── ports/
│       │   └── store.py              # MODIFY: expand JobStore Protocol with full method set
│       │
│       ├── adapters/
│       │   └── store/
│       │       ├── sqlite.py         # MODIFY: implement all new Protocol methods
│       │       ├── schema.sql        # MODIFY: add job_status, job_status_history,
│       │       │                     #   resume_variants, resume_snapshots, applied audit
│       │       │                     #   cols, companies, cost_ledger, ML gate cols,
│       │       │                     #   triggers, indexes
│       │       ├── postgres.py       # CREATE: PostgresStore (asyncpg)
│       │       └── _normalize.py     # CREATE: shared company/title normalization
│       │
│       ├── cli/
│       │   └── migrate.py            # CREATE: migrate CLI group (inspect-sqlite,
│       │                             #   import-sqlite)
│       │
│       └── config.py                 # MODIFY: add db.url setting for postgres
│
├── migrations/                       # CREATE: Alembic directory
│   ├── alembic.ini                   # CREATE
│   ├── env.py                        # CREATE: async-compatible Alembic env
│   └── versions/
│       └── 0001_initial_schema.py    # CREATE: full PG schema
│
├── tests/
│   ├── conftest.py                   # MODIFY: add store_backend parametrize fixture
│   ├── contract/
│   │   └── test_store_contract.py    # CREATE: shared contract tests (parametrized)
│   ├── integration/
│   │   ├── test_sqlite_store.py      # MODIFY: import shared contract, add SQLite-specific
│   │   └── test_postgres_store.py    # CREATE: PG-specific tests (concurrent writes)
│   ├── fixtures/
│   │   ├── legacy_v16.db            # CREATE: anonymized legacy fixture
│   │   └── legacy_v16_manifest.json # CREATE: expected row counts + checksums
│   └── e2e/
│       └── test_legacy_import.py     # CREATE: import + parity assertion
│
├── docker-compose.yml                # MODIFY: add postgres service
└── pyproject.toml                    # MODIFY: add asyncpg, alembic, testcontainers deps
```

---

## Task 0: Domain Model Expansion

**Files:**
- Modify: `src/jobfeed/domain/models.py`
- Create: `src/jobfeed/domain/status.py`
- Test: `tests/unit/test_models.py` (expand)
- Test: `tests/unit/test_status.py`

**What to build:**
Add domain models needed by the expanded store layer. All pure Python, zero external imports. If `models.py` would exceed the 300-line hygiene limit, split into focused private modules and re-export.

New models in `models.py`:
- **StatusTransition** — `job_id: str`, `from_status: JobStatus | None`, `to_status: JobStatus`, `reason: str | None`, `changed_at: datetime`, `resume_variant: str | None`. Represents one row in `job_status_history`.
- **ApplicationRecord** — `job_id: str` (PRIMARY KEY semantics), `applied_at: datetime`, `master_resume_hash: str | None`, `tailored_resume_hash: str | None`, `cover_letter: str | None`, `application_method: str | None`, `block_a_snapshot: str | None` (JSON), `block_c_snapshot: str | None` (JSON), `block_e_snapshot: str | None` (JSON), `notes: str | None`.
- **ResumeSnapshot** — `resume_hash: str` (sha256, PRIMARY KEY), `captured_at: datetime`, `source: str` (literal: "master" | "tailored"), `content: str`, `notes: str | None`. Content-addressed, append-only.
- **ResumeVariant** — `name: str`, `description: str | None`, `created_at: datetime`.
- **CompanyRecord** — `slug: str`, `ats_vendor: str | None`, `ats_override: bool`, `last_verified_at: datetime | None`, `last_probe_attempt_at: datetime | None`, `job_count_last_scan: int`, `consecutive_discover_failures: int`, `notes: str | None`.
- **WorkflowAttention** — `follow_up_today: list[dict]`, `interview_prep: list[dict]`, `going_ghosted: list[dict]`. Return type for workflow attention queries.
- **CostEntry** — `day: str` (YYYY-MM-DD), `spent_usd: float`, `calls: int`, `last_updated: datetime`.
- **StatusInfo** — `job_id: str`, `status: JobStatus`, `next_followup_at: datetime | None`, `resume_variant: str | None`, `notes: str | None`, `last_status_change_at: datetime`. Represents the current state of `job_status` for a job.

New module `status.py` — pure domain logic for status transitions:
- `STATUS_VALUES: frozenset[str]` — all 14 statuses.
- `ALLOWED_TRANSITIONS: dict[str, frozenset[str]]` — the manual transition graph from the design spec. Terminal statuses (`ignored`, `archived`, `rejected`, `offer`, `ghosted`) map to empty frozensets.
- `validate_transition(from_status: str, to_status: str, *, force: bool = False) -> str | None` — returns `None` if the transition is valid (either in ALLOWED_TRANSITIONS or force=True), or an error message string if invalid. Pure validation, no IO, no side effects.
- `is_terminal(status: str) -> bool`.
- `DECAY_SOURCES: frozenset[str]` — statuses subject to auto-ghost: `{"applied", "interviewing", "oa", "hr_call", "second_round", "final_round"}`.

**Acceptance criteria:**
- [ ] All new dataclasses instantiable with valid data, declared `kw_only=True`
- [ ] StatusTransition captures from/to with optional reason
- [ ] ApplicationRecord has all audit snapshot fields
- [ ] ResumeSnapshot `__post_init__` validates resume_hash is non-empty hex string
- [ ] validate_transition returns None for `scored → shortlisted`
- [ ] validate_transition returns error for `scored → offer` without force
- [ ] validate_transition returns None for `scored → offer` with force=True
- [ ] is_terminal returns True for archived/ignored/rejected/offer/ghosted
- [ ] ALLOWED_TRANSITIONS matches design spec graph exactly
- [ ] No imports from outside stdlib
- [ ] All tests pass, committed

---

## Task 1: JobStore Protocol Hardening

**Files:**
- Modify: `src/jobfeed/ports/store.py`

**What to build:**
Expand the JobStore Protocol from Phase 0's minimal interface to the full production method set. All new methods are `async`. All type signatures reference domain models only (no sqlite3, no asyncpg, no dict returns).

Add these method signatures to the Protocol:

**Status management:**
- `async def transition_status(self, *, job_id: str, new_status: str, reason: str | None = None, resume_variant: str | None = None, force: bool = False, followup_grace_days: int = 7) -> str` — enforces ALLOWED_TRANSITIONS, writes history row, returns new_status. Sets `next_followup_at` on transition to `applied`.
- `async def get_status(self, job_id: str) -> StatusInfo | None`
- `async def restore_from_archived(self, job_id: str) -> str` — walks history to find pre-archive status, transitions there.
- `async def auto_decay(self, *, ghost_days: int = 30, archive_ignored_days: int = 14) -> dict[str, int]` — returns `{"ghosted": N, "archived": M}`.
- `async def list_statuses(self, *, statuses: frozenset[str] | None = None, days: int | None = None, limit: int | None = None) -> list[StatusInfo]`
- `async def append_note(self, *, job_id: str, text: str) -> None` — appends timestamped note, resets ghost clock.

**Application audit:**
- `async def record_application(self, record: ApplicationRecord) -> None` — transactional: write application record + status transition to `applied`.
- `async def list_applications(self, *, limit: int = 100) -> list[ApplicationRecord]`
- `async def application_stats(self, *, since_days_ago: int = 30, by_resume: bool = False) -> dict[str, object]`

**Resume snapshots:**
- `async def save_resume_snapshot(self, snapshot: ResumeSnapshot) -> None` — content-addressed insert, no-op if hash exists.
- `async def get_resume_snapshot(self, resume_hash: str) -> ResumeSnapshot | None`
- `async def register_resume_variant(self, *, name: str, description: str | None = None) -> bool` — returns True if new, False if already existed.

**Company management:**
- `async def upsert_company(self, company: CompanyRecord) -> None`
- `async def get_company(self, slug: str) -> CompanyRecord | None`
- `async def list_companies(self, *, vendor: str | None = None, include_removed: bool = False) -> list[CompanyRecord]`
- `async def mark_company_removed(self, slug: str) -> bool`

**ML Gate:**
- `async def save_ml_gate_result(self, job_id: str, result: MLGateResult, *, ml_gate_version: str | None = None) -> None`

**Workflow:**
- `async def workflow_attention(self, *, auto_ghost_days: int = 30, lookahead_days: int = 5) -> WorkflowAttention`
- `async def compute_reapply_notice(self, *, job_id: str, lookback_days: int = 60) -> str | None`

**State / cost:**
- `async def get_state(self, key: str) -> str | None`
- `async def set_state(self, key: str, value: str) -> None`
- `async def record_cost(self, *, day: str, spent_usd: float, calls: int) -> None`

**Existing methods to refine (if needed):**
- `save_job` must implement quality-ladder protection on upsert: incoming quality worse than stored → keep stored jd_text + quality. Also implement soft-key dedup: same `(platform, company_norm, title_norm)` with different canonical_id → return existing row ID instead of inserting.
- `job_exists(self, *, platform: str, canonical_id: str) -> bool` — if not already in Phase 0, add it.

**Acceptance criteria:**
- [ ] All new methods added to Protocol with correct async signatures
- [ ] All type signatures use domain models, no adapter-specific types
- [ ] Protocol still `@runtime_checkable`
- [ ] Existing Phase 0 methods unchanged or refined with backward-compatible additions
- [ ] No implementations in port file
- [ ] mypy passes on the Protocol definition
- [ ] Committed

---

## Task 2: SQLiteStore Schema Hardening

**Files:**
- Modify: `src/jobfeed/adapters/store/schema.sql`
- Create: `src/jobfeed/adapters/store/_normalize.py`

**What to build:**
Expand the SQLiteStore's schema to full production parity with the legacy store's v16 schema. This task is schema-only (DDL + normalization helpers); method implementation is Task 3.

`schema.sql` additions:

**Table: `applied`** (application audit trail):
```sql
CREATE TABLE IF NOT EXISTS applied (
    job_id              INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    applied_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    notes               TEXT,
    master_resume_hash  TEXT,
    tailored_resume_hash TEXT,
    cover_letter        TEXT,
    application_method  TEXT,
    block_a_snapshot    TEXT,
    block_c_snapshot    TEXT,
    block_e_snapshot    TEXT
);
```

**Table: `resume_snapshots`** (content-addressed resume storage):
```sql
CREATE TABLE IF NOT EXISTS resume_snapshots (
    resume_hash    TEXT PRIMARY KEY,
    captured_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    source         TEXT NOT NULL,
    content        TEXT NOT NULL,
    notes          TEXT
);
```

**Table: `resume_variants`** (named resume variants):
```sql
CREATE TABLE IF NOT EXISTS resume_variants (
    name        TEXT PRIMARY KEY,
    description TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
```

**Table: `job_status`** (current status per job):
```sql
CREATE TABLE IF NOT EXISTS job_status (
    job_id                INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    status                TEXT NOT NULL DEFAULT 'new'
        CHECK (status IN ('new','scored','shortlisted','archived','ignored',
                          'applied','interviewing','rejected','offer','ghosted',
                          'oa','hr_call','second_round','final_round')),
    next_followup_at      TEXT,
    resume_variant        TEXT REFERENCES resume_variants(name),
    notes                 TEXT,
    last_status_change_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
```

**Table: `job_status_history`** (append-only transition log):
```sql
CREATE TABLE IF NOT EXISTS job_status_history (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id                   INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    from_status              TEXT,
    to_status                TEXT NOT NULL,
    changed_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    reason                   TEXT,
    resume_variant_at_change TEXT
);
```

**Table: `companies`** (ATS source tracking):
```sql
CREATE TABLE IF NOT EXISTS companies (
    slug                        TEXT PRIMARY KEY,
    ats_vendor                  TEXT,
    ats_override                INTEGER NOT NULL DEFAULT 0,
    last_verified_at            TEXT,
    last_probe_attempt_at       TEXT,
    job_count_last_scan         INTEGER NOT NULL DEFAULT 0,
    notes                       TEXT,
    consecutive_discover_failures INTEGER NOT NULL DEFAULT 0
);
```

**Table: `cost_ledger`** (daily cost tracking):
```sql
CREATE TABLE IF NOT EXISTS cost_ledger (
    day             TEXT PRIMARY KEY,
    spent_usd       REAL NOT NULL DEFAULT 0.0,
    calls           INTEGER NOT NULL DEFAULT 0,
    last_updated    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
```

**Columns added to `jobs`:** `reapply_notice TEXT`, `hard_filter TEXT`, `seniority_level TEXT`, `degree_required TEXT`, `clearance_required INTEGER`, `school_restricted INTEGER`, `domain_tags TEXT`, `tech_required TEXT`, `role_type TEXT`, `yoe_min INTEGER`, `ml_gate_score REAL`, `ml_gate_result TEXT`, `ml_gate_at TEXT`, `ml_gate_version TEXT`, `is_swe_role INTEGER`.

**Trigger: `trg_jobs_seed_status`** — AFTER INSERT ON jobs, auto-seed `(job_id, 'new')` into both `job_status` and `job_status_history`.

**Indexes:** `idx_jobs_dedup_softkey`, `idx_jobs_scraped_at`, `idx_companies_vendor`, `idx_eval_stage_a_score`, `idx_eval_block_c_score` (json_extract), `idx_eval_stage_b_queue`, `idx_job_status_status`, `idx_job_status_followup`, `idx_job_status_stale`, `idx_job_status_history_job`.

`_normalize.py` — shared normalization helpers used by both SQLiteStore and PostgresStore:
- `_NORM_RE`, `_CORP_SUFFIXES` — same as legacy `store.py`.
- `normalize(s: str | None) -> str` — lowercase + collapse non-alphanum.
- `normalize_company(s: str | None) -> str` — base normalize + peel trailing corporate-form tokens.
- `QUALITY_RANK: dict[str | None, int]` — quality-ladder for upsert downgrade protection.
- `quality_rank(q: str | None) -> int`.

**Acceptance criteria:**
- [ ] `schema.sql` creates all tables, triggers, and indexes when executed fresh
- [ ] SQLiteStore `connect()` applies schema idempotently (CREATE IF NOT EXISTS + trigger)
- [ ] All 14 status values present in job_status CHECK constraint
- [ ] Auto-seed trigger fires on INSERT into jobs (integration test)
- [ ] `_normalize.py` matches legacy behavior: `normalize_company("Palantir Technologies, Inc.")` → `"palantir"`
- [ ] Quality ladder: `quality_rank("full") > quality_rank("partial") > quality_rank(None)`
- [ ] All tests pass, committed

---

## Task 3: SQLiteStore Method Implementation

**Files:**
- Modify: `src/jobfeed/adapters/store/sqlite.py`
- Test: `tests/integration/test_sqlite_store.py` (expand with targeted integration tests)

**What to build:**
Implement all new Protocol methods in SQLiteStore. This is the heaviest implementation task. Each method must be async (using aiosqlite), transactionally correct, and idempotent where applicable.

**Status management methods:**
- `transition_status` — calls `domain.status.validate_transition()` for the domain rule, then writes the DB update + history insert in one transaction. Same-status re-mark with different `resume_variant` only updates variant. Transition to `applied` sets `next_followup_at`. `archived → new` requires `force=True` (gated by the domain validator).
- `get_status` — returns StatusInfo from `job_status` join.
- `restore_from_archived` — query `job_status_history` for most recent non-archived `to_status`, call `transition_status` with `force=True`.
- `auto_decay` — sweep: `applied`/`interviewing`/interview-sub-stages silent ≥ `ghost_days` → ghosted; `ignored` silent ≥ `archive_ignored_days` → archived. Uses `transition_status(force=True)` per row with reason tags.
- `list_statuses` — filtered query on `job_status JOIN jobs`.
- `append_note` — append timestamped line to `job_status.notes`, reset `last_status_change_at`.

**save_job refinements:**
- Quality-ladder protection: if incoming quality rank < stored quality rank, drop jd_text + quality from the UPDATE (keep existing richer JD).
- Soft-key dedup: on INSERT, check for existing row with same `(platform, company_norm, title_norm)` but different `canonical_id`. If found, return existing ID as non-inserted.
- Compute `company_norm`, `title_norm`, `location_norm` via `_normalize.py`.

**Application audit methods:**
- `record_application` — transactional: INSERT into `applied` + `transition_status(..., new_status="applied")`. If `applied` row already exists for this `job_id`, raise (unique by job_id).
- `list_applications` — join `applied` with `jobs`.
- `application_stats` — aggregate from `job_status_history`: applied count, responses, interviews, offers, rejections, median days-to-response. Optional `by_resume` breakdown.

**Resume snapshot methods:**
- `save_resume_snapshot` — INSERT OR IGNORE by `resume_hash` (content-addressed, immutable once written).
- `get_resume_snapshot` — SELECT by hash.
- `register_resume_variant` — INSERT OR IGNORE, returns True if new.

**Company management methods:**
- `upsert_company` — COALESCE-style UPDATE or INSERT.
- `get_company`, `list_companies`, `mark_company_removed` — queries matching legacy behavior.

**ML Gate:**
- `save_ml_gate_result` — UPDATE `jobs` columns: `ml_gate_score`, `ml_gate_result`, `ml_gate_at`, `ml_gate_version`.

**Workflow:**
- `workflow_attention` — three queries: follow_up_today, interview_prep, going_ghosted. Returns WorkflowAttention.
- `compute_reapply_notice` — soft-key match for same company_norm with active application.

**State / cost:**
- `get_state`, `set_state` — key-value on `state` table.
- `record_cost` — upsert into `cost_ledger`.

**Acceptance criteria:**
- [ ] `transition_status("scored", "shortlisted")` succeeds
- [ ] `transition_status("scored", "offer")` raises ValueError without force
- [ ] `transition_status("scored", "offer", force=True)` succeeds with FORCE reason tag
- [ ] `restore_from_archived` walks history and restores to pre-archive status
- [ ] `auto_decay` ghosts applied jobs silent >30 days, archives ignored jobs >14 days
- [ ] `save_job` upsert keeps higher-quality JD when incoming quality is worse
- [ ] `save_job` soft-key dedup returns existing ID for same company_norm+title_norm
- [ ] `record_application` writes applied record + transitions status in one transaction
- [ ] `record_application` raises on duplicate job_id (second apply is no-op per parity)
- [ ] `save_resume_snapshot` is idempotent (same hash = no-op)
- [ ] `application_stats` returns correct counts and median_days_to_response
- [ ] `append_note` resets ghost clock
- [ ] All async, all use aiosqlite
- [ ] Targeted integration tests for each method group (status, application, company, ML gate)
- [ ] All tests pass, committed

---

## Task 4: Shared Store Contract Test Suite

**Files:**
- Create: `tests/contract/test_store_contract.py`
- Modify: `tests/conftest.py`

**What to build:**
Parameterized test suite that runs identical behavioral tests against both SQLiteStore and PostgresStore. Uses a `store_backend` pytest fixture that yields a connected, empty store instance. For Phase 1, the SQLite parameterization runs immediately; the PG parameterization is added in Task 7 after PostgresStore is built.

`conftest.py` additions:
- `@pytest.fixture(params=["sqlite"])` (initially; Task 7 adds `"postgres"`)
- Each parameterization yields a connected `JobStore` instance with a fresh empty schema
- SQLite: tmp_path-based file
- Postgres: testcontainers ephemeral PG instance (added in Task 7)

Contract test groups (each group tests one Protocol concern):

**Group 1: Job CRUD + Upsert**
- Insert a job, get it back, verify all fields
- Insert same `(platform, canonical_id)` twice → second call returns same ID, `inserted=False`
- Soft-key dedup: same `(platform, company_norm, title_norm)` with different canonical_id → returns existing ID
- Quality-ladder: upsert with lower quality → keeps existing richer JD
- Quality-ladder: upsert with higher quality → updates JD
- `list_jobs` returns inserted jobs
- `job_exists` returns True/False correctly

**Group 2: Evaluation Pipeline**
- Insert job → save_stage_a → verify `load_pending_stage_a` no longer returns it
- Save stage_a with score >= threshold → appears in `load_pending_stage_b`
- Save stage_a with score < threshold → does NOT appear in `load_pending_stage_b`
- `save_stage_a_error` → job stays in pending_stage_a queue
- `save_stage_b` → `list_evaluated_jobs` includes it with full StageAResult + StageBResult
- `save_stage_b_error` → stage_b_status is "error"

**Group 3: Status Lifecycle**
- Fresh job → auto-seeded to `new`
- `new → scored → shortlisted → applied` sequence
- Invalid transition without force → ValueError
- Invalid transition with force → succeeds, reason tag "FORCE: ..."
- Same-status re-mark with different resume_variant → variant updated, history row added
- Same-status re-mark with same variant → no-op, no extra history row
- Transition to `applied` → `next_followup_at` set
- `restore_from_archived` → walks history, restores to pre-archive status
- `auto_decay`: applied job silent >N days → ghosted
- `auto_decay`: ignored job silent >N days → archived
- `append_note` → note appended, ghost clock reset
- Forward-only interview stages: applied → oa → hr_call OK; hr_call → oa raises

**Group 4: Application Audit Trail**
- `record_application` → creates applied record + transitions to applied
- `record_application` on already-applied job → raises
- `list_applications` returns records with all snapshot fields
- `application_stats` returns correct aggregation
- `application_stats(by_resume=True)` returns per-variant breakdown

**Group 5: Resume Snapshots**
- `save_resume_snapshot` → `get_resume_snapshot` returns it
- Same hash twice → no error, content unchanged
- `register_resume_variant` → True on first call, False on second

**Group 6: Company Management**
- `upsert_company` insert → `get_company` returns it
- `upsert_company` update → COALESCE semantics (None fields preserved)
- `list_companies` filters by vendor, excludes removed by default
- `mark_company_removed` → excluded from default list

**Group 7: ML Gate**
- `save_ml_gate_result` → job has ml_gate_score, ml_gate_result, ml_gate_at set

**Group 8: State / Cost / Pipeline**
- `set_state` → `get_state` returns it
- `record_cost` → upserts cost_ledger
- `record_pipeline_run` → `get_pipeline_run` returns it

**Group 9: Workflow Queries**
- `workflow_attention` returns follow_up_today, interview_prep, going_ghosted lists
- `compute_reapply_notice` detects same-company active application

**Acceptance criteria:**
- [ ] All contract tests pass against SQLiteStore
- [ ] Tests are parameterized by backend, not hardcoded to SQLite
- [ ] Each test group is independent — failures in one group don't cascade
- [ ] No SQLite-specific SQL in contract tests (tests use Protocol methods only)
- [ ] Test fixtures provide deterministic timestamps for reproducibility
- [ ] All tests pass, committed

---

## Task 5: Docker Compose Postgres + Alembic Setup

**Files:**
- Modify: `docker-compose.yml`
- Modify: `pyproject.toml`
- Modify: `src/jobfeed/config.py`
- Create: `migrations/alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/versions/0001_initial_schema.py`

**What to build:**

**Docker Compose:** Add a `postgres` service:
```yaml
postgres:
  image: postgres:16
  environment:
    POSTGRES_USER: jobfeed
    POSTGRES_PASSWORD: jobfeed_dev
    POSTGRES_DB: jobfeed_dev
  ports:
    - "5432:5432"
  volumes:
    - pgdata:/var/lib/postgresql/data
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U jobfeed"]
    interval: 5s
    timeout: 3s
    retries: 5
```
Add `pgdata` named volume. The `jobfeed-cli` service depends_on postgres (but only when `db.backend = "postgres"` in config — not a hard startup dependency).

**pyproject.toml:** Add dependencies: `asyncpg >= 0.30`, `alembic >= 1.15`. Dev dependency: `testcontainers[postgres] >= 4.0`.

**config.py:** Add `db.url` field (default: `postgresql://jobfeed:jobfeed_dev@localhost:5432/jobfeed_dev`) and `db.backend` enum (`"sqlite"` | `"postgres"`, default `"sqlite"`). The DI factory (`create_app()`) selects SQLiteStore or PostgresStore based on `db.backend`.

**Alembic setup:** `alembic.ini` with `sqlalchemy.url` pointing to PG (overridable via env). `env.py` uses synchronous psycopg2 connection for DDL (Alembic's runner is synchronous; the application uses asyncpg). Target metadata is not SQLAlchemy ORM (we don't use ORM) — migrations are hand-written SQL via `op.execute()`.

**Initial migration `0001_initial_schema.py`:** Creates the full PG schema equivalent of `schema.sql`. Key PG differences:
- `SERIAL` instead of `INTEGER PRIMARY KEY AUTOINCREMENT`
- `JSONB` for block_a_verdict, block_b_jd_summary, block_c_fit_analysis, block_e_resume_hooks (in evaluations table) — stores structured data queryable via `->` operator
- `TIMESTAMPTZ` instead of `TEXT` for all `_at` columns — PG-native timestamp handling
- `CHECK` constraints: same status enum, score ranges
- `ON CONFLICT (platform, canonical_id) DO UPDATE` for upsert (PG has native UPSERT)
- `CREATE INDEX` with same logical intent as SQLite indexes
- No `json_extract` index (PG uses `jsonb_path_ops` GIN index instead)
- `CREATE OR REPLACE FUNCTION` + `CREATE TRIGGER` for auto-seed (PG trigger syntax)
- `CREATE INDEX CONCURRENTLY` is not used in initial migration (empty table, no concurrency concern)

**Acceptance criteria:**
- [ ] `docker compose up postgres -d` starts PG and healthcheck passes
- [ ] `docker compose run --rm jobfeed-cli alembic upgrade head` applies migration
- [ ] `alembic downgrade base` reverses migration cleanly
- [ ] All tables, triggers, indexes exist in PG after migration
- [ ] `config.py` correctly parses `db.backend = "postgres"` and `db.url`
- [ ] Alembic migration is hand-written SQL (not autogenerated from ORM)
- [ ] `TIMESTAMPTZ` columns in PG vs `TEXT` in SQLite — adapter layer handles conversion
- [ ] All tests pass, committed

---

## Task 6: PostgresStore Adapter

**Files:**
- Create: `src/jobfeed/adapters/store/postgres.py`
- Test: `tests/integration/test_postgres_store.py`

**What to build:**
Full PostgresStore implementing the entire JobStore Protocol via asyncpg. Every method from the Protocol — including all Phase 0 methods and all Phase 1 additions — must be implemented.

**Connection management:**
- `connect()` creates an asyncpg connection pool (default pool_size=5, configurable).
- `close()` closes the pool.
- Each method acquires a connection from the pool, executes within a transaction where needed, and releases it.

**PG-specific SQL patterns:**
- `INSERT ... ON CONFLICT (platform, canonical_id) DO UPDATE` for `save_job` upsert (replaces SQLite's SELECT-then-UPDATE pattern).
- `RETURNING id` on INSERT to get the generated ID.
- `$1, $2, ...` parameter placeholders (not `?`).
- `JSONB` column access via `->` and `->>` operators.
- `TIMESTAMPTZ` columns — asyncpg handles Python `datetime` ↔ PG `timestamptz` natively. No manual ISO string conversion needed (unlike SQLite's TEXT columns).
- `row_to_dict()` helper: asyncpg returns `asyncpg.Record` objects; convert to domain models in adapter.

**Quality-ladder and soft-key dedup:** Same business logic as SQLiteStore, using `_normalize.py` helpers. PG SQL syntax differs but semantics are identical.

**Status trigger:** PG uses a `CREATE FUNCTION` + `CREATE TRIGGER` pair (created by Alembic migration). The SQLite trigger auto-seeds on INSERT; the PG trigger does the same. The adapter does not re-create triggers — that's Alembic's job.

**Transaction boundaries:**
- `record_application`: single transaction wrapping INSERT into `applied` + `transition_status`.
- `auto_decay`: each row's transition is its own small transaction (matching SQLite behavior — partial completion is OK).
- `transition_status`: single transaction: UPDATE `job_status` + INSERT `job_status_history`.

**PG-specific integration tests** (`test_postgres_store.py`):
- Concurrent `save_job` upserts from multiple asyncio tasks → no lost updates, no duplicate rows.
- Concurrent `transition_status` on same job → serialization error handled (retry or raise).
- Connection pool exhaustion behavior.
- These tests require a running PG instance (testcontainers or Docker Compose).

**Acceptance criteria:**
- [ ] PostgresStore passes `isinstance(store, JobStore)` check
- [ ] Every Protocol method implemented and functional
- [ ] `save_job` uses `ON CONFLICT DO UPDATE` (single statement, not SELECT-then-UPDATE)
- [ ] `RETURNING id` used for INSERT (no lastrowid workaround)
- [ ] JSONB columns store/retrieve structured data correctly
- [ ] TIMESTAMPTZ columns handle timezone-aware datetimes
- [ ] Quality-ladder and soft-key dedup behave identically to SQLiteStore
- [ ] PG-specific concurrent write tests pass
- [ ] Connection pool lifecycle (connect/close) works correctly
- [ ] All integration tests pass against Docker Compose PG, committed

---

## Task 7: Contract Tests Against PostgresStore

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/contract/test_store_contract.py` (if needed)

**What to build:**
Add `"postgres"` parameterization to the store contract test fixture. The same contract tests from Task 4 now run against both SQLiteStore and PostgresStore.

**conftest.py fixture expansion:**
- `@pytest.fixture(params=["sqlite", "postgres"])` — parameterized backend.
- Postgres parameterization uses testcontainers to spin up an ephemeral PG instance, runs Alembic migration, yields a connected PostgresStore, and tears down after the test.
- Marker: `@pytest.mark.postgres` on PG-parameterized runs so CI can run them separately (PG tests are slower).
- Skip PG tests if Docker is not available (graceful degradation for local dev without Docker).

**Expected outcomes:**
- All 9 contract test groups pass on both backends.
- Any behavioral difference between SQLite and PG is a bug — fix the adapter, not the test.
- SQLite-specific behaviors (e.g. `json_extract` vs PG's `->`) are abstracted inside the adapter; contract tests never see dialect differences.

**Known SQLite/PG divergences to handle in adapters (not in tests):**
- `INTEGER PRIMARY KEY AUTOINCREMENT` vs `SERIAL` — adapter normalizes IDs to `str`.
- `TEXT` timestamps vs `TIMESTAMPTZ` — adapter converts to/from `datetime` at the boundary.
- `json_extract(col, '$.key')` vs `col->>'key'` — adapter uses dialect-appropriate SQL.
- `strftime('%Y-%m-%dT%H:%M:%SZ', 'now')` vs `NOW()` — adapter handles.
- SQLite `INSERT OR IGNORE` vs PG `ON CONFLICT DO NOTHING` — adapter handles.

**Acceptance criteria:**
- [ ] All 9 contract test groups pass against SQLiteStore
- [ ] All 9 contract test groups pass against PostgresStore
- [ ] No test has SQLite- or PG-specific assertions
- [ ] PG tests use testcontainers (no dependency on running Docker Compose PG)
- [ ] PG tests skipped gracefully when Docker unavailable
- [ ] All tests pass, committed

---

## Task 8: Legacy SQLite v16 Migration Fixture

**Files:**
- Create: `tests/fixtures/legacy_v16.db`
- Create: `tests/fixtures/legacy_v16_manifest.json`
- Create: `tests/fixtures/generate_legacy_fixture.py`
- Create: `src/jobfeed/adapters/store/legacy_import.py`

**What to build:**

**Fixture generation (`generate_legacy_fixture.py`):**
A one-time script (not shipped in production) that reads the real legacy database at `~/.jobfeed/jobs.db`, anonymizes PII/sensitive content, and writes a representative test fixture. Anonymization rules:
- Job titles: keep structure, replace company-specific words with generic equivalents.
- Company names: replace with fake company names (keep normalization-relevant suffixes like "Inc.", "Technologies").
- URLs: replace domains with `example.com`.
- JD text: replace with lorem-ipsum-style placeholder of the same approximate length, preserving quality band distribution.
- Resume content in snapshots: replace with generic placeholder.
- Cover letters: replace with placeholder.
- LLM response JSON (block_a/b/c/e): keep structure, replace specific text.
- Notes: clear.
- Preserve: schema version (16), row counts per table, status distribution, evaluation score distribution, posted_at/scraped_at date ranges, company_norm/title_norm patterns, platform distribution.

**This script is run manually once, NOT in CI.** The output `legacy_v16.db` is checked into the repo as a test fixture.

**Manifest (`legacy_v16_manifest.json`):**
```json
{
  "schema_version": 16,
  "tables": {
    "jobs": {"row_count": <N>, "sample_checksums": [...]},
    "evaluations": {"row_count": <N>},
    "job_status": {"row_count": <N>, "status_distribution": {...}},
    "job_status_history": {"row_count": <N>},
    "applied": {"row_count": <N>},
    "resume_snapshots": {"row_count": <N>, "hash_list": [...]},
    "companies": {"row_count": <N>},
    "cost_ledger": {"row_count": <N>},
    "state": {"row_count": <N>}
  }
}
```

**Import logic (`legacy_import.py`):**
- `async def import_legacy_sqlite(legacy_path: Path, target_store: JobStore) -> ImportReport` — reads legacy SQLite v16, maps rows to domain models, writes through the JobStore Protocol.
- Import order respects FK dependencies: `resume_variants` → `jobs` (which triggers auto-seed of `job_status`) → `evaluations` → `applied` → `resume_snapshots` → `companies` → `cost_ledger` → `state`.
- After importing `jobs`, overwrite the auto-seeded `job_status` rows with the legacy `job_status` data (the trigger seeds `new`, but the legacy row may be `scored`/`applied`/etc.). Import `job_status_history` after `job_status`.
- ID preservation: legacy `jobs.id` values are preserved in the new store. For SQLiteStore this is natural (same ID space). For PostgresStore, INSERT with explicit ID and reset the sequence after import.
- No recomputation: scores, statuses, company_norm, prompt_hash are preserved as-is from legacy data.
- `ImportReport` dataclass: `tables_imported: dict[str, int]` (table → row count), `warnings: list[str]`, `errors: list[str]`, `duration_s: float`.

**Acceptance criteria:**
- [ ] `legacy_v16.db` is a valid SQLite v16 database (schema_version=16 in state table)
- [ ] Fixture contains representative data across all core tables
- [ ] No real PII in fixture (all anonymized)
- [ ] `legacy_v16_manifest.json` matches fixture's actual row counts
- [ ] `import_legacy_sqlite` reads fixture without errors
- [ ] Import preserves legacy job IDs
- [ ] Import writes through JobStore Protocol (not raw SQL)
- [ ] ImportReport reports correct per-table counts
- [ ] All tests pass, committed

---

## Task 9: Parity Assertion Harness

**Files:**
- Create: `src/jobfeed/adapters/store/parity.py`
- Create: `tests/e2e/test_legacy_import.py`

**What to build:**

**Parity assertion module (`parity.py`):**
`async def verify_import_parity(legacy_path: Path, target_store: JobStore, manifest: dict) -> ParityReport`

Checks:
1. **Row count match** — per core table (jobs, evaluations, job_status, job_status_history, applied, resume_snapshots, companies, cost_ledger, state). Count from legacy SQLite vs count from target store.
2. **FK integrity** — every `applied.job_id` resolves to a job. Every `evaluations.job_id` resolves to a job. Every `job_status.job_id` resolves to a job. Every `job_status_history.job_id` resolves to a job.
3. **Resume snapshot hash verification** — for each `resume_snapshots` row, sha256 of the imported `content` matches the stored `resume_hash`.
4. **Status enum validation** — every `job_status.status` value is in STATUS_VALUES.
5. **Stage B JSON parseability** — every non-NULL `block_c_fit_analysis` in evaluations is valid JSON with expected keys (`score_0_100`, `strong_match`, `gaps`).
6. **Normalization presence** — every job has non-empty `company_norm` and `title_norm`.
7. **ID preservation** — sample N jobs by legacy ID and verify they exist with the same ID in the new store.
8. **Evaluation score ranges** — all `stage_a_score` values in 0–100.

`ParityReport` dataclass: `passed: bool`, `checks: list[ParityCheck]` where `ParityCheck` has `name: str`, `passed: bool`, `details: str`.

**E2E test (`test_legacy_import.py`):**
- Load `tests/fixtures/legacy_v16.db` and `legacy_v16_manifest.json`.
- Import into a fresh SQLiteStore (tmp_path) via `import_legacy_sqlite`.
- Run `verify_import_parity`.
- Assert all checks pass.
- Repeat import into PostgresStore (testcontainers) if Docker available.
- Assert all checks pass on PG too.

**Acceptance criteria:**
- [ ] Parity harness checks all 8 verification categories
- [ ] Import into SQLiteStore + parity assertion passes
- [ ] Import into PostgresStore + parity assertion passes (if Docker available)
- [ ] ParityReport clearly identifies which check failed and why
- [ ] Resume snapshot content hashes verified
- [ ] Stage B JSON is parseable after import
- [ ] Legacy job IDs preserved in both backends
- [ ] All tests pass, committed

---

## Task 10: Migrate CLI Commands

**Files:**
- Create: `src/jobfeed/cli/migrate.py`
- Modify: `src/jobfeed/cli/__init__.py`
- Test: `tests/e2e/test_cli_migrate.py`

**What to build:**
CLI commands for the legacy migration workflow. These are thin Click wrappers around `legacy_import.py` and `parity.py`.

**Commands:**
- `jobfeed migrate inspect-sqlite <path>` — reads a legacy SQLite DB, prints schema version, row counts per table, and a basic health check (FK integrity, NULL counts). Does NOT write anything. Output format: human-readable table.
- `jobfeed migrate import-sqlite --from <path> [--dry-run] [--backup] [--verify]` — runs the import pipeline. `--dry-run` simulates without writing (prints what would be imported). `--backup` copies `<path>` to `<path>.bak-pre-migration-YYYYMMDD-HHMMSS` before import. `--verify` runs the parity assertion harness after import and prints the ParityReport. Default behavior (no flags): import + verify.
- Both commands use the currently configured store backend (from `--config` or defaults). If backend is `postgres`, import writes to PG. If `sqlite`, import writes to the new SQLite.

**Safety:**
- `--from` path must exist and be a valid SQLite v16 database (check `state.schema_version`).
- Import never modifies the source database.
- `--backup` creates backup before any import work begins.
- Import failure must not leave target store in a partially-migrated state (transaction wrapping where possible; for PG, the entire import can be one transaction; for SQLite, batch per-table transactions are acceptable).

**E2E test (`test_cli_migrate.py`):**
- `jobfeed migrate inspect-sqlite tests/fixtures/legacy_v16.db` → exits 0, prints row counts.
- `jobfeed migrate import-sqlite --from tests/fixtures/legacy_v16.db --verify` → exits 0, parity passes.
- `jobfeed migrate import-sqlite --from nonexistent.db` → exits 1 with clear error.
- `jobfeed migrate import-sqlite --from tests/fixtures/legacy_v16.db --dry-run` → exits 0, prints plan, no data written.

**Acceptance criteria:**
- [ ] `inspect-sqlite` prints schema version + row counts
- [ ] `import-sqlite` imports all tables from fixture
- [ ] `--dry-run` prints plan without writing
- [ ] `--backup` creates backup file before import
- [ ] `--verify` runs parity harness and prints report
- [ ] Import of non-v16 database rejected with clear error
- [ ] Import of nonexistent file rejected with clear error
- [ ] Source database never modified by import
- [ ] All tests pass, committed

---

## Task 11: CI Update for PostgreSQL Tests

**Files:**
- Modify: `.github/workflows/ci.yml`

**What to build:**
Update CI to run the full test suite including PostgreSQL integration tests.

**CI changes:**
- Add a `postgres-tests` job that runs after `quality-gate`.
- Uses testcontainers (Docker-in-Docker) or GitHub Actions `services:` to start PG.
- Runs: `pytest tests/contract/ tests/integration/test_postgres_store.py tests/e2e/test_legacy_import.py -v --tb=short`.
- Separate from the existing unit/integration test job (PG tests are slower).
- The existing integration test job still runs SQLite-only tests.

**pyproject.toml pytest markers:**
- `@pytest.mark.postgres` — tests requiring a PG instance.
- Default `pytest` invocation (no markers) runs all non-PG tests.
- CI PG job runs with `-m postgres` or runs the specific test directories.

**Acceptance criteria:**
- [ ] CI runs PG contract tests in a separate job
- [ ] CI job starts PG via testcontainers or services
- [ ] All contract tests pass on both SQLite and PG in CI
- [ ] Legacy import + parity assertion passes in CI
- [ ] SQLite-only tests still pass without Docker
- [ ] CI failure output clearly shows which backend failed
- [ ] Committed

---

## Task 12: Verification + Phase 1 Acceptance

**Files:** None new. This is a verification-only task.

**What to verify:**

1. **Contract test parity:** `pytest tests/contract/` passes with both `--store-backend=sqlite` and `--store-backend=postgres`. Same test, same assertions, both green.

2. **Status transition parity:**
   - `new → scored → shortlisted → applied → oa → hr_call → second_round → final_round → offer` succeeds on both backends.
   - `scored → offer` without force → ValueError on both.
   - `auto_decay` behavior identical on both.

3. **Evaluation upsert parity:**
   - Save Stage A → save Stage B → `list_evaluated_jobs` returns complete evaluation on both backends.
   - Stage A error → job stays in pending queue on both.

4. **Duplicate upsert parity:**
   - Same `(platform, canonical_id)` → upsert (not duplicate) on both backends.
   - Quality-ladder protection identical on both.

5. **Legacy data import:**
   - `legacy_v16.db` imports into SQLiteStore → all 8 parity checks pass.
   - `legacy_v16.db` imports into PostgresStore → all 8 parity checks pass.
   - Job IDs preserved on both backends.

6. **Architecture boundaries:**
   - `domain/status.py` imports stdlib only.
   - `ports/store.py` imports domain models only.
   - `adapters/store/postgres.py` imports asyncpg, not aiosqlite.
   - `adapters/store/sqlite.py` imports aiosqlite, not asyncpg.
   - Both adapters import `_normalize.py` (shared).

7. **`make quality` passes** (ruff + mypy + all tests).

8. **Docker smoke:** `docker compose up postgres -d && docker compose run --rm jobfeed-cli alembic upgrade head` succeeds.

**Acceptance criteria (Phase 1 milestone):**
- [ ] Same JobStore contract tests pass on both SQLiteStore and PostgresStore
- [ ] Status transition persists correctly on both backends
- [ ] Evaluation upsert works correctly on both backends
- [ ] Duplicate upsert (canonical_id + soft-key) works correctly on both backends
- [ ] Legacy SQLite v16 data importable into both backends
- [ ] Parity assertion harness passes for both backends
- [ ] Architecture boundary tests pass
- [ ] `make quality` passes
- [ ] All committed
