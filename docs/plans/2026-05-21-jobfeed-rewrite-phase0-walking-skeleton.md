# Phase 0: Walking Skeleton — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a new repo with hexagonal architecture that runs a Dockerized mock end-to-end chain: host launcher → jobfeed-cli container → MockSource → SQLiteStore → MockLLM → CLI scan/evaluate/digest.

**Architecture:** Hexagonal (Ports & Adapters). domain/ has zero external imports. ports/ defines Protocol interfaces. adapters/ implements them. services/ orchestrates (async). cli/ is a thin sync Click shell inside the container that calls async services via `asyncio.run()`. The user-facing CLI is a host wrapper (`bin/jobfeed` / `bin/jobfeed.ps1`) that forwards arguments to Docker Compose; it contains no business logic. All application dependencies are wired via a manual DI factory.

**Tech Stack:** Python 3.11+, Docker, Docker Compose, Click, pydantic, structlog, pytest, pytest-asyncio, aiosqlite, ruff, mypy, pre-commit

**Spec reference:** `docs/specs/2026-05-20-jobfeed-rewrite-design.md`

**Plan path:** `docs/plans/2026-05-21-jobfeed-rewrite-phase0-walking-skeleton.md`

**New variables in this phase:** 2 (Dockerized CLI runtime + real SQLite DB). All other IO is mocked.

**Environment isolation:** Default config/DB home is repo-local `.jobfeed-dev/`, not `~/.jobfeed/`. Docker Compose mounts `./.jobfeed-dev` into the `jobfeed-cli` container at the same repo-relative path so the default SQLite path works consistently across macOS/Linux/Windows. This is a Phase 0 safety override of the architecture doc's steady-state `~/.jobfeed/` defaults. Phase 0 implementation/tests must never read or write `~/.jobfeed/`; explicit config paths are allowed only when they point outside `~/.jobfeed/`.

**Implementation repo:** implement this rewrite in this repository. The legacy repo is the sibling path `../job-apply`; do not implement Phase 0 rewrite tasks there.

**Precedence:** for Phase 0 implementation, this phase plan is the source of truth when it conflicts with the architecture spec. The architecture spec describes steady-state/cutover behavior.

**Commit strategy:** commit each task separately with a task-sized conventional commit. Do not wait for one large end-of-phase commit.

**Naming parity policy:** Phase 0 may use cleaner internal domain/port names. Legacy SQLite/API field-name compatibility is handled later in the migration/cutover phases, not by forcing legacy names into the new internal model.

**Execution mode:** run Phase 0 tasks sequentially. Subagents may be used for read-only review or isolated follow-up tasks, but do not run parallel writers against the same new repo during Phase 0.

---

## Async/Sync Boundary

All ports, adapters, and services are **async**. CLI is **sync** Click. Each CLI command bridges via `asyncio.run()`:

```python
@cli.command()
@click.pass_context
def scan(ctx, ...):
    asyncio.run(_scan(ctx.obj, ...))

async def _scan(app, ...):
    await app.scan_service.run(...)
```

Store lifecycle: `create_app()` builds but does NOT connect. CLI command calls `await store.connect()` / `await store.close()` (or uses async context manager).

---

## File Map

```
jobfeed/                              # NEW repo root
├── src/
│   └── jobfeed/                      # Python package
│       ├── __init__.py               # version
│       ├── domain/
│       │   ├── __init__.py
│       │   ├── errors.py             # shared expected domain exceptions
│       │   ├── models.py             # public domain model exports
│       │   ├── types.py              # shared Literal aliases
│       │   ├── scoring.py            # render prompts + parse responses (NO LLM calls)
│       │   ├── filtering.py          # HardFilters + apply_hard_filters
│       │   ├── quality.py            # assess_quality → QualityBand
│       │   └── digest.py             # render_digest → Markdown str
│       │
│       ├── ports/
│       │   ├── __init__.py
│       │   ├── store.py              # JobStore Protocol
│       │   ├── llm.py                # LLMClient Protocol
│       │   └── source.py             # SimpleSource, SessionSource, DiscoverResult, EnrichResult
│       │
│       ├── adapters/
│       │   ├── __init__.py
│       │   ├── store/
│       │   │   ├── __init__.py
│       │   │   ├── sqlite.py         # SQLiteStore
│       │   │   ├── sqlite_mapping.py # row/domain mapping helpers
│       │   │   ├── sqlite_stage_b_mapping.py # validated Stage B raw block mapping
│       │   │   ├── sqlite_row.py     # shared row validation helpers
│       │   │   ├── sqlite_params.py  # SQL parameter helpers
│       │   │   ├── sqlite_sql.py     # SQL statement constants
│       │   │   └── schema.sql        # DDL
│       │   ├── llm/
│       │   │   ├── __init__.py
│       │   │   └── mock.py           # MockLLM
│       │   └── sources/
│       │       ├── __init__.py
│       │       └── mock.py           # MockSource
│       │
│       ├── services/
│       │   ├── __init__.py
│       │   ├── error_handler.py      # recoverable service error policy
│       │   ├── runs.py               # PipelineRun factory helper
│       │   ├── scan.py               # ScanService
│       │   ├── evaluate.py           # EvaluateService
│       │   └── digest.py             # DigestService
│       │
│       ├── cli/
│       │   ├── __init__.py           # Click group + create_app() DI factory
│       │   ├── scan.py
│       │   ├── evaluate.py
│       │   └── digest.py
│       │
│       ├── config.py                 # manual TOML/env merge + pydantic validation
│       └── observability.py          # structlog setup + run_id binding
│
├── tests/
│   ├── conftest.py                   # shared pytest setup
│   ├── support/                      # shared factories + hygiene checker helpers
│   ├── unit/
│   │   ├── test_models.py
│   │   ├── test_scoring.py
│   │   ├── test_quality.py
│   │   ├── test_filtering.py
│   │   ├── test_digest.py
│   │   ├── test_mock_adapters.py
│   │   ├── test_architecture_boundaries.py
│   │   └── test_code_hygiene.py
│   ├── integration/
│   │   ├── test_sqlite_store.py
│   │   └── test_services.py
│   └── e2e/
│       └── test_cli_skeleton.py
│
├── .github/workflows/
│   └── ci.yml                        # ruff + mypy + pytest
├── bin/
│   ├── jobfeed                       # macOS/Linux host launcher → Docker Compose
│   └── jobfeed.ps1                   # Windows PowerShell host launcher → Docker Compose
├── docs/
│   ├── engineering-standards.md      # naming, complexity, boundaries
│   └── adr/
│       └── 0001-architecture-and-quality-gates.md
├── AGENTS.md                         # agent execution contract
├── .pre-commit-config.yaml
├── .dockerignore
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── Makefile
├── config.example.toml
└── README.md
```

---

## Task 0: Engineering Standards + Quality Gates

