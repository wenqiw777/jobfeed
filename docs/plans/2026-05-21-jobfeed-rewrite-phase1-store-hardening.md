# Phase 1: Store + Domain Contract Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the JobStore Protocol to full production parity, build PostgresStore (asyncpg + Alembic), prove both SQLite and Postgres pass the same shared contract test suite, and verify legacy SQLite v16 data can import into the new schema with semantic parity assertions (all values preserved; column names canonicalized per the import mapping table).

**Architecture:** Same hexagonal structure from Phase 0. Phase 1 introduces a second store adapter (PostgresStore) behind the same `JobStore` Protocol. The shared contract test suite ensures both adapters have identical behavior. Alembic manages PG schema evolution. Legacy import is a CLI command that reads the old SQLite v16 and writes through the `BulkImportPort` Protocol (bypasses business rules for raw data migration); post-import verification uses `ParityReadPort` for raw table inspection.

**Tech Stack additions:** asyncpg, alembic, testcontainers-python (CI only), psycopg2-binary (Alembic DDL runner)

**Spec reference:** `docs/specs/2026-05-20-jobfeed-rewrite-design.md` (Sections 4, 6, 19)

**Plan path:** `docs/plans/2026-05-21-jobfeed-rewrite-phase1-store-hardening.md`

**Prerequisite:** Phase 0 complete. Walking skeleton runs: MockSource → SQLiteStore → MockLLM → CLI scan/evaluate/digest. Before executing Phase 1, update `AGENTS.md` and `CLAUDE.md` to: (1) replace "Phase 0 plan is source of truth" with "Phase 1 plan is source of truth"; (2) allow PostgreSQL, Alembic, asyncpg, testcontainers, psycopg2-binary as permitted dependencies; (3) keep `~/.jobfeed/` isolation constraint (explicit path only).

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
│       │       ├── _sqlite_status.py  # CREATE: status/decay/workflow methods (mixin)
│       │       ├── _sqlite_application.py # CREATE: application/resume methods (mixin)
│       │       ├── _sqlite_import.py  # CREATE: BulkImportPort implementation
│       │       ├── postgres.py        # CREATE: PostgresStore facade (delegates to mixins)
│       │       ├── _pg_status.py      # CREATE: PG status/decay/workflow methods
│       │       ├── _pg_application.py # CREATE: PG application/resume methods
│       │       ├── _pg_import.py      # CREATE: PG BulkImportPort implementation
│       │       └── _normalize.py      # CREATE: shared company/title normalization
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
├── docker-compose.yml                # UNCHANGED (base, SQLite default)
├── docker-compose.postgres.yml       # CREATE: postgres service override
└── pyproject.toml                    # MODIFY: add asyncpg, alembic, testcontainers deps
```

---

## Prerequisite Task: Phase 0 Schema Rename (block columns → semantic names)

**Files (find all via `grep -r "block_[abce]_json" src/ tests/`):**
- Modify: `src/jobfeed/adapters/store/schema.sql` — column definitions
- Modify: `src/jobfeed/adapters/store/sqlite_sql.py` — SQL query strings
- Modify: `src/jobfeed/adapters/store/sqlite_stage_b_mapping.py` — Stage B result mapping
- Modify: `src/jobfeed/adapters/store/sqlite.py` — if any direct references remain
- Modify: `tests/integration/test_sqlite_store.py` — assertions referencing old names
- Modify: any other files returned by the grep

**What to build:**
Rename Phase 0's opaque block columns to semantic names. This is a Phase 0 fixup executed **before** Phase 1 begins (no migration needed — Phase 0 schema is not yet shipped, no existing production data).

**Renames in `schema.sql` evaluations table:**

| Current (Phase 0) | New (canonical) |
|---|---|
| `block_a_json` | `stage_b_verdict_json` |
| `block_b_json` | `stage_b_summary_json` |
| `block_c_json` | `stage_b_fit_json` |
| `block_e_json` | `stage_b_hooks_json` |

**Steps:**
- [ ] `schema.sql`: rename 4 columns in evaluations CREATE TABLE
- [ ] `sqlite.py`: grep and update all SQL strings referencing old names
- [ ] Tests: update any assertions or fixtures referencing old names
- [ ] `make quality` passes
- [ ] Committed as a standalone commit before Phase 1 work begins

**Why this is a prerequisite, not Phase 1:** Phase 1 schema additions (`ALTER TABLE ADD COLUMN`) assume the canonical names already exist. If this rename were part of Phase 1, it would create a migration step for non-existent production data — unnecessary complexity.

---

## Task 0: Domain Model Expansion

**Files:**
- Modify: `src/jobfeed/domain/models.py`
- Create: `src/jobfeed/domain/status.py`
- Test: `tests/unit/test_models.py` (expand)
- Test: `tests/unit/test_status.py`

**What to build:**
Add domain models needed by the expanded store layer. Domain modules may import stdlib and `jobfeed.domain.*` (internal cross-references); they must NOT import adapters, infrastructure, or third-party runtime libraries. If `models.py` would exceed the 300-line hygiene limit, split into focused private modules and re-export.

New models in `models.py`:
- **StatusTransition** — `job_id: str`, `from_status: JobStatus | None`, `to_status: JobStatus`, `reason: str | None`, `changed_at: datetime`, `resume_variant: str | None`. Represents one row in `job_status_history`.
- **ApplicationRecord** — `job_id: str` (PRIMARY KEY semantics), `applied_at: datetime`, `master_resume_hash: str | None`, `tailored_resume_hash: str | None`, `cover_letter: str | None`, `application_method: str | None`, `verdict_snapshot: str | None` (JSON), `fit_snapshot: str | None` (JSON), `hooks_snapshot: str | None` (JSON), `notes: str | None`.
- **ResumeSnapshot** — `resume_hash: str` (sha256, PRIMARY KEY), `captured_at: datetime`, `source: str` (literal: "master" | "tailored"), `content: str`, `notes: str | None`. Content-addressed, append-only.
- **ResumeVariant** — `name: str`, `description: str | None`, `created_at: datetime`.
- **CompanyRecord** — `slug: str`, `ats_vendor: str | None`, `ats_override: bool`, `last_verified_at: datetime | None`, `last_probe_attempt_at: datetime | None`, `job_count_last_scan: int`, `consecutive_discover_failures: int`, `notes: str | None`.
- **WorkflowAttentionItem** — `job_id: str`, `title: str`, `company: str`, `status: str`, `reason: str`, `days_since: int`. Single item in a workflow attention list.
- **WorkflowAttention** — `follow_up_today: list[WorkflowAttentionItem]`, `interview_prep: list[WorkflowAttentionItem]`, `going_ghosted: list[WorkflowAttentionItem]`. Return type for workflow attention queries.
- **AutoDecayResult** — `ghosted: int`, `archived: int`. Return type for `auto_decay`.
- **ResumeVariantStats** — `sent: int`, `responses: int`, `interviews: int`, `offers: int`, `rejections: int`. Per-variant breakdown.
- **ApplicationStats** — `applied_count: int`, `response_count: int`, `interview_count: int`, `offer_count: int`, `rejection_count: int`, `median_days_to_response: float | None`, `by_resume: dict[str, ResumeVariantStats] | None`. Return type for `application_stats`.
- **CostEntry** — `day: str` (YYYY-MM-DD), `spent_usd: float`, `calls: int`, `last_updated: datetime`.
- **StatusInfo** — `job_id: str`, `status: JobStatus`, `next_followup_at: datetime | None`, `resume_variant: str | None`, `notes: str | None`, `last_status_change_at: datetime`. Represents the current state of `job_status` for a job.

New module `status.py` — pure domain logic for status transitions:
- `STATUS_VALUES: frozenset[str]` — all 14 statuses.
- `ALLOWED_TRANSITIONS: dict[str, frozenset[str]]` — the manual transition graph from the design spec. Terminal statuses (`ignored`, `archived`, `rejected`, `offer`, `ghosted`) map to empty frozensets.
- `validate_transition(from_status: str, to_status: str, *, force: bool = False, i_mean_it: bool = False) -> str | None` — returns `None` if the transition is valid (either in ALLOWED_TRANSITIONS, or force=True for most forced transitions, or force=True AND i_mean_it=True for the destructive `archived → new`), or an error message string if invalid. Pure validation, no IO, no side effects.
- `is_terminal(status: str) -> bool`.
- `DECAY_SOURCES: frozenset[str]` — statuses subject to auto-ghost: `{"applied", "interviewing", "oa", "hr_call", "second_round", "final_round"}`.
- `RESPONSE_STATUSES: frozenset[str]` — statuses that count as "employer responded" for `application_stats`: `{"interviewing", "oa", "hr_call", "second_round", "final_round", "offer", "rejected"}`. **Intentional behavior improvement over legacy** (which only counted `{"interviewing", "offer", "rejected"}`): interview sub-stages now count as responses, giving more accurate response-rate stats.

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
- [ ] No imports from adapters, infrastructure, or third-party runtime libs (stdlib + `jobfeed.domain.*` only)
- [ ] All tests pass, committed

---

## Task 1: JobStore Protocol Hardening

**Files:**
- Modify: `src/jobfeed/ports/store.py`

**What to build:**
Expand the JobStore Protocol from Phase 0's minimal interface to the full production method set. All new methods are `async`. All type signatures reference domain models only (no sqlite3, no asyncpg, no dict returns).

Add these method signatures to the Protocol:

**Status management:**
- `async def transition_status(self, *, job_id: str, new_status: str, reason: str | None = None, resume_variant: str | None = None, force: bool = False, i_mean_it: bool = False, followup_grace_days: int = 7) -> str` — enforces ALLOWED_TRANSITIONS, writes history row, returns new_status. Sets `next_followup_at` on transition to `applied`. `archived → new` is deliberately destructive and requires BOTH `force=True` AND `i_mean_it=True` (double-gate, matching legacy behavior). Other forced transitions only need `force=True`.
- `async def get_status(self, job_id: str) -> StatusInfo | None`
- `async def restore_from_archived(self, job_id: str) -> str` — walks history to find pre-archive status, transitions there.
- `async def auto_decay(self, *, ghost_days: int = 30, archive_ignored_days: int = 14) -> AutoDecayResult` — returns typed result with `ghosted` and `archived` counts.
- `async def list_statuses(self, *, statuses: frozenset[str] | None = None, days: int | None = None, limit: int | None = None) -> list[StatusInfo]`
- `async def append_note(self, *, job_id: str, text: str) -> None` — appends timestamped note, resets ghost clock.

**Application audit:**
- `async def record_application(self, record: ApplicationRecord) -> bool` — transactional: write application record + status transition to `applied`. Returns True if new application recorded, False if job already has an applied record (no-op, preserves original audit row).
- `async def list_applications(self, *, limit: int = 100) -> list[ApplicationRecord]`
- `async def application_stats(self, *, since_days_ago: int = 30, by_resume: bool = False) -> ApplicationStats`

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
- `async def save_ml_gate_result(self, job_id: str, result: MLGateResult) -> None` — persists gate decision AND extracted features in a single write. `MLGateResult` contains: `score: float`, `result: str` ("pass"/"fail"), `fail_reason: str | None` (human-readable explanation when result="fail", e.g., "clearance_required", "not_swe_role" — critical for pipeline debugging and false-negative analysis), `version: str | None`, `is_swe_role: bool | None`, `seniority_level: str | None`, `degree_required: str | None`, `clearance_required: bool | None`, `school_restricted: bool | None`, `yoe_min: int | None`, `domain_tags: list[str] | None`, `tech_required: list[str] | None`, `role_type: str | None`. All feature fields map directly to `jobs` columns added in Phase 1. Note: Phase 0's existing `MLGateResult` model (`domain/models.py`) has `fit`/`probability`/`fail_reason` — Phase 1 expands it with the feature fields and renames `fit` → `result`, `probability` → `score` for clarity. The `fail_reason` is preserved.

**Workflow:**
- `async def workflow_attention(self, *, auto_ghost_days: int = 30, lookahead_days: int = 5) -> WorkflowAttention`
- `async def compute_reapply_notice(self, *, job_id: str, lookback_days: int = 60) -> str | None`

**State / cost:**
- `async def get_state(self, key: str) -> str | None`
- `async def set_state(self, key: str, value: str) -> None`
- `async def record_cost(self, *, day: str, spent_usd: float, calls: int) -> None`
- `async def get_cost(self, day: str) -> CostEntry | None` — read today's spend for budget-gate in digest/evaluate.
- `async def get_cost_range(self, *, since_days: int = 30) -> list[CostEntry]` — for CLI stats display.

**Existing methods to refine (if needed):**
- `save_job` must implement quality-ladder protection on upsert: incoming quality worse than stored → keep stored jd_text + quality. Compute and persist `company_norm`, `title_norm`, `location_norm` via `_normalize.py` on every save. **No soft-key dedup at DB layer** — each `(platform, canonical_id)` is an independent row; twin folding is UI/API layer only (per design spec Section 4 "Dedupe / Twin Semantics").
- `job_exists(self, *, platform: str, canonical_id: str) -> bool` — if not already in Phase 0, add it.

**Explicitly deferred (not in Phase 1 Protocol):**
- `record_llm_usage` — deferred to **Phase 3** (First Real LLM). Phase 1 has `record_cost` for day-level aggregation; per-call usage tracking arrives with real LLM integration.
- `diff_resume_snapshots` — deferred to **Phase 6** (Status + Apply Audit). Requires resume content diffing which is not needed until the full apply workflow.
- `list_resume_snapshots(source?)` — deferred to **Phase 6**. Phase 1 only needs `save` + `get` by exact hash.
- `resolve_hash_prefix(prefix: str)` — deferred to **Phase 6**. Prefix resolution is a CLI convenience for `snapshot show/diff`.
- `list_applications(resume_hash_prefix?)` — deferred to **Phase 6**. Phase 1's `list_applications(limit)` is sufficient for basic audit; per-resume filtering arrives with full apply workflow.

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
    verdict_snapshot    TEXT,
    fit_snapshot        TEXT,
    hooks_snapshot      TEXT
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

**Table: `state`** (key-value store for pipeline/config state):
```sql
CREATE TABLE IF NOT EXISTS state (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
```

**Phase 0 baseline `jobs` columns (already exist, NOT new in Phase 1):** `id`, `platform`, `canonical_id`, `url`, `title`, `company`, `location`, `jd_text`, `jd_quality`, `posted_at`, `discovered_at`, `enriched_at`, `enrich_source`. These 13 columns ARE the current Phase 0 schema (`src/jobfeed/adapters/store/schema.sql`). Phase 1 does not re-create them; it adds to them.

**Columns added to `jobs` in Phase 1:** `company_norm TEXT`, `title_norm TEXT`, `location_norm TEXT`, `jd_lang TEXT`, `enrich_error TEXT`, `quality_rubric_version INTEGER`, `reapply_notice TEXT`, `hard_filter TEXT`, `seniority_level TEXT`, `degree_required TEXT`, `clearance_required INTEGER`, `school_restricted INTEGER`, `domain_tags TEXT`, `tech_required TEXT`, `role_type TEXT`, `yoe_min INTEGER`, `ml_gate_score REAL`, `ml_gate_result TEXT`, `ml_gate_fail_reason TEXT`, `ml_gate_at TEXT`, `ml_gate_version TEXT`, `is_swe_role INTEGER`.

**Phase 0 baseline `evaluations` columns (already exist):** `id`, `job_id`, `stage_a_score`, `stage_a_one_line`, `stage_a_timing_eligible`, `stage_a_status`, `stage_a_error`, `stage_a_model`, `stage_a_cost_usd`, `stage_a_prompt_hash`, `stage_a_resume_hash`, `stage_b_verdict`, `stage_b_jd_summary`, `stage_b_verdict_json`, `stage_b_summary_json`, `stage_b_fit_json`, `stage_b_hooks_json`, `stage_b_status`, `stage_b_error`, `stage_b_model`, `stage_b_cost_usd`, `stage_b_prompt_hash`, `stage_b_resume_hash`. Phase 0 block columns (`block_a_json` etc.) are renamed to semantic names before Phase 1 begins — this is a Phase 0 schema fixup, not a Phase 1 migration.

**Columns added to `evaluations` in Phase 1:** `stage_a_at TEXT`, `stage_b_at TEXT`.

**Evaluations status enum (Phase 0 fixup if not already present):** `stage_a_status` and `stage_b_status` allow: `NULL` (pending), `"completed"`, `"error"` (retryable), `"skipped_below_threshold"` (terminal — Stage B only, set when Stage A score < threshold). Legacy v16 writes `skipped_below_threshold`; import and parity depend on this value being preserved.

**Phase 0 baseline `pipeline_runs` columns (already exist):** `id`, `run_id`, `started_at`, `source`, `jobs_discovered`, `jobs_inserted`, `jobs_updated`, `jobs_filtered`, `jobs_ml_gated`, `stage_a_scored`, `stage_b_scored`, `jobs_scored`, `total_llm_cost_usd`, `errors`, `finished_at`. No columns added in Phase 1.

**Schema naming decision:** The new repo uses its own column names (e.g., `discovered_at` not `scraped_at`, `jd_quality` not `jd_text_quality`). The legacy import layer (`legacy_import.py`) handles column mapping during migration. Block columns use semantic names (`stage_b_verdict_json`, `stage_b_summary_json`, `stage_b_fit_json`, `stage_b_hooks_json`) instead of opaque `block_X_json`.

**Legacy → new column mapping (handled by import layer):**

| Legacy v16 column | New column | Table | Notes |
|---|---|---|---|
| `scraped_at` | `discovered_at` | jobs | Rename only |
| `jd_text_quality` | `jd_quality` | jobs | Shortened |
| `block_a_verdict` | `stage_b_verdict_json` | evaluations | Semantic rename |
| `block_b_jd_summary` | `stage_b_summary_json` | evaluations | Semantic rename |
| `block_c_fit_analysis` | `stage_b_fit_json` | evaluations | Semantic rename |
| `block_e_resume_hooks` | `stage_b_hooks_json` | evaluations | Semantic rename |
| `timing_eligible` | `stage_a_timing_eligible` | evaluations | Prefixed with stage |
| `resume_hash` | `stage_a_resume_hash` + `stage_b_resume_hash` | evaluations | Legacy has single hash; import copies to **both** stage_a and stage_b when `stage_b_status='completed'`; stage_b gets NULL only if stage_b was never run |
| `block_a_snapshot` | `verdict_snapshot` | applied | Semantic rename |
| `block_c_snapshot` | `fit_snapshot` | applied | Semantic rename |
| `block_e_snapshot` | `hooks_snapshot` | applied | Semantic rename |

All other columns share the same name between legacy and new schema. The import layer (`legacy_import.py`) applies this mapping dict at read time before passing rows to `BulkImportPort`.

**Evaluations primary key divergence:** Legacy uses `job_id INTEGER PRIMARY KEY` (no separate surrogate). Phase 0 new schema uses `id INTEGER PRIMARY KEY` + `job_id UNIQUE`. On import: `evaluations.id` is auto-generated (NOT imported from legacy — legacy has no equivalent column). The `job_id` FK is the semantic key. Parity checksum (check #9) **excludes** the surrogate `evaluations.id` from hash computation — it compares rows by `job_id` match, not by `id` equality.

**Trigger: `trg_jobs_seed_status`** — AFTER INSERT ON jobs, auto-seed `(job_id, 'new')` into both `job_status` and `job_status_history`.

**Indexes:** `idx_jobs_dedup_softkey`, `idx_jobs_discovered_at`, `idx_companies_vendor`, `idx_eval_stage_a_score`, `idx_eval_fit_score` (json_extract on `stage_b_fit_json`), `idx_eval_stage_b_queue`, `idx_job_status_status`, `idx_job_status_followup`, `idx_job_status_stale`, `idx_job_status_history_job`.

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
- Modify: `src/jobfeed/adapters/store/sqlite.py` (facade only — delegates to mixins)
- Create: `src/jobfeed/adapters/store/_sqlite_status.py` (status, decay, workflow, append_note)
- Create: `src/jobfeed/adapters/store/_sqlite_application.py` (record_application, list_applications, application_stats, resume snapshots)
- Create: `src/jobfeed/adapters/store/_sqlite_import.py` (BulkImportPort implementation)
- Test: `tests/integration/test_sqlite_store.py` (expand with targeted integration tests)

**What to build:**
Implement all new Protocol methods in SQLiteStore. This is the heaviest implementation task. Each method must be async (using aiosqlite), transactionally correct, and idempotent where applicable.

**Adapter split strategy (300-line hygiene gate):** Current `sqlite.py` is ~280 lines. Phase 1 adds ~400+ lines of new method implementations. To stay under 300 lines per file, split into private mixin modules: `_sqlite_status.py`, `_sqlite_application.py`, `_sqlite_import.py`. The main `sqlite.py` remains the facade — it inherits/composes the mixins and satisfies the Protocol. Same pattern applies to PostgresStore (`_pg_status.py`, `_pg_application.py`, `_pg_import.py`). Each mixin file receives the `aiosqlite.Connection` from the facade and operates on it.

**Status management methods:**
- `transition_status` — calls `domain.status.validate_transition()` for the domain rule, then writes the DB update + history insert in one transaction. Same-status re-mark with different `resume_variant` only updates variant. Transition to `applied` sets `next_followup_at`. `archived → new` requires `force=True AND i_mean_it=True` (double-gate per legacy parity; `restore_from_archived()` is the non-destructive path).
- `get_status` — returns StatusInfo from `job_status` join.
- `restore_from_archived` — query `job_status_history` for most recent non-archived `to_status`, then call internal `_transition_status_in_tx(conn, ..., force=True, i_mean_it=True)` to bypass the double-gate. This is the **only** sanctioned path for `archived → <previous_status>`. A direct `transition_status(archived, new, force=True)` without `i_mean_it=True` MUST still fail — `restore_from_archived` is the safe wrapper that reads history first.
- `auto_decay` — sweep: `applied`/`interviewing`/interview-sub-stages silent ≥ `ghost_days` → ghosted; `ignored` silent ≥ `archive_ignored_days` → archived. Uses `transition_status(force=True)` per row with reason tags.
- `list_statuses` — filtered query on `job_status JOIN jobs`.
- `append_note` — append timestamped line to `job_status.notes`, reset `last_status_change_at`.

**save_job refinements:**
- Quality-ladder protection: if incoming quality rank < stored quality rank, drop jd_text + quality from the UPDATE (keep existing richer JD).
- Compute `company_norm`, `title_norm`, `location_norm` via `_normalize.py` on every insert/update.
- **No soft-key dedup at DB layer.** Each `(platform, canonical_id)` is its own row. The `company_norm + title_norm` index exists for UI twin-folding queries and `compute_reapply_notice`, not for insert-time dedup.

**Application audit methods:**
- `record_application` — transactional: INSERT into `applied` + status transition to `"applied"` with `force=True` (legacy parity — allows apply from any status, e.g., `new` or `scored` that haven't been shortlisted). Implementation uses a **private helper** `_transition_status_in_tx(conn, ..., force=True)` that accepts an existing connection/transaction — does NOT call the public `transition_status()` method (which acquires its own connection and would break atomicity or create nested transactions). If `applied` row already exists for this `job_id`, **no-op and return False** (preserves original audit row, matches legacy INSERT OR IGNORE semantics). Returns True on first application.
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
- `save_ml_gate_result` — single UPDATE on `jobs` setting all gate + feature columns: `ml_gate_score`, `ml_gate_result`, `ml_gate_fail_reason`, `ml_gate_at`, `ml_gate_version`, `is_swe_role`, `seniority_level`, `degree_required`, `clearance_required`, `school_restricted`, `yoe_min`, `domain_tags` (JSON array as TEXT), `tech_required` (JSON array as TEXT), `role_type`. All from `MLGateResult` fields.

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
- [ ] `save_job` with different canonical_id but same company_norm+title_norm inserts a new row (no DB-layer dedup)
- [ ] `record_application` writes applied record + transitions status in one transaction, returns True
- [ ] `record_application` on already-applied job returns False (no-op, original audit row preserved)
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
- Different `canonical_id` with same `company_norm + title_norm` → inserts as separate row (no DB-layer soft-key dedup; twin folding is UI-layer)
- Quality-ladder: upsert with lower quality → keeps existing richer JD
- Quality-ladder: upsert with higher quality → updates JD
- `list_jobs` returns inserted jobs
- `job_exists` returns True/False correctly
- `company_norm`, `title_norm`, `location_norm` populated on save

**Group 2: Evaluation Pipeline**
- Insert job → save_stage_a → verify `load_pending_stage_a` no longer returns it
- Save stage_a with score >= threshold → appears in `load_pending_stage_b`
- Save stage_a with score < threshold → does NOT appear in `load_pending_stage_b`; `stage_b_status` set to `"skipped_below_threshold"`
- `load_pending_stage_b` does NOT return jobs with `stage_b_status = "skipped_below_threshold"` (skipped is terminal for Stage B, not retryable)
- `save_stage_a_error` → job **stays in** pending_stage_a queue (retry semantics, matches legacy behavior: `stage_a_status = 'error'` is retryable, `load_pending_stage_a` returns jobs where `stage_a_status IS NULL OR stage_a_status = 'error'`)
- `save_stage_b` → `list_evaluated_jobs` includes it with full StageAResult + StageBResult
- `save_stage_b_error` → stage_b_status is "error", job stays in pending_stage_b queue (same retry semantics)

**Behavioral decision (Phase 1 change from Phase 0):** Phase 0's minimal store may have treated errors as terminal. Phase 1 adopts legacy retry semantics: error = retryable. The `load_pending_*` queries include error rows. This is intentional — production pipelines need automatic retry on transient LLM failures.

**Group 3: Status Lifecycle**
- Fresh job → auto-seeded to `new`
- `new → scored → shortlisted → applied` sequence
- Invalid transition without force → ValueError
- Invalid transition with force → succeeds, reason tag "FORCE: ..."
- Same-status re-mark with different resume_variant → variant updated, history row added
- Same-status re-mark with same variant → no-op, no extra history row
- Transition to `applied` → `next_followup_at` set
- `restore_from_archived` → walks history, restores to pre-archive status
- `transition_status(archived, new, force=True)` without `i_mean_it=True` → raises ValueError (double-gate enforced)
- `transition_status(archived, new, force=True, i_mean_it=True)` → succeeds (explicit destructive path)
- `auto_decay`: applied job silent >N days → ghosted
- `auto_decay`: ignored job silent >N days → archived
- `append_note` → note appended, ghost clock reset
- Forward-only interview stages: applied → oa → hr_call OK; hr_call → oa raises

**Group 4: Application Audit Trail**
- `record_application` → creates applied record + transitions to applied, returns True
- `record_application` on already-applied job → returns False (no-op, original audit row preserved)
- After duplicate `record_application`, `list_applications` still shows original record (not overwritten)
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
- `save_ml_gate_result` with result="pass" → job has ml_gate_score, ml_gate_result="pass", ml_gate_fail_reason=NULL, ml_gate_at set
- `save_ml_gate_result` with result="fail", fail_reason="clearance_required" → ml_gate_fail_reason persisted correctly
- `save_ml_gate_result` persists all feature columns (is_swe_role, seniority_level, etc.) — readback matches input

**Group 8: State / Cost / Pipeline**
- `set_state` → `get_state` returns it
- `record_cost` → upserts cost_ledger; `get_cost(day)` returns matching CostEntry
- `record_cost` twice on same day → upserts (accumulated, not overwritten)
- `get_cost_range(since_days=7)` → returns entries within range, ordered by day
- `get_cost` for nonexistent day → returns None
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
- Create: `docker-compose.postgres.yml`
- Modify: `pyproject.toml`
- Modify: `src/jobfeed/config.py`
- Create: `migrations/alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/versions/0001_initial_schema.py`

**What to build:**

**Docker Compose:** Create `docker-compose.postgres.yml` as a separate override file:
```yaml
# docker-compose.postgres.yml — PG override, not loaded by default
services:
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

volumes:
  pgdata:
```

The base `docker-compose.yml` is **unchanged** — it still defines only `jobfeed-cli` with SQLite as default. This preserves Phase 0's default path: `./bin/jobfeed` runs with SQLite, no PG needed.

To use PG: `docker compose -f docker-compose.yml -f docker-compose.postgres.yml up postgres -d`, then configure `db.backend = "postgres"` in config. No `depends_on: postgres` anywhere — Compose `depends_on` is static and would force PG on every SQLite user.

**pyproject.toml:** Add dependencies: `asyncpg >= 0.30`, `alembic >= 1.15`, `psycopg2-binary >= 2.9` (Alembic DDL runner needs synchronous PG driver). Dev dependency: `testcontainers[postgres] >= 4.0`.

**config.py:** Add `db.url` field (default: `postgresql://jobfeed:jobfeed_dev@localhost:5432/jobfeed_dev`) and `db.backend` enum (`"sqlite"` | `"postgres"`, default `"sqlite"`). The DI factory (`create_app()`) selects SQLiteStore or PostgresStore based on `db.backend`.

**Alembic setup:** `alembic.ini` with `sqlalchemy.url` pointing to PG (overridable via env). `env.py` uses synchronous psycopg2 connection for DDL (Alembic's runner is synchronous; the application uses asyncpg). Target metadata is not SQLAlchemy ORM (we don't use ORM) — migrations are hand-written SQL via `op.execute()`.

**Initial migration `0001_initial_schema.py`:** Creates the full PG schema equivalent of `schema.sql`. Key PG differences:
- `SERIAL` instead of `INTEGER PRIMARY KEY AUTOINCREMENT`
- `JSONB` for `stage_b_verdict_json`, `stage_b_summary_json`, `stage_b_fit_json`, `stage_b_hooks_json` (in evaluations table) — stores structured data queryable via `->` operator
- `TIMESTAMPTZ` instead of `TEXT` for all `_at` columns — PG-native timestamp handling
- `CHECK` constraints: same status enum, score ranges
- `ON CONFLICT (platform, canonical_id) DO UPDATE` for upsert (PG has native UPSERT)
- `CREATE INDEX` with same logical intent as SQLite indexes
- No `json_extract` index (PG uses `jsonb_path_ops` GIN index instead)
- `CREATE OR REPLACE FUNCTION` + `CREATE TRIGGER` for auto-seed (PG trigger syntax)
- `CREATE INDEX CONCURRENTLY` is not used in initial migration (empty table, no concurrency concern)

**Acceptance criteria:**
- [ ] `docker compose -f docker-compose.yml -f docker-compose.postgres.yml up postgres -d` starts PG and healthcheck passes
- [ ] `docker compose -f docker-compose.yml -f docker-compose.postgres.yml run --rm jobfeed-cli alembic -c migrations/alembic.ini upgrade head` applies migration
- [ ] Base `docker-compose.yml` unchanged — `./bin/jobfeed --help` still works without PG
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
- `INSERT ... ON CONFLICT (platform, canonical_id) DO UPDATE ... RETURNING id, (xmax = 0) AS inserted` for `save_job` upsert. The `xmax = 0` trick: PG sets `xmax` to the updating transaction's xid on UPDATE; on a fresh INSERT `xmax` remains 0. This gives a boolean `inserted` column in the result to populate `SaveJobResult.inserted` without a second query.
- `$1, $2, ...` parameter placeholders (not `?`).
- `JSONB` column access via `->` and `->>` operators.
- `TIMESTAMPTZ` columns — asyncpg handles Python `datetime` ↔ PG `timestamptz` natively. No manual ISO string conversion needed (unlike SQLite's TEXT columns).
- `row_to_dict()` helper: asyncpg returns `asyncpg.Record` objects; convert to domain models in adapter.

**Quality-ladder and normalization:** Same business logic as SQLiteStore, using `_normalize.py` helpers. PG SQL syntax differs but semantics are identical. No soft-key dedup at DB layer (each platform+canonical_id is independent).

**Status trigger:** PG uses a `CREATE FUNCTION` + `CREATE TRIGGER` pair (created by Alembic migration). The SQLite trigger auto-seeds on INSERT; the PG trigger does the same. The adapter does not re-create triggers — that's Alembic's job.

**Transaction boundaries:**
- `record_application`: single transaction wrapping INSERT into `applied` + status transition via `_transition_status_in_tx(conn, ..., force=True)`. Does NOT call public `transition_status()` — that would acquire a separate connection and break atomicity.
- `auto_decay`: each row's transition is its own small transaction (matching SQLite behavior — partial completion is OK).
- `transition_status`: single transaction: UPDATE `job_status` + INSERT `job_status_history`. Internally uses `_transition_status_in_tx(conn, ...)` — the public method just wraps it with connection acquisition.

**Private helper pattern:** Both SQLiteStore and PostgresStore extract `_transition_status_in_tx(conn, *, job_id, new_status, force, ...)` as a private method that operates on a passed-in connection. The public `transition_status()` acquires a connection and delegates. `record_application` reuses the same helper within its own transaction.

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
- [ ] Quality-ladder and normalization behave identically to SQLiteStore
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
```python
@pytest.fixture(params=[
    "sqlite",
    pytest.param("postgres", marks=pytest.mark.postgres),
])
```
- The `pytest.param(..., marks=...)` ensures PG tests carry the `postgres` marker automatically.
- `pyproject.toml` adds **both** marker registration and default deselection:
  ```toml
  [tool.pytest.ini_options]
  markers = ["postgres: requires PostgreSQL (Docker)"]
  addopts = "-m 'not postgres'"
  ```
- With `addopts = "-m 'not postgres'"`, bare `pytest` (and `make quality`) automatically skips PG tests — no Docker pull.
- PG tests run explicitly via `pytest -m postgres` (overrides addopts) or in CI's dedicated PG job.
- To run **all** tests including PG: `pytest -m ""` (empty marker expression overrides the default exclusion).
- Postgres parameterization uses testcontainers to spin up an ephemeral PG instance, runs Alembic migration, yields a connected PostgresStore, and tears down after the test.
- **Local dev (default):** Skip PG tests if Docker is not available (graceful degradation via `pytest.importorskip("testcontainers")`).
- **CI (mandatory):** Set env var `JOBFEED_REQUIRE_POSTGRES=1`. When set, the conftest fixture **fails** (not skips) if testcontainers/Docker is unavailable — prevents silent green CI when PG contract coverage is broken.

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
A one-time script (not shipped in production) that reads a legacy database and anonymizes PII/sensitive content into a test fixture. Usage: `python generate_legacy_fixture.py --from /path/to/jobs.db --out tests/fixtures/legacy_v16.db`. The `--from` path is **required** (no default) — the script must never hardcode or default to `~/.jobfeed/` per the environment isolation constraint. Anonymization rules:
- Job titles: keep structure, replace company-specific words with generic equivalents.
- Company names: replace with fake company names (keep normalization-relevant suffixes like "Inc.", "Technologies").
- URLs: replace domains with `example.com`.
- JD text: replace with lorem-ipsum-style placeholder of the same approximate length, preserving quality band distribution.
- Resume content in snapshots: replace with generic placeholder, then **recompute `resume_hash` as sha256 of the new content**. The fixture is a **legacy v16 database** (not new schema), so cascade the recomputed hashes to legacy column names: `applied.master_resume_hash`, `applied.tailored_resume_hash`, `evaluations.resume_hash` (legacy's single hash column), and `state.last_resume_hash`. The import layer then maps `evaluations.resume_hash` → `stage_a_resume_hash` + `stage_b_resume_hash` per the import mapping table. Any hash column that pointed to the original content must be remapped — leaving real resume fingerprints in any table defeats anonymization.
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

The legacy importer does NOT write through the normal `JobStore` Protocol. Normal business methods can't express: explicit IDs, raw timestamps, overwrite auto-seeded status, bulk history insert, PG sequence reset. Instead, it uses a dedicated `BulkImportPort`:

Each table's row type is a `TypedDict` in `legacy_import.py` (e.g., `JobRow`, `EvaluationRow`, `AppliedRow`) to lock down the shape at the import boundary. The `BulkImportPort` methods accept the typed rows:

```python
class BulkImportPort(Protocol):
    """Adapter-specific bulk load operations for legacy migration.
    
    Not part of JobStore — these bypass business rules (triggers, 
    auto-seed, quality ladder) intentionally for data migration.
    """
    async def bulk_insert_jobs(self, rows: list[JobRow]) -> int: ...
    async def bulk_insert_evaluations(self, rows: list[EvaluationRow]) -> int: ...
    async def bulk_insert_job_status(self, rows: list[JobStatusRow]) -> int: ...
    async def bulk_insert_job_status_history(self, rows: list[StatusHistoryRow]) -> int: ...
    async def bulk_insert_applied(self, rows: list[AppliedRow]) -> int: ...
    async def bulk_insert_resume_snapshots(self, rows: list[ResumeSnapshotRow]) -> int: ...
    async def bulk_insert_companies(self, rows: list[CompanyRow]) -> int: ...
    async def bulk_insert_cost_ledger(self, rows: list[CostLedgerRow]) -> int: ...
    async def bulk_insert_state(self, rows: list[StateRow]) -> int: ...
    async def reset_sequences(self) -> None: ...  # PG only; SQLite no-op
    async def disable_triggers(self) -> None: ...
    async def enable_triggers(self) -> None: ...
```

The TypedDicts are defined in `legacy_import.py` (not in `domain/models.py`) — they describe the wire format for migration, not domain concepts.

Both SQLiteStore and PostgresStore implement `BulkImportPort` alongside `JobStore`. The importer uses `BulkImportPort` for raw data load, then the parity harness uses `JobStore` read methods to verify results.

**Trigger disable/enable strategy (per backend):**
- **SQLite:** `disable_triggers` executes `DROP TRIGGER IF EXISTS trg_jobs_seed_status`. `enable_triggers` re-creates it via `CREATE TRIGGER trg_jobs_seed_status AFTER INSERT ON jobs ...`. SQLite has no `ALTER TABLE ... DISABLE TRIGGER` — drop/create is the only mechanism.
- **PostgreSQL:** `disable_triggers` executes `ALTER TABLE jobs DISABLE TRIGGER trg_jobs_seed_status`. `enable_triggers` executes `ALTER TABLE jobs ENABLE TRIGGER trg_jobs_seed_status`. PG natively supports per-trigger enable/disable without DDL drop.

- `async def import_legacy_sqlite(legacy_path: Path, target: BulkImportPort) -> ImportReport`
- Import flow: `disable_triggers` → `try:` bulk insert tables in FK order (`resume_variants` → `jobs` → `job_status` → `job_status_history` → `evaluations` → `applied` → `resume_snapshots` → `companies` → `cost_ledger` → `state`) → `reset_sequences` → `finally: enable_triggers`. The `try/finally` guarantees triggers are re-enabled even if a bulk insert fails mid-way.
- ID preservation: legacy `jobs.id` values inserted as-is. `evaluations.id` is NOT imported (auto-generated surrogate; legacy has no equivalent). `job_status_history.id` values inserted as-is.
- For PG, `reset_sequences()` resets **all** serial/sequence columns to `MAX(id) + 1`: `jobs.id`, `evaluations.id`, `job_status_history.id`. Missing any of these causes PK collision on the next INSERT after import.
- Trigger disabled during import: avoids auto-seed conflict (legacy `job_status` rows imported directly, not generated by trigger).
- No recomputation: scores, statuses, company_norm, prompt_hash are preserved as-is from legacy data (values are identical; column names are mapped per the import mapping table).
- **state table key mapping:** legacy `schema_version=16` is imported as `legacy_schema_version=16`. The new DB's own `schema_version` is managed by Alembic (PG) or schema.sql version (SQLite) and is NOT overwritten by import. All other state keys are imported as-is.
- `ImportReport` dataclass: `tables_imported: dict[str, int]` (table → row count), `warnings: list[str]`, `errors: list[str]`, `duration_s: float`.

**Acceptance criteria:**
- [ ] `legacy_v16.db` is a valid SQLite v16 database (schema_version=16 in state table)
- [ ] Fixture contains representative data across all core tables
- [ ] No real PII in fixture (all anonymized)
- [ ] `legacy_v16_manifest.json` matches fixture's actual row counts
- [ ] `import_legacy_sqlite` reads fixture without errors
- [ ] Import preserves legacy job IDs
- [ ] Import uses `BulkImportPort` (not normal JobStore business methods, not raw SQL outside adapter)
- [ ] PG `reset_sequences()` resets all serial columns (`jobs.id`, `evaluations.id`, `job_status_history.id`) to `MAX(id) + 1`
- [ ] Triggers disabled during import, re-enabled after
- [ ] Failure path test: simulate error mid-import → triggers still re-enabled (try/finally)
- [ ] Failure path test: simulate error mid-import → target DB has zero imported rows (PG: transaction rolled back; SQLite: temp file deleted, original target untouched). Verify with row count + checksum assertion on both backends.
- [ ] ImportReport reports correct per-table counts
- [ ] All tests pass, committed

---

## Task 9: Parity Assertion Harness

**Files:**
- Create: `src/jobfeed/adapters/store/parity.py`
- Create: `tests/e2e/test_legacy_import.py`

**What to build:**

**Parity assertion module (`parity.py`):**

The parity harness needs to read raw rows from tables that JobStore doesn't expose (job_status_history, cost_ledger, state). It uses a dedicated `ParityReadPort`:

```python
class ParityReadPort(Protocol):
    """Raw table read access for migration verification only."""
    async def read_all_rows(self, table: str) -> list[dict]: ...
    async def count_rows(self, table: str) -> int: ...
```

Both SQLiteStore and PostgresStore implement `ParityReadPort` alongside `JobStore` and `BulkImportPort`. The parity harness uses `ParityReadPort` for raw inspection and `JobStore` for business-level checks.

`async def verify_import_parity(legacy_path: Path, target: ParityReadPort, manifest: dict) -> ParityReport`

Checks:
1. **Row count match** — per core table (jobs, evaluations, job_status, job_status_history, applied, resume_snapshots, companies, cost_ledger, state). Count from legacy SQLite vs count from target store.
2. **FK integrity** — every `applied.job_id` resolves to a job. Every `evaluations.job_id` resolves to a job. Every `job_status.job_id` resolves to a job. Every `job_status_history.job_id` resolves to a job.
3. **Resume snapshot hash verification** — for each `resume_snapshots` row, sha256 of the imported `content` matches the stored `resume_hash`.
4. **Status enum validation** — every `job_status.status` value is in STATUS_VALUES.
5. **Stage B JSON parseability** — every non-NULL `stage_b_fit_json` in evaluations is valid JSON with expected keys (`score_0_100`, `strong_match`, `gaps`).
6. **Normalization presence** — every job has non-empty `company_norm` and `title_norm`.
7. **ID preservation** — sample N jobs by legacy ID and verify they exist with the same ID in the new store.
8. **Evaluation score ranges** — all `stage_a_score` values in 0–100.
9. **Canonical row checksum** — for each core table, read all rows from legacy (with column mapping applied) and from target, compute a per-row canonical hash (sort JSON keys before hashing to handle SQLite TEXT vs PG JSONB key-order differences), compare full-set or sampled-N checksums. This proves actual values are preserved, not just structural integrity.

`ParityReport` dataclass: `passed: bool`, `checks: list[ParityCheck]` where `ParityCheck` has `name: str`, `passed: bool`, `details: str`.

**E2E test (`test_legacy_import.py`):**
- Load `tests/fixtures/legacy_v16.db` and `legacy_v16_manifest.json`.
- Import into a fresh SQLiteStore (tmp_path) via `import_legacy_sqlite`.
- Run `verify_import_parity`.
- Assert all checks pass.
- Repeat import into PostgresStore (testcontainers) if Docker available.
- Assert all checks pass on PG too.

**Acceptance criteria:**
- [ ] Parity harness checks all 9 verification categories
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
- `jobfeed migrate import-sqlite --from <path> [--dry-run] [--backup] [--replace] [--verify]` — runs the import pipeline. `--dry-run` simulates without writing (prints what would be imported). `--backup` backs up the **target** store before import (SQLite: copies target .db file; PG: not supported, prints warning — use pg_dump manually). `--replace` allows import into a non-empty target (default: fail if target has existing data). `--verify` runs the parity assertion harness after import and prints the ParityReport. Default behavior (no flags): import + verify.
- Both commands use the currently configured store backend (from `--config` or defaults). If backend is `postgres`, import writes to PG. If `sqlite`, import writes to the new SQLite.

**Safety:**
- `--from` path must exist and be a valid SQLite v16 database (check `state.schema_version`).
- Import never modifies the source database.
- **Target must be empty** (no rows in `jobs` table) unless `--replace` is passed. This prevents accidental double-import.
- **`--replace` semantics:** TRUNCATE all tables in reverse-FK order (`job_status_history` → `job_status` → `applied` → `evaluations` → `resume_snapshots` → `resume_variants` → `companies` → `cost_ledger` → `state` → `jobs`) within a single transaction, then proceed with normal import. For PG: `TRUNCATE ... CASCADE` in one statement. For SQLite: `DELETE FROM` per table in reverse-FK order (SQLite has no TRUNCATE). Sequence reset happens after re-import (same as normal flow). If any step fails, the entire transaction rolls back — target is left in its pre-replace state.
- `--backup` backs up the **target** (not the source — source is never modified). For SQLite targets: `cp <target.db> <target.db>.bak-pre-import-YYYYMMDD-HHMMSS`. For PG targets: print warning that pg_dump should be done manually.
- Import failure must not leave target store in a partially-migrated state. **PG:** entire import is one transaction — failure rolls back everything. **SQLite:** import writes to a temporary database file, then atomic-swaps (`os.replace`) onto the target path on success. If any table fails mid-import, the temp file is deleted and the original target is untouched.

**E2E test (`test_cli_migrate.py`):**
- `jobfeed migrate inspect-sqlite tests/fixtures/legacy_v16.db` → exits 0, prints row counts.
- `jobfeed migrate import-sqlite --from tests/fixtures/legacy_v16.db --verify` → exits 0, parity passes.
- `jobfeed migrate import-sqlite --from nonexistent.db` → exits 1 with clear error.
- `jobfeed migrate import-sqlite --from tests/fixtures/legacy_v16.db --dry-run` → exits 0, prints plan, no data written.

**Acceptance criteria:**
- [ ] `inspect-sqlite` prints schema version + row counts
- [ ] `import-sqlite` imports all tables from fixture
- [ ] `--dry-run` prints plan without writing
- [ ] `--backup` creates backup of target store (not source) before import
- [ ] Import into non-empty target without `--replace` → exits 1 with clear error
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
- Sets `JOBFEED_REQUIRE_POSTGRES=1` and runs: `pytest -m postgres -o "addopts=" -v --tb=short`. The env var makes PG test infra failures hard-fail (not skip). The `-o "addopts="` clears the default `"-m 'not postgres'"` from pyproject.toml so PG-marked tests are actually selected.
- Separate from the existing unit/integration test job (PG tests are slower).
- The existing integration test job still runs SQLite-only tests (uses default addopts, PG auto-deselected).

**pyproject.toml pytest markers:**
- `@pytest.mark.postgres` — tests requiring a PG instance.
- Default `addopts = "-m 'not postgres'"` deselects PG tests for local dev and `make quality`.
- CI PG job explicitly overrides with `-o "addopts="` to select PG tests.

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

1. **Contract test parity:** `pytest tests/contract/ -m "not postgres"` passes (SQLite), and `pytest tests/contract/ -m postgres` passes (PG). Same test, same assertions, both green.

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
   - `legacy_v16.db` imports into SQLiteStore → all 9 parity checks pass.
   - `legacy_v16.db` imports into PostgresStore → all 9 parity checks pass.
   - Job IDs preserved on both backends.

6. **Architecture boundaries:**
   - `domain/status.py` imports stdlib only.
   - `ports/store.py` imports domain models only.
   - `adapters/store/postgres.py` imports asyncpg, not aiosqlite.
   - `adapters/store/sqlite.py` imports aiosqlite, not asyncpg.
   - Both adapters import `_normalize.py` (shared).

7. **`make quality` passes** (ruff + mypy + all non-PG tests). PG contract tests run separately via `pytest -m postgres`.

8. **Docker smoke:** `docker compose -f docker-compose.yml -f docker-compose.postgres.yml up postgres -d && docker compose -f docker-compose.yml -f docker-compose.postgres.yml run --rm jobfeed-cli alembic -c migrations/alembic.ini upgrade head` succeeds.

9. **Base compose preserved:** `./bin/jobfeed --help` works without PG running (SQLite default path intact).

**Acceptance criteria (Phase 1 milestone):**
- [ ] Same JobStore contract tests pass on both SQLiteStore and PostgresStore
- [ ] Status transition persists correctly on both backends (including `archived → new` double-gate)
- [ ] Evaluation upsert works correctly on both backends
- [ ] Duplicate upsert (canonical_id) works correctly on both backends; different canonical_id with same company_norm inserts new row
- [ ] Stage A/B error → job stays in pending queue (retry semantics) on both backends
- [ ] Legacy SQLite v16 data importable into both backends via BulkImportPort
- [ ] Parity assertion harness passes for both backends
- [ ] Architecture boundary tests pass
- [ ] `make quality` passes
- [ ] Base `docker-compose.yml` unchanged — SQLite default path works without PG
- [ ] All committed
