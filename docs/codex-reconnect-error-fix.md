# Codex reconnect error classification

User authorized repair after read-only 403 investigation. Scope: classify the
complete CLI JSONL response; no concurrency/auth changes, no bulk re-evaluation.

- [x] Red regression: recovered reconnect incorrectly failed; final turn.failed
  incorrectly returned an earlier answer. Two failures reproduced.
- [x] Defer intermediate error handling until complete output is parsed. Only
  turn.completed clears pending errors; agent_message alone cannot clear them.
  Final error/turn.failed remains a failure. Validate answer and terminal usage.
- [x] Recovered request returns once, preserving usage without another paid retry.
  Malformed JSONL, missing/invalid usage and subprocess failure handling retained.
- [x] Adapter tests: 18 passed. Full isolated-tree make quality: 2622 passed,
  18 skipped, 477 deselected, 8 expected failures; lint/format/mypy passed.
- [ ] Deployment: local uncommitted changes only; Mini not updated.

Evidence: /tmp/jobfeed-403-red.log and /tmp/jobfeed-403-quality.log.
The upstream intermittent WebSocket 403 cause remains undetermined. Historical
stdout is unavailable; this change cannot prove past failed calls recovered.
