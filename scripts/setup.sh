#!/usr/bin/env bash
# One-command setup for a fresh checkout. Creates config.toml, sets up the
# runtime (host venv if `uv` is available, else the prebuilt Docker image),
# starts Postgres, and applies migrations. After this: ./scan (or ./bin/jobfeed).
#
#   ./setup
#
# Docker is the one hard requirement (PostgreSQL runs in it). `uv` is optional —
# if present you get the faster host-native runtime; otherwise everything runs
# through Docker. MANUAL helper only — installs no cron/launchd/timer.
set -euo pipefail

# Resolve through the repo-root `./setup` symlink so REPO_ROOT is correct.
SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do
  DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  [ "${SOURCE#/}" = "$SOURCE" ] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "error: Docker is required (PostgreSQL runs in it)." >&2
  echo "  install it: https://docs.docker.com/get-docker/" >&2
  exit 1
fi

# 1. Config from template (first run only).
if [ ! -f config.toml ]; then
  cp config.example.toml config.toml
  echo "==> created config.toml from config.example.toml (review sources/keys before a real scan)"
fi

# 2. Runtime: a fast host venv if `uv` is available, else the Docker image.
if command -v uv >/dev/null 2>&1; then
  if [ -x .venv/bin/jobfeed ]; then
    echo "==> host venv already present — skipping"
  else
    echo "==> setting up host venv (python 3.12) ..."
    uv venv --python 3.12 .venv
    uv pip install --python .venv/bin/python -e ".[dev]"
  fi
  runtime=host
else
  echo "==> uv not found — preparing the Docker image (pull, else build) ..."
  docker compose pull jobfeed-cli 2>/dev/null || docker compose build jobfeed-cli
  runtime=docker
fi

# 3. Postgres + migrations.
echo "==> starting Postgres + applying migrations ..."
docker compose up -d --wait postgres
if [ "$runtime" = host ]; then
  .venv/bin/alembic -c migrations/alembic.ini upgrade head
else
  docker compose run --rm jobfeed-cli alembic -c migrations/alembic.ini upgrade head
fi

echo ""
echo "setup complete ($runtime runtime). Next:"
echo "   ./scan --source mock      # offline smoke"
echo "   ./scan                    # your configured sources"
echo "   (real ML gate on host? pre-seed the embedder once: .venv/bin/jobfeed ml-gate fetch)"
