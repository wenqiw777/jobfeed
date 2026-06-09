# Jobfeed Rewrite — Architecture Design Spec

**Date:** 2026-05-20
**Status:** Draft
**Scope:** Complete rewrite of jobfeed into a new repo with industrial-level architecture

---

## 1. Goals

Rewrite the existing jobfeed project (~14,780 LOC Python + React) into a new repository that meets production-level standards:

1. Clear business boundaries — each module has one responsibility
2. Clear dependency direction — core logic does not depend on DB, frameworks, or APIs
3. Automated testing — five test layers with coverage gates
4. Repeatable CI/CD — push triggers lint, typecheck, test, build automatically
5. Environment isolation — dev/staging/prod switch via config, not code changes
6. Observability — structured logging, error tracking, distributed tracing
7. Security baseline — auth, secrets, input validation, dependency scanning
8. Documentation and conventions — new contributor can understand and run the project quickly

Functionality stays identical to the current project. This is a pure architecture + code hygiene rewrite.

---

## 2. Tech Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Backend | Python + FastAPI | Mature ecosystem, native ML/LLM library support |
| Database | PostgreSQL (primary) + SQLite (bootstrap/debug fallback) | PG for production/full-stack execution, SQLite for Phase 0 and developer fallback. Dual mode via repository pattern |
| Task Queue | Temporal (full-stack observable mode) | Durable execution, built-in retry/backoff, workflow visualization. The containerized CLI uses InProcessRunner unless the user explicitly selects Temporal; Web triggers, schedules, and durable execution use Temporal |
| Frontend | React 19 + Vite + TanStack Query + Tailwind | Proven stack, no change needed |
| LLM Integration | Abstraction layer: Anthropic SDK, OpenAI SDK, Claude CLI, Codex CLI, MockLLM | Multi-provider support behind LLMClient Protocol |
| Deployment | Docker Compose + thin host launchers | Docker is the canonical production-parity runtime. Host launchers normalize OS differences and forward commands into the containerized app CLI |
| Observability | structlog + Sentry + OpenTelemetry (Jaeger/Prometheus/Grafana) | Structured logs + error tracking + distributed tracing |
| CI/CD | GitHub Actions | containerized lint + typecheck + test + CLI smoke + LLM review |

---

## 2.1 Runtime Contract

Jobfeed is **Docker-first** for user-facing and production-parity execution.

The canonical user command is a host launcher:

```bash
./bin/jobfeed scan
./bin/jobfeed evaluate
./bin/jobfeed digest
```

The host launcher does not run application code directly. It forwards arguments
to the containerized application CLI:

```bash
docker compose run --rm jobfeed-cli jobfeed "$@"
```

The real application CLI still lives in the Python package (`src/jobfeed/cli`).
It parses arguments, loads config, wires dependencies, opens/closes adapters,
and calls services. The host launcher only absorbs host OS differences
(macOS/Linux shell vs Windows PowerShell, path handling, Docker invocation).

Host-native execution (`uv run jobfeed ...` or `python -m jobfeed ...`) is a
developer/debug fallback, not the supported user path and not the production
parity standard.

Phase 0 uses a Dockerized CLI walking skeleton: `jobfeed-cli` container,
mounted repo-local `.jobfeed-dev/` SQLite data, MockSource, and MockLLM. It does
not start Postgres, Temporal, Web, or observability yet.

Full-stack mode uses Docker Compose for the user-facing CLI, Web API/UI,
Postgres, Temporal, and observability.

---

## 3. Architecture: Hexagonal (Ports & Adapters)

### Dependency Direction

```
domain/  →  ports/ (Protocol interfaces)  ←  adapters/ (concrete implementations)
                                           ←  services/ (orchestration)
cli/ (thin shell) → services
web/ (thin shell) → services
temporal/ (workflows) → services
```

Domain depends on nothing. Ports define interfaces. Adapters implement interfaces. Services compose domain logic with ports. CLI, Web, and Temporal are thin entry points that delegate to services.

### Directory Structure

```
jobfeed/                              # repo root
├── src/
│   └── jobfeed/
│       ├── domain/                   # pure business logic, zero external imports
│       │   ├── errors.py             # JobfeedError, ScoringParseError
│       │   ├── models.py             # JobPosting, Evaluation, Status, LLMUsage, PipelineRun
│       │   ├── types.py              # shared Literal aliases
│       │   ├── scoring.py            # prompt rendering + response parsing (NO LLM calls)
│       │   ├── filtering.py          # hard-filter rule engine
│       │   ├── ml_gate.py            # feature extraction + vectorization + prediction
│       │   ├── quality.py            # JD text quality assessment
│       │   └── digest.py             # Markdown summary rendering
│       │
│       ├── ports/                    # Protocol interfaces (dependency inversion core)
│       │   ├── store.py              # JobStore protocol
│       │   ├── llm.py                # LLMClient protocol
│       │   ├── source.py             # SimpleSource + SessionSource protocols
│       │   ├── embedder.py           # Embedder protocol
│       │   └── notifier.py           # Notifier protocol (PLANNED, not current parity)
│       │
│       ├── adapters/                 # concrete implementations of ports
│       │   ├── store/
│       │   │   ├── postgres.py       # PostgreSQL via asyncpg
│       │   │   ├── sqlite.py         # SQLite via aiosqlite
│       │   │   ├── sqlite_mapping.py # row/domain mapping helpers
│       │   │   ├── sqlite_stage_b_mapping.py # validated raw block mapping
│       │   │   ├── sqlite_row.py     # shared row validation helpers
│       │   │   ├── sqlite_params.py  # SQL parameter helpers
│       │   │   └── sqlite_sql.py     # SQL statement constants
│       │   ├── llm/
│       │   │   ├── anthropic.py      # Anthropic SDK (prompt caching)
│       │   │   ├── openai.py         # OpenAI SDK (Codex/GPT)
│       │   │   ├── claude_cli.py     # claude -p subprocess
│       │   │   ├── codex_cli.py      # codex exec subprocess
│       │   │   └── mock.py           # deterministic responses for testing + dev
│       │   ├── sources/
│       │   │   ├── _jobspy.py        # shared JobSpy wrapper
│       │   │   ├── linkedin_playwright.py
│       │   │   ├── linkedin_jobspy.py
│       │   │   ├── indeed_jobspy.py
│       │   │   ├── ats.py
│       │   │   └── speedyapply.py
│       │   ├── embedder/
│       │   │   └── sentence_transformer.py
│       │   └── notifier/             # PLANNED, not current parity
│       │       └── gmail.py
│       │
│       ├── services/                 # orchestration: compose domain + ports
│       │   ├── error_handler.py      # recoverable service error policy
│       │   ├── scan.py               # discover + store pipeline
│       │   ├── evaluate.py           # ML gate -> Stage A -> Stage B
│       │   ├── workflow.py           # status transitions, decay, attention
│       │   ├── application.py        # apply audit trail: snapshots + record
│       │   └── digest.py             # pull data + render
│       │
│       ├── cli/                      # Click commands (thin shell)
│       │   ├── __init__.py           # Click group entry
│       │   ├── scan.py               # scan
│       │   ├── evaluate.py           # evaluate
│       │   ├── manage.py             # mark, note, followup, archive, companies
│       │   ├── apply.py              # apply, apply-history
│       │   ├── snapshots.py          # snapshots list/show/diff
│       │   ├── ml_gate.py            # ml-gate train/info
│       │   └── serve.py              # serve
│       │
│       ├── web/                      # FastAPI (thin shell)
│       │   ├── app.py                # app factory
│       │   ├── deps.py               # dependency injection
│       │   ├── schemas.py            # Pydantic response models
│       │   └── routes/
│       │       ├── jobs.py
│       │       ├── stats.py
│       │       └── health.py
│       │
│       ├── config.py                 # pydantic-settings
│       ├── observability.py          # structlog + OTel + Sentry init
│       └── temporal/                 # Temporal workflow definitions
│           ├── workflows.py          # ScanWorkflow, EvaluateWorkflow
│           └── activities.py         # each activity = one service call
│
├── migrations/                       # Alembic
│   ├── alembic.ini
│   └── versions/
│
├── web-ui/                           # React frontend (unchanged stack)
│
├── tests/
│   ├── unit/                         # domain pure logic, zero IO
│   ├── integration/                  # real DB + mock HTTP
│   ├── e2e/                          # CLI + API end-to-end
│   ├── contract/                     # frontend-backend type sync
│   └── mutation/                     # mutmut config for domain layer
│
├── docker-compose.yml
├── docker-compose.dev.yml
├── Dockerfile
├── Makefile
├── pyproject.toml
├── .env.example
└── .github/workflows/
    └── ci.yml
```

