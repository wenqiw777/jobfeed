# Codex evaluation isolation

Result: evaluation starts Codex with Apps and plugins disabled, retains existing
model authentication/provider routing, and records complete redacted failure output.

Scope: Codex adapter and subprocess failure capture. No scoring policy, database
schema, credentials, or unrelated workspace changes. No hashes.

Verification: failing regression checks, passing adapter/subprocess tests, lint and
types, then real Quick/Detailed calls using Mini's CLI in an isolated test directory.
Confirm no MCP startup in captured output. Production activation and retry require
shipping authorization; do not interrupt the active evaluation.

Progress:
- Identified Apps/plugins enabled by default despite `--ignore-user-config`.
- Historical stderr summaries truncate at 200 characters; full stdout is discarded
  on nonzero exit. MCP warnings alone do not establish the terminal failure cause.
- Implemented explicit `features.apps=false`, `features.plugins=false`,
  `features.remote_plugin=false`, and empty MCP configuration. Existing auth and
  selected OpenAI-auth provider routing remain intact; no credential copies.
- Failed Codex calls now retain complete stdout/stderr in private (0600), redacted
  diagnostic files under `~/.local/share/jobfeed/diagnostics/codex/`. The persisted
  error summary includes the path. Diagnostic write failure preserves the original
  model error. Retry limits and scoring policy are unchanged.
- Regression evidence: 4 failures before implementation; 42 passing tests after,
  including canonical failed-item retry integration. Focused Ruff check/format,
  mypy (3 source files), and `git diff --check` passed.
- Mini smoke at 2026-10-14 15:01 EDT used patched modules loaded from
  `/tmp/jobfeed-codex-isolation.iRHGtr`, without changing the running checkout:
  `gpt-6-luna` 4217 ms and `gpt-6.1-sol` 5046 ms, valid structured responses,
  empty stderr, no MCP/command tool-call events. These are connection/schema
  checks, not a full rerun of failed jobs. Proof:
  `artifacts/codex-evaluation-isolation/mini-proof.json`.
- Historical MCP warnings remain insufficient evidence of the original terminal
  cause. Full diagnostics will allow future failures to be attributed correctly.
- User authorized combined deployment with the new daily scan windows on Oct 14.
  Windows are 08:00–10:00, 14:00–16:00, 19:00–21:00 America/Detroit, one random
  minute per window. Scan still precedes evaluation. Existing attempts are retained;
  today's unattempted 22:12 slot will move into the evening window.
- Scheduler checks: 7 failures before implementation; 61 combined scheduler,
  adapter, and canonical retry tests pass after. Old overnight slots cannot launch,
  including when an old daily plan remains on disk. No catch-up scans.
- Deployed on Mini Oct 14 after active runs finished: adapter commit `97ed2f5`,
  scheduling commit `38cdad7`. Existing Mini scheduling changes were preserved
  (pre-deployment files matched the Air baseline); other dirty files were untouched.
- Temporarily unloaded the interval scheduler, applied both changes, restarted the
  server, verified DB/Redis health, and restored the scheduler. Server PID 42711.
- Today's plan retains the 04:02 and 14:44 attempts; only 22:12 was replaced with
  19:45. Original files/plan backed up at
  `data/scheduler/deployment-backup-20261014` on Mini. Tomorrow's persisted choices
  are 09:45, 14:11, 20:50, each within the requested window.
- Real calls importing deployed modules passed: Quick 3522 ms, Detailed 4907 ms,
  valid structured responses, empty stderr, no MCP/command tool-call events.
  See `artifacts/codex-evaluation-isolation/deployment-proof.json` and
  `artifacts/codex-evaluation-isolation/deployed-model-proof.json`.
- Complete: requested changes are live. Historical failed-job retry was not part
  of this deployment; the next scheduled full cycle has not run yet.
