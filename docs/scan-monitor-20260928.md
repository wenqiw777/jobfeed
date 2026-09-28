# Live all-enabled-source scan diagnosis

- Run: `70716756-32da-44eb-8aec-5de8faf4b1ce`
- Started: 2026-09-28 18:45:29 UTC (14:45:29 America/Detroit).
- Scope: user authorized one all-enabled-source scan and live monitoring/diagnosis.
- Sources observed running: speedyapply, jobright, linkedin-extension, handshake.
- Acceptance: terminal run state, source durations and errors, direct persistence evidence, evidence-supported bottleneck diagnosis.
- Preserve existing dirty checkout; no implementation/configuration changes or cleanup authorized by this diagnostic task.
- Background heartbeat automation: `scan`, once per minute, resumes monitoring this run only.

## Evidence

18:45:54 UTC (~25 seconds): Jobright fetching 200/1000, LinkedIn 50/1000, Handshake 275/700; SpeedyApply browser enrichment 11/11. No run errors. Bridge connected.

18:46:15 UTC (~46 seconds): SpeedyApply saving 2300/3050, Jobright fetching 360/1000, LinkedIn 100/1000, Handshake 600/700. Scan counters: 2300 discovered, 0 inserted, 2300 updated, 0 errors. Counters are not final and `updated` does not establish material content changes.

Redis observed at 197.26 MiB of 512 MiB, noeviction, 46 clients, 0 blocked clients. No evidence of current maxmemory exhaustion.

Concurrent evaluation run `9b0b2409-931a-4d2b-becf-d0e9ae7c2a0a` remains active. Resource contention is a hypothesis, not established.

## Findings and dispositions

- Scan service launches these sources concurrently. Simple source persistence starts after fetch returns (`_scan_simple_source`), so discovery counters can stay zero during active browser fetching. This alone is not proof of a stall.
- Early LinkedIn throughput is lower than Jobright and Handshake. Need later samples and browser-source scheduling evidence before attributing cause.
- `data/jobfeed-serve.log` is stale (August); current backend PID 77427 writes stdout/stderr to `/dev/ttys001`. Do not use the stale log to diagnose this run.

## Terminal result

Run ended at 18:46:44.706 UTC, after 75.17 seconds, status failed, failure_code `user_stopped`, message `Run stopped by user`. No stop request was sent by this investigation. Concurrent evaluation ended with the same code at 18:46:29.080 UTC. Backend PID remained 77427, bridge remained connected. The initiator is unverified: `run_orchestration._record_failure` maps any asyncio.CancelledError to user_stopped, so this code alone does not prove a human clicked Stop.

| Source | Last observed result | Duration / progress |
| --- | --- | --- |
| Handshake | completed, 637 saved | 52.47 seconds, about 12.1 jobs/second |
| SpeedyApply | completed, 3050 saved | 52.72 seconds, about 57.9 jobs/second |
| Jobright | fetching at cancellation | 380/1000; last batch 18:46:18, about 27 seconds without a new batch before cancellation |
| LinkedIn extension | fetching at cancellation | 150/1000; last batch 18:46:36, about 2.3 jobs/second to last batch |

Direct SQLite verification: 38 write receipts contain 3687 records; joining receipt job_id to jobs.id found zero missing records. Run reports 13 inserted and 3674 updated. No persisted Jobright/LinkedIn write receipts for this run were observed. Their browser progress must not be counted as committed jobs.

LinkedIn implementation performs page discovery, backend reuse lookup, then sequential detail requests for remaining rows. This is a potential throughput cost, but live per-request timings were not captured and cannot establish which stage dominated. Production options use pacingMs=0 in pilot-worker.js; do not attribute this run's latency to the generic batch default of one second.

Jobright implements 403/429/5xx backoff but current response status was not captured; the 27-second quiet period does not prove rate limiting. Redis maxmemory exhaustion and extension disconnection were not observed.

