# Jobfeed

Jobfeed is a local-first job scanning and evaluation pipeline. The current rewrite branch uses a Dockerized Click CLI, PostgreSQL persistence, ATS source adapters, configurable LLM backends, and Markdown digest rendering.

## Docker-First Quickstart

```sh
docker compose build jobfeed-cli
docker compose run --rm jobfeed-cli alembic -c migrations/alembic.ini upgrade head
./bin/jobfeed --config tests/fixtures/docker-smoke.toml scan --source mock
./bin/jobfeed --config tests/fixtures/docker-smoke.toml evaluate --limit 3
./bin/jobfeed --config tests/fixtures/docker-smoke.toml digest
```

The canonical runtime is Docker Compose through `./bin/jobfeed`. Run Alembic once for a fresh PostgreSQL volume before the first scan. The Docker quickstart uses `tests/fixtures/docker-smoke.toml`, which selects mock LLM backends so it runs without a local `codex` or `claude` toolchain.

PostgreSQL is the supported store backend. The Docker Compose stack owns the local database runtime; Phase 0/3 rewrite work intentionally does not read or write `~/.jobfeed/`. Omitting `--config` uses repo-local defaults, which currently select real `codex-cli` models and require the relevant executable inside the `jobfeed-cli` runtime.

## Developer Fallback

```sh
pip install -e ".[dev]"
jobfeed --help
make test
make lint
make quality
make fmt
pre-commit install
```

Host-native commands are for local debugging only. Use `make docker-quality` and the Docker-first quickstart for production-parity verification.

## Architecture

The Python package lives under `src/jobfeed/`. The project follows a hexagonal architecture: `domain/` contains pure business logic and shared domain errors, `ports/` defines async Protocol contracts, `adapters/` implements concrete IO, `services/` orchestrates domain logic through ports, and `services/error_handler.py` centralizes recoverable service errors. PostgreSQL migrations live under `migrations/`; store access is async through `PostgresStore`. Evaluation uses configured LLM concurrency, timeout, budget, and claim-lease limits, preserves unknown per-call cost as `None`, and persists recoverable scoring failures as explicit stage errors. The CLI is a thin sync boundary that uses `asyncio.run()` to call async services. The full design is in [docs/specs/2026-05-20-jobfeed-rewrite-design.md](docs/specs/2026-05-20-jobfeed-rewrite-design.md).

## Engineering Standards

Read [docs/engineering-standards.md](docs/engineering-standards.md) and [docs/adr/0001-architecture-and-quality-gates.md](docs/adr/0001-architecture-and-quality-gates.md) before changing code.
