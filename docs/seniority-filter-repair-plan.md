# Seniority filter repair (2026-09-22)

## Boundary and acceptance

- [x] Preserve explicit senior/lead/management and clearly required over-3-year blocks.
- [x] Do not block on company history, age notes, preferred experience, a range whose lower bound is at most 3, or a viable education alternative.
- [x] Keep explicit entry and multiple-level roles out of the ambiguous model path.
- [x] Recheck the affected 380-job cohort using the configured gate without changing stored jobs or evaluations.

## Evidence

- Before implementation, `tests/unit/test_seniority_gate.py` had 6 new failures covering company history, age, preferred years, education alternatives, and entry titles. Additional range and college-hire tests failed before their fixes.
- The completed run `a0b4f9f1-4a32-4af8-9e84-ff430b762ee8` recorded 380 candidates, 295 seniority exclusions, and 85 Quick evaluations.
- A read-only replay of the same reconstructed 380-job cohort through the configured rule and model (`v20260826T201223Z`, threshold `0.9210827946662903`) produced 203 exclusions and 177 survivors. The original run and stored evaluations were not modified.
- The 10 remaining model exclusions are ambiguous titles; none of the inspected direct-college-hire or preferred-only examples remains excluded.
- `pytest` on seniority, evaluation funnel, eligibility, and priority behavior: 113 passed. `ruff check`, `ruff format --check`, and `git diff --check` passed for the touched code and tests.

## Runtime boundary

- The targeted rescore below loaded the repaired code in a new process and wrote its evaluations. The original run's counter remains historical. An already-running web process may still need a restart before future evaluations use the repaired rule.

## Targeted rescore (2026-09-23 UTC)

- User authorized rescoring. Reconstructed the original run's 295 unscored candidates, then applied the repaired gate to select exactly 92 newly eligible job IDs. A scoped Stage A dry run returned the same 92 IDs with zero hard-filter or seniority exclusions.
- Evaluation run `247d86cf-09ce-4e2d-b8d6-09294ab324f9` succeeded with 92 Quick scores, 27 Detailed reviews, zero errors, and recorded LLM cost of $1.31591016. The run did not change the original historical counter.
- Read-only DB verification found all 92 target IDs with completed Quick scores; 27 had completed Detailed reviews and 65 had `skipped_below_threshold`. Detailed verdicts were 22 `apply`, 4 `consider`, and 1 `skip`. The evaluation lease was released.

## MTS and junior-path repair (2026-09-26)

- Boundary: preserve MTS as a neutral title; keep explicit new-grad, co-op, entry-level, 0–3-year, and multi-level junior paths visible. Continue blocking clear required experience above three years and explicit senior ownership.
- Before the code change, the targeted tests had 9 failures. The original `acc8e001-2714-42e0-b669-2c991d7331ef` cohort was reconstructed read-only at exactly 688 candidates; the configured gate reproduced 583 rule blocks, 54 model blocks, and 51 survivors.
- After the change, read-only replay on the same cohort produced 456 rule blocks, 48 model blocks, and 184 survivors. Of 28 MTS-title roles, 26 now pass and 2 retain explicit over-three-year or senior-ownership evidence. The prior run remains unchanged.
- Targeted unit and funnel tests passed (78 total); Ruff and diff checks passed. The revised rules have not been activated in a running web process or used for paid rescoring.
