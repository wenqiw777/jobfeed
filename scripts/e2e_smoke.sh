#!/usr/bin/env bash
#
# Full-stack e2e smoke for jobfeed sources.
#
# Spins up an ephemeral PostgreSQL container, applies migrations, then scans a
# source through the REAL stack (CLI -> ScanService -> source adapter -> store
# -> Postgres), prints what landed, and tears everything down on exit.
#
# MANUAL only. It makes real outbound scrapes — the JobSpy sources (indeed,
# linkedin-jobspy) hit anti-bot'd endpoints, so an empty result usually means
# "blocked right now", not an adapter regression; re-run later. This is a smoke
# aid, NOT a CI gate (CI's deterministic chain is the mock docker-build lane +
# the testcontainers `-m postgres` suite).
#
# Scope: this drives the HOST-NATIVE CLI (.venv/bin/jobfeed) — a developer smoke,
# which ADR 0001 explicitly permits "for developer debugging, not for
# production-parity acceptance". For production-parity, the canonical path is the
# Dockerized `./bin/jobfeed` wrapper (already exercised by the CI docker-build
# lane). This script trades image fidelity for fast iteration on the new sources.
#
# Usage:
#   make e2e                      # SOURCE=indeed (bounded, default)
#   make e2e SOURCE=all           # mock seed + indeed + linkedin-jobspy (ats on by default)
#   ./scripts/e2e_smoke.sh speedyapply   # NOTE: scans the full real README (heavy)
#
# Env overrides:
#   JOBFEED_E2E_PORT       host port for the throwaway PG (default 55433)
#   JOBFEED_E2E_MAX_JOBS   per-URL cap for the JobSpy sources (default 5)
#   JOBFEED_E2E_INDEED_URL / JOBFEED_E2E_LINKEDIN_URL   override the search URLs
set -euo pipefail

SOURCE="${1:-indeed}"
PORT="${JOBFEED_E2E_PORT:-55433}"
MAX_JOBS="${JOBFEED_E2E_MAX_JOBS:-5}"
INDEED_URL="${JOBFEED_E2E_INDEED_URL:-https://www.indeed.com/jobs?q=software+engineer+intern&l=Remote}"
LINKEDIN_URL="${JOBFEED_E2E_LINKEDIN_URL:-https://www.linkedin.com/jobs/search/?keywords=software%20engineer%20intern&location=United%20States}"

case "$SOURCE" in
  mock | ats | speedyapply | indeed | linkedin-jobspy | all) ;;
  *)
    echo "unknown SOURCE '$SOURCE' (use: mock|ats|speedyapply|indeed|linkedin-jobspy|all)" >&2
    exit 2
    ;;
esac

CONTAINER="jobfeed-e2e-$$"
DSN="postgresql://jobfeed:jobfeed_dev@localhost:${PORT}/jobfeed_dev"
CFG="$(mktemp -t jobfeed-e2e).toml"

# Prefer the project venv's entrypoints; fall back to PATH (an activated venv).
BIN=""
[ -x ".venv/bin/jobfeed" ] && BIN=".venv/bin/"

cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  rm -f "$CFG" 2>/dev/null || true
}
trap cleanup EXIT

echo ">> [1/5] starting ephemeral postgres:16 on :${PORT} (${CONTAINER})"
docker run -d --name "$CONTAINER" \
  -e POSTGRES_USER=jobfeed -e POSTGRES_PASSWORD=jobfeed_dev -e POSTGRES_DB=jobfeed_dev \
  -p "${PORT}:5432" postgres:16 >/dev/null
# Wait inside the container so the host doesn't need to poll.
docker exec "$CONTAINER" sh -c \
  'for _ in $(seq 1 60); do pg_isready -U jobfeed -d jobfeed_dev >/dev/null 2>&1 && exit 0; sleep 1; done; exit 1'
echo "   postgres ready"

export JOBFEED_DB_URL="$DSN"
echo ">> [2/5] applying migrations"
"${BIN}"alembic -c migrations/alembic.ini upgrade head >/dev/null
echo "   schema ready"

echo ">> [3/5] writing config for '${SOURCE}'"
{
  printf '[db]\nurl = "%s"\n\n' "$DSN"
  # `all` enables only the BOUNDED JobSpy sources here; speedyapply is left
  # disabled (its full-README scan is heavy) and is exercised by running it
  # explicitly. ats defaults enabled, mock is always seeded.
  case "$SOURCE" in
    indeed | all)
      printf '[sources.indeed]\nenabled = true\nsearch_urls = ["%s"]\nmax_jobs = %s\n\n' "$INDEED_URL" "$MAX_JOBS"
      ;;
  esac
  case "$SOURCE" in
    linkedin-jobspy | all)
      printf '[sources.linkedin_jobspy]\nenabled = true\nsearch_urls = ["%s"]\nmax_jobs = %s\n\n' "$LINKEDIN_URL" "$MAX_JOBS"
      ;;
  esac
  case "$SOURCE" in
    speedyapply)
      printf '[sources.speedyapply]\nenabled = true\n\n'
      ;;
  esac
} > "$CFG"

echo ">> [4/5] real scan: jobfeed scan --source ${SOURCE}"
"${BIN}"jobfeed --config "$CFG" scan --source "$SOURCE"

echo ">> [5/5] rows persisted to Postgres:"
docker exec "$CONTAINER" psql -U jobfeed -d jobfeed_dev -c \
  "SELECT platform, count(*) AS rows, count(jd_text) AS with_jd, count(posted_at) AS with_date \
   FROM jobs GROUP BY platform ORDER BY platform;"
echo ">> done — tearing down ${CONTAINER}"