---

## 4. Domain Models

All domain types are pure Python dataclasses with zero external dependencies.

### Core Models

- **JobPosting** — id (store identity; `None` before persistence), platform, canonical_id, url, title, company, location, jd_text, jd_quality, posted_at, discovered_at (scraped_at), enriched_at, enrich_source. Natural identity remains `(platform, canonical_id)`.
- **QualityBand** — enum: full, good, partial, stub, missing, abandoned
- **StageAResult** — score (0-100), one_line, timing_eligible (eligible/mismatch/unclear), model, optional cost_usd, prompt_hash, resume_hash
- **StageBResult** — verdict, jd_summary, fit_analysis, resume_hooks, model, optional cost_usd, prompt_hash, resume_hash, raw_blocks (optional exact `block_a`/`block_b`/`block_c`/`block_e` object for audit persistence)
- **Verdict** — enum: apply, consider, skip
- **FitAnalysis** — score (0-100), strengths (list[MatchItem]), gaps (list[GapItem])
- **MatchItem** — requirement, evidence
- **GapItem** — requirement, severity (critical/major/minor), mitigation
- **MLGateResult** — see Section 14 (ML Gate subsystem)

### Status Model

**JobStatus** — parity enum with future-compatible interview metadata:

Current parity statuses (preserved exactly):
- Pipeline: `new`, `scored`, `shortlisted`
- Application: `applied`
- Interview sub-stages: `oa`, `hr_call`, `second_round`, `final_round`, `interviewing` (coarse-grained legacy)
- Terminal: `offer`, `rejected`, `ghosted`
- Side: `archived` (Skip — reviewed and rejected), `ignored` (Ignore — dismissed without review)

Future-compatible interview metadata (extension, does not replace enum):
- Optional `interview_round` metadata may store: round_index, label, scheduled_at, completed_at, notes
- Legacy statuses map to default labels: oa → "OA", hr_call → "HR Call", second_round → "Second Round", final_round → "Final Round", interviewing → "Interview"
- This allows future support for arbitrary round labels without breaking current UI/API/stats/status history

**archived vs ignored:**

| | archived | ignored |
|--|---------|---------|
| Meaning | Reviewed, not interested | Dismissed without review |
| Trigger | Skip action | Ignore action (typically on Pending JD rows lacking JD) |
| Skipped tab | Yes | No |
| Pending JD | Excluded | Excluded |
| Restore | `restore_from_archived()` restores to pre-archive status | No dedicated restore; force transition only |
| Auto-decay | No | Yes: `ignored` → `archived` after 14 days silent |
| Scope | Current row (bulk mark cascades to twins) | Current source row only |

**Transition graph (parity with current repo):**

```
new → scored
scored → shortlisted | applied | archived | ignored
shortlisted → applied | archived
applied → oa | hr_call | second_round | final_round | interviewing | rejected | ghosted | offer
oa → hr_call | second_round | final_round | offer | rejected | ghosted
hr_call → second_round | final_round | offer | rejected | ghosted
second_round → final_round | offer | rejected | ghosted
final_round → offer | rejected | ghosted
interviewing → offer | rejected | ghosted
ignored, archived, rejected, offer, ghosted → (terminal)
```

Interview sub-stages are forward-only: can skip ahead (oa → final_round) but cannot rewind.

Special paths:
- **auto-decay**: `applied` / interview stages → `ghosted` after N days with no activity (via `force=True`)
- **auto-decay**: `ignored` → `archived` after 14 days
- **restore from archived**: moves job back to pre-archive status with history preserved
- **status history**: every transition recorded in `job_status_history` (from_status, to_status, reason, timestamp)
- **follow-up tracking**: next_action date attached to status, surfaced in workflow attention
- **note resets ghost clock**: adding a note resets the auto-ghost timer

### Application Audit Trail

`apply` is not just `mark applied`. It is a transactional audit path that freezes all materials at apply time:

- **ApplicationRecord** — job_id (PRIMARY KEY), applied_at, master_resume_hash, tailored_resume_hash (optional), cover_letter_text (optional), block_a_snapshot (verdict JSON), block_c_snapshot (fit analysis JSON), block_e_snapshot (resume hooks JSON), resume_variant_name, application_method (optional), notes (optional)
- **ResumeSnapshot** — content-addressed (sha256), append-only. Same content across 100 applications = one row. Modified resume = new snapshot.

Port methods:
- `record_application()` — transactional: write snapshots + application record + status transition together
- `list_applications()` — apply history query
- `save_resume_snapshot()` / `get_resume_snapshot()` / `diff_resume_snapshots()` — resume version management

Questions this answers: what resume did I submit to this company? Was there a tailored version? What did the AI recommend at that time? Can I recover that exact version?

### Time Field Semantics

Two time fields with different meanings — must not be confused:

- **scraped_at** (`discovered_at`): when the system first observed this posting. Primary freshness filter for Today, Pending JD, evaluate queue. A job posted April 1 but scraped May 20 is "fresh" because it's new to the user's workflow.
- **posted_at**: when the employer published the JD. Secondary pruning only: `non_apply_posted_within_days` hides old consider/skip/unscored postings, but keeps old apply-worthy jobs visible. `posted_at = NULL` always passes (many sources lack accurate publish dates).

Rule: `scraped_at` = system-observed freshness (main filter). `posted_at` = employer-posted freshness (secondary pruning for non-apply old postings).

### Business Metrics Models

