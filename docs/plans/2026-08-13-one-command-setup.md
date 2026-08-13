# One-command setup

**Outcome:** after cloning the repository, an end user runs `./setup.sh` once
and reaches the local Jobfeed GUI without installing Node or Docker.

## Boundaries

- Ordinary runtime remains host-native SQLite under `data/`.
- The script may bootstrap `uv` and Python, but does not install system services.
- PostgreSQL/Docker migration and rollback tooling stays outside this path.
- The production GUI is built by maintainers and committed as a release artifact.
- Existing `./setup` remains a compatibility alias.

## Acceptance

- A fresh checkout with Git and network access creates config and SQLite state.
- Runtime sync excludes the development extra.
- The local server becomes healthy at `127.0.0.1:7654` and the browser opens.
- Repeating setup does not start a duplicate server.
- A fresh checkout does not need Node/npm to serve the GUI.
- Focused setup tests, `make quality`, and a real fresh-checkout browser smoke pass.

## Status

Complete.

- RED: four focused tests failed because `setup.sh` and a committed GUI bundle
  did not exist and the old setup did not open the GUI.
- GREEN: four focused setup tests pass, including repeat-run deduplication.
- A clean checkout exported from the Git index contained no `.venv`, `data/`,
  `node_modules`, or local config. `./setup.sh` installed 90 runtime packages,
  created schema v1, and reached `{"status":"ok","db":"ok"}`.
- The detached server survived its setup shell with PPID 1; repeating setup
  retained the same PID instead of launching another process.
- Direct SQLite verification returned `integrity_check=ok`, zero FK violations,
  and an empty initial jobs table.
- Real Chrome loaded all seven GUI zones from the committed 1.6 MB production
  bundle without Node/npm. Screenshot: `/tmp/jobfeed-one-command-fresh-clone.png`.
- Frontend lint, typecheck, design check, production build, and all 150 tests
  passed. `make quality` passed Ruff, format, mypy, and 1,865 Python tests.

The fresh runtime currently installs 90 packages and occupies about 706 MB.
Moving disabled ML, browser-source, observability, and PostgreSQL rollback
dependencies into optional extras is a separate footprint optimization; it is
not required for the one-command behavior and must preserve the active rollback
window.
