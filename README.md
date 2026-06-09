# Jobfeed

Jobfeed is a local-first job scanning and evaluation pipeline. The current rewrite branch uses a Dockerized Click CLI, PostgreSQL persistence, ATS source adapters, configurable LLM backends, and Markdown digest rendering.

## Quick start

A fresh machine needs only **Git + Docker** (PostgreSQL is required, so Docker is the single prerequisite; `uv` is optional and gives a faster host runtime). Then:

```sh
git clone https://github.com/wenqiw777/jobfeed.git
cd jobfeed
./setup                   # one-time: config + runtime + Postgres + migrations
./scan --source mock      # offline smoke; then ./scan for your configured sources
```

`./setup` bootstraps everything: it creates `config.toml` from the template, sets up the runtime (a host venv if `uv` is present, else it pulls — or builds — the Docker image `ghcr.io/wenqiw777/jobfeed`), starts Postgres, and applies migrations. `./scan` afterwards just makes sure Postgres is up and scans. You never run `docker compose` / `alembic` by hand.

## Evaluate against your résumé

`jobfeed evaluate` scores every job by how well it fits **your** résumé (résumé ↔ job-description match), so it needs one Markdown file: your *master résumé*. Your real résumé is personal data and is **never committed** — instead the repo ships an example so you can see the format and run end-to-end the moment you clone. Out of the box `config.example.toml` points `master_resume_path` at the committed [`resume.example.md`](resume.example.md), so `evaluate` works immediately, scoring against the example candidate until you swap in your own:

```sh
cp resume.example.md resume.md     # resume.md is gitignored
$EDITOR resume.md                  # replace the example with your real résumé
#  then in config.toml:  master_resume_path = "resume.md"
```

**Format** is plain Markdown with no fixed schema. The scorer weights **project / work bullets** highest (skills-line keywords and coursework count less), so lead with concrete projects; state your graduation date / availability so the timing check works. You may keep internal-only sections (e.g. a compensation floor) — they inform scoring but are never echoed in any output. Optionally, a personal calibration appendix (hiring window, real-outcome anchors, GPA notes) sharpens scoring — copy [`preamble_personal.example.md`](preamble_personal.example.md) to `preamble_personal.md` (also gitignored) and set `preamble_personal_path`.

Scoring needs an LLM backend. To try it with **no toolchain**, use the mock config — `./bin/jobfeed --config tests/fixtures/docker-smoke.toml evaluate` selects mock LLM backends. For real scoring set `stage_a` / `stage_b` to a real backend (`codex-cli/*`, `openai-compat/*`, or `claude-cli/*`); see the `[llm]` block in `config.example.toml`.

## Docker-First Quickstart

```sh
docker compose pull jobfeed-cli   # prebuilt image; or build locally: docker compose build jobfeed-cli
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
- The embedder is `all-MiniLM-L6-v2` served by [`fastembed`](https://github.com/qdrant/fastembed) over **onnxruntime** (no PyTorch). `fastembed` is a **core** dependency, so the gate runs in the default install and the default Docker image. The ONNX weights (~87MB) are too large to commit to git, so they are obtained one of two ways depending on how you run jobfeed (below).
- **Default Docker image — zero runtime download, offline-ready.** The `Dockerfile` **bakes the ONNX weights into the image at build time** at the same path the runtime reads (`JOBFEED_ML_CACHE_DIR=/cache/jobfeed/fastembed`, an image-level default that `warm_embedder()` resolves so the bake can't drift from runtime). The canonical `./bin/jobfeed` / `docker compose run --rm jobfeed-cli` path therefore performs **no** weight download and works **offline**. A fresh `mlcache` named volume is initialized from the image's baked directory on first mount, so the weights are present even with the volume attached; the volume then persists them across `--rm`. (Upgraders who already have an *empty* `mlcache` from before the bake should run `docker volume rm jobfeed_mlcache` once so it re-seeds from the new image.)
- **Host-native installs — one-time per-machine download.** Outside Docker the weights are **downloaded once from Hugging Face** into `$JOBFEED_ML_CACHE_DIR` (else `~/.cache/jobfeed/fastembed`, never `~/.jobfeed`) and reused on every later run. On a cold cache the gate logs a single `embedder_weights_downloading` line (model id + cache dir + pre-seed hint) **before** downloading, so it is never a surprise mid-evaluation stall. Pre-seed it explicitly with `jobfeed ml-gate fetch` (prints where the weights landed and their size); offline machines can pre-seed the cache dir directly.

#### Pre-seeding the embedder weights (`jobfeed ml-gate fetch`)

```sh
jobfeed ml-gate fetch                 # download/warm the default all-MiniLM-L6-v2 weights
jobfeed ml-gate fetch --embedding-model BAAI/bge-small-en-v1.5
```

`fetch` materializes the ONNX weights into the resolved cache dir and prints the location + on-disk size. It is unnecessary for the default Docker image (which already bundles them); it's the explicit, offline-friendly pre-seed step for host-native runs.

#### Running the real ML gate

Because the fastembed/onnxruntime embedder is a core dependency (no heavy torch optional extra), the **real** ML gate runs anywhere the package is installed — including the canonical Docker image, which installs `.[dev]` and therefore pulls fastembed (and bakes the weights). Enable it with `scoring.ml_gate_enabled=true`, or point `ml_gate.model_dir="mock"` at the deterministic mock gate to exercise the funnel without the embedder or any weight download.

## Architecture

The Python package lives under `src/jobfeed/`. The project follows a hexagonal architecture: `domain/` contains pure business logic and shared domain errors, `ports/` defines async Protocol contracts, `adapters/` implements concrete IO, `services/` orchestrates domain logic through ports, and `services/error_handler.py` centralizes recoverable service errors. PostgreSQL migrations live under `migrations/`; store access is async through `PostgresStore`. Evaluation uses configured LLM concurrency, timeout, budget, and claim-lease limits, preserves unknown per-call cost as `None`, and persists recoverable scoring failures as explicit stage errors. The CLI is a thin sync boundary that uses `asyncio.run()` to call async services. The full design is in [docs/specs/2026-05-20-jobfeed-rewrite-design.md](docs/specs/2026-05-20-jobfeed-rewrite-design.md).

## Engineering Standards

Read [docs/engineering-standards.md](docs/engineering-standards.md) and [docs/adr/0001-architecture-and-quality-gates.md](docs/adr/0001-architecture-and-quality-gates.md) before changing code.