- **LLMUsage** — model, input_tokens, output_tokens, cost_usd, cached, latency_ms, timestamp
- **PipelineRun** — run_id, started_at, source, jobs_discovered, jobs_inserted, jobs_updated, jobs_filtered, jobs_ml_gated, stage_a_scored, stage_b_scored, jobs_scored, total_llm_cost_usd, errors, finished_at. `source` is the operation/source label: source name for single-source scan, `scan` for aggregate scan, `evaluate` for evaluation.

Domain models != database schema. The adapter layer handles conversion between domain objects and database rows.

### Dedupe / Twin Semantics

**DB stores all source rows.** Each `platform + canonical_id` is an independent row. LinkedIn posting and Greenhouse posting for the same job = two DB rows. Reposts with new post IDs = new rows.

**UI/API layer folds twins** at query time by `company_norm + title_norm`. The `dedupe=true` query parameter selects a representative per twin cluster. This is display-layer behavior, not DB mutation.

Implications:
- Different sources may have different JD quality — all preserved
- `scraped_at` / `posted_at` differ per source row — all preserved
- Status actions on a single row affect that row only; bulk mark cascades to twin cluster
- Application audit records which specific source URL was applied to

---

## 5. LLM Abstraction Layer

### Protocol

```python
class LLMClient(Protocol):
    async def complete(self, request: LLMRequest) -> LLMResponse: ...
```

- **LLMRequest**: messages, model, temperature, max_tokens, response_schema (optional)
- **LLMResponse**: content, model, input_tokens, output_tokens, optional cost_usd, cached

### Provider Routing

Model spec format: `provider/model-name` (e.g., `anthropic/claude-haiku-4-5`, `openai/gpt-4.1`)

Factory parses the prefix and builds the corresponding adapter. Each stage can use a different provider:

```toml
[llm]
stage_a = "anthropic/claude-haiku-4-5"
stage_b = "openai/gpt-4.1"
```

### Five Adapters

| Adapter | Backend | Provider-specific behavior |
|---------|---------|---------------------------|
| AnthropicLLM | anthropic SDK | Prompt caching (system + long prefix marked with cache_control) |
| OpenAILLM | openai SDK | response_format for structured output |
| ClaudeCliLLM | `claude -p` subprocess | JSON envelope parsing, dev-mode when Claude Code is available |
| CodexCliLLM | `codex exec` subprocess | Codex CLI backend, supports configured model names, read-only/ephemeral mode |
| MockLLM | in-memory | Deterministic responses, zero cost, for testing + dev |

Domain layer (`domain/scoring.py`) renders prompts and parses responses; it raises expected parse failures via `domain/errors.py` and does NOT call LLMClient. The prompt renderers state the exact JSON keys that the parsers require. LLM orchestration lives in `services/evaluate.py`, which depends on the LLMClient Protocol, applies configured timeout/concurrency limits, and records recoverable runtime failures as explicit stage errors. Recoverable service failures, including scoring parse failures, LLM runtime failures, and per-source fetch failures, are handled through `services/error_handler.py` so persistence, logging, and run counters use one naming pattern. Provider-specific optimizations happen inside the adapter, invisible to services.

### Prompt Composition

Stage A/B prompts are not just templates. Composition order:

```
system prompt = base_template + preamble_personal (if exists)
user prompt   = resume.md + score_rubric.md + JD text
```

The configured `llm.preamble_personal_path` file is a per-machine personal calibration layer (preferred companies, directions to emphasize, timing judgments). It is **included in `prompt_hash`** so that:
- Results are traceable to the exact prompt version + personal calibration
- Changing preamble changes the hash, enabling re-evaluation with new calibration
- Reproducibility: same hash = same prompt inputs

### Adding New Providers

1. Create `adapters/llm/new_provider.py` implementing `LLMClient`
2. Add a case in the factory
3. Done. Zero changes to domain/, ports/, or other adapters.

---

## 6. Store Abstraction (Dual Mode)

### Protocol

JobStore protocol defines: save_job, get_job, list_jobs, job_exists, save_stage_a, save_stage_a_error, save_stage_b, save_stage_b_error, load_pending_stage_a, load_pending_stage_b, transition_status, get_status, record_llm_usage, record_pipeline_run, record_application, list_applications, save_resume_snapshot, get_resume_snapshot, diff_resume_snapshots, application_stats, save_ml_gate_result, connect, close.

### Two Adapters

| | PostgresStore | SQLiteStore |
|--|---------------|-------------|
| Connection | asyncpg connection pool | aiosqlite single file |
| Migration | Alembic | Embedded SQL (simplified) |
| JSON columns | JSONB + `->` operator | `json_extract()` |
| Concurrency | Multi-connection, row-level locks | WAL mode, single writer |
| Full-text search | tsvector + GIN index | FTS5 |
| Use case | Production, CI | Dev, offline, quick testing |

Phase 0 implementation note: only SQLite is wired. `db.backend != "sqlite"` is a fail-fast configuration error until the Postgres adapter is implemented. SQLite connections enable `PRAGMA foreign_keys = ON`, `connect()` is idempotent, and `close()` serializes with in-flight store operations before closing. `save_job` uses conflict-aware insert plus update inside one immediate transaction for race-safe `(platform, canonical_id)` upserts while preserving inserted/updated flags. The Phase 0 SQLite schema mirrors domain-required `url`, `location`, and `pipeline_runs.source` as NOT NULL, applies CHECK constraints for persisted enum/score fields, and records `created_at`/`updated_at` on evaluation rows.

### ATS Companies in Database

ATS company list lives in the `companies` table (not in config):

- slug, ats_vendor (auto-probed), last_verified_at, consecutive_failures
- Managed via CLI (`jobfeed companies add/list/remove`). Web UI management is **NEW** (current repo has CLI only, no Web companies API)
- Auto-probe detects Greenhouse/Ashby/Lever (current parity), caches with TTL
- ScanWorkflow reads from DB at runtime, not from config
- `bootstrap-companies` seeds from config/external lists for coverage expansion

Config only holds global ATS parameters: enabled, max_concurrent, probe_ttl_days, failure_threshold.

---

## 7. Temporal Workflows

### ScanWorkflow

Sources run with per-source concurrency policies (not blindly "all parallel"):

| Source | Concurrency | Reason |
|--------|------------|--------|
| LinkedIn Playwright | 1 (sequential URLs, with cooldown) | Anti-bot detection, Playwright main-thread-only, URL cooldown between searches |
| LinkedIn JobSpy | 1 | NEW source, TLS fingerprint risk |
| Indeed JobSpy | 1 (serial, after others) | Cloudflare throttling, non-deterministic GraphQL pagination |
| ATS companies | Parallel (max_concurrent from config) | Pure HTTP, thread-safe, no anti-bot |
| SpeedyApply | 1 | Single GitHub list fetch |

LinkedIn/ATS run concurrently with each other (different platforms). Indeed runs serial after others (current behavior). Within ATS, each company is an independent parallel unit. Total concurrency controlled by `scan.max_concurrent_sources` config.

Individual failures retry independently. DB stores all source rows (no cross-source dedup at scan time).

