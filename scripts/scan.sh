#!/usr/bin/env bash
# One-click scan. Brings up the dev Postgres (idempotent), waits until it's
# accepting connections, applies any pending migrations, then runs the scan via
# the project venv. No separate `docker compose up` / `alembic upgrade` needed.
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

# The scan runs host-native through the project venv (fast; only Postgres needs
# Docker). Fail early with setup instructions if the venv is missing.
if [ ! -x "$REPO_ROOT/.venv/bin/jobfeed" ]; then
  echo "error: .venv/bin/jobfeed not found — set up the project venv first:" >&2
  echo "  uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e \".[dev]\"" >&2
  echo "  (or use the Docker path: ./bin/jobfeed scan ...)" >&2
  exit 1
fi
if [ ! -f "$REPO_ROOT/config.toml" ]; then
  echo "error: config.toml not found — create it from the template first:" >&2
  echo "  cp config.example.toml config.toml   # then edit sources / db.url" >&2
  exit 1
fi

# 1. Dev Postgres up, blocking until the healthcheck passes (idempotent — a
#    no-op when it is already running and healthy).
docker compose up -d --wait postgres

# 2. Apply pending migrations (idempotent — a no-op once the schema is at head).
#    alembic.ini already points at localhost:5432/jobfeed_dev, matching config.toml.
.venv/bin/alembic -c migrations/alembic.ini upgrade head

# 3. Run the scan via the project venv.
exec .venv/bin/jobfeed --config config.toml scan "$@"
