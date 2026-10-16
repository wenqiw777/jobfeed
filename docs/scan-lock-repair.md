# Scan write contention and recovery

Scope: diagnose Mini scan e6303410-b87f-46cc-88c0-a5f1a542ad3f, repair bounded lock handling and scheduled recovery, deploy and push main. Preserve receipts, leases, application history, scheduling windows, unrelated edits. No new hashes.

Evidence: October 16 09:08:32 Detroit checkpoint busy; 09:09:01 scan failed in saving. No writer ownership telemetry existed, so historical PID cannot be proven. The legacy evaluation process 11084 has a closed asyncio loop and a log last modified October 14; open descriptors alone do not prove write ownership. Current write probes succeed.

Mini snapshot replay: 100 Handshake updates hold a transaction 2.779 s; 100 SpeedyApply updates 2.873 s. Every identity resolution executes an unindexed LIKE over state (60,781 rows / 232,950,689 value bytes). EXPLAIN reports SCAN state. Concurrent checkpoints are outside scan's writer lock and fail after 5 s; explicit checkpoint failure aborts otherwise durable scanning. Scheduler aborts before evaluation and has no retry of a confirmed terminal lock failure.

Acceptance: indexed alias lookup preserves exact tenant mappings; lock acquisition retries only SQLite busy errors with bounded wait, cancellation and rollback intact; receipt replay/fences remain valid; scheduled terminal database-lock scan resumes once through existing retry API, then evaluates only after success; unknown POST outcomes and other failures never retry. Test first, snapshot replay, live health and resumed run, CI.

Implemented: indexed case-sensitive GLOB alias prefix (same existing primary-key index), six bounded busy acquisition attempts (5-second SQLite timeout each), rollback for cancellation while queued BEGIN is completing, and one scheduler journal retry for a confirmed failed scan with database-is-locked message. No transaction-body replay and no ambiguous POST retry.

Verification: initial regression run 2 failed / 17 passed; after repair 294 relevant contract/unit/integration checks passed. Snapshot replay after indexed lookup: Handshake 0.297 s, SpeedyApply 0.365 s per 100 (approximately 8-9x faster).

Remaining: deployed verification, CI. Historical owner remains unprovable without pre-existing telemetry; do not label failed_source as the lock owner.
