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

## Release boundary

- Code and tests only. A running server must load the new code and a new evaluation must process the previously excluded jobs. Historical run counters remain historical.
