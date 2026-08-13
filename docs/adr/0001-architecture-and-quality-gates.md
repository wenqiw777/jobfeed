# ADR 0001: Architecture And Quality Gates

## Status

Accepted. Runtime boundary amended 2026-08-13 after the SQLite cutover.

## Context

Phase 0 starts the Jobfeed rewrite in this repository. The legacy repo remains the sibling path `../job-apply` and must not receive Phase 0 rewrite changes. The implementation must establish the architecture and quality contract before feature work begins. The Phase 0 plan is the source of truth when it conflicts with the broader architecture spec.

Phase 0 must avoid external network IO, real LLM calls, browser automation, Temporal, Postgres, frontend code, and reads or writes to `~/.jobfeed/`.

## Decision

- Use hexagonal architecture with explicit dependency direction: `domain/`, `ports/`, `services/`, `adapters/`, and `cli/`.
- Keep `domain/` pure stdlib.
- Keep `ports/` limited to domain model imports and protocol definitions.
- Keep `services/` dependent only on ports and domain.
- Put concrete IO behind adapters.
- Centralize recoverable service error handling in `services/error_handler.py`; stores only persist explicit error state.
- Make ports, adapters, and services async.
- Keep the Click CLI synchronous and bridge into async services at the boundary with `asyncio.run()`.
- Treat `./bin/jobfeed` and `./bin/jobfeed.ps1` as the canonical user-facing
  entrypoints. Ordinary commands execute the repo-local host `.venv` so local
  LLM CLIs, browser integration, and SQLite share the user's OS identity.
- Keep host launchers thin: they may select the checked repo-local executable,
  set the working directory, and preserve exit status, but contain no business
  logic. Only explicit migration/rollback control-plane commands enter Docker.
- Use repo-local `.jobfeed-dev/` as the Phase 0 config and DB home.
- Use SQLite as the only real IO in Phase 0.
- Fail fast when config requests a non-SQLite backend or when an explicit config path does not exist.
- Enable SQLite foreign-key enforcement on every adapter connection.
- Make SQLite connection lifecycle idempotent and serialize close with in-flight store operations.
- Make `save_job` race-safe for duplicate `(platform, canonical_id)` inserts by using conflict-aware insert plus update inside one transaction.
- Require `pipeline_runs.source` at the schema boundary to match the domain model.
- Keep SQLite persisted enum/score fields guarded with CHECK constraints and preserve evaluation audit timestamps.
- Apply configured LLM `max_concurrent` and `timeout_s` in `EvaluateService`; parse and runtime failures persist explicit stage errors through `ServiceErrorHandler`.
- Preserve unknown per-call LLM cost as `None` in stage results; only aggregate run totals coerce unknown cost to zero for summing.
- Treat all source, LLM, browser, Temporal, Postgres, and frontend behavior as out of scope for Phase 0 unless mocked.
- Require mandatory quality gates through `make quality` and CI.
- Enforce every automatable engineering standard from `docs/engineering-standards.md`.

## Consequences

Implementation starts with the contract and gates before feature code. Task 1 must create `pyproject.toml`, `Makefile`, and CI wiring that preserve the thresholds defined in `docs/engineering-standards.md`.

The sync CLI boundary is intentionally thin. Business logic belongs in domain functions and async services. Concrete implementations belong in adapters. Docker remains an isolation tool for migration, rollback, CI, and optional deployment—not a requirement for the local SQLite runtime. Any implementation reality that conflicts with this ADR or the Phase 0 plan must stop work until the plan is updated.