Monitoring automation `scan` paused after terminal-state diagnosis. No automatic retry, because the run carries a stop marker. Full-run bottleneck analysis remains limited by cancellation; establish whether stopping was intentional before resuming.

## User resumed scan

User reported resuming. Verified active run `c08848a3-ad4f-4686-95ad-31a0212ddbb8`, started 18:49:22.824 UTC. Existing heartbeat `scan` retargeted and activated.

At first observation (after 18:50:25 UTC), SpeedyApply 3050 and Handshake 637 are completed using restored counters. Jobright 340/1000 last progressed at 18:49:50.537; LinkedIn 175/1000 at 18:49:49.428. These two sources had no new published batch for at least 35 seconds. Concurrent evaluation `413696c7-43a1-4876-8005-462207728ca5` is preparing. No scan errors reported. Restored 3687 write count is not new work in the resumed run.

18:52:26 UTC: resumed run became `interrupted`, message `Run interrupted after its worker stopped responding`, and links to automatic replacement `f18e16d1-f5e1-4c6d-b37e-0419d6ae2e1e` (restart_count 1). By 18:52:40 replacement LinkedIn reached 50 and Jobright 120. The long simultaneous lack of progress is associated with worker lease loss/recovery, not established LinkedIn throttling. `_renew_with_transient_retry` suppresses renewal exceptions and returns false after bounded retries; the underlying failed renewal exception is unavailable from run state. Need live worker diagnostics to establish the underlying cause. No stop/restart requests issued by this investigation.

## Confirmed terminal-log findings

Read the actual VS Code terminal scrollback via native accessibility, after inspecting the LinkedIn page through Chrome extension (logged in; no visible challenge).

- 18:50:18 and 18:50:24: repeated `database is locked` HTTP 500 errors. Trace: GET /api/runs -> recover_stale_runs -> recover_expired_run_leases -> BEGIN IMMEDIATE. /api/runs/active also errored at 18:50:24. Database write contention is directly established during the first stall. The precise writer holding the lock is not captured in these traces. Heartbeat renewal also requires a write transaction, explaining vulnerability to this contention; its individual exception was suppressed.
- 18:54:03: `_sqlite_real_job_evaluation.py` file modification time. Terminal explicitly says `StatReload detected changes in 'src/jobfeed/adapters/store/_sqlite_real_job_evaluation.py'. Reloading...`, followed by `Shutting down` and waiting for connections.
- 18:54:04.568: LinkedIn source fails with `Jobright Chrome extension disconnected`. This establishes development auto-reload as the immediate cause of this disconnect, not the Chrome inspection itself. This investigation did not edit the triggering file.
- During shutdown Jobright official-page enrichment logs ZipRecruiter HTTP 403 and several LinkedIn HTTP 429 responses. These belong to Jobright official JD enrichment, not evidence that the LinkedIn extension search API was throttled.
- 18:54:20.487: replacement scan failed after saving partial results: LinkedIn 278, Jobright 428, SpeedyApply 3050, Handshake 637. Run totals 4393, inserted 192, updated 4201. A query for receipts under the replacement run ID returned zero; receipts must be traced via the original pipeline root before claiming direct row verification for this replacement.
- Shutdown raises `cannot close SQLite lifecycle with an active connection`; app lifespan closes the store without first draining RunManager tasks. Old backend PID 77427 exits; new PID 25999 starts at 18:54:58.158. This is a separate shutdown coordination defect.
- Screenshot's false green Completed comes from LiveRunRow.tsx LiveStatus: any sse.isDone returns success without checking run.status. It denotes transport completion, incorrectly presented as scan success.

Minimal repair targets: shorten/bound canonical claim write transactions and keep read-only run polling free of write recovery; avoid automatic source reload during live scans; drain active tasks before store shutdown; render terminal status from run.status. Verify lock-holder attribution before selecting a database implementation fix. No production code changed by this investigation.

## Final heartbeat verification (18:58 UTC)

