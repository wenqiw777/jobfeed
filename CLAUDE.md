# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Jobfeed is a local-first job scanning and evaluation pipeline. Phase 0 was a Dockerized walking skeleton with mock adapters and a Click CLI. Phase 1 hardened the store layer with PostgreSQL as the sole persistence backend (SQLite adapter removed). Phase 2 wired the first real external data source (ATS: Greenhouse / Ashby / Lever) end-to-end with auto-probe vendor detection, per-company error isolation, and dead-slug recovery. Phase 3 wires real LLM subprocess adapters (`codex-cli/*`, `claude-cli/*`) behind ports while keeping mock backends for deterministic local and CI smoke runs.

## Commands

```bash
make quality          # lint + test (unit + contract, no PG needed)
make lint             # ruff check . && ruff format --check . && mypy src/
make fmt              # ruff format . && ruff check --fix .
pip install -e ".[dev]"  # local dev install (or: uv pip install --python .venv/bin/python -e ".[dev]")
```

Run entry points (repo root): `./setup` (one-time: config + runtime + Postgres + migrations) then `./scan` (run a scan; `./scan --source mock` for an offline smoke). `./bin/jobfeed` is the canonical Docker CLI (`docker compose run --rm jobfeed-cli jobfeed ...`).

Run a single test: `python -m pytest tests/unit/test_scoring.py::test_name -v`

**Test markers (Phase 2):** Default `addopts` excludes `postgres` and `live` markers. `make quality` runs unit + contract tests without PG. To run PG-backed tests: `pytest -m postgres -o "addopts=" -v`. To run live smoke tests (real HTTP): `pytest -m live -o "addopts="`. Store/integration/e2e tests require PostgreSQL via `PGTEST_DSN` (e.g. `export PGTEST_DSN="postgresql://jobfeed:jobfeed@localhost:5432/jobfeed_test"`) or testcontainers (Docker required).

## Architecture

Hexagonal (ports & adapters) with strict layer boundaries enforced by `test_architecture_boundaries.py`:

- **`domain/`** — Pure stdlib only. Zero external imports. Models (`models.py`, `models_llm.py`, `models_ops.py`, `models_application.py`, `models_status.py`), scoring (`scoring.py` plus the `scoring_parse.py` / `scoring_refusal.py` / `scoring_schema.py` split), filtering, digest rendering, status, quality, shared errors (`errors.py`), shared types (`types.py`).
- **`ports/`** — Protocol interfaces. Imports domain only (plus sibling-port DTOs: `ports/enrich.py` reuses `EnrichResult` from `ports/source.py`). `JobStore` (`store.py`), `StoreOpsMixin` (`store_ops.py`), evaluation-batch/claim extensions (`store_ext.py`, `store_claims.py`), the application-audit capability (`StoreApplicationMixin` in `store_application.py`), the status/workflow capability (`StoreStatusMixin` in `store_status.py`), `LLMClient` (`llm.py`), `PromptRenderer` (`prompts.py`), `SimpleSource` (`source.py`), `JobEnricher` (`enrich.py`).
- **`services/`** — Async orchestration. Depends on ports + domain only. Never imports concrete adapters. `ScanService` (`scan.py`), `EvaluateService` (`evaluate.py`) with its budget/claims/helpers split (`_evaluate_budget.py`, `_evaluate_claims.py`, `_evaluate_helpers.py`, `evaluate_types.py`), `DigestService` (`digest.py`), `EnrichService` (`enrich.py`), shared run factory (`runs.py`), error handling (`error_handler.py`).
- **`adapters/`** — Implements ports. May use external libraries. Store: `postgres.py` (PostgresStore, asyncpg), `legacy_import.py` (BulkImportPort + legacy v16 migration via stdlib sqlite3), `parity.py` (ParityReadPort + import verification), `_normalize.py` (company/title normalization). Sources: `ats.py` (ATSSource facade, SimpleSource), `_ats_greenhouse.py` / `_ats_ashby.py` / `_ats_lever.py` (vendor adapters), `_ats_probe.py` (auto-probe), `_http.py` (shared httpx client + exception classes), `linkedin_guest.py` (anonymous guest-endpoint source + `LinkedInGuestEnricher` facade) with its `_linkedin_guest_parse.py` / `_linkedin_guest_http.py` / `_linkedin_guest_discover.py` split; JobSpy (`indeed_jobspy.py` + `_jobspy*.py`) is now Indeed-only. LLM: `llm/codex.py` (CodexCliLLM), `llm/claude.py` (ClaudeCliLLM), `llm/mock.py` (MockLLM) behind the `LLMClient` port, plus provider routing (`llm/_factory.py`), Jinja2 prompt rendering (`llm/_prompts.py`), shared subprocess helpers (`llm/_subprocess.py`), and cost estimation over a vendored price table (`llm/_pricing.py`, `llm/model_prices.json`).
- **`cli/`** — Thin sync Click shell. Bridges to async via `asyncio.run()`. DI wiring in `cli/__init__.py` (`create_app`). Commands: `scan.py`, `evaluate.py`, `digest.py`, `migrate.py`, `enrich.py` (enrich-paste + enrich-linkedin-guest). No business logic. LLM clients and `EvaluateService` are built lazily inside the `evaluate` command (not `create_app`), so `scan`/`digest`/`migrate` run without an LLM toolchain.

All ports, adapters, and services are async. CLI is sync.

## Coding Standards

Read `docs/engineering-standards.md` before editing code. Key enforced rules:

