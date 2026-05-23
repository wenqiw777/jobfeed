# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Jobfeed is a local-first job scanning and evaluation pipeline. Phase 0 was a Dockerized walking skeleton with mock adapters and a Click CLI. Phase 1 hardened the store layer with PostgreSQL as the sole persistence backend (SQLite adapter removed). Phase 2 wired the first real external data source (ATS: Greenhouse / Ashby / Lever) end-to-end with auto-probe vendor detection, per-company error isolation, and dead-slug recovery. LLM remains MockLLM.

## Commands

```bash
make quality          # lint + test (unit + contract, no PG needed)
make lint             # ruff check . && ruff format --check . && mypy src/
make fmt              # ruff format . && ruff check --fix .
pip install -e ".[dev]"  # local dev install
```

Run a single test: `python -m pytest tests/unit/test_scoring.py::test_name -v`

**Test markers (Phase 2):** Default `addopts` excludes `postgres` and `live` markers. `make quality` runs unit + contract tests without PG. To run PG-backed tests: `pytest -m postgres -o "addopts=" -v`. To run live smoke tests (real HTTP): `pytest -m live -o "addopts="`. Store/integration/e2e tests require PostgreSQL via `PGTEST_DSN` (e.g. `export PGTEST_DSN="postgresql://jobfeed:jobfeed@localhost:5432/jobfeed_test"`) or testcontainers (Docker required).

## Architecture

Hexagonal (ports & adapters) with strict layer boundaries enforced by `test_architecture_boundaries.py`:

- **`domain/`** — Pure stdlib only. Zero external imports. Models, scoring logic, filtering, digest rendering, shared errors (`errors.py`), shared types (`types.py`).
- **`ports/`** — Protocol interfaces. Imports domain only.
- **`services/`** — Async orchestration. Depends on ports + domain only. Never imports concrete adapters. Shared run factory in `runs.py`, error handling in `error_handler.py`.
- **`adapters/`** — Implements ports. May use external libraries. Store: `postgres.py` (PostgresStore, asyncpg), `legacy_import.py` (BulkImportPort + legacy v16 migration via stdlib sqlite3), `parity.py` (ParityReadPort + import verification), `_normalize.py` (company/title normalization). Sources: `ats.py` (ATSSource facade, SimpleSource), `_ats_greenhouse.py` / `_ats_ashby.py` / `_ats_lever.py` (vendor adapters), `_ats_probe.py` (auto-probe), `_http.py` (shared httpx client + exception classes).
- **`cli/`** — Thin sync Click shell. Bridges to async via `asyncio.run()`. DI wiring in `cli/__init__.py` (`create_app`). No business logic.

All ports, adapters, and services are async. CLI is sync.

## Coding Standards

Read `docs/engineering-standards.md` before editing code. Key enforced rules:

- Cyclomatic complexity hard max: 10. Nesting depth max: 2. Max args: 5.
- No bare `except`. No silent `pass` in exception handlers.
- Production files under `src/jobfeed/` must be ≤300 lines, enforced as a blocking gate. **Exempt:** the `adapters/store/` layer and `cli/migrate.py` — the PostgresStore + legacy-migration surface is inherently large, and fragmenting it into ≤300-line shards harms readability. The gate stays blocking for `domain/`, `services/`, `ports/`, and the rest of `cli/`. (Phase 1 amendment, approved 2026-05-22.)
- Nested loops forbidden in `cli/` and `services/`. Allowed elsewhere only in named helpers with documented time complexity.
- `snake_case` for modules/functions/variables. `PascalCase` for classes/protocols/enums.
- Boolean names: `is_`, `has_`, `can_`, `should_`, `needs_` prefixes.
- Ports = capability nouns (`JobStore`). Adapters = implementation nouns (`PostgresStore`). Services = workflow nouns (`ScanService`).

## Constraints

- No real LLM calls, browser automation, Temporal, or frontend code. ATS adapters make outbound HTTP to public vendor APIs (Greenhouse, Ashby, Lever) — this is the only sanctioned external IO beyond PostgreSQL.
- PostgreSQL is the only supported store backend. Config: `db.url` (DSN) or env `JOBFEED_DB_URL`.
- No reads/writes to `~/.jobfeed/`.
- Do not weaken quality gates without explicit human approval.
- Phase 0 plan (`docs/plans/2026-05-21-jobfeed-rewrite-phase0-walking-skeleton.md`) is the Phase 0 source of truth; Phase 1 plan (`docs/plans/2026-05-21-jobfeed-rewrite-phase1-store-hardening.md`) governs store hardening; Phase 2 plan (`docs/plans/2026-05-23-jobfeed-rewrite-phase2-first-real-source.md`) governs ATS source integration.
## Commit Convention

Format: `type(scope): summary` — e.g. `feat(phase0):`, `fix(phase0):`, `test(phase0):`, `docs(phase0):`.

For complex commits, add a body after a blank line explaining:
- Why this approach was chosen
- What tradeoffs were made
- Edge cases or migration risks
- Non-obvious business constraints

Do not add `Co-Authored-By` or any Claude/AI attribution to commit messages.
