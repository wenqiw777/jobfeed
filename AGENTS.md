# Agent Contract

- Read `docs/engineering-standards.md` before editing code.
- Implement Phase 0 rewrite tasks in this repository; do not edit the legacy sibling repo `../job-apply` for this rewrite.
- Treat the Phase 0 plan as source of truth when it conflicts with the architecture spec.
- Do not read or write `~/.jobfeed/` in Phase 0.
- Do not add external network IO, real LLM calls, browser automation, Temporal, Postgres, or frontend code in Phase 0.
- Treat `./bin/jobfeed ...` as the canonical user-facing CLI path. It executes
  the repo-local host `.venv`; bare `jobfeed ...` or `uv run jobfeed ...` is
  developer-only. Docker is reserved for migration, rollback, CI, or optional
  deployment checks.
- Keep `domain/` pure stdlib and keep services adapter-free.
- Write docstrings for public production APIs and comments only for why, constraints, invariants, complexity, or failure modes.
- Run `make quality` before marking any task complete.
- Keep commits task-sized and conventional with scope, for example `docs(phase0): ...` or `test(phase0): ...`.
- Execute Phase 0 tasks sequentially unless a human explicitly approves parallel writers.
- Stop and update the plan if implementation reality conflicts with the plan.
- Never silence a failing quality gate by weakening the gate without explicit human approval.

## Review guidelines

- Prioritize correctness bugs, behavioral regressions, data-loss risks, security issues, and missing tests for changed behavior.
- Treat violations of the host-runtime boundary as important: `./bin/jobfeed ...`
  is canonical, repo-local SQLite must stay under `data/`, and ordinary commands
  must not require Docker. Migration/rollback commands may use isolated Docker.
- Treat architecture boundary violations as important: `domain/` must stay stdlib-only, `services/` must stay adapter-free, and `cli/` must stay a thin sync shell.
- Treat SQLite persistence issues as important when they affect transactions, foreign keys, idempotency, enum/score validation, or domain-required NOT NULL fields.
- Treat review findings as actionable only when they cite concrete changed files or line-level behavior. Avoid style-only comments unless they affect maintainability or the documented engineering standards.
