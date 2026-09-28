# SQLite claim cleanup concurrency repair

## Boundary and acceptance

- Limit canonical evaluation claim cleanup to the configured LLM concurrency.
- Cover both Stage A and Stage B exception cleanup paths.
- Preserve claim release semantics and existing evaluation results.
- Accept when the regression test proves the cleanup peak is bounded, focused
  evaluation and lease tests pass, SQLite integrity is intact, and live health
  and active-run endpoints respond successfully.

## Result and evidence

- Confirmed cause: run `a9884254-71f1-425d-9844-aff5465cd365` failed in
  `ml_gate`; unbounded claim cleanup opened about 3,300 threads and 6,400 SQLite
  file handles, blocking lease recovery and API reads.
- Failing check: the cleanup helper rejected a requested concurrency limit
  before the repair.
- Passing check: 20 Stage A and Stage B releases now peak at the configured
  concurrency of 2.
- Focused tests: `21 passed` across canonical evaluation and evaluation timing.
- Lease and web route regression tests: `24 passed`.
- Static check: Ruff passed for the two changed Python files; `git diff --check`
  passed. The focused mypy run still reports 29 pre-existing canonical-store
  protocol errors outside this repair.
- Live verification: SQLite `PRAGMA quick_check` returned `ok`; the worker fell
  from about 3,300 threads and 6,400 SQLite handles to 14 threads and 15 handles;
  `/api/health` and `/api/runs/active` return HTTP 200.

## Dispositions

- The interrupted evaluation remains failed and was not automatically retried,
  avoiding a new paid evaluation run without an explicit request.
- No database rows were manually edited or deleted.