**ATS fetch resilience (degraded-vendor policy).** A vendor's public API can go
intermittently degraded without being down. Observed 2026-05-28: Ashby's
`posting-api` returned *valid* boards slowly (Notion 2.3 MB in 10.2s, Ramp
1.9 MB in 17.7s) while others exceeded 30s, and the set of succeeding slugs
flapped run-to-run. Non-existent slugs kept returning 404 in <100ms throughout —
ruling out IP-ban / rate-limit; this was upstream slowness, not a client problem.
The policy that follows:

- **Keep the per-fetch timeout generous (30s); do NOT shorten it to "fail fast."**
  A short timeout silently drops slow-but-valid large boards.
- **Retry transport errors (timeout/connect/read/DNS) once** at the `fetch_json`
  layer — a slug that times out one moment often answers on the next. HTTP status
  errors (404/410/5xx) and JSON-decode failures are never retried (definitive).
- **Per-slug concurrency** (the parallel ATS policy above) bounds wall-time so one
  hanging board can't serialize the batch. A *serial* fetch loop turned one
  degraded-Ashby scan into a 45-minute wall (150+ slugs × up-to-30s).

Scheduling: Temporal Schedule (when using TemporalRunner) replaces external cron.

See **Temporal Flow Detail** above for full workflow/activity diagrams.

### Execution Runners

Two runners, same business services underneath:

**InProcessRunner (default runner inside the containerized CLI)**
- `./bin/jobfeed scan` / `./bin/jobfeed evaluate` → host launcher enters `jobfeed-cli` container; the real `jobfeed` CLI calls services directly, in-process, blocking
- No Temporal dependency. In Phase 0 this needs only the CLI container + mounted SQLite data/config; in full-stack Docker it may use Postgres if config selects it
- Uses DB writes as coarse-grained resume boundary: re-running skips completed evaluations / upserts existing scan rows
- Debug: terminal stack trace, standard logging

**TemporalRunner (optional observable mode)**
- `./bin/jobfeed scan --runner temporal` → submits ScanWorkflow to Temporal server
- Web / Schedule → default Temporal
- Activity-level retry, backoff, timeout, resume after worker crash
- Workflow/activity status visible in Temporal UI
- Configurable as default: `[execution] default_runner = "temporal"` in config

Both runners emit the same PipelineRun / PipelineStep metrics to DB.

Host-native `uv run jobfeed ...` may invoke the same InProcessRunner for
developer debugging, but it is not the canonical user-facing runtime.

**Web triggers via Temporal** (async, non-blocking):

- `POST /api/runs/evaluate` → start EvaluateWorkflow → return 202 + workflow_id → frontend polls `/api/runs/{workflow_id}`

### Pipeline Metrics

Temporal provides execution visibility, but long-term pipeline optimization requires queryable metrics in DB.

**Per-stage metrics (stored in DB, queryable):**

- Scan: per-source duration, discovered count, new/upserted count, error count
- Enrich (LinkedIn): JD quality distribution, Tier 1/Tier 2 fallback count, detail-page cap hit count
- Freshness: scraped_at/posted_at distribution, freshness-filtered count
- Dedupe: twin cluster count, representative source distribution, repost count
- Hard filter: blocked count by reason
- ML gate: pass/fail/hard-fail count, score distribution, false negative audit hooks
- Stage A/B: latency, token usage, cost, cache hit rate, error rate by model

**Temporal search attributes (for workflow queries):**

run_id, source, stage, job_id, platform, model, jd_quality, ml_gate_result, verdict, error_type

**DB tables for metrics:**

- `pipeline_runs` — one row per scan/evaluate run
- `pipeline_steps` — one row per activity within a run
- `llm_usage` — per-LLM-call token/cost tracking
- `source_health_daily` — daily rollup of source success/failure rates

### Idempotency & Retry Contract

Temporal retries make duplicate writes and duplicate LLM calls more likely. Every activity must be safe to retry.

**LLM call idempotency (best-effort):**
- Before calling LLM, write `llm_requests(request_hash, status='started')` where `request_hash = hash(stage, job_id, prompt_hash, resume_hash, model)`
- If same hash already has `status='succeeded'`, return cached response (zero cost)
- After LLM call, update to `status='succeeded'` with response
- Crash window: if process dies between LLM response and DB write, retry will re-call LLM (small cost leak). This is accepted as best-effort — true exactly-once would require provider-side dedup which is not available

**DB write idempotency:**
- Scan activities: upsert by `(platform, canonical_id)` — safe to retry
- Evaluation writes: upsert by `(job_id, stage, prompt_hash, resume_hash, model)` — safe to retry
- Metrics: keyed by `(run_id, step_id, job_id, attempt)` — retries create new attempt rows, final aggregation picks last successful
- Application records: unique by `job_id` (parity: same job re-apply is no-op, preserves original audit row). Multiple application attempts per job is NOT current behavior

**Activity payload rule:**
- Activities write directly to DB, return only small summaries: `{count, error_summary, job_ids_sample}`
- Never return full JD text, resume content, or Stage B blocks through workflow history
- Temporal payload limit: 2MB default, 4MB gRPC max — large payloads will crash the workflow

### Temporal Flow Detail

Workflow = orchestration only (schedule activities, wait for results). Activity = real IO (scrape, LLM call, DB write). Large payloads stay in DB — workflows pass job_id/run_id only.

```
ScanWorkflow(run_id, config)
├── Activity: fetch_linkedin_playwright()    ─┐
│     discover cards + best-effort inline JD   │
│     enrich missing: Tier 1 → Tier 2 capped  │
├── Activity: fetch_indeed_jobspy()           ─┤
├── Activity: fetch_speedyapply()             ─┼─ all parallel
├── Activity: fetch_ats(company_a)            ─┤
├── Activity: fetch_ats(company_b)            ─┤
├── Activity: fetch_ats(company_c ... N)      ─┘
│     each activity upserts jobs + writes source metrics
└── record pipeline_run finished

EvaluateWorkflow(run_id, options)
├── Activity: select_candidates()
├── per job (parallel, max_concurrent):
│   ├── Activity: run_ml_gate(job_id)
│   │     → fail? persist, skip
│   ├── Activity: run_stage_a(job_id)
│   │     → below threshold? persist, skip
│   └── Activity: run_stage_b(job_id)
│         → persist full evaluation
└── record pipeline_run finished
```

---

## 8. JobSource Abstraction

### Protocol — Two Tiers

Not all sources are equal. JobSpy and ATS return full JD inline in one call. LinkedIn Playwright requires a multi-phase lifecycle with session management, cookies, anti-bot delays, and tiered fallback.

```python
class SimpleSource(Protocol):
    """Sources where fetch = discover + JD in one call (JobSpy, ATS, SpeedyApply)."""
    async def fetch_jobs(self, config: SourceConfig) -> list[JobPosting]: ...

class SessionSource(Protocol):
    """Sources that need expensive session setup and multi-phase scraping."""
    async def discover(self, config: SourceConfig) -> DiscoverResult: ...
    async def enrich_session(self) -> AsyncContextManager[EnrichSession]: ...
```

### LinkedIn Playwright Behavioral Contract

LinkedIn Playwright is a SessionSource with a three-layer scraping model:

```
discover(search_urls):
  per URL (can parallel across URLs):
    scroll search results → collect cards
    per card: best-effort inline JD from search pane
  return postings (some with jd_text, some without)

batch enrich(postings, session):
  sequential within one enrich_session (shared Playwright context)
  per posting:
    if discover-inline JD is fresh + quality >= good:
      skip (cached-fresh)
    else:
      Tier 1: search-pane re-fetch
      if Tier 1 quality >= good:
        return
      else:
        Tier 2: detail-page backup (capped, anti-bot limited)
```

Key constraints: discover produces optional JD. Enrich only fills gaps (missing/low-quality/expired). Enrich is sequential within a session. Tier 2 is a capped fallback, not unlimited retry.

### Five Source Types

| Source | Type | Implementation | Notes |
|--------|------|---------------|-------|
| LinkedIn (Playwright) | SessionSource | Login + Playwright browser | Three-layer: discover with inline JD → enrich Tier 1/2 fallback |
| LinkedIn (JobSpy) | SimpleSource | JobSpy, no login | **NEW** (not current parity). Single call, less data but simpler |
| Indeed (JobSpy) | SimpleSource | JobSpy, TLS-client | Cloudflare bypass via GraphQL API |
| ATS | SimpleSource | httpx, public API per company | Auto-probe supports Greenhouse/Ashby/Lever only (current parity) |
| SpeedyApply | SimpleSource | httpx, GitHub markdown list | Scrapes curated job lists, routes to various ATS vendors (GH/Ashby/Lever/Workday/SmartRecruiters/iCIMS) for JD |

### JobSpy Integration

Shared internal module `adapters/sources/_jobspy.py`:
- Encapsulates all JobSpy interaction (lazy import, DataFrame → JobPosting conversion)
- Handles pandas-specific messiness: NaN coercion, date parsing, URL fallback
- Lazy imports `jobspy` + `pandas` — not loaded when only Playwright/ATS paths run

Per-platform adapters are thin shells: `indeed_jobspy.py` and `linkedin_jobspy.py` configure search params and call `_jobspy.scrape()`. Each adapter only knows "what to search", the shared module handles "how to talk to JobSpy".

Boundary: JobSpy's DataFrame, pandas types, and tls-client never leak past `_jobspy.py`. Domain layer sees only `list[JobPosting]`.

### Adding New Sources

1. Create `adapters/sources/glassdoor.py` implementing `SimpleSource` or `SessionSource`
2. If JobSpy-backed: thin shell calling `_jobspy.scrape(site_name="glassdoor", ...)`
3. If custom scraper: implement `fetch_jobs()` independently
4. Add `[sources.glassdoor]` section in config.toml
5. ScanWorkflow auto-discovers enabled sources from config and schedules them
6. Zero changes to domain/, ports/, or other sources

---

## 9. Config & Dependency Injection

### Single Config File

Two user-local config files at `~/.jobfeed/` (NOT committed to git). Repo ships example templates.

**Phase override:** early implementation phases may deliberately use repo-local
development paths for safety. Phase 0 Walking Skeleton defaults to
`.jobfeed-dev/` and must not read or write `~/.jobfeed/` unless a human
explicitly passes a real user config path. The `~/.jobfeed/` paths below are the
steady-state/cutover defaults, not the Phase 0 safety defaults.

**`config.toml`** — system configuration (platforms, DB, LLM backends, thresholds):


```toml
[db]
backend = "postgres"
url = "postgresql://localhost:5432/jobfeed"
sqlite_path = "~/.jobfeed/dev.db"

[llm]
stage_a = "anthropic/claude-haiku-4-5"
stage_b = "openai/gpt-4.1"
max_concurrent = 4
timeout_s = 180

[sources.linkedin_playwright]
enabled = true
search_urls = [...]

[sources.linkedin_jobspy]
enabled = true
search_queries = [...]

[sources.indeed]
enabled = true
search_queries = [...]

[sources.ats]
enabled = true
max_concurrent = 10
probe_ttl_days = 7
failure_threshold = 3

[temporal]
host = "localhost:7233"
namespace = "jobfeed"

[observability]
log_level = "info"
log_format = "json"
otel_endpoint = "http://localhost:4317"

[sources.speedyapply]
enabled = true

[scoring]
stage_a_threshold = 60
ml_gate_enabled = true
max_daily_score_calls = 150

[scan]
max_concurrent_sources = 10

[execution]
default_runner = "in_process"    # or "temporal"
```

**`preferences.yml`** — hard filter rules and personal preferences:

```yaml
hard_filters:
  title_blocklist: ["Senior", "Staff", "Lead", "Manager", "Director", "Principal"]
  company_blocklist: ["company-x"]
  location_allowlist: ["Remote", "San Francisco", "New York"]
  posted_within_days: 10
  big_company_days: 14
  big_company_list: ["google", "meta", "amazon", "apple", "microsoft"]
```

**Legacy config import**: migration plan (Section 19) must also import `~/.jobfeed/config.toml` and `~/.jobfeed/preferences.yml` from existing install, preserving hard_filters, big_company_list, big_company_days, and max_daily_score_calls.

Secrets in environment variables only (`.env`, gitignored):

```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
SENTRY_DSN=https://...
DATABASE_PASSWORD=...
```

Load priority: env vars > .env > config.toml > defaults.

Environment switching: `~/.jobfeed/config.toml` (steady-state user config) or `--config /path/to/other.toml` to override. In Docker-first runtime, user config and data paths must be mounted into the container at stable container paths; the host launcher is responsible for using the Compose profile that provides those mounts. Repo only contains example templates such as `config.example.toml` and Docker-specific examples. Phase 0's `load_settings()` uses repo-local defaults only when no config path is supplied; an explicit missing `--config` path is a user error.

### Dependency Injection

No DI framework. One factory function wires everything:

```
create_app(settings)
├── build store adapter (Phase 0: SQLite only; later PG or SQLite based on config)
├── build LLM clients (per-stage, based on provider/model spec)
├── build enabled source adapters (from config)
├── build services with above adapters
└── inject into CLI / Web / Temporal worker
```

Three entry points share the same factory:

| Entry | How it gets dependencies |
|-------|--------------------------|
| CLI | Host launcher (`bin/jobfeed` / `bin/jobfeed.ps1`) enters Docker; containerized `cli/__init__.py` calls `create_app()`, stores services in Click context |
| Web | `web/app.py` app factory calls `create_app()`, injects via FastAPI `Depends()` |
| Temporal | Worker startup calls `create_app()`, activities access services from worker context |

---

## 10. Observability

### Three Pillars

**Structured Logging (structlog)**
- JSON format in production, human-readable in dev
- Human logs use TTY-aware color rendering so redirected output is not polluted with ANSI escapes
- request_id propagated across entire request lifecycle
- Key fields on every log: event, job_id, source, model, latency_ms, error

**Error Tracking (Sentry)**
- Automatic exception capture with stack traces
- Frequency, impact, and grouping
- Free tier SaaS, zero infrastructure

**Distributed Tracing (OpenTelemetry)**
- Traces across scan → evaluate → digest pipeline
- Per-step latency breakdown (ML gate: 2s, LLM API: 25s, DB write: 3s)
- Visualized in Jaeger
- Metrics (counters, histograms) exported to Prometheus, dashboarded in Grafana

### Business Metrics vs Operational Metrics

