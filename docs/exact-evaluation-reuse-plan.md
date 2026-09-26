# Run-local exact evaluation reuse

## Agreed scope

Preserve all existing scoring prompts and every input character, including JD
whitespace. Reuse only within one leased evaluation run, for the same stage,
the identical LLM client instance (`is`), and equal complete `LLMRequest`
(messages, model, temperature, max tokens and response schema). No persistence,
schema changes, new digest fields/computation or historical-result inference.

Different locations/platforms remain different evaluation inputs under the
existing prompt. They are not silently made equivalent. Different job IDs
remain independently eligible and receive independently persisted results.

## Implementation

- [x] Add linear exact-equality request cache and per-request in-flight locks.
- [x] Bound retained inputs/results to 256 entries and 16 MiB (conservative
  Python object sizing); evict oldest successes and decline oversized entries.
- [x] Initialize only after obtaining the evaluate lease; clear on success and
  failure, after workers have drained. No idle-service retention across runs.
- [x] Publish only after successful parse and job result persistence, with a
  lease check after saving. Failed/cancelled calls do not publish a result.
- [x] Hits receive deep-copied results with incremental cost zero, do not reserve
  a provider call or write provider usage, and emit `evaluation_reused` with
  `job_id`, `stage`, `reused_from_job_id` (no prompt/resume/JD in logs).
- [x] Stage B claim maintenance surrounds lock waiting and completion. Waiter
  cancellation cannot cancel the owner. Sweep clients have separate identity.
- [x] Stage A exceptions cancel/drain sibling workers before releasing claims
  under the still-valid lease, matching Stage B's cleanup boundary.
- [x] Preserve every distinct post in the evaluation funnel. SQLite/Postgres
  candidate queries no longer exclude a pending post because another post with
  the same company/title or external identity completed evaluation.

## Evidence

Test-first checks: new cache test initially failed collection because the module
did not exist. The candidate-preservation tests failed with one surviving post
instead of two and SQLite returning the later row instead of the pending twin.
After implementation these checks pass.

The final targeted suite passed **110 tests** (2026-09-22), covering cache,
service, funnel, lease scheduling, real temporary SQLite evaluations/claims,
schema/lifecycle and PostgreSQL query construction. No live provider or runtime
database was used. No scan, service restart, deployment or commit was performed.

Key direct SQLite receipt: one `run(stage="a")` with two distinct job IDs and
identical real rendered inputs made **one synthetic paid call**. Both rows were
`completed`, costs were `0.125` and `0.0`, and the ledger recorded one call and
`0.125` total spend. A newly inserted identical pending post in a second run
required a second call, proving run-local isolation and candidate retention.

Service tests use the real prompt renderer and demonstrate misses for changed
location, platform, title, company, JD trailing whitespace and Stage A score;
hits after budget exhaustion; no cache publication after parse/save/lease
failure; and Stage B waiter cancellation while maintaining its claim.

One existing concurrency fixture returned empty messages for every job, which
made formerly distinct synthetic requests identical and deadlocked its own
two-worker rendezvous under correct single-flight behavior. The fixture now
retains title/rough-score input and uses distinct titles for that cancellation
test. Its original cancellation and claim-release assertions still pass.

Verification commands:

```sh
uv run pytest -q tests/unit/test_evaluation_reuse.py tests/unit/test_evaluation_reuse_service.py tests/unit/test_evaluation_post_retention.py tests/unit/test_evaluate_funnel.py tests/unit/test_evaluate_timing.py tests/unit/test_evaluate_lease_scheduling.py tests/integration/test_evaluation_reuse_sqlite.py tests/integration/test_sqlite_evaluation_claims.py tests/integration/test_sqlite_claim_release.py tests/integration/test_sqlite_schema_lifecycle.py tests/unit/test_sqlite_schema.py tests/unit/test_sqlite_schema_contract.py
uv run mypy src/jobfeed/services/_evaluate_reuse.py src/jobfeed/services/evaluate.py src/jobfeed/services/_evaluate_stage_b.py src/jobfeed/services/_evaluate_funnel.py src/jobfeed/adapters/store/_sqlite_claim_filters.py
```

## Limitations and review dispositions

This is conservative request reuse, not semantic JD matching. It uses no LLM
duplicate classifier. It deliberately does not promise cross-location/source
reuse while those fields remain actual scoring inputs. No reuse survives a
run/restart; eviction can cause an old request to be evaluated again. Cache
storage is bounded; in-flight requests are transient and bounded by workers.

Removing cross-post suppression may expose more legitimate pending jobs and
increase paid work relative to the old incorrect soft grouping. Existing run
limits and paid-call budgets remain in force. LinkedIn display preference and
per-location Results behavior are separate changes owned by the Results work.

The implementing agent initially checked PostgreSQL SQL construction only.
Coordinator follow-up verified the ephemeral PostgreSQL gate-candidates suite:
21 passed. Two old assertions expected pending same-title posts to disappear;
their observed failures were updated to the approved independent-post contract.
Source-size
baseline already exceeded 300 lines in `evaluate.py`; this change does not
claim a repository-wide file-length cleanup. Final independent review belongs
to the coordinating agent; no shipping authorization is implied.

Coordinator independent review found no P1/P2 issues in run-local reuse.
Combined evaluation/Results/SQLite/web regression: 150 passed, 13 deselected.
No real provider calls or live evaluation runs were used for this validation.
