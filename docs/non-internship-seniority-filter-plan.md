# Explicit non-internship experience filter

Add only mandatory minimums of at least three years explicitly excluding internships. Generic three-year requirements and two-year non-internship requirements keep their existing behavior. Preserve preferred requirements, viable degree/experience alternatives, junior paths, mixed-level postings, and contradictory evidence. Do not broaden general year parsing, change thresholds, add fields or hashes, or run paid evaluations.

Acceptance: test-first rule and model-bypass evidence; rerun the frozen 2,402 Results sample to identify the exact impact and preserve the 21 broad-expansion cases; verify evaluation filtering; targeted lint/types and tracked release tests. Continue the already authorized main → Mini delivery, watch remote CI, and deploy only after active runs finish.

## Progress and evidence

- Rule red evidence: 6 failures and 15 passing protected controls before implementation. The rule now precedes the generic three-year junior path, without broadening general year syntax.
- Paid-boundary red evidence: 5 failures exposed existing Detailed-only and canonical dry-run paths bypassing the seniority gate. The same narrow rule now guards these paths when mode is `filter`; `off` and `shadow` behavior remains intact. Claims are released, Quick scores preserved, and no paid call made for the blocked job.
- Green: 48 new unit/integration cases passed across canonical/legacy, Quick+Detailed/Detailed-only, dry-run/real mock scoring, and filter/off/shadow modes. Formal tracked release suite plus new tests: 2,765 passed, 18 skipped, 478 deselected, 8 expected failures. Changed-file Ruff/format, full mypy (361 modules), and diff whitespace checks passed.
- Frozen sample verification: all 6,396 union inputs replayed against their saved baseline. Exactly 109 of 2,402 Results inputs changed from in-scope to out-of-scope; all 21 broad-expansion cases were unchanged. Across the union, 156 additional blocks and zero reversals. Evidence: `artifacts/seniority-regex-impact-20261001/implemented-rule-impact.json`.
- Existing evaluated Results rows and evaluation history are not removed by this change; the 109 count is a rule-layer projection. New Quick/Detailed work is filtered and eligibility uses the shared rule. No Results SQL predicate or history backfill is included.
- Pending: release CI and Mini readback.
