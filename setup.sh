#!/usr/bin/env bash
# Prepare the repo-local runtime, start Jobfeed, and open its local GUI.
set -euo pipefail

REPO_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
GUI_URL="http://127.0.0.1:7654"
DATA_DIR="$REPO_ROOT/data"
PID_FILE="$DATA_DIR/jobfeed-serve.pid"
LOG_FILE="$DATA_DIR/jobfeed-serve.log"

fail() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

launch_only=0
case "$#" in
    0) ;;
    1)
        [ "$1" = "--launch-only" ] \
            || fail "usage: ./setup.sh [--launch-only]"
        launch_only=1
        ;;
    *) fail "usage: ./setup.sh [--launch-only]" ;;
esac

find_or_install_uv() {
    if command -v uv >/dev/null 2>&1; then
        command -v uv
        return
    fi
    command -v curl >/dev/null 2>&1 \
        || fail "uv is missing and curl is unavailable; install uv and retry"
    printf '%s\n' "==> installing uv ..." >&2
    installer="$(mktemp "${TMPDIR:-/tmp}/jobfeed-uv-installer.XXXXXX")"
    trap 'rm -f -- "${installer:-}"' RETURN
    curl -LsSf https://astral.sh/uv/install.sh -o "$installer"
    env UV_NO_MODIFY_PATH=1 sh "$installer"
    rm -f -- "$installer"
    trap - RETURN
    for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return
        fi
    done
    fail "uv installation completed but the uv executable was not found"
}

install_launcher() {
    uv_bin="$1"
    launcher_dir="$("$uv_bin" tool dir --bin)"
    [ -n "$launcher_dir" ] || fail "uv returned an empty tool executable directory"
    mkdir -p -- "$launcher_dir"
    launcher="$launcher_dir/jobfeed"
    launcher_target="$REPO_ROOT/bin/jobfeed"
    if [ -e "$launcher" ] || [ -L "$launcher" ]; then
        if [ ! -L "$launcher" ] || [ "$(readlink "$launcher")" != "$launcher_target" ]; then
            fail "$launcher already exists and is not managed by this checkout"
        fi
    fi
    ln -sfn -- "$launcher_target" "$launcher"
    if ! "$uv_bin" tool update-shell >/dev/null 2>&1; then
        printf '%s\n' "warning: add $launcher_dir to PATH to use the jobfeed command" >&2
    fi
}

is_healthy() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsS "$GUI_URL/api/health" >/dev/null 2>&1
        return
    fi
    "$REPO_ROOT/.venv/bin/python" - "$GUI_URL/api/health" <<'PY' >/dev/null 2>&1
import sys
import urllib.request

with urllib.request.urlopen(sys.argv[1], timeout=1) as response:
    if response.status != 200:
        raise SystemExit(1)
PY
}

start_server() {
    if is_healthy; then
        printf '%s\n' "==> Jobfeed server is already running"
        return
    fi
    if [ -f "$PID_FILE" ]; then
        stale_pid="$(cat "$PID_FILE")"
        if kill -0 "$stale_pid" 2>/dev/null; then
            kill "$stale_pid" 2>/dev/null || true
        fi
        rm -f -- "$PID_FILE"
    fi

    printf '%s\n' "==> starting Jobfeed GUI ..."
    server_pid="$("$REPO_ROOT/.venv/bin/python" - \
        "$REPO_ROOT" "$LOG_FILE" <<'PY'
import subprocess
import sys
from pathlib import Path

repo_root = Path(sys.argv[1])
log_path = Path(sys.argv[2])
with log_path.open("ab", buffering=0) as log:
    process = subprocess.Popen(
        [str(repo_root / "bin" / "jobfeed"), "serve"],
        cwd=repo_root,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
print(process.pid)
PY
)"
    printf '%s\n' "$server_pid" >"$PID_FILE"

    attempts=0
    while [ "$attempts" -lt 80 ]; do
        if is_healthy; then
            return
        fi
        if ! kill -0 "$server_pid" 2>/dev/null; then
            break
        fi
        attempts=$((attempts + 1))
        sleep 0.25
    done

    kill "$server_pid" 2>/dev/null || true
    rm -f -- "$PID_FILE"
    printf '%s\n' "--- $LOG_FILE ---" >&2
    tail -40 "$LOG_FILE" >&2 || true
    fail "Jobfeed did not become healthy"
}

open_gui() {
    if [ "${JOBFEED_SETUP_NO_OPEN:-0}" = "1" ]; then
        return
    fi
    open_url="$GUI_URL"
    if [ ! -f "$REPO_ROOT/config.toml" ]; then
        open_url="$GUI_URL/setup"
    fi
    case "$(uname -s)" in
        Darwin) open "$open_url" ;;
        Linux)
            if command -v xdg-open >/dev/null 2>&1; then
                xdg-open "$open_url" >/dev/null 2>&1 || true
            else
                printf '%s\n' "Open $open_url in your browser."
            fi
            ;;
        *) printf '%s\n' "Open $open_url in your browser." ;;
    esac
}

cd "$REPO_ROOT"
[ -f web-ui/dist/index.html ] \
    || fail "the prebuilt GUI is missing; reinstall from a complete release checkout"

mkdir -p "$DATA_DIR"
if [ "$launch_only" -eq 0 ]; then
    uv_bin="$(find_or_install_uv)"
    printf '%s\n' "==> installing the Jobfeed runtime ..."
    "$uv_bin" sync --locked --no-dev --python 3.12
    install_launcher "$uv_bin"
else
    [ -x "$REPO_ROOT/.venv/bin/jobfeed" ] \
        || fail "host runtime is not installed; run ./setup.sh"
fi

start_server
open_gui

printf '\nJobfeed is ready: %s\n' "$GUI_URL"
printf 'Data: %s\n' "$DATA_DIR/jobfeed.sqlite"
printf 'Logs: %s\n' "$LOG_FILE"
if [ "$launch_only" -eq 0 ]; then
    printf '%s\n' "Command: open a new terminal and run jobfeed"
fi
