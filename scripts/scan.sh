#!/usr/bin/env bash
# Run a scan. Assumes `./setup` has been run once (it creates config.toml, sets
# up the runtime, and applies the DB schema). This just makes sure Postgres is
# up, then scans — no manual `docker compose` needed.
#
#   ./scan                          # all enabled sources from config.toml
#   ./scan --source speedyapply     # a single source
#   ./scan --source mock            # offline, deterministic (no network)
#
# MANUAL helper only — it installs no cron/launchd/timer.
set -euo pipefail

# Resolve through the repo-root `./scan` symlink so REPO_ROOT is correct.
SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do
  DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  [ "${SOURCE#/}" = "$SOURCE" ] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [ ! -f config.toml ]; then
  echo "error: config.toml not found — run ./setup first." >&2
  exit 1
fi

# Make sure Postgres is up (idempotent; handles a stopped container after reboot).
docker compose up -d --wait postgres

# Use the host venv if ./setup created one (fast), else the Docker image.
if [ -x "$REPO_ROOT/.venv/bin/jobfeed" ]; then
  exec .venv/bin/jobfeed --config config.toml scan "$@"
fi
exec docker compose run --rm \
  -v "$REPO_ROOT/config.toml:/app/config.toml:ro" \
  jobfeed-cli jobfeed scan "$@"