**Files:**
- Create: `docs/engineering-standards.md`
- Create: `docs/adr/0001-architecture-and-quality-gates.md`
- Create: `AGENTS.md`
- Create: `.pre-commit-config.yaml`
- Create: `tests/unit/test_code_hygiene.py`

**What to build:**
Write the engineering contract before feature implementation starts. This is not advisory prose; every rule that can be automated must be enforced by `make quality` and CI.

`docs/engineering-standards.md` must define:
- Naming:
  - Modules, files, functions, methods, and variables use `snake_case`.
  - Classes, dataclasses, enums, exceptions, and protocols use `PascalCase`.
  - Boolean names start with `is_`, `has_`, `can_`, `should_`, or `needs_`.
  - Ports are capability nouns: `JobStore`, `LLMClient`, `SimpleSource`, `SessionSource`.
  - Adapters are implementation nouns: `SQLiteStore`, `MockLLM`, `MockSource`.
  - Services are workflow nouns: `ScanService`, `EvaluateService`, `DigestService`.
- Function shape:
  - Cyclomatic complexity target: <= 8; hard max: 10.
  - Nesting depth max: 2; prefer guard clauses and early returns.
  - Positional/keyword arguments max: 5; use a dataclass/config object beyond that.
  - Statements target: <= 40.
  - Functions should have a single clear responsibility.
  - Avoid nested loops in application/service code.
  - If nested loops are necessary in algorithmic or data-processing code, extract them into a named helper and document time complexity.
  - Avoid mixing validation, transformation, IO, and persistence in one function.
- Documentation and comments:
  - Markdown docs must state purpose, constraints, source-of-truth status, and acceptance criteria when they describe implementation work.
  - Every production module, except empty `__init__.py` package markers, must start with a module docstring that names the module's responsibility.
  - Every public production class, dataclass, enum, protocol, function, and method must have a docstring.
  - Function and method docstrings must document behavior, arguments, return value, raised errors, preconditions, side effects, and complexity when those concerns apply.
  - Inline comments explain information the code cannot express clearly: why this implementation was chosen, business constraints, external system behavior, historical compatibility, invariants, edge cases, complexity, and failure modes.
  - Do not write comments that only repeat the code's surface action, such as "increment i", "check if user is None", or "loop through orders".
  - Prefer better names or smaller functions when a comment is only needed to explain what the code is doing.
  - Do not satisfy file-length or function-shape gates by deleting useful docstrings or comments. Split the module or extract named helpers instead.
- Layer boundaries:
  - `domain/` imports stdlib only.
  - `ports/` imports domain models only; no adapters.
  - `services/` depends on ports + domain only; no concrete adapters.
  - `adapters/` may depend on external libraries and implements ports.
  - `cli/` parses options, wires DI, handles process output, and does not contain business logic.
- Error handling:
  - No bare `except`.
  - No silent `pass` in exception handlers.
  - Errors must be named and logged with `run_id` where execution context exists.
- Testing:
  - New behavior starts with a failing test unless the task is pure scaffolding.
  - Unit tests cover domain logic.
  - Integration tests cover adapters and service orchestration.
  - E2E tests cover both the fast in-process Click path and the canonical Dockerized CLI walking skeleton.

`docs/adr/0001-architecture-and-quality-gates.md` records the accepted decisions: hexagonal architecture, Docker-first user-facing runtime, host launchers as thin wrappers, async ports/services with sync Click boundary inside the container, repo-local `.jobfeed-dev/`, SQLite as the only real persistence in Phase 0, and mandatory quality gates.

`AGENTS.md` is the operational contract for coding agents. It must be short, imperative, and explicit:
- Read `docs/engineering-standards.md` before editing code.
- Implement in this repository; do not edit the legacy sibling repo `../job-apply` for Phase 0 rewrite tasks.
- Treat this Phase 0 plan as source of truth when it conflicts with the architecture spec.
- Do not read or write `~/.jobfeed/` in Phase 0.
- Do not add external network IO, real LLM calls, browser automation, Temporal, Postgres, or frontend code in Phase 0.
- Treat `./bin/jobfeed ...` as the canonical user-facing CLI path. Host-native `uv run jobfeed ...` is allowed only for developer debugging, not for production-parity acceptance.
- Keep `domain/` pure stdlib and keep services adapter-free.
- Run `make quality` before marking any task complete.
- Keep commits task-sized and conventional (`chore:`, `feat:`, `test:`, `docs:`).
- Execute Phase 0 tasks sequentially unless a human explicitly approves parallel writers.
- Stop and update the plan if implementation reality conflicts with the plan.
- Never silence a failing quality gate by weakening the gate without explicit human approval.

`.pre-commit-config.yaml` runs `ruff format`, `ruff check --fix`, and `mypy src/`. Do not add slow integration/e2e tests to pre-commit; those stay in CI.

`tests/support/code_hygiene*.py` uses `ast` and `pathlib` to enforce rules that Ruff does not fully cover. `tests/unit/test_code_hygiene.py` contains focused tests and synthetic fixtures:
- No nested `for` or `while` loops in application/service code.
- Nested loops in algorithmic or data-processing code must live in a named helper with documented time complexity.
- Production modules, except empty `__init__.py`, must have module docstrings.
- Public production classes, functions, and methods must have docstrings.
- Public function and method docstrings must include `Args:`, `Returns:`, and `Raises:` sections when the signature or body needs them.
- No `if` nesting deeper than 2 under `src/jobfeed/`.
- No production `.py` file under `src/jobfeed/` longer than 300 lines in Phase 0.
- No bare `except`.
- No empty `except` block containing only `pass`.
- The hygiene checker and its tests must also stay under the same file-length gate and pass the checker's structural rules.

**Acceptance criteria:**
- [ ] `docs/engineering-standards.md` contains naming, complexity, boundary, error-handling, and test standards.
- [ ] ADR 0001 records the irreversible architecture/quality-gate choices, including Docker-first user-facing runtime.
- [ ] `AGENTS.md` contains the agent execution contract and links to `docs/engineering-standards.md`.
- [ ] `.pre-commit-config.yaml` is present and references Ruff + mypy.
- [ ] `test_code_hygiene.py` fails on a synthetic service nested loop fixture and passes on clean code.
- [ ] `test_code_hygiene.py` allows documented algorithm/data-processing nested-loop helpers and fails undocumented ones.
- [ ] `test_code_hygiene.py` fails public production functions/classes missing required docstrings or docstring sections.
- [ ] `test_code_hygiene.py` checks its own file and `tests/support/code_hygiene*.py` so the checker is not exempt from hygiene gates.
- [ ] Task 0 documents that Task 1 `pyproject.toml` and `Makefile` must implement the same thresholds from this task.
- [ ] All committed.

---

## Task 1: Repo Scaffold + Minimal CLI Entry Point

