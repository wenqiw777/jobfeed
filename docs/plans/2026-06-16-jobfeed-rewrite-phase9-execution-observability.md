# Phase 9: In-Process Pipeline Execution + Observability — Design & Implementation Plan (merged)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement Part B task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the Phase 8 read-only Runs zone gap: web-triggered scan/evaluate with live progress, backed by in-process async execution (no Temporal). Add distributed tracing (OTel SDK + Jaeger), error tracking (Sentry), Postgres-based step timing metrics, and a new Performance zone (7th web zone, 11 recharts panels) for critical-path analysis. After Phase 9, the user can trigger, monitor, and diagnose pipeline runs entirely from the browser.

**Architecture:** Hexagonal, same as Phases 0–8. New `services/run_manager.py` (async task lifecycle + concurrency locks + progress broadcast). OTel instrumentation in `observability.py` (SDK init + auto-instrumentation + structlog bridge); Sentry init alongside it. New `step_timings` Postgres table for per-step duration metrics. New `web/routes/performance.py` + `services/performance.py` for the Performance zone backend. Frontend extends Runs zone (trigger + SSE) and adds Performance zone (recharts panels over existing data).

**Tech Stack additions:** `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc`, `opentelemetry-instrumentation-httpx`, `opentelemetry-instrumentation-asyncpg`, `opentelemetry-instrumentation-fastapi`, `sentry-sdk[fastapi]`. Docker: `jaegertracing/all-in-one` (Badger disk persistence). Frontend: no new deps (recharts already in Phase 8).

**Implementation repo:** `/Users/wenqiwang/wwq/jobfeed` (NOT legacy `job-apply`). Bare paths below are rooted there. Mirror a copy into `docs/plans/` after saving.
**Commit strategy:** one conventional commit per task, no AI attribution. Two PR slices: **PR-A = Tasks 0–7 (backend)**, **PR-B = Tasks 8–9 (frontend)**.
**Execution mode:** Sequential.

> **History:** Designed 2026-06-16 via brainstorm. Key user decisions: no Temporal (in-process async + crontab), no Prometheus/Grafana (Postgres metrics + Performance zone), OTel SDK + Jaeger all-in-one (Badger), Sentry free tier. Performance zone = 7th web zone with 11 panels, all user-confirmed. Phase 8 A5 deferred items reviewed — none pulled into Phase 9 scope.

---

# PART A — DESIGN

## A1. Positioning

Phases 0–8 built the full pipeline, status/apply, CLI parity, and the web UI (six-zone local app with read-only Runs zone). Phase 9 is the **execution + observability** slice: the web app gains pipeline triggering with live progress, the codebase gains distributed tracing and error tracking, and a new Performance zone surfaces critical-path metrics. After this phase, the user can trigger, monitor, and diagnose pipeline runs entirely from the browser. Running the pipeline via CLI continues to work unchanged.

## A2. Current State (verified in code 2026-06-16)

