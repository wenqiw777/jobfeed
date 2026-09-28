# Local Redis scan pipeline

Start Redis before enabling `[redis_pipeline].enabled`:

```sh
docker --context orbstack compose -p jobfeed-pipeline -f compose.redis.yaml up -d
uv pip install --python .venv/bin/python 'redis>=5,<7'
```

Redis is published only on `127.0.0.1:6379`. The named Docker volume stores AOF
data; `appendfsync always` favors durability over peak throughput. Do not delete
the volume while unfinished scans exist. Backups need both SQLite and that
volume to preserve accepted but uncommitted work.

## Behavior

The four source coroutines remain inside the API/CLI process. Redis Streams is
their durable task journal, not an independently deployed worker fleet:

1. Persist discovery inputs/results and native enrichment tasks before work.
2. Persist native results and each received browser batch. On recovery, reuse
   completed native results and exclude received browser targets from resubmission.
3. Persist the completed source output, then write SQLite batches of 100, with
   any final smaller batch committed immediately. Jobs and insertion receipts
   share a transaction; Redis completion is acknowledged after the DB commit.
4. Check all source workers finished and no pending tasks remain. Source errors
   cannot produce a successful Redis-backed run.

This currently writes after each source finishes, not continuously after every
individual JD. The other source lanes do not wait for that source. Progress is
coalesced separately and does not create one DB writer per UI tick.

## Recovery and limits

- API process death: wait for the existing 180-second DB lease to expire, then
  the recovery check runs every 10 seconds and waits for the extension to reconnect.
- Manual Stop is not automatically resumed. Retry of an unfinished workflow
  restores its journal; Retry after a fully drained source-error run starts an
  incremental scan using stored JDs and retry cooldowns.
- Incomplete listing discovery may repeat pages. There is no page-cursor protocol.
  A crash between an external response and Redis persistence may repeat that
  external request; external requests are not exactly once.
- Completed, drained and DB-finalized journals are released immediately.
  Browser batch copies are released atomically after their complete result is saved.
  Small run mappings retain a seven-day diagnostic TTL.
  Unfinished work does not expire. Redis has a 512 MiB no-eviction memory limit
  and refuses admission when full rather than dropping tasks.
- SQLite batch receipts persist to make commit-before-ACK replay safe for both
  row identity and original inserted/updated outcomes. No new job identity
  calculation or schema migration is involved.
- `/api/health` reports Redis failure as HTTP 503 when Redis mode is enabled.
  A Redis outage never silently switches scans back to volatile execution.

## Verification

```sh
JOBFEED_TEST_REDIS_URL=redis://127.0.0.1:6379/15 \
  .venv/bin/pytest tests/integration/test_redis_pipeline.py \
  tests/contract/test_scan_batch_receipts.py
```

Tests use unique namespaces and do not flush the Redis database. The Redis
integration suite skips when no local Redis exists unless the explicit test URL
is provided. PostgreSQL is not a supported runtime for this feature; this
repository's normal runtime already requires SQLite.
