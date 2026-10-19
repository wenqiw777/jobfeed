# Automatic posting screening and human review

## Accepted result and boundaries

Use stored job content before scoring. Explicit multi-signal evidence blocks a
posting; uncertainty goes to a review queue and remains visible/eligible. A single
keyword, frequency, unknown company, or company-name substring never blocks a
company. Reuse existing state storage; preserve jobs, scores and workflow history.
No new hashes, paid models, logged-in LinkedIn reads, or broad re-evaluation.

Company promotion requires at least three independent canonical jobs with three
distinct titles, all observed company postings carrying strong third-party
publisher redirect evidence. Unassessed/clean/review/official-ATS postings veto
promotion. AI work and training-placement findings alone never promote a company.
Human job decisions override automatic company decisions; manual company decisions
persist across rescans. Existing explicit blacklist rules remain authoritative.

## Tasks and evidence

1. Conservative pure classifier: test-first positive examples and adversarial
   normal-company controls, quoted/negated text, URL host boundaries, templates,
   AI contributor versus engineering work, training/placement versus normal training.
2. Persistent source assessment on save/enrichment, exact-company promotion and
   reversible decisions; SQLite production plus PostgreSQL-compatible state queries.
   Results rows/count/selection and canonical/legacy paid paths share enforcement.
   Test with real temporary SQLite DB, duplicates, mixed employers, rescans,
   human overrides, enrichment and existing completed scores.
3. Review API and small UI page: evidence, source links, pending/blocked findings,
   keep/block job and company decisions. API/UI tests and real local browser pass,
   with a screenshot for user approval before merge. No live deployment is implied
   by approval to implement this behavior.
4. Read-only sample audit using stored Mini content, final local verification and
   scoped commit. Record counts, remaining limits and release evidence here.

## Readiness

Implementation-ready. Use exact case/whitespace normalized company names. Unknown
or missing company cannot promote. Need not guess true employer from generic prose;
ambiguous attribution stays review-only. Automatic decisions are explainable and
reversible. Historical records can be explicitly backfilled using a bounded audit
operation; startup does not rewrite the database.

## Progress

- Planning: inspected persistence, canonical claim/Results, legacy scoring and UI.
- Test-first: classifier/SQLite/API/UI cases failed before implementation and pass
  after wiring. The final legacy-funnel regression first failed with a persisted
  `spam:ai_contributor_work` hard_filter; spam no longer writes that sticky column.
  All eight evaluation-mode cases pass, including restored dry-run eligibility
  after Keep job and zero paid calls for blocked jobs.
- Feature verification: 39 classifier, persistence, API and evaluation tests pass;
  separate real PostgreSQL integration passes (1). Ruff passes; mypy passes for
  382 source files. Frontend: 188 tests pass, ESLint and production build pass.
- Browser: local Chrome at localhost:5177/spam-review; exercised Keep job, Keep
  company, company confirmation and reset. Screenshot:
  `artifacts/automatic-spam-review/review-preview.jpg`.
- Read-only Mini audit: 4,673 stored recent postings after existing blacklist
  exclusions; 0 automatic blocks and 3 review-only findings (Burtch Works,
  iDelsoft, Pave Talent). These findings do not establish spam or fraud.
- Limits: conservative rules can miss new patterns. New/enriched postings are
  checked automatically; historical backfill is explicit and bounded. No paid
  classifier or background automation was added. No production data changed.
- Full tracked backend regression: 3,286 passed, 19 skipped, 8 xfailed; one
  pre-existing code-hygiene test failed. Identified baseline
  hygiene failures concern missing docstring Args/Returns in unchanged
  `_codex_diagnostics.py`, `_linkedin_application.py`, `_linkedin_guest_shared.py`;
  each verified identical to HEAD. No unrelated fixes included.