**Files:**
- Create: `pyproject.toml`
- Create: `Makefile`
- Create: `config.example.toml`
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.dockerignore`
- Create: `bin/jobfeed`
- Create: `bin/jobfeed.ps1`
- Create: `src/jobfeed/__init__.py` (version string only)
- Create: all `__init__.py` files for subpackages (empty)
- Create: `src/jobfeed/cli/__init__.py` (minimal Click group)
- Create: `tests/conftest.py` (shared pytest fixtures)
- Create: `tests/unit/test_smoke.py`

**What to build:**
Initialize a new Python project and the Dockerized user-facing CLI runtime. `pyproject.toml`: package name `jobfeed`, requires-python >= 3.11, entry point `jobfeed = "jobfeed.cli:cli"`. Runtime dependencies: click, pydantic, structlog, aiosqlite. Dev dependencies: pytest, pytest-asyncio, ruff, mypy, pre-commit.

Tool config in `pyproject.toml` must enforce the Task 0 standards:
- Ruff line length 88.
- Ruff lint select: `E`, `F`, `I`, `UP`, `B`, `SIM`, `C4`, `C901`, `RET`, `ARG`, `PL`, `RUF`.
- Ruff lint ignore: `PLR0911`, `PLR0912`, and `PLR0915` so returns, branches, and statements stay review-guided instead of hard-blocked.
- Ruff mccabe max complexity: 10; reviewers should push back above the target of 8 unless the function remains clearer.
- Ruff pylint max args: 5.
- mypy strict mode with `warn_unused_ignores = true`, `warn_return_any = true`, and `disallow_any_generics = true`.
- pytest `asyncio_mode = "auto"`.

`Makefile` targets: `test` (pytest), `lint` (ruff check . && ruff format --check . && mypy src/), `fmt` (ruff format . && ruff check --fix .), `quality` (make lint && make test), `docker-build` (docker compose build jobfeed-cli), and `docker-quality` (rebuilds `jobfeed-cli`, then runs docker compose run --rm jobfeed-cli make quality). `config.example.toml` with structure from spec Section 9: `[db]` backend="sqlite" sqlite_path=".jobfeed-dev/dev.db", `[llm]` stage_a="mock/stage-a" stage_b="mock/stage-b", `[scoring]` stage_a_threshold=60, `[execution]` default_runner="in_process", `[observability]` log_level="info" log_format="human". `cli/__init__.py` has a minimal Click group with `--config` option and `--verbose` flag. `test_smoke.py` verifies `from jobfeed.cli import cli` works.

`Dockerfile`: Python 3.11 slim base, workdir `/app`, installs the package with dev dependencies, sets `PATH` so the `jobfeed` console script is available, and uses `jobfeed` as the default command.

`docker-compose.yml`: defines a one-shot `jobfeed-cli` service built from `Dockerfile`, working dir `/app`, and a bind mount `./.jobfeed-dev:/app/.jobfeed-dev` so Phase 0 data is repo-local and persistent across runs. It must not define Postgres, Temporal, Web, or observability services in Phase 0.

`bin/jobfeed`: POSIX shell launcher for macOS/Linux. It prepares the repo-local `.jobfeed-dev/` runtime directory, forwards all args unchanged to `docker compose run --rm jobfeed-cli jobfeed "$@"`, and preserves Docker's exit status via `exec`. It contains no business logic.

`bin/jobfeed.ps1`: Windows PowerShell launcher. It prepares the repo-local `.jobfeed-dev/` runtime directory, forwards all args unchanged to `docker compose run --rm jobfeed-cli jobfeed @args`, and exits with Docker's status code. It contains no business logic.

**Acceptance criteria:**
- [ ] `docker compose build jobfeed-cli` succeeds
- [ ] `./bin/jobfeed --help` prints Click help through Docker
- [ ] `docker compose run --rm jobfeed-cli jobfeed --help` prints Click help
- [ ] Host-native `pip install -e .` still succeeds as developer fallback
- [ ] `make lint` passes (ruff + mypy)
- [ ] `make test` passes smoke test
- [ ] `make quality` runs lint + tests
- [ ] `make docker-quality` runs quality gates inside the container
- [ ] `config.example.toml` parseable by `tomllib`
- [ ] All committed

---

## Task 2: Basic GitHub Actions CI

**Files:**
- Create: `.github/workflows/ci.yml`

**What to build:**
GitHub Actions workflow triggered on push and pull_request. Single job: checkout, set up Docker Buildx, `docker compose build jobfeed-cli`, `docker compose run --rm jobfeed-cli make quality`, and `docker compose run --rm jobfeed-cli jobfeed --help`. No external DB service, no Postgres, no Temporal; SQLite integration tests and CLI e2e tests run via pytest tmp_path or repo-local `.jobfeed-dev/` inside the container.

**Acceptance criteria:**
- [ ] CI YAML is valid (can validate with actionlint or just syntax check)
- [ ] Builds `jobfeed-cli` image
- [ ] Runs ruff check, ruff format --check, mypy src/, pytest inside the container
- [ ] `make test` runs all layers: unit + integration + e2e + architecture boundary tests + code hygiene tests
- [ ] CI uses `make quality` inside Docker, not a hand-written partial copy of local commands
- [ ] CI verifies `jobfeed --help` inside Docker
- [ ] Would pass given current repo state
- [ ] All committed

---

## Task 3: Domain Models

**Files:**
- Create: `src/jobfeed/domain/models.py`
- Test: `tests/unit/test_models.py`

**What to build:**
All domain dataclasses and enums from spec Section 4. Pure Python only (stdlib: dataclasses, enum, datetime, typing). Use `@dataclass(kw_only=True)` for all domain dataclasses so optional identity fields like `JobPosting.id` cannot conflict with required fields and call sites stay explicit. Keep public imports available from `jobfeed.domain.models`. If the implementation would make `models.py` exceed the Phase 0 300-line hygiene limit, split definitions into focused private modules such as `job_models.py`, `evaluation_models.py`, and `pipeline_models.py`, then re-export them from `models.py`. Do not relax the file-length gate.

Models:
- JobPosting: `id: str | None = None` is the store-assigned DB identity after persistence. Before persistence it is `None`. Natural identity is always `(platform, canonical_id)`. Other fields: platform, canonical_id, url, title, company, location, jd_text optional, jd_quality optional QualityBand, posted_at optional datetime, discovered_at datetime, enriched_at optional, enrich_source optional.
- QualityBand enum: full/good/partial/stub/missing/abandoned.
- StageAResult: score int 0-100, one_line str, timing_eligible str, model str, cost_usd optional float, prompt_hash str, resume_hash str.
- StageBResult: verdict Verdict, jd_summary str, fit_analysis FitAnalysis, resume_hooks list[str], model str, cost_usd optional float, prompt_hash str, resume_hash str, raw_blocks optional dict[str, object]. `raw_blocks` preserves the exact block-shaped LLM response when available; normalized fields remain the canonical domain API.
- Verdict enum: apply/consider/skip.
- FitAnalysis: score int 0-100, strengths list[MatchItem], gaps list[GapItem].
- MatchItem: requirement str, evidence str.
- GapItem: requirement str, severity literal critical/major/minor, mitigation str.
- JobStatus enum: new/scored/shortlisted/applied/oa/hr_call/second_round/final_round/interviewing/offer/rejected/ghosted/archived/ignored.
- MLGateResult: fit bool, probability float, fail_reason optional str.
- LLMUsage: model str, input_tokens int, output_tokens int, cost_usd float, cached bool, latency_ms int, timestamp datetime.
- SaveJobResult: job_id str, inserted bool, updated bool.
- PipelineRun: run_id str, started_at datetime, source str, jobs_discovered int default 0, jobs_inserted int default 0, jobs_updated int default 0, jobs_filtered int default 0, jobs_ml_gated int default 0, stage_a_scored int default 0, stage_b_scored int default 0, jobs_scored int default 0, total_llm_cost_usd float default 0.0, errors int default 0, finished_at optional datetime. `source` is the operation/source label: ScanService uses the single source name when exactly one source is scanned and `"scan"` for aggregate multi-source runs; EvaluateService uses `"evaluate"`. `jobs_scored` is the total scored count (`stage_a_scored + stage_b_scored`) for coarse metrics; CLI uses the stage-specific counters.
- Message: role literal system/user/assistant, content str.
- LLMRequest: messages list[Message], model str, temperature float default 0.0, max_tokens int default 4096, response_schema optional dict.
- LLMResponse: content str, model str, input_tokens int, output_tokens int, cost_usd optional float, cached bool default False.

Also define `JobEvaluation` dataclass: job: JobPosting, stage_a: StageAResult | None, stage_b: StageBResult | None. This is used by DigestService.

Validation: StageAResult.__post_init__ raises ValueError if score not 0-100. FitAnalysis.__post_init__ same.

**Acceptance criteria:**
- [ ] All dataclasses instantiable with valid data
- [ ] All domain dataclasses are declared with `kw_only=True`
- [ ] JobPosting can be created before persistence with `id=None`
- [ ] StageAResult(score=150, ...) raises ValueError
- [ ] FitAnalysis(score=-1, ...) raises ValueError
- [ ] All enums have correct string values
- [ ] JobEvaluation holds job + optional stage results
- [ ] SaveJobResult distinguishes inserted vs updated upserts
- [ ] No imports from outside stdlib
- [ ] All tests pass, committed

---

## Task 4: Port Protocols

**Files:**
- Create: `src/jobfeed/ports/store.py`
- Create: `src/jobfeed/ports/llm.py`
- Create: `src/jobfeed/ports/source.py`

**What to build:**
Protocol interfaces using `typing.Protocol` with `@runtime_checkable`.

`JobStore` Protocol — async methods: `save_job(job: JobPosting) -> SaveJobResult`, `get_job(job_id: str) -> JobPosting | None`, `list_jobs(limit: int = 100) -> list[JobPosting]`, `save_stage_a(job_id: str, result: StageAResult) -> None`, `save_stage_a_error(job_id: str, error: str) -> None`, `save_stage_b(job_id: str, result: StageBResult) -> None`, `save_stage_b_error(job_id: str, error: str) -> None`, `load_pending_stage_a(limit: int = 100) -> list[JobPosting]`, `load_pending_stage_b(threshold: int = 60, limit: int = 100) -> list[JobPosting]`, `list_evaluated_jobs(limit: int = 100) -> list[JobEvaluation]`, `record_pipeline_run(run: PipelineRun) -> None`, `get_pipeline_run(run_id: str) -> PipelineRun | None`, `connect() -> None`, `close() -> None`.

`LLMClient` Protocol — async method: `complete(request: LLMRequest) -> LLMResponse`.

`SimpleSource` Protocol — async method: `fetch_jobs(config: dict) -> list[JobPosting]`.

`SessionSource` Protocol — async methods: `discover(config: dict) -> DiscoverResult`, `enrich_session() -> AsyncContextManager[EnrichSession]`. Define `DiscoverResult` dataclass (postings list[JobPosting], needs_reauth bool default False, error optional str, duration_s float default 0.0), `EnrichResult` dataclass (jd_text str, quality QualityBand, enrich_source str, error optional str, posted_at optional datetime), `EnrichSession` Protocol with method `async enrich(posting: JobPosting) -> EnrichResult`.

**Acceptance criteria:**
- [ ] All protocols importable
- [ ] All use `@runtime_checkable`
- [ ] All method signatures reference domain models only
- [ ] DiscoverResult and EnrichResult are dataclasses in source.py
- [ ] JobStore includes `list_evaluated_jobs`, `get_pipeline_run`, `save_stage_a_error`, and `save_stage_b_error`
- [ ] No implementations in port files
- [ ] All committed

---

## Task 5: Config + Observability Baseline

**Files:**
- Create: `src/jobfeed/config.py`
- Create: `src/jobfeed/observability.py`
- Test: `tests/unit/test_config.py`

**What to build:**
`config.py`: use manual TOML loading plus Pydantic validation. Define `Settings` and nested config models as `pydantic.BaseModel` classes for db (backend, url, sqlite_path), llm (stage_a, stage_b, max_concurrent, timeout_s), scoring (stage_a_threshold, ml_gate_enabled, max_daily_score_calls), execution (default_runner), observability (log_level, log_format). `load_settings(config_path: Path | None = None) -> Settings` reads TOML via `tomllib`, merges explicit env overrides, then validates by constructing `Settings`. Omitting `config_path` returns defaults. Passing an explicit missing path raises `FileNotFoundError` so `--config` typos fail fast. Env vars with prefix `JOBFEED_` and nested delimiter `__` override values (e.g., `JOBFEED_DB__BACKEND=postgres`). Do not use `BaseSettings` source magic in Phase 0; the intended behavior is manual TOML + explicit env merge + Pydantic validation. Default sqlite_path = `.jobfeed-dev/dev.db` (repo-local, NOT ~/.jobfeed/).

`observability.py`: `configure_logging(log_level: str = "info", log_format: str = "human")` sets up structlog. "json" format → JSON lines processor, "human" → readable console with colors only when stdout is a TTY. `JobfeedLogger` exposes `info`, `warning`, `error`, and `debug`. `bind_run_id(run_id: str)` binds run_id to structlog context. `get_logger()` returns bound structlog logger.

**Acceptance criteria:**
- [ ] `load_settings()` returns defaults when no config file
- [ ] `load_settings(tmp_path / "missing.toml")` raises FileNotFoundError
- [ ] `load_settings(Path("config.example.toml"))` succeeds
- [ ] Env var `JOBFEED_DB__BACKEND=postgres` overrides file value
- [ ] Default sqlite_path is `.jobfeed-dev/dev.db`, not `~/.jobfeed/`
- [ ] `configure_logging("info", "json")` produces JSON output
- [ ] `configure_logging("info", "human")` produces readable output
- [ ] Human log output does not emit ANSI color escapes when stdout is not a TTY
- [ ] `bind_run_id("test-123")` adds run_id to log entries
- [ ] All tests pass, committed

---

## Task 6: Domain Scoring + Quality + Filtering

**Files:**
- Create: `src/jobfeed/domain/scoring.py`
- Create: `src/jobfeed/domain/errors.py`
- Create: `src/jobfeed/domain/quality.py`
- Create: `src/jobfeed/domain/filtering.py`
- Test: `tests/unit/test_scoring.py`
- Test: `tests/unit/test_quality.py`
- Test: `tests/unit/test_filtering.py`

**What to build:**
`errors.py`: Defines shared expected domain exceptions: `JobfeedError` base class and `ScoringParseError`. `ScoringParseError` carries optional `raw_response` context for failed LLM parsing.

`types.py`: Shared domain type aliases used across domain/services/adapters for cross-module contracts, including `StageName` (`"stage_a" | "stage_b"`) and `Severity` (`"critical" | "major" | "minor"`).

`scoring.py`: Pure functions, NO LLM calls. Imports and raises `ScoringParseError` from `domain.errors`; it does not re-export error types. `render_stage_a_prompt(jd_text: str, resume_md: str, rubric_md: str) -> list[Message]` builds message list. `parse_stage_a_response(raw: str, model: str, prompt_hash: str, resume_hash: str, cost_usd: float | None = None) -> StageAResult` parses JSON, handles markdown-wrapped JSON (strips ```json fences), validates score 0-100, extracts one_line and timing_eligible. Metadata (model, prompt_hash, resume_hash, cost_usd) is passed in by the caller (service layer) and included in the returned StageAResult; omitted cost remains `None` to mean unknown, not zero. Raises `ScoringParseError` on invalid JSON, out-of-range score, missing keys, and attaches the raw response when available.