- Cyclomatic complexity hard max: 10. Nesting depth max: 2. Max args: 5.
- No bare `except`. No silent `pass` in exception handlers.
- Production files under `src/jobfeed/` must be ≤300 lines, enforced as a blocking gate. **Exempt:** the `adapters/store/` layer, `cli/migrate.py`, and `domain/ml_features.py` — the PostgresStore + legacy-migration surface is inherently large, and `ml_features.py` keeps the ML-gate vocab name lists together with the compiled regex tables that index them (they must stay in lockstep); fragmenting any of these into ≤300-line shards harms readability. The gate stays blocking for the rest of `domain/`, `services/`, `ports/`, and the rest of `cli/`. (Phase 1 amendment, approved 2026-05-22; `ml_features.py` added 2026-06-07.)
- Nested loops forbidden in `cli/` and `services/`. Allowed elsewhere only in named helpers with documented time complexity.
- `snake_case` for modules/functions/variables. `PascalCase` for classes/protocols/enums.
- Boolean names: `is_`, `has_`, `can_`, `should_`, `needs_` prefixes.
- Ports = capability nouns (`JobStore`). Adapters = implementation nouns (`PostgresStore`). Services = workflow nouns (`ScanService`).

## Constraints

- No Temporal or frontend code. Browser automation is forbidden EXCEPT Playwright in the LinkedIn SessionSource adapter (`adapters/sources/linkedin.py`) and its one-time login command (`cli/login.py`) — permitted per the **Phase 4 amendment, approved 2026-05-29**, solely for LinkedIn scraping; its cookie profile and cross-process enrich lock live under `~/.cache/jobfeed/`, never `~/.jobfeed/`. ATS adapters may make outbound HTTP to public vendor APIs (Greenhouse, Ashby, Lever); the Phase 4 SpeedyApply and JobSpy sources may likewise fetch public job-board APIs / lists, and the `linkedin_guest` source may fetch LinkedIn's anonymous guest endpoints (no login, no browser). Phase 3 allows real LLM calls only through the subprocess-backed LLM adapters and only when the configured backend/toolchain is intentionally selected; use mock LLM config for deterministic smoke and CI flows.
- PostgreSQL is the only supported store backend. Config: `db.url` (DSN) or env `JOBFEED_DB_URL`.
- `~/.jobfeed/` is **read-only** (amendment, approved 2026-06-04). Reading the user's existing legacy artifacts there (the v16 `jobs.db`, legacy config, and the legacy-trained ML model read **once** to seed `models/ml_gate/`) is permitted; **never create, modify, move, or delete anything under `~/.jobfeed/`** — it is the user's real production data. Runtime and test writes (cookie profiles, locks, dev DBs, digests) go under `~/.cache/jobfeed/` or repo-local `.jobfeed-dev/`.
- ML gate (Phase 5 amendment, approved 2026-06-04): the trained XGBoost model is **committed in-repo** under `models/ml_gate/` (`[ml_gate].model_dir` defaults there) — no `~/.cache` copy and no `~/.jobfeed/` dependency at runtime, so `git clone` is self-contained on any machine. `xgboost` / `numpy` / `fastembed` are permitted **core** dependencies; the embedder is `all-MiniLM-L6-v2` run via `fastembed` on **onnxruntime** (no torch), so the real gate runs in the default install and Docker image with no segfault risk. The ONNX embedder weights (~87MB, too large to commit) are **baked into the default Docker image at build time** at the runtime cache path (`JOBFEED_ML_CACHE_DIR=/cache/jobfeed/fastembed`), so the canonical `docker compose run`/`./bin/jobfeed` evaluation path does **ZERO** runtime download and works **offline**; a fresh `mlcache` named volume is seeded from those baked weights on first mount. The only outbound network for the weights is a **host-native** (non-Docker) one-time, per-machine HuggingFace download, triggered by `@mlmodel`-marked tests and real host evaluation runs — never by `make quality` (the `mlmodel` marker is excluded from default `addopts`). A host cold-cache miss logs one `embedder_weights_downloading` line before downloading; run `jobfeed ml-gate fetch` to pre-seed explicitly, or offline machines may pre-seed the cache dir directly.
- Do not weaken quality gates without explicit human approval.
- Phase 0 plan (`docs/plans/2026-05-21-jobfeed-rewrite-phase0-walking-skeleton.md`) is the Phase 0 source of truth; Phase 1 plan (`docs/plans/2026-05-21-jobfeed-rewrite-phase1-store-hardening.md`) governs store hardening; Phase 2 plan (`docs/plans/2026-05-23-jobfeed-rewrite-phase2-first-real-source.md`) governs ATS source integration; Phase 3 plan (`docs/plans/2026-05-23-jobfeed-rewrite-phase3-first-real-llm.md`) governs real LLM integration; Phase 4 plan (`docs/plans/2026-05-29-jobfeed-rewrite-phase4-source-expansion.md`) governs source expansion (SpeedyApply + JobSpy + LinkedIn Playwright); Phase 5 plan (`docs/plans/2026-06-04-jobfeed-rewrite-phase5-eval-pipeline-ml-gate.md`) governs the evaluation funnel + ML gate (hard filter + dedupe + in-repo XGBoost inference).
## Commit Convention

Format: `type(scope): summary` — e.g. `feat(phase0):`, `fix(phase0):`, `test(phase0):`, `docs(phase0):`.

For complex commits, add a body after a blank line explaining:
- Why this approach was chosen
- What tradeoffs were made
- Edge cases or migration risks
- Non-obvious business constraints

Do not add `Co-Authored-By` or any Claude/AI attribution to commit messages.
