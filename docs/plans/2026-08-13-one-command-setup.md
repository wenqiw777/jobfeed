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

- A fresh checkout with Git and network access creates SQLite state, opens the
  GUI setup stage, and creates config only after the user saves valid settings.
- Runtime sync excludes the development extra.
- The local server becomes healthy at `127.0.0.1:7654` and the browser opens.
- Repeating setup does not start a duplicate server.
- Setup installs a user-local `jobfeed` command; zero arguments start or reuse
  the server and open the correct GUI route from any working directory.
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
- The installed launcher resolves its repo symlink, so `jobfeed` works outside
  the checkout; subcommands still enter the same repo-local runtime.
- Real terminal proof: `setup.sh` installed
  `~/.local/bin/jobfeed -> <checkout>/bin/jobfeed`; after stopping the server,
  running `jobfeed` from `/tmp` started a new detached PID, opened the GUI, and
  returned `{"status":"ok","db":"ok"}`. `jobfeed --help` from `/tmp` retained
  the full existing command surface.
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

## GUI configuration follow-up

Status: complete (2026-08-13).

Purpose: remove the remaining file-editing step from first-run setup. A fresh
checkout starts from validated built-in defaults, opens the GUI configuration
stage, and persists the user's choices only after the form validates.

Acceptance criteria:

- `setup.sh` does not create `config.toml`; a fresh run opens `/setup`, while a
  configured checkout opens the normal application.
- `GET /api/config` reports whether the project is configured and returns the
  effective user-editable settings. `PUT /api/config` validates and atomically
  writes `config.toml` without exposing database switching or secret values.
- Saved source, evaluation, filter, and ML-gate settings apply to newly started
  web runs without restarting the process.
- The SPA gates an unconfigured checkout on a responsive configuration form,
  enters Triage after a successful save, and keeps Settings reachable later.
- Backend and frontend tests pass, the committed SPA bundle is rebuilt, a real
  fresh-checkout setup is exercised, and the full form is verified in the
  user's Chrome extension with a screenshot.

Risks and constraints:

- In-flight runs retain the settings they started with; only newly scheduled
  work observes a saved update.
- API-key values remain host environment variables. The GUI may configure only
  the environment-variable name, never read or persist the secret itself.
- SQLite's path stays fixed for the process lifetime and is intentionally not a
  GUI field.

Evidence:

- RED: focused setup, API, and frontend tests failed before the setup redirect,
  configuration endpoints, atomic editor, and GUI route existed. A later
  Library regression test reproduced that an unscored `new` row exposed
  decision buttons which the workflow correctly rejected.
- GREEN: the backend focused suite passed, all 153 frontend tests passed with
  type generation, lint, typecheck, and zero design-ban findings, and the
  production SPA bundle rebuilt successfully. `make quality` passed Ruff,
  format, mypy, and 1,871 Python tests (418 deselected).
- A real checkout without config ran `./setup.sh`, opened `/setup`, saved a
  mode-0600 `config.toml`, applied later threshold/filter changes without
  restarting the server, and kept SQLite `integrity_check=ok`.
- The user's Chrome extension exercised first-run validation, save/reload,
  mock scan and both evaluation stages, Runs detail, Library search/statuses,
  notes/follow-up, Triage selection/Shortlist/Skip, application recording,
  Pipeline, interview creation/completion, archive/restore, Insights and
  Performance windows, Sources add/remove, density, and advanced Settings.
- The unscored-row action defect was fixed test-first and rechecked in the same
  Chrome session: the `new` detail now exposes no Apply/Shortlist/Skip actions.
  Chrome reported no Jobfeed console errors; the only warnings came from the
  installed Grammarly extension.
