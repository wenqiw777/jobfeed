# Results first-load diagnosis — 2026-09-22

Read-only investigation using the current database and explicit config.toml.
No production code/configuration changes, restart, cache flush, or scan resume.
Diagnostic Python processes used SQLite mode=ro connections and the actual
JobsViewService/store SQL; no schema initialization. Timers and profilers existed
only in the diagnostic process. This differs from measuring the live ASGI process.

## Measured application path

Exact Results reads 6,829 candidates and 32.8 MB of JD text before hard filters.
Filters retain 2,359 candidates. Another 81 in-flight twins enter display folding;
2,440 input rows produce 2,090 representatives, of which 2,009 belong in Results.
Only then are 50 rows returned. Existing maximum candidate cap is 10,000.

| Stage | Empty fold cache | Same-process repeat |
|---|---:|---:|
| SQL corpus including hydration/counts | 1.494s | 0.218s |
| Hard filters | 0.015s | 0.020s |
| In-flight twin lookup | 0.038s | 0.042s |
| Content fold | 1.540s | 0.004s |
| Total | 3.114s | 0.317s |

Another warm repeat was 0.239s. Empty-cache means a new in-process fold cache;
OS file caches were not cleared, so this is not a fully cold-machine benchmark.

Worker-thread cProfile on a separate empty-cache pass: 1.792s fold, 1.791s CPU;
9,087 `_same_prepared` calls; `_strip_locations` 0.800s cumulative; regex `findall`
0.616s exclusive; regex `sub` 0.486s exclusive. Cumulative profile entries overlap
and must not be summed. Normalization/location stripping and conservative content
comparison dominate, not the final ordering of 2,009 representatives.

SQL split in another pass: candidate query 1.430s, JD batches 0.047s, counts
0.025s, in-flight twins 0.053s. Query plan scans idx_eval_verdict_job, searches jobs
and status by primary key, and uses a temporary B-tree for ORDER BY. This is not
evidence of an unindexed scan of the entire jobs table. Another score-sort query
took 0.115s. Timings across separate passes are not additive.

## System-level latency amplification

Same read-only candidate SQL, same connection, 6,829 rows each:

| Attempt | Wall time | Process CPU |
|---|---:|---:|
| 1 | 1.996s | 0.210s |
| 2 | 1.346s | 0.200s |
| 3 | 0.092s | 0.086s |

Discovery-day Python callback used 0.009s across 6,829 calls, not the missing
1.8s. The wall/CPU gap establishes waiting/descheduling, not its precise source.

Machine: 17,179,869,184 bytes RAM (16 GiB); vm.swapusage used 16,184.31 MiB.
During three actual HTTP requests (9.051s, 3.047s, 0.545s), vm_stat deltas were:
405,241 pageins, 1,450 pageouts, 52,358 swapins, 125,788 swapouts. Pages are 16 KiB:
approximately 0.858 GB swap-in and 2.061 GB swap-out during this observation.
These are system-wide counters, NOT attribution of these bytes to Jobfeed.
Active runs remained empty. All three requests returned HTTP 200 and total 2,009.

Inference: active system memory pressure/paging is a credible major amplifier of
the variable cold-read latency. CPU computation alone does not explain it.
The previous 22.275s request has no per-stage trace and was NOT reproduced exactly;
do not claim its entire duration is explained or assign a precise percentage.

## Why the first load is different

- Results triage sorts bypass the provisional fast query; exact filtering and
  display folding finish before the first 50 rows are returned.
- Process-local four-entry fold reuse is empty after restart and may miss for a
  new input ordering or changed data. It does not eliminate repeated SQL/JD reads.
- Bodies are fetched before metadata-only hard filters: 4,470 of 6,829 candidates
  (65.5%) have already had bodies loaded when the hard filter discards them.
- API startup schema repair is awaited before lifespan yields. That contributes
  to server startup time, but is not automatically part of a post-ready request.
- Moving CPU work to a thread prevents direct event-loop execution; it does not
  remove the computation or provide unrestricted Python CPU parallelism.

## Minimal repair options, not implemented

1. Apply existing metadata hard filters before hydrating JD bodies, retaining
   identical candidate cap, order, filter logic and read consistency. Verify full
   result-ID parity across all four sorts, status changes and boundary dates.
2. Move expensive content preparation/display grouping off the page-request path
   into a correctly invalidated persistent view. Preserve order-sensitive grouping,
   in-flight suppression, fresh scores/statuses, and current representative rules;
   do not simply paginate 50 raw candidates before dedupe.
3. Re-benchmark under stable memory pressure, then define separate first-request
   and repeated-request targets. A cache warm-up alone merely moves the cost.

No evidence that replacing SQLite alone would remove the Python content work or
system swapping. No production behavior was changed by this investigation.