| | Business Metrics | Operational Metrics |
|--|-----------------|---------------------|
| Who looks | You, analyzing cost and effectiveness | Ops/oncall, monitoring system health |
| Where stored | PostgreSQL (LLMUsage, PipelineRun tables) | Prometheus (time-series, auto-expires) |
| Examples | Daily LLM spend $2.3, funnel conversion 15% | P99 latency 3.2s, error rate 0.5% |
| In code | Domain models + DB persistence | OTel instrumentation in adapter layer |

### Infrastructure

Three additional Docker containers: Jaeger, Prometheus, Grafana.

Total Docker Compose: 8 containers (PG, Temporal, Temporal UI, Backend, Frontend, Jaeger, Prometheus, Grafana).

---

## 11. Testing Strategy

### Five Test Layers

**Layer 1: Unit Tests (`tests/unit/`)**
- Tests domain layer pure functions, zero IO, no mocks
- Filtering rules, LLM response parsing (valid + malformed: truncated JSON, markdown-wrapped, refusal, 429, token exceeded), ML gate classification + reproducibility (fixed seed), digest rendering
- Coverage target: >= 90%
- Must complete in < 5 seconds (enforced in CI)

**Layer 2: Integration Tests (`tests/integration/`)**
- Tests adapter interaction with real external systems
- PostgresStore / SQLiteStore: real DB, CRUD + migration + concurrent write tests
- LLM adapters: mock HTTP, verify request construction + response parsing + error handling
- Sources: mock HTTP, verify HTML/JSON parsing
- Security: API parameter injection, request body size limits
- Observability: verify structlog output contains required fields (job_id, request_id)
- Coverage target: >= 70%

**Layer 3: E2E Tests (`tests/e2e/`)**
- Full pipeline with MockLLM + MockSource + real DB
- CLI: scan → evaluate → digest flow
- Web: TestClient API calls, verify response format + error states
- Frontend: loading/error states, API client error paths (401/500/timeout)
- Temporal: workflow execution + activity failure retry

**Layer 4: Contract Tests (`tests/contract/`)**
- Frontend-backend type sync: pydantic2ts generates TS types from Pydantic models, diffs against `types.ts`, fails if mismatched
- LLM API contract: pact-python records provider response schemas, detects API changes

**Layer 5: Mutation Tests (`tests/mutation/`)**
- mutmut modifies domain code (e.g., `>` to `>=`, `True` to `False`)
- Verifies unit tests catch the mutations
- Domain layer only, incremental on PRs

### Test Tools

| Tool | Purpose | Priority |
|------|---------|----------|
| pytest + pytest-asyncio | Test framework + async support | Required |
| pytest-cov + coverage | Coverage measurement + gate | Required |
| pytest-xdist | Parallel unit test execution | Recommended |
| testcontainers-python | Auto-start PG container in CI | Required |
| respx | Mock httpx requests | Required |
| factory-boy | Test data construction | Required |
| MockLLM / MockSource | First-class adapters for testing + dev | Required |
| pydantic2ts | Frontend-backend contract | Required |
| pact-python | LLM API contract | Recommended |
| mutmut | Mutation testing | Recommended |

### Mock Adapters as First-Class Citizens

MockLLM and MockSource live in `adapters/`, not in `tests/`. They are usable in development (`LLM_STAGE_A=mock/instant`) to run the full pipeline without spending API money.

---

## 12. CI/CD Pipeline

### Static Analysis & Quality Gate

| Tool | Purpose | Priority |
|------|---------|----------|
| ruff | Lint + import sort (replaces flake8 + isort) | Required |
| mypy --strict | Type checking | Required |
| bandit | Security scanning (SQL injection, hardcoded secrets) | Required |

All tool configuration centralized in `pyproject.toml`.

### Pipeline Structure

```
on: [push, pull_request]
│
├── job: quality-gate                              (~30s, no dependencies)
│   ├── ruff check .
│   ├── ruff format --check
│   ├── mypy src/
│   └── bandit -r src/
│
├── job: unit-tests           (needs: quality-gate, no Docker)
│   ├── pytest tests/unit/ -x --cov=src/jobfeed/domain
│   ├── timeout 5s performance baseline
│   └── upload coverage artifact
│
├── job: integration-tests    (needs: quality-gate, parallel with unit)
│   ├── services: postgres via testcontainers
│   ├── pytest tests/integration/ --cov=src/jobfeed/adapters
│   └── upload coverage artifact
│
├── job: frontend             (needs: quality-gate, parallel with unit)
│   ├── eslint + tsc --noEmit
│   ├── vitest --coverage
│   └── vite build
│
├── job: contract-check       (needs: quality-gate)
│   └── pydantic2ts generate → diff types.ts → fail if different
│
├── job: e2e-tests            (needs: unit + integration + frontend)
│   ├── docker compose up
│   └── pytest tests/e2e/ -v
│
├── job: mutation-tests       (needs: unit-tests, PR only)
│   └── mutmut run --paths-to-mutate=src/jobfeed/domain/
│
├── job: llm-review           (needs: quality-gate, PR only, advisory)
│   ├── Codex CLI review → post PR comment
│   └── Claude Code review → post PR comment
│
└── job: coverage-report      (needs: all test jobs)
    ├── merge coverage artifacts
    ├── fail if < 80% overall
    └── post PR comment with coverage diff
```

### Key Design Decisions

- quality-gate fails → no Docker startup wasted
- unit fails → no E2E run
- lint/unit/integration/frontend run in parallel where possible
- E2E runs last (slowest, most expensive)
- LLM review is advisory (non-blocking) — avoids false-positive blocking merges
- Coverage gate is hard — merge blocked below 80%
- PR branch protection: all hard-gate jobs must pass

### Dependency Caching

- pip: `actions/cache` keyed on `hashFiles('pyproject.toml')`
- Docker layers: `docker/setup-buildx-action` + buildx cache
- node_modules: `actions/cache` keyed on `hashFiles('web-ui/package-lock.json')`

---

## 13. Docker Compose

Docker Compose is the canonical runtime for production-parity execution.
User-facing CLI commands enter Compose through host launchers.

### Services

Full-stack mode has 8 long-running containers plus one one-shot CLI service:

| Container | Image | Purpose |
|-----------|-------|---------|
| jobfeed-cli | ./Dockerfile | One-shot user-facing CLI service used by `bin/jobfeed` / `bin/jobfeed.ps1` |
| postgres | postgres:16 | Application DB + Temporal persistence (separate databases on same instance) |
| temporal | temporalio/auto-setup | Workflow engine (uses postgres for persistence, NOT Redis) |
| temporal-ui | temporalio/ui | Workflow visualization |
| backend | ./Dockerfile | Python FastAPI + Temporal worker |
| frontend | ./web-ui/ | React dev server (dev) or nginx (prod) |
| jaeger | jaegertracing/all-in-one | Trace visualization |
| prometheus | prom/prometheus | Metrics collection |
| grafana | grafana/grafana | Dashboards |

Note: Redis removed. Temporal uses PostgreSQL for persistence (official supported backend). No separate Redis needed.

### Dev Override