**Already implemented — REUSE, do not recreate:**
- Phase 8 web UI merged (PR #17, commit `0e547a4`): FastAPI thin shell (`web/app.py`, 204 lines), 7 route modules (runs/jobs/workflow/applications/insights/companies/health, totaling ~1010 lines), `web/deps.py` (128 lines) for DI. React SPA in `web-ui/` with 6 zones (Triage/Pipeline/Library/Insights/Runs/Sources), recharts for Insights charts (`insights.tsx` + `StatusDonut/SankeyFunnel/DailyTimeline` components). `cli/serve.py` binds `127.0.0.1:7654`.
- `ScanService.run(sources) -> PipelineRun` (`services/scan.py`, 183 lines): async, `asyncio.gather` over sources, `bind_run_id`, records run on completion. No progress callback.
- `EvaluateService.run(stage, corpus, limit, max_days, dry_run) -> PipelineRun` (`services/evaluate.py`, 279 lines): async, auto_decay → funnel → stage_a → stage_b, semaphore concurrency, budget tracking. No progress callback.
- `PipelineRun` (`domain/models.py:184`): flat counters dataclass (run_id, started_at, source, jobs_discovered/inserted/updated/filtered/ml_gated, stage_a_scored/stage_b_scored/jobs_scored, total_llm_cost_usd, errors, finished_at, dry_run_preview). **No `status` field.**
- `start_pipeline_run(source) -> PipelineRun` (`services/runs.py`, 27 lines): UUID + UTC start.
- `record_pipeline_run` / `get_pipeline_run` / `list_pipeline_runs` (`ports/store.py:218,226`, `ports/store_views.py:81`): persistence + query ports, implemented in `postgres.py`.
- Runs zone (`web/routes/runs.py`, 71 lines): `GET /api/runs` + `GET /api/runs/{run_id}`, **read-only**. Frontend `runs.tsx` shows "Runs are read-only here; triggering scans from this page arrives with Phase 9."
- `observability.py` (130 lines): structlog config with context vars, `JobfeedLogger` protocol, `bind_run_id()` for run_id context propagation, `configure_logging(log_level, log_format)`. Human/JSON output modes. **No OTel, no Sentry.**
- `ObservabilitySettings` (`config.py:145`): `log_level: str = "info"`, `log_format: Literal["human","json"] = "human"`. Only two fields.
- `ExecutionSettings` (`config.py:137`): `default_runner: str = "in_process"` — placeholder, unused.
- `ServiceErrorHandler` (`services/error_handler.py`): centralized error logging + run counter increment.
- `LLMUsage` (`domain/models_llm.py`): per-call model, input_tokens, output_tokens, cost_usd, latency_ms, cached, timestamp, job_id, stage, run_id. Persisted via `record_llm_usage`.
- `cost_entries` table: day, spent_usd, calls. Used by budget tracking.
- Docker compose (`docker-compose.yml`): `postgres:16` + `jobfeed-cli` (prebuilt GHCR image). Named volumes `pgdata` + `mlcache`. **No Jaeger or worker container.**
- Alembic migrations: `0001` through `0006` (latest: `0006_phase6_status_apply.py`). Next = `0007`.
- `create_app(config_path) -> AppContext` (`cli/__init__.py`): DI wiring for CLI. Web app factory `create_web_app` (`web/app.py`) wraps the same path.
- ML gate inference is synchronous CPU-bound (`adapters/ml/xgboost_gate.py`): `predict_batch` runs extract → embed → predict in the calling coroutine. **No `to_thread` offload.**

**Net-new (CREATE):** `services/run_manager.py` (async task lifecycle + concurrency + progress broadcast); `services/performance.py` (metrics aggregation); `services/_timing.py` (step timing context manager + recording); `domain/models_perf.py` (StepTiming + performance query/response models); `ports/store_perf.py` (step timing + performance query ports); `web/routes/performance.py`; `web/schemas/performance.py`; Alembic migration `0007` (PipelineRun.status + step_timings table); `observability.py` extensions (OTel init + Sentry init + structlog bridge); `config.py` extensions (OTelSettings + SentrySettings); `docker-compose.yml` Jaeger service; frontend `web-ui/src/routes/performance.tsx` + `web-ui/src/components/performance/*` + `web-ui/src/lib/use-sse.ts`; Runs zone trigger UI extensions.

## A3. Decisions (all user-confirmed during brainstorm)

- **D1 — In-process async, not Temporal.** `asyncio.create_task()` inside the serve process. ScanService/EvaluateService are already async. `asyncio.Lock` enforces max 1 concurrent scan + 1 concurrent evaluate; concurrent trigger returns 409. Temporal's durable execution is unnecessary: scan upserts and evaluate's `load_pending` filtering make crash recovery = re-trigger. Scheduled runs use system crontab/launchd, not an internal scheduler.

- **D2 — RunManager service owns background task lifecycle.** New `services/run_manager.py`: `trigger_scan(source, config) → run_id`, `trigger_evaluate(params) → run_id`, `get_active_runs() → list[ActiveRun]`, `subscribe(run_id) → AsyncIterator[ProgressEvent]`. Creates PipelineRun (status=running), spawns `asyncio.Task`, catches exceptions → `status=failed`, records PipelineRun on completion. CLI path stays unchanged (direct service calls, no RunManager).

- **D3 — PipelineRun gains `status` field.** Values: `pending | running | succeeded | failed`. Alembic migration adds nullable `status TEXT` column; backfills all existing rows to `'succeeded'`. CLI path: `status` set to `'running'` on creation, `'succeeded'` when `finished_at` is set. Web path: RunManager manages transitions.

- **D4 — SSE for live progress, not WebSocket.** `GET /api/runs/{run_id}/progress` → `text/event-stream`. Services accept an optional `on_progress: Callable[[PipelineRun], None]` callback; RunManager bridges callback → SSE subscribers via `asyncio.Queue`. SSE is simpler (unidirectional, auto-reconnect, no upgrade handshake), sufficient for counter updates.

- **D5 — CPU-bound ML gate inference uses `asyncio.to_thread()`.** Wraps `XGBoostGate.predict_batch` in `to_thread` at the service call site (`_evaluate_funnel.py`), preventing event-loop blocking during embedding + XGBoost inference. LLM calls (subprocess) and source fetches (httpx) are already async I/O — no change needed.

- **D6 — OTel SDK + Jaeger all-in-one (Badger persistence).** One Docker container. `SPAN_STORAGE_TYPE=badger` persists traces to disk across restarts; retention configurable via `BADGER_SPAN_STORE_TTL`. SDK exports directly to Jaeger's OTLP gRPC endpoint (port 4317) — no OTel Collector intermediary. Auto-instrumentation: `opentelemetry-instrumentation-httpx` (ATS/Indeed/LinkedIn fetches), `opentelemetry-instrumentation-asyncpg` (all DB queries), `opentelemetry-instrumentation-fastapi` (all API requests). ~15 manual spans for service-layer steps (see Task 3). Config `observability.otel_enabled = false` → no tracer provider registered, all `tracer.start_as_current_span` calls become no-op. structlog bridge injects `trace_id` + `span_id` into every log record when OTel is active.

- **D7 — Metrics in Postgres, not Prometheus.** New `step_timings` table: `(id, run_id FK, step_type, step_name, elapsed_ms, is_error, created_at)`. Services record timing at ~15 key points matching span boundaries. Existing `llm_usage` (per-call latency/tokens/cost) and `cost_entries` (daily totals) tables cover LLM metrics. Performance zone queries Postgres directly. Rationale: the critical-path analysis needs source-level and step-level granularity (not individual HTTP request level); Postgres handles this without a separate metrics backend (Prometheus/Grafana = 2 extra containers for marginal gain at this granularity).

- **D8 — Sentry SDK for error tracking.** `sentry-sdk[fastapi]`, free tier (10k events/month). Init in `observability.py`, called from CLI `create_app` and web factory. Auto-captures unhandled exceptions with stack traces + breadcrumbs. Error grouping surfaces recurring failures (LLM parse errors, source fetch errors). Especially valuable for cron-triggered CLI runs where the user isn't watching the terminal. Config: `sentry_dsn` + `sentry_environment` in `ObservabilitySettings`.

- **D9 — Performance zone = 7th web zone, 11 panels.** New navigation entry between Runs and Sources. Global time filter (7d/30d/90d). All charts use recharts (Phase 8 dep). Data from 4 Postgres tables: `step_timings`, `pipeline_runs`, `llm_usage`, `cost_entries`. Panel layout:

  | Row | Left Panel | Right Panel |
  |-----|-----------|-------------|
  | 1 | KPI cards ×4 (avg scan/eval duration, LLM cost, error rate; period delta) | — |
  | 2 | Scan Source Duration (grouped bar per run, one bar per source) | Evaluate Breakdown (grouped bar: funnel/stage_a/stage_b) |
  | 3 | Gate Pass/Fail Rate (stacked area, hard_fail vs model_fail) | Gate Substep Timing (grouped bar: extract/embed/predict) |
  | 4 | LLM Latency P50/P95 (line chart, daily) | Daily Cost (bar chart) |
  | 5 | Token Usage Trend (line chart, avg input/output per call, daily) | — (full width) |
  | 6 | Funnel Conversion (horizontal bar funnel) | Per-Source Errors (stacked bar per run) + Errors per Run (bar chart) |

- **D10 — Trigger UI in Runs zone.** Two buttons: "Trigger Scan" (source selector: all/ats/indeed/linkedin-guest/speedyapply) and "Trigger Evaluate" (stage/corpus/limit form). Running runs display at top of list with animated live counters (SSE). 409 on concurrent conflict → toast explaining the lock. Completed runs transition to historical list.

## A4. Surface Delta

**New/modified API routes:**

| Route | Method | Backs | Notes |
|---|---|---|---|
| `/api/runs/scan` | POST | Trigger scan | `{source}` → `{run_id, status}`, 409 if scan already running |
| `/api/runs/evaluate` | POST | Trigger evaluate | `{stage?, corpus?, limit?}` → `{run_id, status}`, 409 if evaluate already running |
| `/api/runs/{run_id}/progress` | GET | SSE live stream | `text/event-stream`, emits PipelineRun counter snapshots, closes on run completion |
| `/api/runs/active` | GET | Active runs | List of currently running runs (0–2: at most one scan + one evaluate) |
| `/api/performance/overview` | GET | KPI cards | `?window=30` → avg durations, total cost, error rate, period-over-period delta |
| `/api/performance/step-timings` | GET | Critical path panels | `?window=30&step_type=source_fetch` → per-step duration series for charts |
| `/api/performance/llm-stats` | GET | LLM panels | `?window=30` → latency P50/P95 daily, token usage daily avg, cache hit rate |
| `/api/performance/funnel` | GET | Funnel + gate panels | `?window=30` → conversion counts per funnel step, gate pass/fail/hard_fail rate per run |

**New Docker service:**

```yaml
jaeger:
  image: jaegertracing/all-in-one:latest
  ports:
    - "16686:16686"   # Jaeger UI
    - "4317:4317"     # OTLP gRPC
  environment:
    COLLECTOR_OTLP_ENABLED: "true"
    SPAN_STORAGE_TYPE: badger
    BADGER_EPHEMERAL: "false"
    BADGER_DIRECTORY_VALUE: /badger/data
    BADGER_DIRECTORY_KEY: /badger/key
  volumes:
    - jaeger_data:/badger
```

**New web zone:** Performance (7th zone, nav order: Triage / Pipeline / Library / Insights / Runs / Performance / Sources).

## A5. Out of Scope (deferred)

Temporal durable execution; Prometheus + Grafana metrics backend; OTel Collector (Jaeger receives OTLP directly); internal scheduler (crontab suffices); LinkedIn web (login/cookie status stay CLI); dark theme (tokens ready, not built); network mode + auth (Phase 10); command palette; digest page; full WCAG audit; `mark <url>` token resolution; migrating legacy web-ui code.

---

# PART B — IMPLEMENTATION PLAN

## File Structure

```
src/jobfeed/
├── domain/
│   ├── models.py              # MOD — extract PipelineRun to models_run.py, re-export
│   ├── models_run.py           # NEW — PipelineRun (extracted) += status field, DryRunPreviewItem
│   └── models_perf.py         # NEW — StepTiming, PerformanceOverview, StepTimingSeries, LLMStats, FunnelStats
├── ports/
│   ├── store.py               # MOD — add update_pipeline_run_status method
│   └── store_perf.py          # NEW — record_step_timing, performance query protocols
├── adapters/
│   ├── store/postgres.py      # MOD — step_timings persistence + performance queries + status UPDATE
│   └── ml/
│       ├── xgboost_gate.py    # MOD — extract validation to _gate_validation.py
│       └── _gate_validation.py # NEW — extracted validation helpers
├── services/
│   ├── run_manager.py         # NEW — RunManager + ActiveRun (async lifecycle, locks, broadcast)
│   ├── performance.py         # NEW — PerformanceService (aggregation queries)
│   ├── _timing.py             # NEW — StepTimer context manager (uses SpanWrapper from observability)
│   ├── _evaluate_factory.py   # NEW — shared EvaluateService construction (CLI + web)
│   ├── _evaluate_gate.py      # NEW — extracted gate helpers from _evaluate_funnel.py
│   ├── scan.py                # MOD — on_progress callback, step timing recording
│   ├── evaluate.py            # MOD — on_progress callback, step timing recording
│   ├── _evaluate_funnel.py    # MOD — to_thread for ML gate, step timing
│   └── runs.py                # MOD — start_pipeline_run sets status='running'
├── web/
│   ├── app.py                 # MOD — RunManager in lifespan, register performance routes
│   ├── deps.py                # MOD — RunManager + PerformanceService accessors
│   ├── schemas/runs.py        # MOD — RunSummary += status field
│   ├── routes/runs.py         # MOD — POST scan/evaluate triggers, GET progress (SSE), GET active
│   └── routes/performance.py  # NEW — Performance zone API routes
├── observability.py           # MOD — OTel init + Sentry init + structlog bridge + SpanWrapper protocol
├── config.py                  # MOD — OTel/Sentry fields in ObservabilitySettings (no per-field docstrings)
└── config.example.toml        # MOD — [observability] otel/sentry sections
migrations/versions/
└── 0007_phase9_observability.py  # NEW — PipelineRun.status + step_timings table
docker-compose.yml             # MOD — jaeger service + jaeger_data volume
pyproject.toml                 # MOD — opentelemetry-*, sentry-sdk[fastapi]
tests/
├── unit/{test_run_manager, test_timing, test_performance_service, test_otel_noop,
│     test_evaluate_factory}.py
├── integration/{test_step_timings_store, test_performance_queries}.py  @postgres
├── web/{test_trigger_routes, test_sse_progress, test_performance_routes}.py  @postgres
└── contract/test_web_openapi.py  # MOD — snapshot regenerated
web-ui/src/
├── routes/performance.tsx                # NEW — Performance zone page
├── routes/runs.tsx                       # MOD — trigger buttons + SSE live progress
├── components/performance/{KpiCards, ScanSourceDuration, EvaluateBreakdown,
│     GatePassFail, GateSubstep, LlmLatency, DailyCost, TokenUsage,
│     FunnelConversion, PerSourceErrors, ErrorsPerRun, TimeFilter}.tsx  # NEW
├── components/runs/{TriggerScanDialog, TriggerEvaluateDialog, LiveRunRow}.tsx  # NEW
├── lib/use-sse.ts                        # NEW — EventSource hook
├── api/queries.ts                        # MOD — trigger mutations, SSE hook, performance queries
└── App.tsx                               # MOD — Performance route + nav entry
```

## Task Dependencies

```
Task 0 (deps/config/docker)
├── Task 1 (migration/models) ──────┐
│   └── Task 2 (OTel/Sentry)        │
│       └── Task 3a (StepTimer + ScanService instrumentation + on_progress)
│           ├── Task 3b (EvaluateService + funnel instrumentation + to_thread)
│           │   └── Task 6a (performance store queries)
│           │       └── Task 6b (performance service + routes)
│           ├── Task 3c (XGBoostGate sync-only OTel substep spans)
│           └── Task 4 (RunManager) ← also depends on Task 1
│               └── Task 5 (trigger/SSE/active routes)
│                   └── Task 7 (OpenAPI snapshot + sweep)
│                       └── Task 8 (frontend runs trigger + SSE)
│                           └── Task 9 (frontend performance zone)
```

---

## Task 0: Dependencies + Config + Docker

**Files:** Modify `pyproject.toml`, `config.py`, `config.example.toml`, `docker-compose.yml`; Test `tests/unit/test_config.py`.

**What to build:** Add runtime deps: `opentelemetry-api>=1.25`, `opentelemetry-sdk>=1.25`, `opentelemetry-exporter-otlp-proto-grpc>=1.25`, `opentelemetry-instrumentation-httpx>=0.46b`, `opentelemetry-instrumentation-asyncpg>=0.46b`, `opentelemetry-instrumentation-fastapi>=0.46b`, `sentry-sdk[fastapi]>=2.0`. Extend `ObservabilitySettings` (`config.py`): `otel_enabled: bool = False`, `otel_endpoint: str = "http://localhost:4317"`, `otel_service_name: str = "jobfeed"`, `sentry_dsn: str | None = None`, `sentry_environment: str = "dev"`. **File budget:** `config.py` is 293 lines — add these 5 fields inline without per-field docstrings (the class-level docstring covers them); verify the file stays ≤300 lines after the edit. Add `[observability]` block in `config.example.toml` with all new fields commented. Add `jaeger` service to `docker-compose.yml` (per A4 spec: all-in-one, Badger, ports 16686+4317, `jaeger_data` named volume). Existing config fields and services unchanged.

**Acceptance:**
- [ ] `pip install -e .` succeeds; `import opentelemetry`, `import sentry_sdk` work
- [ ] `Settings(observability={"otel_enabled": True})` validates; unknown keys raise `extra="forbid"`
- [ ] `docker compose up jaeger` starts; Jaeger UI reachable at `localhost:16686`
- [ ] `docker compose down && docker compose up jaeger` → traces survive restart (Badger persistence)
- [ ] All tests pass, committed

## Task 1: Alembic migration — PipelineRun.status + step_timings

**Depends on:** Task 0.

**Files:** Create `domain/models_run.py` (extract from `models.py`); Modify `domain/models.py` (re-export); Create `migrations/versions/0007_phase9_observability.py`; Create `domain/models_perf.py` (StepTiming); Create `ports/store_perf.py` (record + query protocols); Modify `ports/store.py` (add `update_pipeline_run_status`); Modify `adapters/store/postgres.py` (implement); Modify `web/schemas/runs.py` (RunSummary += status); Test `tests/integration/test_step_timings_store.py` (`@postgres`).

**What to build:**

**Step 0 — Extract PipelineRun to avoid 300-line breach.** `domain/models.py` is exactly 300 lines and NOT exempt. Extract `DryRunPreviewItem` + `PipelineRun` (lines 173–201, ~29 lines) into a new `domain/models_run.py`. Re-export both from `models.py` via `from jobfeed.domain.models_run import DryRunPreviewItem, PipelineRun` so all existing imports stay valid. This frees headroom for the `status` field.

**Step 1 — PipelineRun.status.** Add `status: str = "running"` to `PipelineRun` in `models_run.py`.

**Step 2 — Alembic migration `0007`.** (1) `ALTER TABLE pipeline_runs ADD COLUMN status TEXT`; backfill all existing rows to `'succeeded'`; `ALTER COLUMN status SET NOT NULL`. (2) `CREATE TABLE step_timings (id SERIAL PRIMARY KEY, run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id), step_type TEXT NOT NULL, step_name TEXT NOT NULL, elapsed_ms DOUBLE PRECISION NOT NULL, is_error BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())`; indexes: `(run_id)`, `(step_type, created_at)`.

**Step 3 — StepTiming model.** `domain/models_perf.py`: `StepTiming` dataclass with `run_id, step_type, step_name, elapsed_ms, is_error: bool, created_at`. Boolean field uses `is_error` per engineering standards (is_/has_ prefix).

**Step 4 — Store ports.** `ports/store_perf.py`: `StorePerfMixin` protocol with `record_step_timing(timing: StepTiming)`, `record_step_timings(timings: list[StepTiming])`. `ports/store.py`: add `update_pipeline_run_status(run_id: str, status: str, finished_at: datetime | None = None)` — RunManager needs INSERT at trigger time (`status='running'`) then UPDATE on completion (`status='succeeded'`/`'failed'`). The existing `record_pipeline_run` is INSERT-only and does not support status transitions.

**Step 5 — Postgres implementation.** Add `status` as column in the `record_pipeline_run` INSERT. Implement `update_pipeline_run_status` as `UPDATE pipeline_runs SET status=$1, finished_at=$2 WHERE run_id=$3`. Update `_run_from_record` to hydrate `status`. Implement `record_step_timing` / `record_step_timings` with `INSERT INTO step_timings`.

**Step 6 — RunSummary schema.** Add `status: str` to `RunSummary` in `web/schemas/runs.py` and update the `run_summary()` mapper so the API surfaces the new field.

**Acceptance:**
- [ ] `models.py` stays ≤300 lines after extraction; `models_run.py` is well under 300
- [ ] Migration applies cleanly on a fresh DB and on a DB with existing pipeline_runs (backfill verified)
- [ ] `record_step_timing` round-trips: insert → query by run_id → matches
- [ ] `PipelineRun.status` persists and hydrates correctly for all four values
- [ ] `update_pipeline_run_status` transitions running→succeeded and running→failed
- [ ] `RunSummary` includes `status` field; `GET /api/runs` returns it
- [ ] `make quality` green (mypy, ruff, tests)
- [ ] All tests pass, committed

## Task 2: OTel SDK + Sentry integration

**Files:** Modify `observability.py`; Modify `cli/__init__.py` (`create_app`); Modify `web/app.py` (`create_web_app`); Test `tests/unit/test_otel_noop.py`.

**What to build:** Extend `observability.py`: (1) `init_otel(settings: ObservabilitySettings)` — when `otel_enabled`: create `TracerProvider` with `OTLPSpanExporter(endpoint=settings.otel_endpoint)` + `BatchSpanProcessor`, set as global tracer provider, set `Resource(service.name=settings.otel_service_name)`, call auto-instrumentation hooks for httpx/asyncpg/FastAPI. When disabled: no-op (no provider registered; `tracer.start_as_current_span` returns a dummy context). (2) `init_sentry(settings: ObservabilitySettings)` — when `sentry_dsn` is non-None: `sentry_sdk.init(dsn=..., environment=..., traces_sample_rate=0)` (tracing via OTel, not Sentry); when None: no-op. (3) structlog bridge: add a processor that reads `trace.get_current_span().get_span_context()` and injects `trace_id` + `span_id` (hex) into the event dict when a span is active. (4) **`SpanWrapper` protocol + `get_tracer(name)`** — to keep OTel SDK imports out of the services layer, `observability.py` defines a `SpanWrapper` protocol with a `start_as_current_span(name: str) -> ContextManager` method. `get_tracer(name)` returns a wrapper that delegates to the real OTel tracer (or a no-op when disabled). `services/_timing.py` imports `SpanWrapper` from `observability` — never from `opentelemetry` directly. This preserves the hexagonal boundary (services → observability is allowed; services → external SDK is not). Call `init_otel` + `init_sentry` from both `create_app` (CLI) and `create_web_app` (web), after `configure_logging`.

**Acceptance:**
- [ ] With `otel_enabled=false`: no OTel SDK side effects; structlog output has no trace_id/span_id; `get_tracer("x").start_as_current_span("y")` is a no-op context manager
- [ ] With `otel_enabled=true` + Jaeger running: a manual span appears in Jaeger UI; structlog JSON output includes trace_id + span_id
- [ ] With `sentry_dsn` set: `sentry_sdk.is_initialized()` returns True; test with mocked Sentry transport verifies exception capture
- [ ] With `sentry_dsn=None`: `sentry_sdk.is_initialized()` returns False
- [ ] Auto-instrumentation: an httpx call inside an active span creates a child span (verified in test with in-memory exporter)
- [ ] `SpanWrapper` protocol is importable from `observability`; services never import `opentelemetry` directly
- [ ] All tests pass, committed

## Task 3a: StepTimer + ScanService instrumentation + on_progress

**Depends on:** Tasks 1 and 2.

**Files:** Create `services/_timing.py`; Modify `services/scan.py`; Test `tests/unit/test_timing.py`.

**What to build:** (1) `_timing.py`: `StepTimer` async context manager — records `elapsed_ms` on exit, calls `store.record_step_timing(StepTiming(...))`, and wraps the block in a span via `SpanWrapper.start_as_current_span(step_name)` (imported from `observability`, NOT from `opentelemetry` — see Task 2's `SpanWrapper` protocol). Constructor: `(store, run_id, step_type, step_name, tracer: SpanWrapper)`. On exception: sets `is_error=True` on the timing record and marks the span as errored, then re-raises. (2) Instrument `ScanService.run()`: wrap each `_scan_one_source()` call in `StepTimer(step_type="source_fetch", step_name=source_name)` — timing is per-source since sources run in parallel via `asyncio.gather`. (3) Add `on_progress: Callable[[PipelineRun], None] | None = None` to `ScanService.run()` — called after each source completes. Default `None` = current behavior unchanged. `scan.py` is 183 lines, headroom is ample.

**Acceptance:**
- [ ] After a scan run, `step_timings` table has one row per source with `step_type='source_fetch'`
- [ ] With OTel enabled: Jaeger shows scan_run → source_fetch children
- [ ] With OTel disabled: no spans created, but step_timings still recorded
- [ ] `on_progress` callback fires after each source completion; `None` callback = no error
- [ ] Error in a step: timing recorded with `is_error=True`, span marked error, exception propagates
- [ ] `_timing.py` does NOT import `opentelemetry`; only imports `SpanWrapper` from `observability`
- [ ] All tests pass, committed

## Task 3b: EvaluateService + funnel instrumentation + to_thread

**Depends on:** Task 3a.

**Files:** Create `services/_evaluate_gate.py` (extracted from `_evaluate_funnel.py`); Modify `services/evaluate.py`, `services/_evaluate_funnel.py`; Test `tests/unit/test_evaluate_timing.py`.

**What to build:**

**Step 0 — Extract to avoid 300-line breach.** `_evaluate_funnel.py` is exactly 300 lines and NOT exempt. Extract gate-related helpers (`_gate_unrated` + `_persist_gate_results` and their helper functions, ~40 lines) into a new `services/_evaluate_gate.py`. This brings `_evaluate_funnel.py` to ~260 lines, giving headroom for StepTimer and to_thread additions.

**Step 1 — Instrument `EvaluateService.run()`.** Wrap `run_funnel()` in `StepTimer("stage", "funnel")`, `_run_stage_a()` in `StepTimer("stage", "stage_a")`, `_run_stage_b()` in `StepTimer("stage", "stage_b")`. Add `on_progress: Callable[[PipelineRun], None] | None = None` — fire after each stage completes (after funnel, after stage_a gather, after stage_b gather), not after every individual job scored. **File budget:** `evaluate.py` is 279 lines; per-stage `on_progress` calls add ~6 lines; StepTimer wrapping adds ~6 lines; verify ≤300 after edits.

**Step 2 — Instrument `run_funnel()`.** Wrap `apply_hard_filters` in `StepTimer("funnel_step", "hard_filter")`, `pick_representatives` in `StepTimer("funnel_step", "dedupe")`, `gate.predict_batch` in `StepTimer("funnel_step", "ml_gate")`. Wrap the `gate.predict_batch` call in `asyncio.to_thread()` (D5).

**Acceptance:**
- [ ] `_evaluate_funnel.py` stays ≤300 lines after extraction + instrumentation
- [ ] `evaluate.py` stays ≤300 lines after on_progress + StepTimer additions
- [ ] After an evaluate run, `step_timings` has rows for `funnel`, `stage_a`, `stage_b`, `hard_filter`, `dedupe`, `ml_gate`
- [ ] `on_progress` callback fires after each stage; `None` callback = no error
- [ ] ML gate `predict_batch` runs in a thread (`to_thread`) — verified by checking it doesn't block the event loop
- [ ] All tests pass, committed

## Task 3c: XGBoostGate sync-only OTel substep spans

**Depends on:** Task 3a.

**Files:** Create `adapters/ml/_gate_validation.py` (extracted from `xgboost_gate.py`); Modify `adapters/ml/xgboost_gate.py`; Test `tests/unit/test_gate_spans.py`.

**What to build:**

**Step 0 — Extract to avoid 300-line breach.** `xgboost_gate.py` is exactly 300 lines. It lives under `adapters/ml/`, NOT `adapters/store/`, so it is NOT exempt from the 300-line gate. Extract validation helpers (`_validate_meta_keys`, `_validate_embedding_contract`, ~40 lines) into a new `adapters/ml/_gate_validation.py`.

**Step 1 — Sync-only OTel substep spans.** `predict_batch` runs synchronously inside `asyncio.to_thread()` (from Task 3b). The async `StepTimer` cannot be used here (it awaits `store.record_step_timing`). Instead, use synchronous OTel spans only (no store recording) for `extract_features`, `embed`, `xgboost_predict` substeps. The OTel SDK's `tracer.start_as_current_span` works synchronously. Substep durations are captured by the OTel span tree, not by `step_timings` rows — the parent `ml_gate` StepTimer (at the funnel level, Task 3b) records the aggregate timing.

**Acceptance:**
- [ ] `xgboost_gate.py` stays ≤300 lines after extraction + span additions
- [ ] With OTel enabled: Jaeger shows ml_gate → extract/embed/predict child spans
- [ ] With OTel disabled: no spans created, predict_batch behavior unchanged
- [ ] Gate substep spans do NOT write to `step_timings` table (sync context, no store access)
- [ ] All tests pass, committed

## Task 4: RunManager service + EvaluateService factory extraction

**Depends on:** Tasks 1 and 3a.

**Files:** Create `services/run_manager.py`; Create `services/_evaluate_factory.py`; Modify `cli/_evaluate_build.py` (delegate to shared factory); Add `RunConflictError` to `domain/errors.py`; Test `tests/unit/test_run_manager.py`, `tests/unit/test_evaluate_factory.py`.

**What to build:**

**Step 0 — EvaluateService factory extraction.** `cli/_evaluate_build.py` imports `click`, uses `click.ClickException`, and takes a CLI-specific `_EvalParams` dataclass — RunManager (a web-layer service) cannot call it. Extract the EvaluateService construction logic into a shared `services/_evaluate_factory.py` that takes `AppContext` + plain typed params (`stage`, `corpus`, `limit`, `max_days`, `dry_run`) and returns a constructed `EvaluateService`. Move the `click.ClickException` for missing resume into the CLI caller. Both `cli/_evaluate_build.py` and RunManager call this shared factory.

**Step 1 — RunManager.** `RunManager` class: (1) Constructor takes `store`, `logger`, and lazily-bound service factories (`scan_service_factory`, `evaluate_service_factory` — callables that return the services; avoids eager construction of LLM deps). (2) `trigger_scan(source, config) -> str` — acquires `_scan_lock` (asyncio.Lock), checks no scan active, creates PipelineRun (status=running), persists it via `store.record_pipeline_run`, spawns `asyncio.create_task(_run_scan(...))`, stores task in `_active_scans: dict[str, ActiveRun]`, returns `run_id`. If lock already held: raises `RunConflictError`. (3) `trigger_evaluate(stage, corpus, limit, ...) -> str` — same pattern with `_eval_lock`. (4) `_run_scan` / `_run_evaluate` — extract the common lifecycle pattern into a private `_execute_run(lock, service_call, run)` helper to keep nesting ≤2 and complexity ≤8. On success: `update_pipeline_run_status(run_id, 'succeeded', finished_at)`. On exception: `update_pipeline_run_status(run_id, 'failed', finished_at)` + log + Sentry capture. Always remove from active dict. (5) `get_active_runs() -> list[ActiveRun]` — snapshot. (6) `subscribe(run_id) -> AsyncIterator[ProgressEvent]` — `asyncio.Queue` per subscriber, `_broadcast(run: PipelineRun)` pushes to all queues. (7) **Crash recovery:** `async def recover_stale_runs()` — query `pipeline_runs WHERE status='running'`, update each to `status='failed'` with a note. Called from web app lifespan on startup.

**Step 2 — ActiveRun.** Define `ActiveRun` dataclass in `services/run_manager.py` (it is a service-layer concept, not domain — never persisted). Fields: `run_id, source, started_at, run: PipelineRun`. `RunConflictError` goes in `domain/errors.py`.

**Acceptance:**
- [ ] `trigger_scan` returns run_id; second concurrent trigger raises `RunConflictError`
- [ ] After scan completes: `get_active_runs()` is empty; PipelineRun recorded with `status='succeeded'`
- [ ] Service exception: PipelineRun recorded with `status='failed'`; error logged
- [ ] `subscribe` yields ProgressEvents as the mock service calls `on_progress`; stream closes when run finishes
- [ ] Multiple subscribers on the same run_id all receive the same events
- [ ] `recover_stale_runs` transitions stale 'running' rows to 'failed' on startup
- [ ] Shared `_evaluate_factory.py` constructs EvaluateService without Click dependencies
- [ ] All tests pass, committed

## Task 5: Trigger + SSE + active runs routes

**Depends on:** Task 4.

**Files:** Modify `web/routes/runs.py`, `web/deps.py`, `web/app.py`; Test `tests/web/test_trigger_routes.py`, `tests/web/test_sse_progress.py` (`@postgres`).

**What to build:** (1) Wire `RunManager` into the web app: construct in `build_web_app` lifespan (after store connect), call `recover_stale_runs()`, store as app state, expose via `deps.py` accessor (`get_run_manager`). RunManager's `scan_service_factory` builds `ScanService(store, logger)` from the context. `evaluate_service_factory` uses the shared `_evaluate_factory.py` (Task 4) to build `EvaluateService` without Click deps. (2) `POST /api/runs/scan`: body `{source: str}`, validates source ∈ known sources, calls `run_manager.trigger_scan(source, config)`, returns `{run_id, status: "running"}`. `RunConflictError` → 409 `{error: {code: "scan_already_running", ...}}`. (3) `POST /api/runs/evaluate`: body `{stage?: "a"|"b"|"both", corpus?: str, limit?: int}`, calls `run_manager.trigger_evaluate(...)`, returns `{run_id, status: "running"}`. Same 409 pattern. (4) `GET /api/runs/active`: returns `{runs: [{run_id, source, started_at, counters: {...}}]}`. (5) `GET /api/runs/{run_id}/progress`: `StreamingResponse(media_type="text/event-stream")` — split into a private `_stream_progress` async generator (the route handler does 404/finished-run early returns, then delegates to the generator for the streaming case, keeping complexity ~3). Emit a `: heartbeat\n\n` comment line every 15s to detect stale connections. Set a timeout on the subscriber queue (5 min for scan, 30 min for evaluate) so the generator exits if no event arrives. Unknown run_id → 404. Already-finished run → immediate `done` event.

**Acceptance:**
- [ ] Trigger scan → run appears in `GET /api/runs/active`; after completion → disappears from active, appears in `GET /api/runs` history with `status='succeeded'`
- [ ] Trigger evaluate with `stage="a"` → runs only Stage A (verified via PipelineRun counters)
- [ ] Concurrent scan trigger → 409 with `scan_already_running` code
- [ ] SSE stream: connect → receive counter updates → `done` event on completion → stream closes
- [ ] SSE heartbeat sent every 15s during idle periods
- [ ] SSE on an already-finished run_id → immediate `done` event with final counters
- [ ] SSE on an unknown run_id → 404
- [ ] All tests pass, committed

## Task 6: Performance store queries + service + routes

**Depends on:** Tasks 1 and 3b.

**Files:** Create `services/performance.py`; Modify `domain/models_perf.py` (response models); Modify `ports/store_perf.py` (query protocols); Modify `adapters/store/postgres.py` (SQL); Create `web/routes/performance.py`; Modify `web/app.py` (register routes); Modify `web/deps.py` (add `get_performance_service`); Test `tests/integration/test_performance_queries.py` (`@postgres`), `tests/web/test_performance_routes.py` (`@postgres`).

**What to build:** Routes MUST go through `PerformanceService` (injected via `deps.py`), never calling `get_store` directly — matching the `InsightsService`/`InsightsStore` pattern. (1) Store queries (Postgres SQL, all parameterized by `window_days`): **overview** — avg scan duration, avg evaluate duration, total LLM cost, error rate (errors/total_runs), each with period-over-period delta (current window vs previous window of same length); **step_timings** — `SELECT step_type, step_name, run_id, elapsed_ms, is_error, created_at FROM step_timings WHERE created_at > now() - interval ... ORDER BY created_at` filtered by optional `step_type`; **llm_stats** — `SELECT date(timestamp) as day, percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) as p50, percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) as p95, avg(input_tokens) as avg_input, avg(output_tokens) as avg_output FROM llm_usage WHERE ... GROUP BY 1 ORDER BY 1`; **funnel** — per-run funnel step counts from `pipeline_runs` (total candidates inferred from jobs_filtered + jobs_ml_gated + jobs_scored, after_filter = total - jobs_filtered, after_gate = after_filter - jobs_ml_gated, scored = jobs_scored) + gate pass/fail rate. (2) `PerformanceService`: thin orchestrator calling store queries + assembling response models. (3) Routes: `GET /api/performance/overview?window=30`, `GET /api/performance/step-timings?window=30&step_type=source_fetch`, `GET /api/performance/llm-stats?window=30`, `GET /api/performance/funnel?window=30`. All return JSON matching the frontend panel data shapes.

**Acceptance:**
- [ ] Overview: avg durations computed correctly from seeded pipeline_runs; period delta = (current - previous) / previous
- [ ] Step timings: query returns correct rows filtered by step_type and window; ordering by created_at
- [ ] LLM stats: P50/P95 match hand-computed values from seeded llm_usage rows; daily grouping correct at UTC boundaries
- [ ] Funnel: conversion counts match pipeline_runs counters; gate rate = jobs_ml_gated / (jobs_ml_gated + jobs_scored)
- [ ] Empty window returns zeroes/empty arrays, not errors
- [ ] Routes return correct JSON shapes matching frontend expectations
- [ ] All tests pass, committed

## Task 7: OpenAPI snapshot + final PR-A sweep (closes PR-A)

**Depends on:** Tasks 5 and 6.

**Files:** Modify `web/openapi.json` (regenerated); Modify `tests/contract/test_web_openapi.py` (if needed); Mirror plan to rewrite repo `docs/plans/`.

**What to build:** Regenerate OpenAPI snapshot with all new routes (trigger, SSE, active, performance). Contract test must pass. Run `make quality` across all backend changes. Mirror this plan doc into the rewrite repo's `docs/plans/` directory.

**Acceptance:**
- [ ] `make web-schema` regenerates snapshot; `test_web_openapi.py` passes
- [ ] `make quality` green (mypy + ruff + all test markers except `live`/`mlmodel`)
- [ ] Plan mirrored to rewrite repo
- [ ] All tests pass, committed

## Task 8: Frontend — Runs zone trigger + SSE live progress

**Depends on:** Task 7 (OpenAPI snapshot provides types). PR-B branch based on PR-A head; rebase on main after PR-A merges.

**Files:** Modify `web-ui/src/routes/runs.tsx`; Create `web-ui/src/components/runs/{TriggerScanDialog,TriggerEvaluateDialog,LiveRunRow}.tsx`, `web-ui/src/lib/use-sse.ts`; Modify `web-ui/src/api/queries.ts`; Regenerate `types.gen.ts`; Test `web-ui/src/routes/runs.test.tsx`, `web-ui/src/lib/use-sse.test.ts`.

**What to build:** (1) `use-sse.ts`: `useSSE<T>(url: string | null) → {data: T | null, isConnected, error}` — wraps `EventSource`, auto-reconnects on disconnect, parses `data:` lines as JSON, handles `event: done` for stream completion. (2) `TriggerScanDialog`: dropdown for source selection (all/ats/indeed/linkedin-guest/speedyapply), submit → `POST /api/runs/scan` mutation, 409 → toast "Scan already running". (3) `TriggerEvaluateDialog`: stage (a/b/both), optional corpus/limit fields, same 409 handling. (4) `LiveRunRow`: displayed at top of runs list when `GET /api/runs/active` returns non-empty. Connects to `GET /api/runs/{run_id}/progress` SSE. Animates counter updates (discovered, scored, errors incrementing). On `done` event: invalidates runs query, row moves to history. (5) Remove the "read-only" hint text from `runs.tsx`. Add "Trigger Scan" + "Trigger Evaluate" buttons in the page header.

**Acceptance:**
- [ ] Trigger scan → dialog opens → select source → submit → LiveRunRow appears at top with counters updating
- [ ] 409 → toast "Scan already running" (mocked)
- [ ] SSE disconnect → auto-reconnect (simulated in test by closing mock EventSource)
- [ ] Run completion → LiveRunRow disappears, new entry appears in history list (mocked refetch)
- [ ] "Runs are read-only" text removed
- [ ] `prefers-reduced-motion` honored for counter animations
- [ ] All tests pass, committed

## Task 9: Frontend — Performance zone (closes PR-B)

**Depends on:** Task 8.

**Files:** Create `web-ui/src/routes/performance.tsx`, `web-ui/src/components/performance/{TimeFilter,KpiCards,ScanSourceDuration,EvaluateBreakdown,GatePassFail,GateSubstep,LlmLatency,DailyCost,TokenUsage,FunnelConversion,PerSourceErrors,ErrorsPerRun}.tsx`; Modify `web-ui/src/App.tsx` (route + nav), `web-ui/src/api/queries.ts` (performance query hooks); Test `web-ui/src/routes/performance.test.tsx`.

**What to build:** (1) `TimeFilter`: 7d/30d/90d toggle, state lifted to page level, passed as `window` param to all queries. Default 30d. (2) `KpiCards`: 4 cards (avg scan duration, avg eval duration, LLM cost, error rate) with period-over-period delta arrow (green ↓ good, red ↑ bad; inverted for cost/errors vs duration). (3) `ScanSourceDuration`: recharts `BarChart` grouped by run, one bar per source, colored per source. Tooltip shows source name + elapsed_ms. (4) `EvaluateBreakdown`: same pattern, bars for funnel/stage_a/stage_b. (5) `GatePassFail`: `AreaChart` stacked, pass (cobalt) + hard_fail + model_fail over time. (6) `GateSubstep`: grouped bar (extract/embed/predict per run). (7) `LlmLatency`: `LineChart` with P50 + P95 lines, daily. (8) `DailyCost`: `BarChart`, daily cost from cost_entries. (9) `TokenUsage`: `LineChart` with avg input + output token lines, daily. (10) `FunnelConversion`: horizontal `BarChart` showing candidates → after_filter → after_gate → scored. Latest run or window average (toggle). (11) `PerSourceErrors`: stacked `BarChart` by source per run. (12) `ErrorsPerRun`: simple `BarChart`, errors per run over time. All charts use DESIGN.md tokens (cobalt accent, graphite text, Geist Mono for data labels). Responsive: 2-column grid on wide screens, single column on narrow. Empty state per panel ("No data for this period"). Register `/performance` route in `App.tsx`, add nav entry between Runs and Sources.

**Acceptance:**
- [ ] All 11 panels render from mocked API data without errors
- [ ] Time filter switches window → all queries refetch with new param
- [ ] Empty window → per-panel empty states, not crashes
- [ ] Responsive: 2-col at ≥1024px, 1-col below
- [ ] Charts use DESIGN.md tokens (cobalt, graphite, Geist Mono); no absolute-ban violations
- [ ] Nav shows Performance between Runs and Sources; route `/performance` loads the page
- [ ] Phase acceptance sweep: (1) `serve` with all 7 zones live; (2) trigger scan from Runs → watch live progress → completion → counters in Performance; (3) trigger evaluate → same flow; (4) Performance charts render real data after runs; (5) Jaeger shows traces for the triggered runs (when otel_enabled); (6) stale types fail the build; (7) `npx impeccable detect web-ui/src` clean
- [ ] All tests pass; `make quality` + frontend build/test green; committed

---

## Self-Review

- **Decision coverage:** D1 (in-process) → T4 (RunManager); D2 (RunManager) → T4; D3 (status) → T1; D4 (SSE) → T5/T8; D5 (to_thread) → T3b; D6 (OTel) → T0/T2/T3a/T3c; D7 (Postgres metrics) → T1/T3a/T3b/T6; D8 (Sentry) → T0/T2; D9 (Performance zone) → T6/T9; D10 (trigger UI) → T5/T8. All decisions have implementing tasks.
- **Task dependencies:** Explicit dependency graph in "Task Dependencies" section above. No circular dependencies.
- **File size compliance:** Every file modification specifies current line count and verifies ≤300 after changes. Three at-limit files (models.py, _evaluate_funnel.py, xgboost_gate.py) have explicit extraction steps. config.py (293 lines) has a headroom budget note.
- **Layer boundaries:** OTel SDK confined to `observability.py` via `SpanWrapper` protocol; `services/_timing.py` imports from `observability`, never from `opentelemetry`. `ActiveRun` defined in `services/run_manager.py` (service concept), not `domain/`. Performance routes go through `PerformanceService`, not store directly.
- **Store mutation path:** `record_pipeline_run` (INSERT) + `update_pipeline_run_status` (UPDATE) cover RunManager's lifecycle transitions. `RunSummary` schema includes `status` field.
- **EvaluateService factory:** Shared `_evaluate_factory.py` decouples EvaluateService construction from Click/CLI dependencies, enabling both CLI and web RunManager to build it.
- **Type consistency:** `PipelineRun.status` (T1→T3a/T3b→T4→T5), `StepTiming.is_error` (T1→T3a/T3b→T6), `RunConflictError` (T4→T5), `on_progress` callback signature (T3a/T3b→T4), `SpanWrapper` (T2→T3a/T3b/T3c), performance response models (T6→T9), SSE event format (T5→T8), OpenAPI snapshot (T7→T8/T9).
- **Phase 8 debt addressed:** read-only Runs zone → T5/T8 (trigger + live progress); Phase 8 A5 deferred items reviewed and excluded per user confirmation.
- **Crash recovery:** RunManager startup sweep transitions stale 'running' rows to 'failed'.
