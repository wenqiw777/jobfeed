# AI training contributor role exclusion

## Result and boundaries

Block AI trainer, human AI-response rating, and data annotation/labeling roles before paid Stage A and Stage B, including Engineer titles with an AI Trainer suffix. Block the verified DataAnnotation contributor listings even when their titles omit the suffix. Keep real ML/software engineering roles that mention training models or building annotation tools in their JD.

Use one deterministic rule in evaluation, eligibility, and Results row/count/selection filters. Preserve source records, workflow history, and previous evaluation evidence. No model calls, schema changes, or broad re-evaluation are needed.

Continue the user-authorized main → Mac Mini release and targeted verification workflow from this conversation. No frontend changes are planned.

## Acceptance and progress

- [x] Failing regression evidence: before implementation, 11 contributor-rule tests and 9 pipeline/Results tests failed; 5 legitimate engineering controls passed. Live behavior independently confirmed existing 92-point Stage A scores and Apply verdicts for trainer gigs.
- [x] Both canonical and legacy evaluation paths make zero paid calls, including Stage B with an existing high Stage A score. Dry-run matches. Expanded checks cover AI Trainer suffixes, plain DataAnnotation developer titles, and Data Annotator roles.
- [x] Results rows, exact totals, and bulk selection exclude these jobs while Source Library and evaluation history remain available. SQLite history checks and an actual PostgreSQL test passed.
- [ ] Focused tests, lint/types, release CI, and live Mini read-only verification pass.

## Findings

Live Mini has 13 DataAnnotation `Machine Learning Engineer - AI Trainer` postings, all with completed Stage A and Stage B. The JD describes choosing paid projects, contracting, writing training problems, and evaluating model trajectories. Other DataAnnotation listings omit AI Trainer from Software Engineer/Developer titles. Prolific `AI Trainer / Study Participant` describes ranking AI responses and paid studies.

Risk: matching generic JD words such as training or annotation would reject legitimate engineers. Restrict title matches to explicit contributor roles; DataAnnotation is an exact company match, not a substring blacklist of all AI companies.

## Verification evidence

- Focused regression suite: 151 passed; PostgreSQL compatibility: 1 passed against a migrated disposable database. Ruff checks/formatting passed; mypy found no issues in 361 source files.
- Formal tracked release test suite plus new tests: 2717 passed, 18 skipped, 478 deselected, 8 expected failures. No release test failures.
- The first unrestricted pytest discovery entered unrelated vendored numpy/scipy tests in ignored `data/`; specifying the project test directory corrected collection. The project-directory run then reported 2775 passed and two failures in an untracked old experimental label fixture (`test_label_jd_semantic_gold_50.py`). Both read a baseline sample whose saved JD no longer matches the current local job DB. These experiments and their data are outside this change and were left untouched. Release verification uses tracked tests plus the new regressions.
- Mini baseline: DataAnnotation has 103 source/real jobs, 47 completed A scores and 47 completed B scores. Results currently contains 12 DataAnnotation cards under the active age/workflow filters. Example real job 145507 retains A=92 and B=Apply.