`docker-compose.dev.yml` overrides for local development:
- The canonical CLI remains containerized through `jobfeed-cli`
- Backend and frontend may run on host for hot reload as a developer fallback
- Infrastructure services (PG, Temporal, observability) run in Docker
- Volume mounts for persistent data and repo-local development state

### One Command Start

```bash
# User-facing CLI (canonical)
./bin/jobfeed scan
./bin/jobfeed evaluate
./bin/jobfeed digest

# Full stack (production-like)
docker compose up

# Dev mode (infra in Docker, optional app hot reload on host)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
make dev  # starts backend + frontend on host
```

---

## 14. ML Gate Subsystem

ML Gate is an independent subsystem, not just a result field. It is a cheap local pre-filter that runs before expensive LLM Stage A/B calls to control cost.

### Pipeline

```
title + company + location + jd_text + jd_quality
  → Feature Extraction (regex-based, 9 categories)
  → Sentence-Transformer Embedding (384-dim)
  → Feature Vectorization (STRUCTURED_DIM=66 + 384-dim embedding = 450-dim total)
  → XGBoost Classifier
  → Threshold Decision
  → Persisted Result
```

### Feature Categories

Rule-based extraction from title + JD text (vector layout):
- [0:5] seniority_level one-hot (entry/mid/senior/lead/unknown) — 5 dims
- [5:9] degree_required one-hot (phd/masters/bachelors/none) — 4 dims
- [9] clearance_required — 1 dim
- [10] school_restricted — 1 dim
- [11:16] role_type one-hot (intern/coop/new_grad/fte/contract) — 5 dims
- [16] yoe_min normalized (yoe/10, capped at 1.0) — 1 dim
- [17:26] domain_tags binary (embedded, robotics, aerospace, hardware, trading, manufacturing, security, mobile, gamedev) — 9 dims
- [26:65] tech_required binary (39 techs: c++, c#, typescript, javascript, python, java, rust, go, ruby, scala, kotlin, swift, c, sql, postgresql, mysql, mongodb, redis, kafka, elasticsearch, docker, kubernetes, aws, gcp, azure, terraform, react, node, django, flask, spring, pytorch, tensorflow, cuda, spark, flink, airflow, graphql, grpc) — 39 dims
- [65] is_swe_role — 1 dim

Total structured: 66 dims. Followed by 384-dim embedding = 450-dim input vector.

### Model Lifecycle

- Versioned local artifacts: `~/.jobfeed/ml_gate_models/v*.json` + `v*.meta.json`
- Metadata includes: feature importance, threshold calibration, training set stats
- `jobfeed ml-gate train` — retrain from user-labeled data
- `jobfeed ml-gate info` — show current model version, threshold, feature importance

### Output (parity field names)

- `ml_gate_score` (float) — predicted probability
- `ml_gate_result` (string: "pass" / "fail") — decision. Hard fails also write "fail"; distinguished by `ml_gate_hard_fail_reason` being non-null
- `ml_gate_at` (timestamp) — when gate ran
- `ml_gate_version` (string) — model version used
- `ml_gate_hard_fail_reason` (string, nullable) — why hard-fail rules triggered (yoe, clearance, not SWE, etc.)

### Persistence

Gate results stored per job with the exact field names above. This enables:
- Explaining why a job was filtered before LLM scoring
- Retraining with updated labels
- Auditing false negatives

### Fallback

If model file is missing, pipeline does not crash. Falls through to Stage A (rules-only fallback or unconditional pass, depending on config).

---

## 15. Web UI Feature Parity Contract

The Web UI is not a simple jobs table. The following product semantics must be preserved:

### Page Structure

- `/today` — triage queue: new/scored/shortlisted, deduped, hard-filtered, fresh, sorted by verdict
- `/all` — all postings with tabs: All, Scored, Inconsider (shortlisted), Skipped (archived), Pending JD
- `/applied` — application pipeline: applied + all interview stages + terminal statuses
- `/followups` — jobs with upcoming next_followup_at
- `/stats` — analytics dashboard

### Key Product Semantics

- **Today** is a triage queue, not "all jobs from today". It combines status, dedupe, hard filters, freshness, and verdict to produce an actionable queue. Action on a job auto-advances to the next item.
- **Pending JD** is not a status tab. It filters for: missing/abandoned JD quality, no Stage A score, excludes archived/ignored, applies hard filters + freshness. It is the "I need to manually paste JD" work queue.
- **Dedupe representative**: UI folds twins by company_norm + title_norm, selects a representative. This affects which row is visible, how Skip/Ignore propagate, and whether a twin cluster appears in queues.
- **Bulk actions** cascade to twin clusters and return succeeded/skipped/failed/cascaded counts.
- **Job detail drawer**: status actions, sibling/twin links ("also seen on" with platform + status), Stage A/B/final scores, Stage B content (one-line, strengths, gaps, cover-letter hooks, supporting points, avoid mentioning), notes, followup picker, manual JD paste card.
- **Search / sort / pagination**, tab counts sync with current filters.
- **Selection UX**: single-page select, select-all-matching, bulk action feedback.
- **Stats views**: KPI cards, Sankey funnel, Pipeline donut, Daily timeline, By-resume-variant table.

---

## 16. CLI Parity Contract

All commands from the current repo are preserved except `upgrade-blocks` (stale).

| Command | Status | Notes |
|---------|--------|-------|
| init | Keep | First-run setup: directories, DB, config/templates |
| login \<platform\> | Keep | LinkedIn/Indeed cookie/session prerequisite |
| scan | Keep | Core entry point |
| evaluate | Keep | Core entry point |
| digest | Keep | Daily report/export, not just UI duplicate |
| mark | Keep | Status machine canonical CLI: bulk, restore, force, resume variant, note |
| list | Keep | Scriptable query/admin surface, JSON/Markdown output |
| note | Keep | Lightweight workflow entry; resets auto-ghost clock |
| followup | Keep | Follow-up workflow CLI entry |
| archive | Keep as alias | Sugar for `mark <id> archived`, no independent business logic |
| stats | Keep | CLI analytics, especially --by-resume |
| apply | Keep | Audit trail core (transactional snapshot) |
| apply-history | Keep | Audit trail query |
| enrich-paste | Keep, redesign | Manual JD fallback; accept job_id, support all platforms |
| companies add/list/remove | Keep | ATS source management |
| bootstrap-companies | Keep | Coverage expansion, not a temporary script |
| snapshots list/show/diff | Keep | Resume audit trail |
| serve | Keep | Web UI entry point |
| ml-gate train/info | Keep | ML gate lifecycle |
| upgrade-blocks | **Drop** | Stale: Stage B now always runs full blocks; implementation references undefined `b_model` |

### CLI Options Contract

Key flags that must be preserved (not just command names):

Canonical invocation is through the host launcher:

```bash
./bin/jobfeed <command> [flags]
```

The launcher forwards all command names and flags unchanged to the
containerized `jobfeed` CLI.

**scan:**
- `--linkedin-only` / `--indeed-only` / `--ats` / `--speedyapply-only` — source selectors
- `--ats-workers N` — ATS parallel concurrency
- `--max-jobs N` — per-source cap
- `--headed` — Playwright visible browser (debug)
- `--runner temporal` — use TemporalRunner instead of in-process

**evaluate:**
- `--corpus unrated|all|failed` — which jobs to evaluate (default: unrated = skip already evaluated)
- `--stage a|b|both` — run only Stage A, only Stage B, or both
- `--max-days N` — freshness filter on scraped_at
- `--parallel N` / `-j N` — LLM concurrency (default 4)
- `--dry-run` — show what would be evaluated without calling LLM
- `--runner temporal` — use TemporalRunner

**mark:**
- `--force` — bypass transition graph
- `--bulk` — mark multiple job IDs
- `--restore` — restore from archived
- `--resume-variant NAME` — associate resume variant with status change
- `--note TEXT` — attach note to transition

**apply:**
- `--tailored PATH` — tailored resume to snapshot
- `--cover-letter PATH` — cover letter to snapshot
- `--variant NAME` — resume variant name

**stats:**
- `--by-resume` — breakdown by resume variant
- `--window N` — days to look back

---

## 17. Digest Rendering Contract

`jobfeed digest` renders a daily Markdown summary to terminal + `~/.jobfeed/today.md`. It is not a simple report — it has specific rendering sections and filtering logic.

### Sections (in order)

1. **Header** — today's date
2. **Workflow attention** — follow_up_today, interview_prep, going_ghosted (if any)
3. **Pipeline health** — enrich errors, low_quality_scored (if any). Note: source failures in pipeline health is **NEW**, not current parity
4. **Apply tier** — full body: score, title+company, verdict reasoning, strengths, gaps, tags, URL. Only jobs with verdict=apply. Split into "new since last digest" and "previously seen" using cutoff from previous digest file mtime.
5. **Consider tier** — short one-liners. Jobs with verdict=consider.
6. **Skip tier** — collapsed count only.
7. **Stats summary** — total jobs, scraped today, LLM calls today, Stage B evaluated count, filtered count. Note: response rate belongs to `jobfeed stats` CLI, not digest

### Filtering

- Renders current full evaluated set (all jobs meeting threshold), NOT only "since last digest"
- Previous digest cutoff (from digest file mtime, not state table) only splits the Apply tier into new vs previously seen; cutoff and compared discovery timestamps must be timezone-aware
- Respects hard filters from `preferences.yml`
- Uses two-stage evaluation results (not legacy single-pass scores)
- No state update on render — digest is a read-only report

### Output

- Terminal: human-readable Markdown
- File: `~/.jobfeed/digests/YYYY-MM-DD.md` (dated) + `~/.jobfeed/today.md` (symlink/copy)

---

## 18. Security / Privacy Model

### Auth model

- **Local mode (default):** no auth. Web server binds `127.0.0.1`. This is a single-user local tool.
- **Network mode (0.0.0.0):** warning + requires auth token. Not current parity but must not be accidentally exposed without auth.

### Data sensitivity

| Data | Protection |
|------|-----------|
| Resume / cover letter | Never logged, never sent to Sentry, never in OTel traces |
| LLM prompts/responses | Not sent to error tracking. Provider-side retention depends on API ToS. |
| LinkedIn/Indeed cookies | File-permission protected (`chmod 600`), not committed to git |
| preamble_personal.md | User-local, not committed, not logged |
| API keys | Env vars only, never in config.toml, never logged |

### Subprocess adapters

ClaudeCliLLM and CodexCliLLM run external processes. Policy:
- Working directory = temp dir or repo root (not `~/.jobfeed/`)
- No access to cookies, API keys, or other secrets beyond what's in env vars
- Subprocess stderr captured for error reporting but not persisted in full

---

## 19. Legacy SQLite Migration Plan

Existing SQLite v16 migration is a first-class release requirement. The rewrite ships an importer and verifier before replacing the old runtime.

### Rules

1. **Source of truth**: existing `~/.jobfeed/jobs.db` at schema v16. New system must read it. Lower versions must first be migrated to v16 by the old code before import.

2. **Alembic for new schema, importer for old data**: Alembic creates the new PG schema. A separate importer moves data: `jobfeed migrate import-sqlite --from ~/.jobfeed/jobs.db --to postgres://...`. SQLite fallback adapter uses the same domain/import mapping — no duplicate logic.

3. **Tables to preserve**: jobs, evaluations, job_status, job_status_history, applied, resume_snapshots, companies, cost_ledger, state, ML gate columns / model metadata references.

4. **No historical recomputation**: no re-evaluate, no re-dedupe, no re-hash prompts, no applying new rules to old statuses. Applied audit rows, resume snapshot content, Stage B JSON snapshots preserved byte-for-byte where possible.

5. **Auto-backup before import**: copy old DB to `~/.jobfeed/backups/jobs.db.pre-migration-YYYYMMDD-HHMMSS`. Import failure must not corrupt original DB.

6. **Validation report** after import:
   - Row count match per core table
   - Every jobs.id preserved or has legacy_id → new_id mapping
   - Every applied.job_id resolves to a job
   - Every resume_snapshots.resume_hash content matches
   - Status enum has no illegal values
   - Stage B JSON parseable
   - company_norm / title_norm present
   - Sample N rows checksum verification

7. **ID strategy**: preserve legacy job IDs in new DB to avoid foreign key remapping complexity across evaluations/status/history/applied.

8. **Config/preferences migration**: import `~/.jobfeed/config.toml` and `~/.jobfeed/preferences.yml` from existing install. Key mapping from old → new config structure:

   | Old key | New key |
   |---------|---------|
   | `[general].master_resume_path` | `[app].master_resume_path` |
   | `[general].max_daily_score_calls` | `[scoring].max_daily_score_calls` |
   | `[platforms.linkedin].*` | `[sources.linkedin_playwright].*` |
   | `[platforms.indeed].*` | `[sources.indeed].*` |
   | `[platforms.speedyapply].*` | `[sources.speedyapply].*` |
   | `[anthropic].backend` = api | `[llm].stage_a` = `anthropic/...` (Anthropic SDK adapter) |
   | `[anthropic].backend` = claude-code | `[llm].stage_a` = `claude-cli/...` (ClaudeCliLLM adapter) |
   | `[anthropic].backend` = codex | `[llm].stage_a` = `codex-cli/...` (CodexCliLLM adapter) |
   | `[anthropic].model` | model portion of `[llm].stage_a` |
   | `[anthropic].stage_b_model` | model portion of `[llm].stage_b` |

   **Critical:** `[anthropic].backend` maps to LLM adapter selection, NOT to `[execution].default_runner`. These are orthogonal: backend = how LLM is called, runner = how pipeline is orchestrated. `[execution].default_runner` is new and always defaults to `"in_process"`. It must not be inferred from old `[anthropic].backend`.

   Also preserve: `preferences.yml` hard_filters (title_blocklist, company_blocklist, location rules, big_company_list, big_company_days, posted_within_days).

9. **Migration commands**:
   - `jobfeed migrate inspect-sqlite ~/.jobfeed/jobs.db` — show schema version, row counts, health check
   - `jobfeed migrate import-sqlite --dry-run` — simulate without writing
   - `jobfeed migrate import-sqlite --backup --verify` — full import with backup and validation