- Release: user approved deployment on 2026-10-15. Applied scoped commit on Mini
  as `f266da1`; idle service restarted and scheduler restored. Health, DB, Redis,
  review API, Results API and review HTML return successfully. Both served JS/CSS
  assets exactly match deployed files. Existing unrelated Mini edits preserved.
  Mini has no pytest installation, so prior local verification plus live checks
  were used. Production browser recheck was blocked by the browser tool's policy
  initialization network failure; prior local browser evidence remains valid.
  Review queue starts empty; existing historical sample was not backfilled.
  Evidence: `artifacts/automatic-spam-review/deployment-proof.json`.

## 2026-10-16 agency recruitment correction

- Authorized: block explicit agency recruitment at posting scope, remove normal
  company introduction false positives, deploy to Mini, reassess only the seven
  reviewed IDs (393343, 449687, 449733, 468297, 509325, 510292, 510781).
- Version 2: employer-introduction `Our client` and explicit recruitment for a
  client block the posting; no company promotion from agency evidence. Generic
  customer mentions, aliases and related company names alone do not flag spam.
- Test-first evidence: 8 failures reproduced the original cases; one additional
  standalone client-recruitment case failed before its fix. Final targeted suite:
  51 passed, including SQLite re-audit, visibility, overrides and evaluation;
  scoped Ruff checks pass. No UI changes or schema changes.
- Risk: deterministic rules can miss unfamiliar wording; no claim of universal
  detection. Existing manual decisions retain precedence. Unrelated local and
  Mini edits are excluded.
- Deployed scoped code/tests to Mini as `a914014`; the historical plan differs
  there, so its patch was excluded. Service restarted while idle; scheduler
  restored. Live health reports service/DB/Redis all OK. Deployed classifier
  bytes exactly match the locally tested file.
- Transactionally reassessed all seven IDs using SpamDB.assess_source; original
  assessment backup: Mini `data/spam-recheck-20261016.json`. Direct DB checks
  confirm job rows and manual decisions unchanged, rule_version=2 throughout.
- Talent Vine 468297 and Syndesus 510292: agency_recruitment/block, source
  eligibility false. Valon 393343/449687/449733, CitiusTech 509325, Medal 510781:
  clear, source eligibility true. Live API readback: 2 blocked, 0 pending.
  Only these seven historical records were reassessed; new/enriched postings
  automatically use version 2. No remote push or merge performed.

## 2026-10-18 copied employer introduction correction

- Scope: local implementation and false-positive verification, no deployment or
  production state writes. Rule version 3 adds posting-only `copied_employer_posting`.
- Require an opening `About <employer>` followed by the same employer's introduction,
  plus an application careers/jobs URL on a matching company-name domain. An
  unrelated publisher with consultancy/staffing/recruitment/recruiting in its name
  blocks this posting. Without that additional signal, route to review and retain
  eligibility. Never promote this evidence into a company-wide block.
- Protect spaced/legal/extended company names, descriptions mentioning the publisher
  (possible corporate relationships), ordinary customer mentions, unrelated hosts,
  ATS URLs and non-careers URLs. This intentionally does not resolve arbitrary
  aliases, parent companies, ATS tenant ownership or all introduction formats.
- Test-first: two added positive/review regression cases failed with old rules
  (`clear`), then passed. Exact production record 508964 is retained as a public
  fixture. Final classifier, SQLite, evaluation and API suite: 65 passed. Ruff and
  scoped mypy pass. SQLite verifies exclusions, claim prevention, posting-only
  scope, Keep across rescan, reset and review-only eligibility.
- Read-only Mini snapshot: 9,526 descriptions discovered since 2026-10-11 UTC
  (including the reported ID), spanning 3,313 distinct stored company names.
  Compared old/new classifier outputs: exactly 14 changes, all UMAI Consultancy
  postings with ElevenLabs introductions and elevenlabs.io/careers destinations.
  All 14 were inspected; no other disposition/evidence changes. The 9 direct
  ElevenLabs postings remain clear. This is no observed incremental false positive
  in this sample, not a universal false-positive guarantee.
- Local evidence: `artifacts/copied-employer-screening/sample.json`, `audit.py`,
  `baseline.py` and `audit.json`. Source snapshot and audit are not production writes.
- Release remains pending explicit authorization. Historical persisted assessments
  require a bounded re-audit at release; changing the rule does not backfill them.
