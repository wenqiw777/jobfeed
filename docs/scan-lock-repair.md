# Scan write contention and recovery

Scope: diagnose Mini scan e6303410-b87f-46cc-88c0-a5f1a542ad3f, repair bounded lock handling and scheduled recovery, deploy and push main. Preserve receipts, leases, application history, scheduling windows, unrelated edits. No new hashes.

Evidence: October 16 09:08:32 Detroit checkpoint busy; 09:09:01 scan failed in saving. No writer ownership telemetry existed, so historical PID cannot be proven. The legacy evaluation process 11084 has a closed asyncio loop and a log last modified October 14; open descriptors alone do not prove write ownership. Current write probes succeed.

Mini snapshot replay: 100 Handshake updates hold a transaction 2.779 s; 100 SpeedyApply updates 2.873 s. Every identity resolution executes an unindexed LIKE over state (60,781 rows / 232,950,689 value bytes). EXPLAIN reports SCAN state. Concurrent checkpoints are outside scan's writer lock and fail after 5 s; explicit checkpoint failure aborts otherwise durable scanning. Scheduler aborts before evaluation and has no retry of a confirmed terminal lock failure.

Acceptance: indexed alias lookup preserves exact tenant mappings; lock acquisition retries only SQLite busy errors with bounded wait, cancellation and rollback intact; receipt replay/fences remain valid; scheduled terminal database-lock scan resumes once through existing retry API, then evaluates only after success; unknown POST outcomes and other failures never retry. Test first, snapshot replay, live health and resumed run, CI.

Implemented: indexed case-sensitive GLOB alias prefix (same existing primary-key index), six bounded busy acquisition attempts (5-second SQLite timeout each), rollback for cancellation while queued BEGIN is completing, and one scheduler journal retry for a confirmed failed scan with database-is-locked message. No transaction-body replay and no ambiguous POST retry.

Verification: initial regression run 2 failed / 17 passed; after repair 294 relevant contract/unit/integration checks passed. Snapshot replay after indexed lookup: Handshake 0.297 s, SpeedyApply 0.365 s per 100 (approximately 8-9x faster).

Deployment: four scoped runtime files installed on Mini; originals preserved under data/deployment-backups/scan-lock-20261016/source. API health/db/Redis OK. Recovery of failed scan started as 6ba15e5e-3d57-4173-ae81-4f14870af5f2. OS F_GETLK sampling of WAL write-lock byte 120 observed PID 18745 (current Jobfeed server) in 73 of 100 samples; this is current evidence, not historical PID attribution. Handshake completed and over 4,300 postings saved without new busy errors.

CI: source commit b3e1de7 passed run 38928961392. Additional cancellation-in-queued-BEGIN test passed; busy recovery suite 6 passed.

Live recovery succeeded at 2026-10-16 16:13:36 UTC: 5,785 source outcomes, 444 inserted, 5,341 updated, 0 run errors. Application resolution completed 1,409/1,409. SpeedyApply retained its pre-existing partial-description warning (24/26); no new database-lock errors. Direct DB check found zero duplicate (platform, canonical_id) groups. Follow-up evaluation e41d8c1f-499d-495a-ad95-ed1f479ed744 started and its running row persisted. Full evaluation remains in progress; completion is not claimed.

Repair, tests, deployment, recovery and main push complete. Original failing run remains intact as audit history. Historical owner remains unprovable without pre-existing telemetry; do not label failed_source as the lock owner.