Stage B has one canonical raw LLM contract:
- `block_a.verdict`: "apply" | "consider" | "skip"
- `block_b.summary`: string
- `block_c.score_0_100`: integer 0-100
- `block_c.strong_match`: list of `{requirement, evidence}` objects
- `block_c.gaps`: list of `{requirement, severity, mitigation}` objects
- `block_e.hooks`: list of strings

`parse_stage_b_response(raw: str, model: str, prompt_hash: str, resume_hash: str, cost_usd: float | None = None) -> StageBResult` validates that raw block schema and maps it into normalized domain fields: `verdict`, `jd_summary`, `fit_analysis`, and `resume_hooks`. It also stores the exact parsed block object in `StageBResult.raw_blocks` so the store can persist raw block JSON for later audit/snapshot parity. Omitted cost remains `None` to mean unknown. Walking skeleton prompt templates use simple string formatting (no Jinja2), explicitly name the JSON fields the parser expects, and instruct the model to return only JSON.

`quality.py`: `assess_quality(jd_text: str | None) -> QualityBand`. None/empty → missing, len < 200 → stub, 200-500 inclusive → partial, 501-1000 → good, >1000 → full.

`filtering.py`: `HardFilters` dataclass (title_blocklist: list[str], company_blocklist: list[str]). `apply_hard_filters(job: JobPosting, filters: HardFilters) -> str | None`. Returns None if passes, reason string if filtered (title match or company match, case-insensitive).

