# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Jobfeed is a local-first job scanning and evaluation pipeline. Phase 0 was a Dockerized walking skeleton with mock adapters and a Click CLI. Phase 1 hardened the store layer with PostgreSQL as the sole persistence backend (SQLite adapter removed).

## Commands

```bash
make quality          # lint + test (run before marking any task complete)
make test             # pytest (unit + integration + e2e + hygiene); store tests need PG
make lint             # ruff check . && ruff format --check . && mypy src/
make fmt              # ruff format . && ruff check --fix .
pip install -e ".[dev]"  # local dev install
```

Run a single test: `python -m pytest tests/unit/test_scoring.py::test_name -v`

Store/integration/e2e tests require PostgreSQL. Provide via `PGTEST_DSN` (e.g. `export PGTEST_DSN="postgresql://jobfeed:jobfeed@localhost:5432/jobfeed_test"`) or let testcontainers start one automatically (Docker required). Pure-unit tests (domain, ports, hygiene) run without PG.

## Architecture

Hexagonal (ports & adapters) with strict layer boundaries enforced by `test_architecture_boundaries.py`:

- **`domain/`** — Pure stdlib only. Zero external imports. Models, scoring logic, filtering, digest rendering, shared errors (`errors.py`), shared types (`types.py`).
- **`ports/`** — Protocol interfaces. Imports domain only.
- **`services/`** — Async orchestration. Depends on ports + domain only. Never imports concrete adapters. Shared run factory in `runs.py`, error handling in `error_handler.py`.
- **`adapters/`** — Implements ports. May use external libraries. Store: `postgres.py` (PostgresStore, asyncpg), `legacy_import.py` (BulkImportPort + legacy v16 migration via stdlib sqlite3), `parity.py` (ParityReadPort + import verification), `_normalize.py` (company/title normalization).
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

- No external network IO, real LLM calls, browser automation, Temporal, or frontend code.
- PostgreSQL is the only supported store backend. Config: `db.url` (DSN) or env `JOBFEED_DB_URL`.
- No reads/writes to `~/.jobfeed/`.
- Do not weaken quality gates without explicit human approval.
- Phase 0 plan (`docs/plans/2026-05-21-jobfeed-rewrite-phase0-walking-skeleton.md`) is the Phase 0 source of truth; Phase 1 plan (`docs/plans/2026-05-21-jobfeed-rewrite-phase1-store-hardening.md`) governs store hardening.
## Commit Convention

Format: `type(scope): summary` — e.g. `feat(phase0):`, `fix(phase0):`, `test(phase0):`, `docs(phase0):`.

For complex commits, add a body after a blank line explaining:
- Why this approach was chosen
- What tradeoffs were made
- Edge cases or migration risks
- Non-obvious business constraints

Do not add `Co-Authored-By` or any Claude/AI attribution to commit messages.
