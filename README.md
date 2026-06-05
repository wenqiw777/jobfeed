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
python -m playwright install chromium  # needed only for LinkedIn Playwright scans
jobfeed --help
make test
make lint
make quality
make fmt
pre-commit install
```

Host-native commands are for local debugging only. Use `make docker-quality` and the Docker-first quickstart for production-parity verification.

### ML-gate end-to-end test (`pytest -m mlmodel`)

The ML pre-filter gate has one end-to-end check that runs the **real** XGBoost model and the **real** fastembed (ONNX) embedder over a handful of clear jobs and asserts the gate's pass/block decisions (including the exact deterministic hard-fail reasons). It is **not** part of `make quality` — it carries the `mlmodel` marker, which is excluded from the default test selection, so the fast quality gate never triggers the one-time model download. Treat it as a manual / CI ML check.

```sh
pytest -m mlmodel        # runs tests/mlmodel/test_ml_gate_e2e.py
```

- The trained XGBoost model is **committed in-repo** under `models/ml_gate/` (`v*.json` booster + `.meta.json` threshold), so a plain `git clone` is self-contained — no model download or training step is needed to run the gate.
- The embedder is `all-MiniLM-L6-v2` served by [`fastembed`](https://github.com/qdrant/fastembed) over **onnxruntime** (no PyTorch). `fastembed` is a **core** dependency, so the gate runs in the default install and the default Docker image. The ONNX model weights are **downloaded once from Hugging Face** on the first run and then cached locally; the first invocation is therefore slower and needs network access. That one-time download is the gate's only outbound network call.
- **Where the weights are cached:** the embedder passes an explicit `cache_dir` to fastembed instead of its ephemeral temp default. It resolves to `$JOBFEED_ML_CACHE_DIR` when set, else `~/.cache/jobfeed/fastembed` (never `~/.jobfeed`). For host-native runs this works with zero config and persists the weights across runs. The `jobfeed-cli` Docker service sets `JOBFEED_ML_CACHE_DIR=/cache/jobfeed/fastembed` and mounts the named `mlcache` volume there, so the ~90MB download survives `docker compose run --rm` and is reused on every later run (and reruns work offline).

#### Running the real ML gate

Because the fastembed/onnxruntime embedder is a core dependency (no heavy torch optional extra), the **real** ML gate runs anywhere the package is installed — including the canonical Docker image, which installs `.[dev]` and therefore pulls fastembed. Enable it with `scoring.ml_gate_enabled=true`, or point `ml_gate.model_dir="mock"` at the deterministic mock gate to exercise the funnel without the embedder or the one-time model download.

## Architecture

The Python package lives under `src/jobfeed/`. The project follows a hexagonal architecture: `domain/` contains pure business logic and shared domain errors, `ports/` defines async Protocol contracts, `adapters/` implements concrete IO, `services/` orchestrates domain logic through ports, and `services/error_handler.py` centralizes recoverable service errors. PostgreSQL migrations live under `migrations/`; store access is async through `PostgresStore`. Evaluation uses configured LLM concurrency, timeout, budget, and claim-lease limits, preserves unknown per-call cost as `None`, and persists recoverable scoring failures as explicit stage errors. The CLI is a thin sync boundary that uses `asyncio.run()` to call async services. The full design is in [docs/specs/2026-05-20-jobfeed-rewrite-design.md](docs/specs/2026-05-20-jobfeed-rewrite-design.md).

## Engineering Standards

Read [docs/engineering-standards.md](docs/engineering-standards.md) and [docs/adr/0001-architecture-and-quality-gates.md](docs/adr/0001-architecture-and-quality-gates.md) before changing code.