**Acceptance criteria:**
- [ ] `parse_stage_a_response('{"score": 85, "one_line": "Good fit", "timing_eligible": "eligible"}', model="mock", prompt_hash="h1", resume_hash="h2")` returns StageAResult with score=85, model="mock"
- [ ] `parse_stage_a_response` handles ```json wrapped content
- [ ] `parse_stage_a_response` raises ScoringParseError on score=150
- [ ] `parse_stage_a_response` raises ScoringParseError on truncated JSON
- [ ] `parse_stage_a_response` raises ScoringParseError on LLM refusal text
- [ ] `ScoringParseError.raw_response` preserves invalid raw LLM payloads when parsing fails
- [ ] `parse_stage_b_response` correctly builds StageBResult with FitAnalysis
- [ ] `parse_stage_b_response` preserves raw block JSON in StageBResult.raw_blocks
- [ ] `parse_stage_b_response` raises ScoringParseError on missing block_c keys
- [ ] Prompt rendering includes the exact Stage A and Stage B JSON contracts
- [ ] `ScoringParseError` is defined in `domain.errors`
- [ ] `assess_quality(None)` → MISSING; `assess_quality("x"*1500)` → FULL
- [ ] `apply_hard_filters` blocks title with blocklist word, passes clean job
- [ ] No external imports in any domain file
- [ ] All tests pass, committed

---

## Task 7: MockLLM + MockSource Adapters

**Files:**
- Create: `src/jobfeed/adapters/llm/mock.py`
- Create: `src/jobfeed/adapters/sources/mock.py`
- Test: `tests/unit/test_mock_adapters.py`

**What to build:**
`MockLLM`: implements LLMClient Protocol (async). `complete(request)` inspects `request.model`: if contains "stage-a", returns LLMResponse with content = JSON `{"score": 75, "one_line": "Mock evaluation", "timing_eligible": "eligible"}`; if contains "stage-b", returns JSON with block_a (verdict: "consider"), block_b (summary: "Mock JD summary"), block_c (score_0_100: 72, strong_match: [...], gaps: [...]), block_e (hooks: [...]). cost_usd=0.0, tokens=100, cached=False.

`MockSource`: implements SimpleSource Protocol (async). `fetch_jobs(config)` returns N canned JobPosting objects (N from config.get("count", 3)). platform="mock", canonical_ids "mock-1"/"mock-2"/..., realistic titles/companies, and a fixed long `jd_text` fixture over 500 characters so `assess_quality(jd_text)` deterministically returns `QualityBand.GOOD` or better. Do not use the short string `"Mock JD for ..."` as the whole JD body.

**Acceptance criteria:**
- [ ] MockLLM stage-a response is valid JSON parseable by `parse_stage_a_response` from Task 6
- [ ] MockLLM stage-b response is valid JSON parseable by `parse_stage_b_response` from Task 6
- [ ] MockSource returns list[JobPosting] with platform="mock"
- [ ] MockSource count configurable via config dict
- [ ] MockSource JD text length is >500 characters and quality assessment is deterministic
- [ ] Both satisfy their Protocol (`isinstance` check with runtime_checkable)
- [ ] All tests pass, committed

---

## Task 8: SQLiteStore

**Files:**
- Create: `src/jobfeed/adapters/store/sqlite.py`
- Create: `src/jobfeed/adapters/store/sqlite_mapping.py`
- Create: `src/jobfeed/adapters/store/sqlite_stage_b_mapping.py`
- Create: `src/jobfeed/adapters/store/sqlite_row.py`
- Create: `src/jobfeed/adapters/store/sqlite_params.py`
- Create: `src/jobfeed/adapters/store/sqlite_sql.py`
- Create: `src/jobfeed/adapters/store/schema.sql`
- Test: `tests/integration/test_sqlite_store.py`

**What to build:**
SQLiteStore implementing JobStore protocol via aiosqlite. `schema.sql`: tables `jobs` (id INTEGER PRIMARY KEY, platform TEXT NOT NULL, canonical_id TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL, company TEXT NOT NULL, location TEXT NOT NULL, jd_text TEXT, jd_quality TEXT with CHECK for known `QualityBand` values, posted_at TEXT, discovered_at TEXT NOT NULL, enriched_at TEXT, enrich_source TEXT, UNIQUE(platform, canonical_id)), `evaluations` (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id), stage_a_score INTEGER with 0-100 CHECK, stage_a_one_line TEXT, stage_a_timing_eligible TEXT, stage_a_status TEXT with CHECK for `completed`/`error`, stage_a_error TEXT, stage_a_model TEXT, stage_a_cost_usd REAL, stage_a_prompt_hash TEXT, stage_a_resume_hash TEXT, stage_b_verdict TEXT with CHECK for `apply`/`consider`/`skip`, stage_b_jd_summary TEXT, block_a_json TEXT, block_b_json TEXT, block_c_json TEXT, block_e_json TEXT, stage_b_status TEXT with CHECK for `completed`/`error`, stage_b_error TEXT, stage_b_model TEXT, stage_b_cost_usd REAL, stage_b_prompt_hash TEXT, stage_b_resume_hash TEXT, created_at TEXT, updated_at TEXT), `pipeline_runs` (id INTEGER PRIMARY KEY, run_id TEXT NOT NULL UNIQUE, started_at TEXT NOT NULL, source TEXT NOT NULL, jobs_discovered INTEGER DEFAULT 0, jobs_inserted INTEGER DEFAULT 0, jobs_updated INTEGER DEFAULT 0, jobs_filtered INTEGER DEFAULT 0, jobs_ml_gated INTEGER DEFAULT 0, stage_a_scored INTEGER DEFAULT 0, stage_b_scored INTEGER DEFAULT 0, jobs_scored INTEGER DEFAULT 0, total_llm_cost_usd REAL DEFAULT 0.0, errors INTEGER DEFAULT 0, finished_at TEXT).

`connect()` opens DB file (creates parent dirs), enables `PRAGMA foreign_keys = ON`, and runs schema.sql with `IF NOT EXISTS`; it is idempotent and reuses an existing open connection. `close()` serializes with in-flight store operations before closing so a write cannot lose its connection mid-transaction. `save_job()` must not rely on SQLite `RETURNING` to infer inserted vs updated and must be race-safe for concurrent duplicate source identities: begin a single immediate transaction, perform `INSERT ... ON CONFLICT(platform, canonical_id) DO NOTHING`; if the insert succeeds, return `SaveJobResult(job_id, inserted=True, updated=False)`; otherwise load the existing id and `UPDATE` mutable fields, returning `SaveJobResult(job_id, inserted=False, updated=True)`. The whole inserted/updated decision commits once. On update, nullable enrichment fields must use `COALESCE(new_value, existing_value)`: `jd_text`, `jd_quality`, `posted_at`, `enriched_at`, and `enrich_source`. Partial scan/upsert data must not erase an already enriched JD or enrichment metadata by writing `NULL`. In Phase 0, `updated=True` means "existing row was upserted", not necessarily "field values changed". `get_job()` by rowid and returns JobPosting with `id` populated. `list_jobs()` returns most recent N by discovered_at. `save_stage_a()` uses `INSERT INTO evaluations (job_id, stage_a_status, stage_a_model, stage_a_cost_usd, stage_a_prompt_hash, stage_a_resume_hash, ...) VALUES (...) ON CONFLICT(job_id) DO UPDATE SET stage_a_score=excluded.stage_a_score, stage_a_status=excluded.stage_a_status, stage_a_error=NULL, stage_a_model=excluded.stage_a_model, stage_a_cost_usd=excluded.stage_a_cost_usd, stage_a_prompt_hash=excluded.stage_a_prompt_hash, stage_a_resume_hash=excluded.stage_a_resume_hash, updated_at=now, ...`. On success: sets `stage_a_status='completed'`. `save_stage_a_error(job_id, error)` sets `stage_a_status='error'` and `stage_a_error=error` without modifying Stage B columns. `save_stage_b()` same pattern with separate `stage_b_model`, `stage_b_cost_usd`, `stage_b_prompt_hash`, `stage_b_resume_hash`, sets `stage_b_status='completed'`, clears `stage_b_error`, updates `updated_at`, and stores `block_a_json`, `block_b_json`, `block_c_json`, `block_e_json` from `StageBResult.raw_blocks`. `save_stage_b_error(job_id, error)` sets `stage_b_status='error'` and `stage_b_error=error` without modifying Stage A columns. `load_pending_stage_a()` returns jobs WHERE no evaluation row exists OR stage_a_status IS NULL (never evaluated) — does NOT retry `stage_a_status='error'` automatically. `load_pending_stage_b()` returns jobs WHERE stage_a_status='completed' AND stage_a_score >= threshold AND stage_b_status IS NULL. `list_evaluated_jobs()` JOINs jobs + evaluations and must reconstruct StageAResult and StageBResult losslessly, including stage-specific model/cost/prompt_hash/resume_hash and Stage B raw block JSON. Shared `sqlite_row.py` helpers validate required row values; Stage B raw block mapping must validate required object/list fields and severity values on read; corrupt persisted JSON should fail with `ValueError`, not raw `KeyError`/`TypeError`. `record_pipeline_run()` inserts and requires `source`. `get_pipeline_run()` by run_id.

**Acceptance criteria:**
- [ ] `save_job` + `get_job` round-trips a JobPosting
- [ ] `save_job` with same (platform, canonical_id) updates fields, does not duplicate, and returns inserted/updated flags
- [ ] concurrent `save_job` calls for one source identity produce one row and deterministic inserted/updated flags
- [ ] `save_job` insert/update decision happens in one transaction with one commit
- [ ] `save_job` second call preserves existing jd_text if new one is None (COALESCE)
- [ ] `save_job` preserves nullable enrichment fields (`jd_text`, `jd_quality`, `posted_at`, `enriched_at`, `enrich_source`) when incoming values are None
- [ ] DB CHECK constraints reject invalid scores, statuses, verdicts, and missing domain-required `url`/`location`
- [ ] Evaluation rows record `created_at` and `updated_at`
- [ ] `save_stage_a` + `load_pending_stage_b` only returns above-threshold jobs
- [ ] `save_stage_a_error` persists `stage_a_status='error'` and excludes that job from automatic pending retry
- [ ] `save_stage_b_error` persists `stage_b_status='error'` and excludes that job from automatic pending retry
- [ ] `save_stage_b` persists raw block JSON columns from `StageBResult.raw_blocks`
- [ ] Stage A and Stage B metadata are persisted in separate columns and do not overwrite each other
- [ ] `load_pending_stage_a` excludes already-evaluated jobs
- [ ] `list_evaluated_jobs` returns JobEvaluation with joined stage_a/stage_b data and losslessly reconstructs model/cost/hash metadata
- [ ] `list_evaluated_jobs` validates Stage B raw block JSON and gap severity on read
- [ ] `record_pipeline_run` + `get_pipeline_run` round-trips
- [ ] `record_pipeline_run` + `get_pipeline_run` round-trips stage_a_scored and stage_b_scored separately
- [ ] `pipeline_runs.source` is NOT NULL and rejects missing source values
- [ ] Repeated `connect()` is idempotent and does not leak a connection
- [ ] `close()` waits for an in-flight save before closing the connection
- [ ] `evaluations.job_id` has UNIQUE constraint (verified by attempting duplicate insert)
- [ ] `evaluations.job_id` foreign key is enforced (missing job_id insert fails)
- [ ] Tests use temp file DB via pytest tmp_path
- [ ] All tests pass, committed

---

## Task 9: Domain Digest

**Files:**
- Create: `src/jobfeed/domain/digest.py`
- Test: `tests/unit/test_digest.py`

**What to build:**
`render_digest(evaluations: list[JobEvaluation], stats: dict, cutoff_at: datetime | None = None) -> str`. Groups into tiers by verdict: apply (verdict == apply), consider (verdict == consider), skip (everything else or no stage_b). Stats dict: total_jobs, scraped_today, llm_calls_today, stage_b_evaluated, filtered_count. If `cutoff_at` is provided, it and each compared `JobPosting.discovered_at` must be timezone-aware so digest comparisons do not mix naive and aware datetimes.

Sections: (1) Header "# Daily Digest" + today's date, (2) Apply tier — per job: score, "title @ company", jd_summary, strengths, gaps, URL. If cutoff_at provided, split into "New" (discovered_at > cutoff) and "Previously seen", (3) Consider tier — one-liner: score, "title @ company", one_line, (4) Skip tier — "(N jobs skipped)", (5) Stats summary.

**Acceptance criteria:**
- [ ] Digest contains "# Daily Digest" and date
- [ ] Apply jobs show score, title, company, URL, strengths, gaps
- [ ] Consider jobs show as one-liners
- [ ] Skip shows count
- [ ] cutoff_at splits Apply into "New" / "Previously seen"
- [ ] timezone-naive cutoff_at raises ValueError
- [ ] timezone-naive discovered_at raises ValueError when cutoff_at is used
- [ ] Empty input produces valid digest with zero counts
- [ ] No external imports
- [ ] All tests pass, committed

---

## Task 10: Services (Scan, Evaluate, Digest)

**Files:**
- Create: `src/jobfeed/services/scan.py`
- Create: `src/jobfeed/services/evaluate.py`
- Create: `src/jobfeed/services/digest.py`
- Create: `src/jobfeed/services/error_handler.py`
- Test: `tests/integration/test_services.py`

**What to build:**
All services are async classes accepting ports via constructor. Recoverable service error handling is centralized in `ServiceErrorHandler` with `handle_stage_parse_error`, `handle_stage_runtime_error`, and `handle_source_fetch_error`.

`runs.py`: `start_pipeline_run(source: str) -> PipelineRun` centralizes run_id generation and UTC start time construction for services.

`ScanService(store: JobStore, logger)`: `async run(sources: list[tuple[str, SimpleSource, dict]]) -> PipelineRun`. At start: creates a PipelineRun through `start_pipeline_run()` with `source` set to the single source name when exactly one source is scanned, otherwise `"scan"` for aggregate multi-source runs, then calls `bind_run_id(run_id)`. It fetches configured sources concurrently, and for each (name, source, config) tuple calls `await source.fetch_jobs(config)`, then `await store.save_job()` per posting. It increments `jobs_discovered` by fetched postings, `jobs_inserted` when `SaveJobResult.inserted` is true, and `jobs_updated` when `SaveJobResult.updated` is true. Logs with structlog (source name, counts — run_id auto-attached via context). Catches per-source exceptions, delegates to `ServiceErrorHandler.handle_source_fetch_error`, and continues. At end: sets PipelineRun.finished_at, calls `await store.record_pipeline_run(run)`.

`EvaluateService(store: JobStore, llm: LLMClient, settings, logger)`: `async run(dry_run: bool = False) -> PipelineRun`. At start: creates a PipelineRun through `start_pipeline_run("evaluate")`, then calls `bind_run_id(run_id)`. Loads pending Stage A jobs and Stage B jobs. If `dry_run`, logs both pending queues with the stage name and skips LLM calls. Otherwise: render prompt via domain.scoring, call `llm.complete()` through `asyncio.wait_for(..., timeout=settings.llm.timeout_s)`, capped by `settings.llm.max_concurrent`, then parse response via domain.scoring (passing model, prompt_hash="skeleton", resume_hash="skeleton", and the exact `LLMResponse.cost_usd`, preserving `None` as unknown cost). On success: calls `save_stage_a()`/`save_stage_b()`, increments the stage counter, and recomputes `jobs_scored = stage_a_scored + stage_b_scored`; aggregate `total_llm_cost_usd` treats `None` as 0 only for summing. On `ScoringParseError`, delegates to `ServiceErrorHandler.handle_stage_parse_error(run, stage, job.id, error)` so persistence, logging, and run error counting stay in one service helper. On timeout or other LLM runtime exception, delegates to `ServiceErrorHandler.handle_stage_runtime_error(run, stage, job.id, error)`. Stage B loads after Stage A scoring so newly eligible jobs are included. At end: sets PipelineRun.finished_at, records via store.

`DigestService(store: JobStore, logger)`: `async run(cutoff_at: datetime | None = None) -> str`. Calls `await store.list_evaluated_jobs()`, computes the Phase 0 placeholder stats dict, calls `domain.digest.render_digest()`. The placeholder stats use evaluated rows until Phase 1 adds store-backed run/status aggregate queries.

Tests use MockLLM + MockSource + real SQLiteStore (temp file). These are integration tests (real DB).

**Acceptance criteria:**
- [ ] ScanService saves mock jobs, PipelineRun.jobs_discovered matches
- [ ] ScanService sets PipelineRun.source to the source name for one source and `"scan"` for multi-source runs
- [ ] ScanService records inserted vs updated counts on idempotent reruns
- [ ] ScanService continues on source failure, PipelineRun.errors incremented
- [ ] EvaluateService processes pending, saves Stage A then Stage B
- [ ] EvaluateService returns PipelineRun with stage_a_scored and stage_b_scored populated separately
- [ ] EvaluateService sets PipelineRun.source to `"evaluate"`
- [ ] EvaluateService persists parse failures via explicit error store methods, logs error, continues
- [ ] EvaluateService respects `settings.llm.max_concurrent`
- [ ] EvaluateService respects `settings.llm.timeout_s` and persists timeout/runtime failures as explicit stage errors
- [ ] Recoverable service errors go through `ServiceErrorHandler` using `handle_*_error` methods
- [ ] EvaluateService dry_run=True does not call LLM, logs Stage A and Stage B pending job lists
- [ ] DigestService returns Markdown with mock job data
- [ ] All services accept ports via constructor (no hardcoded adapters)
- [ ] structlog entries include run_id
- [ ] All tests pass, committed

---

## Task 11: CLI Shell + DI Factory

**Files:**
- Modify: `src/jobfeed/cli/__init__.py` (add create_app, wire DI)
- Create: `src/jobfeed/cli/scan.py`
- Create: `src/jobfeed/cli/evaluate.py`
- Create: `src/jobfeed/cli/digest.py`
- Test: `tests/e2e/test_cli_skeleton.py`

**What to build:**
`cli/__init__.py`: `create_app(config_path: Path | None = None) -> dict` loads Settings, configures logging, builds SQLiteStore (from settings.db.sqlite_path), builds MockLLM, builds MockSource, builds services, returns context dict with all services + store. Phase 0 accepts only `settings.db.backend == "sqlite"`; any other backend fails fast with a Click error. Click group stores context. Store lifecycle: each command opens connection at start, closes at end. Store lifecycle and command execution exceptions are converted to `click.ClickException` so normal user failures do not print Python tracebacks.

`cli/scan.py`: `jobfeed scan` with `--source` option (default "mock" for the Phase 0 skeleton until config-selected real sources land). Bridges to async via `asyncio.run()`. Prints "Discovered N jobs, inserted N, updated N".

`cli/evaluate.py`: `jobfeed evaluate` with `--dry-run` flag. Prints "Evaluated N (Stage A), N (Stage B)".

`cli/digest.py`: `jobfeed digest [--cutoff-at ISO_DATETIME]`. Prints digest Markdown to stdout. `--cutoff-at` must be a timezone-aware ISO-8601 datetime and is forwarded to `DigestService.run(cutoff_at=...)`.

Fast pytest E2E test: writes temp config.toml pointing to a pytest `tmp_path` database, passes `--config` to each invocation via Click CliRunner, and never uses `~/.jobfeed/` or the repo-local `.jobfeed-dev/`. Runs scan → evaluate → digest in sequence, verifies each exits 0 and final digest contains mock job titles.

Container E2E smoke: uses the canonical host launcher and repo-local `.jobfeed-dev/` mount. Runs `./bin/jobfeed scan --source mock`, `./bin/jobfeed evaluate`, and `./bin/jobfeed digest` in sequence, verifies each exits 0 and final digest contains mock job titles. This smoke can live in CI as a shell step rather than a pytest test if invoking Docker from pytest is too slow or brittle.

**Acceptance criteria:**
- [ ] `jobfeed --config tmp.toml scan --source mock` exits 0, prints discovered/inserted/updated counts
- [ ] `jobfeed --config tmp.toml evaluate` exits 0, prints evaluation counts
- [ ] `jobfeed --config tmp.toml evaluate --dry-run` exits 0, does not evaluate
- [ ] `jobfeed --config tmp.toml digest` exits 0, outputs Markdown with mock jobs
- [ ] `jobfeed --config tmp.toml digest --cutoff-at 2026-05-21T00:00:00+00:00` exits 0 and renders Apply new/previously-seen sections
- [ ] `jobfeed --config tmp.toml digest --cutoff-at 2026-05-21T00:00:00` exits non-zero with a user-facing validation error
- [ ] explicit missing `--config` exits non-zero with a user-facing error and no traceback
- [ ] unsupported `db.backend` exits non-zero with a user-facing error and no traceback
- [ ] Full chain scan → evaluate → digest in one test produces coherent output
- [ ] Canonical Docker smoke `./bin/jobfeed scan --source mock` → `./bin/jobfeed evaluate` → `./bin/jobfeed digest` runs through Docker, uses repo-local `.jobfeed-dev/`, and produces coherent output
- [ ] `--verbose` increases log output
- [ ] Fast pytest E2E uses isolated temp DB (not `~/.jobfeed/`, not repo-local `.jobfeed-dev/`)
- [ ] All tests pass, committed

---

## Task 12: Architecture Boundary Tests + README + Verification

**Files:**
- Create: `tests/unit/test_architecture_boundaries.py`
- Create: `README.md`

**What to build:**
`test_architecture_boundaries.py`: Import-guard tests that verify hexagonal discipline. Scan `src/jobfeed/domain/` source files and assert none of them import from: click, fastapi, asyncpg, aiosqlite, openai, anthropic, pandas, pydantic, httpx, playwright. Scan `src/jobfeed/ports/` and assert no imports from adapters/. Scan `src/jobfeed/services/` and assert no imports from concrete adapter modules (adapters.store.sqlite, adapters.llm.mock, etc — services should only import from ports).

`README.md`: project description (1 paragraph), Docker-first quickstart (`docker compose build jobfeed-cli`, `./bin/jobfeed scan --source mock`, `./bin/jobfeed evaluate`, `./bin/jobfeed digest`), developer fallback (`pip install -e ".[dev]"`, `make test`, `make lint`, `make quality`, `make fmt`, `pre-commit install`), architecture overview (1 paragraph referencing spec doc), and links to `docs/engineering-standards.md` and ADR 0001.

Manual verification: run full chain through `./bin/jobfeed` from a clean Docker build, confirm DB at `.jobfeed-dev/dev.db` has jobs/evaluations/pipeline_runs rows.

**Acceptance criteria:**
- [ ] Architecture boundary tests pass (domain has no external imports, services don't import concrete adapters)
- [ ] `docker compose build jobfeed-cli && ./bin/jobfeed scan --source mock && ./bin/jobfeed evaluate && ./bin/jobfeed digest` works from clean checkout
- [ ] Host-native `pip install -e ".[dev]" && jobfeed --help` still works as developer fallback
- [ ] DB file created with correct tables and rows
- [ ] README quickstart is accurate
- [ ] `make docker-quality` passes
- [ ] `make test` passes all unit + integration + e2e + boundary + hygiene tests
- [ ] `make lint` passes with zero errors
- [ ] `make quality` passes
- [ ] All committed
