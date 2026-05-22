# Review Guidelines

Use this file for Claude Code Review. Keep findings focused on changed behavior and production risk.

## Important Findings

- Correctness bugs, regressions, data-loss risks, security issues, and missing tests for changed behavior.
- Phase 0 boundary violations: `./bin/jobfeed ...` is the canonical user-facing CLI; host-native commands are debug-only.
- Architecture violations: `domain/` must be stdlib-only, `services/` must be adapter-free, and `cli/` must stay a thin sync shell.
- SQLite persistence issues that affect transactions, foreign keys, idempotency, enum/score validation, or domain-required `NOT NULL` fields.
- Error handling that leaks Python tracebacks to CLI users or bypasses `services/error_handler.py` for recoverable service failures.

## Low Value Findings

- Do not flag formatting or naming-only issues unless they violate `docs/engineering-standards.md` and affect maintainability.
- Do not request broader refactors when a small localized fix addresses the risk.
- Do not ask for real network IO, real LLM calls, browser automation, Temporal, Postgres, or frontend code in Phase 0.
