# Jobfeed

Jobfeed is a local-first job scanning and evaluation pipeline. The current runtime uses a host-native Click CLI, one repo-local SQLite database, ATS source adapters, configurable LLM backends, and Markdown digest rendering.

## Quick start

A fresh machine only needs **Git**. Docker and Node are not required for normal
use. Then:

```sh
git clone https://github.com/wenqiw777/jobfeed.git
cd jobfeed
./setup.sh
```

`setup.sh` installs `uv` when needed, installs the repo-local runtime,
initializes `data/jobfeed.sqlite`, starts Jobfeed, and opens the GUI setup page.
Choose your résumé, models, sources, and filters there; saving creates the
project-local `config.toml` and opens Triage. The production GUI bundle ships in
the repository, so users do not install Node or build the frontend. Repeating
the command is safe: it reuses a healthy server instead of starting a
duplicate. The older `./setup` name remains an alias.

After setup, `./scan --source mock` runs an offline smoke scan; `./scan` uses
the sources enabled in `config.toml`. Every normal `./bin/jobfeed ...` command
opens the same repo-local SQLite database directly.

## Evaluate against your résumé

`jobfeed evaluate` scores every job by how well it fits **your** résumé (résumé ↔ job-description match), so it needs one Markdown file: your *master résumé*. Your real résumé is personal data and is **never committed** — instead the repo ships an example so you can see the format and run end-to-end the moment you clone. Out of the box `config.example.toml` points `master_resume_path` at the committed [`resume.example.md`](resume.example.md), so `evaluate` works immediately, scoring against the example candidate until you swap in your own:

```sh
cp resume.example.md resume.md     # resume.md is gitignored
$EDITOR resume.md                  # replace the example with your real résumé
# Then open Settings in the GUI and set Master resume to "resume.md".
```

**Format** is plain Markdown with no fixed schema. The scorer weights **project / work bullets** highest (skills-line keywords and coursework count less), so lead with concrete projects; state your graduation date / availability so the timing check works. You may keep internal-only sections (e.g. a compensation floor) — they inform scoring but are never echoed in any output. Optionally, a personal calibration appendix (hiring window, real-outcome anchors, GPA notes) sharpens scoring — copy [`preamble_personal.example.md`](preamble_personal.example.md) to `preamble_personal.md` (also gitignored) and set `preamble_personal_path`.

Scoring needs an LLM backend. To try it with **no toolchain**, use the mock config — `./bin/jobfeed --config tests/fixtures/docker-smoke.toml evaluate` selects mock LLM backends. For real scoring set `stage_a` / `stage_b` to `codex-cli/*`, `openai-compat/*`, or `claude-cli/*`. Because the canonical runtime is the host, logged-in local Codex/Claude CLIs are available without copying credentials into a container.

## Host Runtime

```sh
./bin/jobfeed --config tests/fixtures/docker-smoke.toml scan --source mock
./bin/jobfeed --config tests/fixtures/docker-smoke.toml evaluate --limit 3
./bin/jobfeed --config tests/fixtures/docker-smoke.toml digest
./bin/jobfeed serve
```

The canonical entrypoint is always `./bin/jobfeed`; ordinary commands execute `.venv/bin/jobfeed` from the repo root. The smoke fixture selects mock LLM backends so it works offline. Explicit PostgreSQL snapshot migration and rollback rehearsal commands still use isolated Docker Compose projects.

SQLite is the supported normal store backend. Runtime data lives in `data/jobfeed.sqlite`; Jobfeed does not read or write `~/.jobfeed/`. Omitting `--config` discovers repo-local `config.toml`, whose default real `codex-cli` models use the host executable and host login.

## Development

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

Use `./bin/jobfeed` for production-parity verification. Bare `jobfeed` and `uv run jobfeed` remain developer shortcuts.

### ML-gate end-to-end test (`pytest -m mlmodel`)

The ML pre-filter gate has one end-to-end check that runs the **real** XGBoost model and the **real** fastembed (ONNX) embedder over a handful of clear jobs and asserts the gate's pass/block decisions (including the exact deterministic hard-fail reasons). It is **not** part of `make quality` — it carries the `mlmodel` marker, which is excluded from the default test selection, so the fast quality gate never triggers the one-time model download. Treat it as a manual / CI ML check.

```sh
pytest -m mlmodel        # runs tests/mlmodel/test_ml_gate_e2e.py
```

- The trained XGBoost model is **committed in-repo** under `models/ml_gate/` (`v*.json` booster + `.meta.json` threshold), so a plain `git clone` is self-contained — no model download or training step is needed to run the gate.
- The embedder is `all-MiniLM-L6-v2` served by [`fastembed`](https://github.com/qdrant/fastembed) over **onnxruntime** (no PyTorch). `fastembed` is a **core** dependency, so the gate runs in the default install and the default Docker image. The ONNX weights (~87MB) are too large to commit to git, so they are obtained one of two ways depending on how you run jobfeed (below).
- **Canonical host runtime — one-time per-machine download.** Weights are downloaded once from Hugging Face into `$JOBFEED_ML_CACHE_DIR` (else `~/.cache/jobfeed/fastembed`, never `~/.jobfeed`) and reused. Pre-seed explicitly with `./bin/jobfeed ml-gate fetch` for an offline-ready evaluation run.
- **Optional Docker image.** The deployment/CI image still bakes the same weights for isolated checks, but ordinary local commands do not build or enter that image.

#### Pre-seeding the embedder weights (`jobfeed ml-gate fetch`)

```sh
jobfeed ml-gate fetch                 # download/warm the default all-MiniLM-L6-v2 weights
jobfeed ml-gate fetch --embedding-model BAAI/bge-small-en-v1.5
```

`fetch` materializes the ONNX weights into the resolved cache dir and prints the location + on-disk size. It is unnecessary for the default Docker image (which already bundles them); it's the explicit, offline-friendly pre-seed step for host-native runs.

#### Running the real ML gate

Because fastembed/onnxruntime is a core dependency (no heavy torch optional extra), the real ML gate runs in the host `.venv`. Enable it with `scoring.ml_gate_enabled=true`, or point `ml_gate.model_dir="mock"` at the deterministic mock gate.

## Architecture

The Python package lives under `src/jobfeed/`. The project follows a hexagonal architecture: `domain/` contains pure business logic and shared domain errors, `ports/` defines async Protocol contracts, `adapters/` implements concrete IO, and `services/` orchestrates through ports. Normal store access is async through `SQLiteStore`; PostgreSQL code remains only for the approved migration/rollback window. The CLI is a thin sync boundary that uses `asyncio.run()` to call async services.

## Engineering Standards

Read [docs/engineering-standards.md](docs/engineering-standards.md) and [docs/adr/0001-architecture-and-quality-gates.md](docs/adr/0001-architecture-and-quality-gates.md) before changing code.