Recovery chain is terminal: f18e16d1 remains failed with no restarted_by_run_id; active runs empty and both run leases released. Bridge reconnected, but no scan is running. Paused automation `scan`.

Resolved replacement run's durable pipeline root to 70716756-32da-44eb-8aec-5de8faf4b1ce. Direct SQLite query found 46 receipts, 4393 receipt entries, 4393 distinct job IDs, zero missing jobs. Thus partial results survive the failure. These are cumulative pipeline results including the initial run, not 4393 additional resumed writes. Final source counts: SpeedyApply 3050, Handshake 637, Jobright 428, LinkedIn 278; cumulative insertion counter 192 and update counter 4201 (not a material-change count).

## Historical LinkedIn comparison

Queried 45 recent source_fetch timings and successful-run pipeline mappings. Durations include the source wrapper (fetch plus saving), not isolated network time.

| Local start | Run prefix | Jobs | Seconds | Jobs/sec | Comparability |
| --- | --- | ---: | ---: | ---: | --- |
| Sep 26 00:51 | e677b8d4 | 426 | 35.5 | 12.00 | Resumed root ade9cbde; cannot count every job as newly fetched |
| Sep 26 14:55 | df5c382f | 592 | 126.2 | 4.69 | Resumed root 63ab910e |
| Sep 26 15:23 | 225b5161 | 585 | 154.4 | 3.79 | Resumed root 714a55ae |
| Sep 28 09:19 | 6893a802 | 320 | 124.6 | 2.57 | Successful, own pipeline root |
| Sep 28 14:52 | f18e16d1 | 278 | 109.6 | 2.54 | Resumed, interrupted by reload; partial result |

Earlier examples also vary widely: Sep 23 23:44 local, 1182/292.3s; Sep 25 22:47 local own-root run, 505/1320.1s. History supports faster prior runs but not a simple monotonic regression. Current partial aggregate throughput is close to this morning's success; cached/resumed totals and unknown detail request counts prevent an apples-to-apples network comparison.

Correction to prior diagnosis: serial detail fetching already exists in Sep 25 commit 6c6f866, so it does not establish the cause of a recent regression. Sep 28 commit 335500f removes redundant HTML requests for rows with company metadata; its delivery note explicitly says extension was not reloaded at delivery. Runtime activation for each historical run is unknown. Historical stats do not record LinkedIn detail request counts or reuse totals, so no supported numerical cache-hit comparison is possible.

## Authorized fresh LinkedIn timed run

Run b9b9c2d9-eac1-4519-80ee-62817aaa6880: started 19:03:09.791 UTC, succeeded 19:08:39.887 UTC. Total 330.097 seconds (5m30s). 335 jobs, 63 inserted, 272 updated, zero run errors, no restart. Direct receipt-to-row validation performed at completion.

Progress: 125 at 19:03:25, 248 at 19:03:40, 283 at 19:04:40, 335 at 19:05:19. Redis completed bridge steps contained 248 and 87 jobs with error=None and warning=False by the 19:05:40 observation. Current backend budget is 1000 per search URL; second search progress total became 1248. Neither command returned 1000 jobs; the run succeeding is not proof of 1000 fetched or independent proof of upstream exhaustion.

Important additional bottleneck: after each bridge result, jobboard_extension awaits page_extractor.enrich_row for all rows. JobPageExtractor handles repost evidence even when a JD is already present, under Semaphore(2), and progress remains fetching throughout. Redis rows showed 3 repost-extraction candidates among first-query snapshots and 56 among second-query snapshots. Existing JD does not skip these when snapshot contains 'reposted' and isRepost is not already true. Both bridge results were available by 19:05:40, leaving at least approximately 3 minutes before final completion. That remainder includes model interpretation and persistence, not LinkedIn listing fetch. Unattributed non-scoring Luna calls increased during this period, but lack run_id, so cannot assign exact call cost/count to this run. This explains a concrete hidden post-fetch waiting stage that previous serial-fetch-only answers missed.
