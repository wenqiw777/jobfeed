# Resolve current Results review holds

## Result

Resolve every `Needs review` row currently visible in Results without reusing an
unverified legacy score or discarding source evidence.

## Boundaries

- Keep the active scan and evaluation runs running.
- Preserve every source posting and historical evaluation.
- For evaluation-only holds with an unambiguous canonical input, clear the hold
  and let the current evaluator score the canonical input.
- For divergent source descriptions, record the selected source explicitly,
  prefer a direct or official posting and then the most complete description,
  and make later source refreshes respect that resolution.
- Do not change jobs outside the current Results filter.
- Do not add hashes.

## Acceptance

- A focused integration test fails before the implementation and passes after.
- The repair is rehearsed against the online SQLite backup.
- The live repair uses one bounded transaction after rechecking the current
  Results snapshot.
- The current Results view has zero `Needs review` rows after repair.
- `PRAGMA quick_check` is clean and the service health endpoint returns 200.
- A follow-up evaluation run is allowed to finish so repaired rows receive
  current canonical scores.

## Risks

- A summary source can omit requirements found in the employer posting. Source
  selection therefore prefers direct or official URLs and fuller descriptions.
- Concurrent scan writes can change the Results snapshot. The repair re-reads
  the snapshot immediately before its transaction and validates each source.

## Progress and evidence

- Initial inventory: 5,818 held canonical jobs database-wide; 465 were visible
  in Results at the stable inspection point.
- Active evaluate and scan runs were mistakenly stopped, then immediately
  restarted as `e57113cb-b6a7-425a-950c-c039f9fcfba6` and
  `c08848a3-ad4f-4686-95ad-31a0212ddbb8`.
- Online backup: `data/backups/jobfeed-before-needs-review-20260928T1450.sqlite`.
- Red check: `test_reviewed_source_override_survives_divergent_source_refresh`
  failed with an empty Stage A claim before the implementation.
- Green checks: 24 tests in `test_real_job_evaluation_sqlite.py` passed; Ruff
  passed for the implementation, repair script, and focused tests.
- Rehearsal: all 467 live-snapshot IDs present in the rehearsal database had a
  clear review state; `canonical_evaluation_ready=True`; `PRAGMA quick_check=ok`.
- Live repair: 467 rows resolved, including 187 explicit source selections.
  The current Results API returned zero rows with a non-clear review state;
  `canonical_evaluation_ready=True`, health HTTP 200, and quick check `ok`.
- Follow-up evaluation run `f0a69af9-13e9-4d19-9a1c-a9324a999ee2` started for
  backlog Stage A and Stage B and succeeded: Stage A scored 269, Stage B scored
  556, with one error and a total cost of $21.9736.
- The interrupted predecessor had left 229 Stage B claims. All 229 were released
  through the existing revision/generation claim fence. A later web worker
  completed 30 before development-server reloads interrupted it; its remaining
  197 claims were released after the run lease had expired.
- Independent canonical Stage B run `91b05a47-c800-48f6-a255-6d321f630f79`
  claimed 196 of those 197 rows and succeeded: 195 scored, one error, verdicts
  150 apply / 39 consider / 6 skip, cost $7.3526. The missing row was exactly
  beyond the requested 14-day window at claim time (`canonical_posted_at`
  2026-09-14T19:33:48Z), so it was correctly left unclaimed.
- Failed-only canonical retry `6daebbb3-db01-4167-b9b1-d6db5fb1cbb9`
  retried the single current error and succeeded with an apply verdict; cost
  $0.0412.
- Final verification: Results API returned 2,127 rows, all with review state
  `clear`; canonical Stage B dry-run preview for the current 14-day window was
  zero; no Stage B row remained `in_progress`; canonical evaluation readiness
  was true; `PRAGMA quick_check=ok`; health HTTP 200. Two old Stage B status
  rows from 2026-09-26 remain outside Results and outside the current eligible
  preview; neither is an interrupted claim.
