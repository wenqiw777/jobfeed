# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Jobfeed is a local-first job scanning and evaluation pipeline. Phase 0 is a Dockerized walking skeleton with mock adapters, SQLite persistence, and a Click CLI.

## Commands

```bash
make quality          # lint + test (run before marking any task complete)
make test             # pytest (unit + integration + e2e + hygiene)
make lint             # ruff check . && ruff format --check . && mypy src/
make fmt              # ruff format . && ruff check --fix .
make docker-quality   # rebuild container, run quality inside Docker
pip install -e ".[dev]"  # local dev install
```

Run a single test: `python -m pytest tests/unit/test_scoring.py::test_name -v`

Canonical CLI (via Docker): `./bin/jobfeed scan --source mock`, `./bin/jobfeed evaluate`, `./bin/jobfeed digest`

## Architecture

Hexagonal (ports & adapters) with strict layer boundaries enforced by `test_architecture_boundaries.py`:

- **`domain/`** — Pure stdlib only. Zero external imports. Models, scoring logic, filtering, digest rendering, shared errors (`errors.py`), shared types (`types.py`).
- **`ports/`** — Protocol interfaces. Imports domain only.
- **`services/`** — Async orchestration. Depends on ports + domain only. Never imports concrete adapters. Shared run factory in `runs.py`, error handling in `error_handler.py`.
- **`adapters/`** — Implements ports. May use external libraries. Store split: `sqlite.py` (orchestration), `sqlite_sql.py` (SQL strings), `sqlite_mapping.py` / `sqlite_stage_b_mapping.py` (row mapping), `sqlite_row.py` (shared row helpers), `sqlite_params.py` (param serialization).
- **`cli/`** — Thin sync Click shell. Bridges to async via `asyncio.run()`. DI wiring in `cli/__init__.py` (`create_app`). No business logic.

All ports, adapters, and services are async. CLI is sync.

## Coding Standards

Read `docs/engineering-standards.md` before editing code. Key enforced rules:

- Cyclomatic complexity hard max: 10. Nesting depth max: 2. Max args: 5.
- No bare `except`. No silent `pass` in exception handlers.
- Production files under `src/jobfeed/` must be ≤300 lines (Phase 0 gate).
- Nested loops forbidden in `cli/` and `services/`. Allowed elsewhere only in named helpers with documented time complexity.
- `snake_case` for modules/functions/variables. `PascalCase` for classes/protocols/enums.
- Boolean names: `is_`, `has_`, `can_`, `should_`, `needs_` prefixes.
- Ports = capability nouns (`JobStore`). Adapters = implementation nouns (`SQLiteStore`). Services = workflow nouns (`ScanService`).

## Phase 0 Constraints

- No external network IO, real LLM calls, browser automation, Temporal, Postgres, or frontend code.
- No reads/writes to `~/.jobfeed/`. Default data path: `.jobfeed-dev/`.
- Do not weaken quality gates without explicit human approval.
- This Phase 0 plan (`docs/plans/2026-05-21-jobfeed-rewrite-phase0-walking-skeleton.md`) is source of truth when it conflicts with the architecture spec.
## Commit Convention

Format: `type(scope): summary` — e.g. `feat(phase0):`, `fix(phase0):`, `test(phase0):`, `docs(phase0):`.

For complex commits, add a body after a blank line explaining:
- Why this approach was chosen
- What tradeoffs were made
- Edge cases or migration risks
- Non-obvious business constraints

Do not add `Co-Authored-By` or any Claude/AI attribution to commit messages.
