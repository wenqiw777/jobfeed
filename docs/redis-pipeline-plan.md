# Redis-backed scan pipeline

Status: implemented and locally enabled; bounded real-source and process-crash
acceptance passed. No commit, merge or release performed.

## Outcome and boundaries

Use local Redis Streams for durable delivery between scan stages. Keep four
source lanes and one logical SQLite job writer. No generated identity digests,
no new hashing, no removal of source rows, no changes to scoring or application
status. Preserve all unrelated checkout changes. Do not interrupt the active scan.

Redis is not an automatic fix for SQLite contention and does not provide an
atomic transaction with SQLite. Database commits must precede message ACKs;
replays must use existing source/native identities and durable receipts.

## Material decision

1. Recommended: persist discovered enrichment tasks, enrichment results, and
   pending database writes. Resume accepted work after worker/API restart.
2. Write-only: persist only completed source output awaiting database commit.
   This cannot recover work lost before enrichment finishes.

User selected option 1, complete enrichment and persistence coverage. Existing sources
currently combine discovery and enrichment inside fetch_jobs; complete coverage
requires splitting that boundary, not merely wrapping save_job with Redis.

## Proposed complete pipeline contract

- Discovery: publish stable task IDs plus source payloads before expensive
  enrichment. Preserve each source row independently. Record discovery cursors
  only after their page's messages have been accepted.
- Enrichment: consumers consult complete stored JDs/cooldowns, perform native
  then browser work when needed, and persist output before acknowledging input.
  Transfer result and ACK within one Redis transaction/script. A crash between
  an external response and result persistence may repeat the external request;
  do not claim exactly-once browser/network operations.
- Persistence: consume results into bounded batches; perform quality-aware
  upserts plus message receipts in the same DB transaction. ACK only after
  commit. Receipts preserve insertion counters and latest-scan IDs on redelivery.
- Recovery: reclaim pending messages only after an expired consumer lease;
  renew ownership during slow browser work. Fence stale workers using the
  existing run generation. Manual cancellation must not be silently resumed.
- Completion: discovery closed, all tasks resolved, result backlog drained,
  final batch committed and run counters persisted. Queue empty alone is not
  completion because messages may still be pending with consumers.
- Failure: transient retry with bounded attempts/backoff; persist exhausted
  work in a failed-task state visible to the run. Redis outage fails visibly;
  never silently fall back to a volatile queue or mark a failed batch saved.
- Backpressure: cap outstanding messages/payload volume. Do not trim or evict
  unacknowledged work to enforce a size bound. Retention only after completion.
- Progress: independent latest-state snapshots, not one durable message for
  every counter tick. Preserve the checkpoint-storm fix.

## Infrastructure assumption

Local Docker/OrbStack, official Redis image pinned to a tested version, named
data volume, AOF persistence, loopback-only published port, no eviction of queue
data. No cloud service or public exposure. Select and verify fsync policy before
stating power-loss durability guarantees. Backups must include Redis state if
accepted-but-uncommitted work is required to survive loss of its data volume.

## Independently verifiable tasks

1. Redis infrastructure and transport: typed payload contract, consumer groups,
   ACK/reclaim, bounded admission. Verify against real Redis, including Redis
   restart and outage. First deliverable is transport durability evidence.
2. Batched DB writer and receipts: current SQLite runtime, no JD downgrade,
   atomic batch rollback. Crash after commit/before ACK must not duplicate
   jobs, counters, or insertion attribution.
3. Discovery/enrichment split (complete option only): persist tasks before
   enrichment, preserve source lane limits, resume pending accepted tasks.
   Test failed and killed workers before and after result publication.
4. Run lifecycle integration: startup recovery, manual stop versus crash,
   fencing, counters, terminal drain, Redis outage health. CLI/web parity.
5. Acceptance: bounded real-source scan, worker restart recovery, unchanged
   second scan. Original-size Redis scan remains an unperformed load check, not
   a claimed result. Monitor queue lag,
   pending count, write latency, DB handles and lock errors. No shipping or
   merging without separate authorization.

Return to design if exact continuation requires a source cursor the extension
cannot expose, if sources cannot publish pre-enrichment work, or if the user
requires independent multi-host browser workers. Do not substitute repeated
source discovery for exact cursor continuation without stating that limitation.

## Confirmed implementation boundaries

- User explicitly accepted rediscovering unfinished listing pages. Completed
  enrichment output is reused; no extension page-cursor protocol in this change.
- Redis Streams acts as a durable workflow journal. The API/CLI's four source
  coroutines are workers, not a new independently deployed worker fleet. Named
  tasks are claimed under the existing DB lease and generation fence.
