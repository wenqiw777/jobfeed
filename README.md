# Jobfeed

Jobfeed is a local-first job scanning and evaluation pipeline. Phase 0 is a Dockerized walking skeleton that wires a host launcher to a containerized Click CLI, deterministic mock source data, SQLite persistence, a mock LLM adapter, and Markdown digest rendering.

## Docker-First Quickstart

```sh
docker compose build jobfeed-cli
./bin/jobfeed scan --source mock
./bin/jobfeed evaluate
./bin/jobfeed digest
```

The canonical runtime is Docker Compose through `./bin/jobfeed`. Runtime state is written to `.jobfeed-dev/dev.db`; Phase 0 intentionally does not read or write `~/.jobfeed/`.

Phase 0 supports only the SQLite backend. Omitting `--config` uses repo-local defaults, but an explicit missing `--config` path fails fast instead of silently running with defaults.

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

The Python package lives under `src/jobfeed/`. The project follows a hexagonal architecture: `domain/` contains pure business logic and shared domain errors, `ports/` defines async Protocol contracts, `adapters/` implements concrete IO, `services/` orchestrates domain logic through ports, and `services/error_handler.py` centralizes recoverable service errors. SQLite lifecycle is idempotent and serialized around active operations. Evaluation uses the configured LLM concurrency and timeout limits, preserves unknown per-call cost as `None`, and persists recoverable scoring failures as explicit stage errors. The CLI is a thin sync boundary that uses `asyncio.run()` to call async services. The full design is in [docs/specs/2026-05-20-jobfeed-rewrite-design.md](docs/specs/2026-05-20-jobfeed-rewrite-design.md).

## Engineering Standards

Read [docs/engineering-standards.md](docs/engineering-standards.md) and [docs/adr/0001-architecture-and-quality-gates.md](docs/adr/0001-architecture-and-quality-gates.md) before changing code.
