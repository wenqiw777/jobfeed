#!/usr/bin/env bash
# One-click scan. Figures out how to run on its own:
#   * developer machine with the project venv -> fast host-native path
#   * any machine with only Docker            -> self-bootstrapping Docker path
# Either way it brings up Postgres, applies migrations, then scans — no separate
# `docker compose` / `alembic` commands needed.
#
#   ./scan                          # all enabled sources from config.toml
#   ./scan --source speedyapply     # a single source
#   ./scan --source indeed
#   ./scan --source mock            # offline, deterministic (no network)
#
# MANUAL helper only — it installs no cron/launchd/timer.
set -euo pipefail

# Resolve through the repo-root `./scan` symlink so REPO_ROOT is correct no
# matter how this is invoked.
SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do
  DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  [ "${SOURCE#/}" = "$SOURCE" ] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Zero-friction config: create one from the template on first run so a fresh
# checkout just works. Review it before a real (non-mock) scan.
if [ ! -f config.toml ]; then
  cp config.example.toml config.toml
  echo "note: created config.toml from config.example.toml — review sources/keys before a real scan." >&2
fi

# --- Fast path: developer machine with the project venv -----------------------
if [ -x "$REPO_ROOT/.venv/bin/jobfeed" ]; then
  docker compose up -d --wait postgres
  .venv/bin/alembic -c migrations/alembic.ini upgrade head
  exec .venv/bin/jobfeed --config config.toml scan "$@"
fi

# --- Self-bootstrapping Docker path: only Docker required ---------------------
if ! command -v docker >/dev/null 2>&1; then
  echo "error: no project venv and no Docker found. Do ONE of:" >&2
  echo "  - install Docker, then re-run ./scan        (everything else is automatic), or" >&2
  echo "  - set up the host venv:" >&2
  echo "      uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e \".[dev]\"" >&2
  exit 1
fi

echo "==> no .venv found — using Docker (pull prebuilt image -> Postgres -> migrate -> scan)" >&2
# Prefer the prebuilt image; fall back to a local build if it isn't published
# yet or this machine is offline.
docker compose pull jobfeed-cli 2>/dev/null || docker compose build jobfeed-cli
docker compose up -d --wait postgres
docker compose run --rm jobfeed-cli alembic -c migrations/alembic.ini upgrade head
# Mount the host config into the container (the prebuilt image does NOT bake a
# user config) and scan. The CLI auto-discovers /app/config.toml.
exec docker compose run --rm \
  -v "$REPO_ROOT/config.toml:/app/config.toml:ro" \
  jobfeed-cli jobfeed scan "$@"