- Discovery/native enrichment/browser batches are durable before subsequent
  work. A source's completed output is durable before its DB writer starts.
  Writes are immediate batches of 100 plus the final remainder, not a timer
  that waits for 100 jobs. This does not yet stream each enriched JD to SQLite
  while the rest of that same source is still enriching.
- Resume of unfinished workflows uses original inputs and completed outputs.
  A drained run with terminal source errors retries as a fresh incremental
  scan, consulting stored complete JDs and cooldowns. It does not replay a
  cached terminal error indefinitely. Unknown failures leave pending work.
- Automatic restart is for interrupted workers, after lease expiry and browser
  reconnection. Manual stops and ordinary source failures require explicit retry.
  Existing source-specific retry policies remain; no infinite task retry loop.
- Finished, DB-finalized and drained Redis journals expire after seven days;
  unfinished work never expires. Capacity is 512 MiB with no eviction; a full
  Redis fails visibly instead of dropping accepted jobs. DB receipts remain.
- PostgreSQL is compatibility-only: current DBSettings explicitly rejects a
  PostgreSQL runtime. Its legacy lease adapter has no cross-process fencing.
  A preliminary PostgreSQL batch attempt failed a real contract test because
  there is no run_leases table; that unsupported path was removed rather than
  adding an unrelated PostgreSQL migration or claiming fencing parity.

## Verification receipt

- Initial transport tests failed on missing RedisPipeline, then passed against
  real Redis. Pending-completion check failed on missing assert_drained, then
  passed. Deferred browser recovery/manual-stop tests failed on missing method,
  then passed. Redis health outage test failed with HTTP 200, then passed with 503.
- 66 related tests passed (one PostgreSQL-marked test deselected): real Redis,
  SQLite receipts, four lanes, native reuse, browser remaining-target recovery,
  manager recovery, scan audit/checkpoint pressure and web skeleton.
- Injected failure after DB commit but before Redis ACK: replacement run retains
  four inserted outcomes, four persisted jobs, and no repeated completed native
  enrichment calls. Queue drains to zero.
- Real container replacement to pinned redis:8.2.9: AOF preserved completed
  result and unfinished task's original input; recovered task drained to zero.
- Focused mypy and Ruff checks passed. Final rerun follows acceptance.
- Before live writes: absolute SQLite backup at
  artifacts/pre-redis-pipeline-20260918.sqlite; backup PRAGMA quick_check = ok.
- Previous non-Redis run finished: 4676 saved, 215 inserted, 4461 updated,
  two source errors. No active run was interrupted for the Redis switchover.
- Final focused regression: 189 passed, one PostgreSQL-marked test deselected.
  Includes SQLite atomic rollback (no partial jobs and no receipt), terminal
  source-error retry and retention of finished versus unfinished journals.
- Real bounded Redis scan c66303f4-da24-445a-9bac-012262bdc674 succeeded:
  22 updates, zero inserts/errors, four completed lanes, stream backlog zero.
  The temporary per-source limit was five; LinkedIn has multiple searches and
  their existing merge behavior returned seven total rows. Permanent limits
  were not edited. SpeedyApply reused four complete JDs and deferred one retry;
  native/browser enrichment counts both zero.
- Real worker kill: bc94a927-ea69-4307-ba76-3c42c93e8645 was interrupted with
  15 committed rows and two pending journal tasks. After the 180-second lease
  expired, the replacement f98522d0-741a-4838-a91b-e2ac0c7ee51d automatically
  reused all three completed sources and finished LinkedIn: 22 updates, zero
  errors, restart_count=1 and backlog zero. DB readback found four batch receipts
  under the original workflow root. Redis container replacement separately
  verified AOF recovery.
- Local dependency installed: redis 6.4.0. User approved updating uv.lock with
  standard package integrity metadata only. The lock now includes redis 6.4.0
  and its conditional async-timeout 5.0.1 dependency for Python before 3.11.3;
  no existing dependency versions changed. `uv lock --check` passed.
- Final live process restarted without temporary limit overrides. Health reports
  db=ok and redis=ok; Chrome extension reconnected; no active scan. Live SQLite
  PRAGMA quick_check = ok. Full-size Redis load testing is not claimed.

## Evidence so far

- No existing Redis dependency, deployment file, or running container found.
- OrbStack Docker context responds; no local redis-server/redis-cli found.
- Current scan was still active at inspection: 1,605 saved rows, zero run errors;
  Jobright/Handshake completed, SpeedyApply/LinkedIn ongoing.
- Existing source fetch returns post-enrichment lists; current persistence
  performs a separate transaction per job. These boundaries need explicit
  changes for the recommended full pipeline.

## Primary references

- https://redis.io/docs/latest/develop/data-types/streams/
- https://redis.io/docs/latest/commands/xautoclaim/
- https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/
