# Migration source release, 2026-09-28

User authorization: fix outstanding validation, push, and rebase merge to main.
Keep held offline experiments and generated data local. No mini deployment.

Acceptance: tracked-source lint/format/mypy, backend and frontend tests/build,
real browser review and user screenshot approval, independent review, remote CI,
then rebase merge and main CI. Never weaken checks to hide failures.

- [x] Repair stale schema fixtures and source progress contracts.
- [x] Restore port typing, service boundaries and production documentation.
- [x] Format and repair tracked offline tooling.
- [x] Verify clean exported source without local data or excluded experiments.
- [ ] Browser screenshot and independent review.
- [ ] Push, watch CI, rebase merge, watch main CI.

Baseline: combined tree 2659 passed / 13 failed; tracked Ruff 331 findings
(the earlier 507 included held files); mypy 135 errors. Runtime SDE hash
implementation is explicitly grandfathered; no new hash logic is authorized.

## Verification evidence

- Clean source export excludes held experiments, generated models and private data.
  `make quality`: 2617 passed, 477 deselected, 8 xfailed; Ruff and mypy pass.
- Frontend: 186 tests pass; lint and production build pass. Extension: 90 pass.
- Browser lane: 1 passed, 3101 deselected. Real browser: Library, correct posting
  detail and Runs evaluation scope verified with three synthetic jobs; no console errors.
- Structural/Redis focused verification: 49 pass. Stale fixture regressions: 74 pass.
- CI installs Node and frontend dependencies required by Python DOM-parser tests,
  and verifies extension/frontend alongside backend quality.
- Independent review found no release blocker in journal extraction or canonical casts.
- Screenshots remain local in artifacts/migration-release-20260928/.
- Pending: screenshot approval, remote CI and rebase merge.

Remote CI initially failed in optype 0.17.1 because its type-alias syntax conflicts
with the configured Python 3.11 mypy target. Pin optype 0.9.3, matching the locally
verified typing environment; rerun CI without changing the supported Python target.

CI mypy 2.3.1 additionally reproduced four count-comprehension inference errors
not emitted by local mypy 2.1.0. Explicitly type the decision tuple and remove
now-redundant casts. Mypy 2.3.1 now passes all 352 source files; focused workflow,
route and query-shape regressions pass.
