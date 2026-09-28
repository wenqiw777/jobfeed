# Simple seniority filter and uncertain-case experiment

Scope: apply the simplest explicit-title filter, then focus on unresolved cases in the frozen 100-job sample. No job status changes, no new model calls, no new hashes. Production activation versus demo-only is awaiting user clarification because the development server auto-reloads and a 750-job evaluation is active.

Acceptance: preserve original 100 samples and baseline; conservative keyword classification; mixed levels/title-body conflicts stay visible; no invented junior label when JD does not specify seniority; inspect unknown causes and demonstrate bounded improvements with regression checks. Training-sample improvements are not holdout accuracy.

Progress:
- Confirmed live evaluator active; no watched backend files edited.
- Read 46 uncertain-case evidence extracts and selected full requirement sections.
- Primary causes: escaped Markdown and number words; missing section headings/experience grammar; treating any `or` as a degree alternative; truly unstated seniority; multi-path or conflicting requirements.
- Building offline experiment and tests before any activation decision.

Verification and current disposition:
- RED: new test module initially failed collection because the new judge implementation did not exist.
- GREEN: `pytest -q tests/unit/test_seniority_uncertain_demo.py` -> 9 passed.
- Frozen 100-sample second pass: original 46 uncertain -> 3 block, 5 keep, 24 review, 14 not_extracted. All evidence snippets checked as original JD substrings.
- Three new block suggestions inspected against their requirements evidence. Other outputs are not independently labelled gold.
- Report: artifacts/seniority-demo-100/uncertain-v2/REPORT.md.
- No changes to watched src, live configuration, database, or job states. No production activation; user scope clarification remains pending.
