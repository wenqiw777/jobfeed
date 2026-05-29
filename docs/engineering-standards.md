# Engineering Standards

These standards are the engineering contract for Jobfeed. Rules that can be automated must be enforced by `make quality` and CI. Task 1 must wire these same thresholds into `pyproject.toml`, `Makefile`, and CI without weakening them.

## Naming

- Modules, files, functions, methods, and variables use `snake_case`.
- Classes, dataclasses, enums, exceptions, and protocols use `PascalCase`.
- Boolean names start with `is_`, `has_`, `can_`, `should_`, or `needs_`.
- Ports are capability nouns: `JobStore`, `LLMClient`, `SimpleSource`, `SessionSource`.
- Adapters are implementation nouns: `PostgresStore`, `MockLLM`, `MockSource`.
- Services are workflow nouns: `ScanService`, `EvaluateService`, `DigestService`.

## Function Shape

- Cyclomatic complexity target: <= 8; hard max: 10.
- Nesting depth max: 2. Prefer guard clauses and early returns.
- Positional/keyword arguments max: 5. Use a dataclass or config object beyond that.
- Line length: <= 88 characters (`line-length = 88` in `pyproject.toml`, Ruff-enforced via format check + `E501`).
- Statements target: <= 40.
- File length: <= 300 lines, enforced as a blocking hygiene gate. Exempt: the `adapters/store/` layer and `cli/migrate.py`. The PostgresStore and legacy-migration surface are inherently large; fragmenting them into <=300-line shards harms readability. The gate stays blocking everywhere else. (Phase 1 amendment, approved 2026-05-22.)
- Functions should have a single clear responsibility.
- Avoid nested loops in application/service code.
- If nested loops are necessary in algorithmic or data-processing code, extract them into a named helper and document time complexity.
- Avoid mixing validation, transformation, IO, and persistence in one function.

The hard automated rules are complexity hard max, nesting depth, argument count, and layer-specific nested-loop rules. The target and responsibility rules are review standards: they should trigger refactoring discussion, not mechanical rewrites that make code harder to read.

### Scoped rule relaxations

The thresholds above are global defaults. A small set of scoped relaxations live in `pyproject.toml` and must stay in sync with this document (adding or widening one requires explicit approval, per the "do not weaken quality gates" constraint):

- **Global `ignore`:** `PLR0911`/`PLR0912`/`PLR0915` (too-many-returns/branches/statements) are disabled project-wide — these overlap the cyclomatic-complexity and statements-target review rules above rather than adding a separate hard gate.
- **`per-file-ignores`:**
  - `ports/*.py` — `ARG002` (Protocol methods legitimately have unused args), `PLR0913` (capability signatures can exceed 5 args).
  - `adapters/store/postgres.py` — `ARG002`, `E501`, `PLR0913`, `SIM117` (the 300-line-exempt store surface; large SQL-bearing methods).
  - `tests/**/test_ports.py` — `ARG002`, `PLR0913`. `tests/conftest.py` / `migrations/env.py` — `PLC0415`. `migrations/versions/*.py` — `E501`. `tests/fixtures/generate_legacy_fixture.py` — `PLR2004`, `E501`, `C901`, `PLC0415`.
- **Targeted `# noqa`:** for a one-off legitimate exceedance outside the scoped paths, a per-line `# noqa: <rule>` is the escape hatch rather than a new per-file ignore (e.g. `adapters/sources/_http.py` `fetch_json` / `probe_url` carry `# noqa: PLR0913` — low-level HTTP helpers with `client`/`url`/`slug`/`vendor`/`timeout`/`retries`).

## Documentation And Comments

- Markdown docs must state purpose, constraints, source-of-truth status, and acceptance criteria when they describe implementation work.
- Every production module, except empty `__init__.py` package markers, must start with a module docstring that names the module's responsibility.
- Every public production class, dataclass, enum, protocol, function, and method must have a docstring.
- Function and method docstrings must document what the callable does, arguments, return value, raised errors, preconditions, side effects, and complexity when those concerns apply.
- Private helpers may omit docstrings only when the name, type hints, and local context make intent obvious.
- Inline comments explain information the code cannot express clearly: why this implementation was chosen, business constraints, external system behavior, historical compatibility, invariants, edge cases, complexity, and failure modes.
- Do not write comments that only repeat the code's surface action, such as "increment i", "check if user is None", or "loop through orders".
- Prefer better names or smaller functions when a comment is only needed to explain what the code is doing.
- Comments must stay concise, accurate, local to the relevant code, and updated in the same change as the code they describe.
- Do not satisfy file-length or function-shape gates by deleting useful docstrings or comments. Split the module or extract named helpers instead.

## Layer Boundaries

- `domain/` imports stdlib only.
- `ports/` imports domain models only; never adapters.
- `services/` depends on ports and domain only; never concrete adapters.
- `adapters/` may depend on external libraries and implements ports.
- `cli/` parses options, wires dependency injection, handles process output, and contains no business logic.

## Error Handling

- No bare `except`.
- No silent `pass` in exception handlers.
- Expected shared domain exceptions live in `src/jobfeed/domain/errors.py`.
- Recoverable service-layer handling lives in `src/jobfeed/services/error_handler.py` and uses `handle_*_error` method names.
- Stores persist explicit error state; they do not own business error policy.
- CLI commands must convert expected configuration, store lifecycle, and command execution failures into user-facing Click errors without Python tracebacks.
- Long-running external-port calls must honor configured timeout and concurrency limits when such settings exist.
- Errors must be named and logged with `run_id` where execution context exists.

## Testing

- New behavior starts with a failing test unless the task is pure scaffolding.
- Unit tests cover domain logic.
- Integration tests cover adapters and service orchestration.
- E2E tests cover the CLI walking skeleton.

## Quality Gates

`make quality` and CI must run the automated parts of this contract:

- Ruff format check.
- Ruff lint with the hard complexity and function-shape thresholds from this document.
- Mypy over `src/`.
- Unit tests, including `tests/unit/test_code_hygiene.py`.
- Production docstring coverage for public modules, classes, functions, and methods.
- The code hygiene checker must check its own test file and support modules for file length and structural hygiene.
- Later CI-only integration and E2E tests once Task 1 and later tasks create those layers.

Pre-commit may run only fast local checks: Ruff format, Ruff check with fixes, and mypy over `src/`. Slow integration and E2E tests belong in CI, not pre-commit.
