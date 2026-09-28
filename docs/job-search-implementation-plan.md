# Job Search Policy Implementation Plan

Status: implemented and locally verified as of 2026-09-03.

Source of truth: `docs/job-search-policy.md`.

## Outcome

Every discovered in-scope US software New Grad/0-3 job is visible, receives a
hard-blocker eligibility result, and is ordered ahead of internships. Ordering
uses the confirmed queue tiers and ranking inputs. Evidence Fit is a separate,
evidence-only evaluation and never controls visibility or eligibility.

## Boundaries

- Preserve current source ingestion, dedupe, and user-owned job decisions.
- Keep unknown data neutral or pending; never turn missing data into a blocker.
- Do not scrape Levels.fyi, submit applications, change source rate limits, or
  ship/merge as part of this plan.
- Reuse current Stage A/B storage only where compatibility is safe; do not keep
  their old mixed semantics under a new label.
- Add tests only for executable behavior that could silently regress. Prompt
  wording is verified by rendering and representative evaluations, not brittle
  string assertions.

## Tasks and acceptance evidence

- [x] **1. Baseline and data-path audit**
  - Result: current discovery -> eligibility -> evaluation -> persistence ->
    jobs view -> UI path and existing uncommitted work are accounted for.
  - Evidence: focused current tests run; schema and live SQLite fields inspected.
  - Return to design only if an existing migration or public API makes the
    confirmed state model impossible without destructive conversion.

- [x] **2. Eligibility and Triage semantics** (depends on 1)
  - Result: Pending, Apply, and Blocked are derived from explicit hard-blocker
    evidence; Applied and Ignored remain user decisions; Consider is absent.
  - Evidence: behavior tests cover US/remote-US, >3 years, active versus
    obtainable clearance, graduation intersection, uncertainty, and low-fit
    jobs remaining Apply/visible.
  - Return to design only for a hard condition not covered by confirmed policy.

- [x] **3. Independent Evidence Fit evaluation** (depends on 1)
  - Result: a lean new prompt and response model return four evidence dimensions;
    code computes `40/25/20/15`; no eligibility, company, freshness, or Apply
    decision enters the score.
  - Evidence: parser/weighting behavior tests plus representative Luna/Terra
    evaluations checking evidence traceability, ordering, cost, and latency.
  - Return to design only if the configured providers cannot reliably return the
    required structured result.

- [x] **4. Priority inputs and queue ranking** (depends on 2 and 3)
  - Result: queue tiers are strict; Primary uses compensation `20`, verified
    company strength `15`, freshness `30`, New Grad clarity `25`, and Evidence
    Fit `10`; internships use `40/25/25/10`. Unknown values are neutral.
  - Evidence: pure scoring tests cover queue dominance, freshness, explicit New
    Grad signals, pay present/missing, verified/unknown companies, and internship
    threshold behavior.
  - Return to design only if a ranking input has no trustworthy persisted source.

- [x] **5. Persistence, API, and UI integration** (depends on 2-4)
  - Result: additive Stage B storage supports Evidence Fit dimensions; eligibility,
    tier, component scores, and priority are deterministically derived from the
    posting and verified company cache. Results immediately shows Pending jobs,
    sorts by tier/priority/posting date, and uses the confirmed state labels.
  - Evidence: migration/store contracts, API tests, web tests, and a real browser
    pass against the local app.
  - Return to design only if compatibility requires discarding existing user data.

- [x] **6. Existing-data reconciliation and final verification** (depends on 5)
  - Result: current saved jobs are recalculated without hiding or deleting rows;
    a current scan/evaluation produces the intended queue.
  - Evidence: before/after SQLite counts, representative row inspection, full
    focused backend/frontend checks, and a Triage screenshot for user review.

## Progress and findings

- 2026-09-03: Existing uncommitted company-intelligence work is preserved. It
  enriches API rows but does not yet calculate company strength or priority.
- 2026-09-03: Existing Triage sorting still groups old Stage B verdicts
  (`apply/consider/skip`) and falls back to Stage A score; this must be replaced.
- 2026-09-03: A standalone Evidence Fit template draft exists but is not wired.
  It will be shortened before use and will not inherit `_shared_preamble.md`.
- 2026-09-03: A proposed static prompt-string unit test was removed as brittle
  and unnecessary before it entered the final change.
- 2026-09-03: ATS title keywords are empty and the seniority gate runs in shadow;
  FDE, AI, Solutions, Founding, and ordinary software roles are not excluded by
  a short search-title list.
- 2026-09-03: SEC, YC, startup portfolio, and AI-hiring catalogs synced to 27,947
  identities. Google, Amazon, Meta, and Palantir aliases resolve to SEC evidence;
  unknown companies remain neutral.
- 2026-09-03: Luna and Terra returned traceable Evidence Fit on the same Palantir
  FDE. Luna scored 74 in 26.5s for $0.00275; Terra scored 71 in 19.0s for
  $0.03199. Luna is the batch default because Terra cost 11.6x more here.
- 2026-09-03: Live-data review found and fixed physical-engineering false
  positives, foreign-market `(m/w/d)` postings mislabeled United States, salary
  sentence punctuation, and null hooks in the new detail payload.
- 2026-09-03: Wait/Consider was removed from Triage. Legacy stored decisions
  remain intact and available in Library.
- 2026-09-03: Final review corrected two evidence boundaries: a generic
  `Engineer I` title becomes Apply when the full official JD confirms software
  work, while partial/Jobright-summary rows stay Pending and release their
  Stage B claim until a full official JD is available.
- 2026-09-04: A separate-field parser demo ran Luna on graduation, YOE, degree,
  and compensation for the same 50 official company-career URLs, then escalated
  only 33 validator-flagged fields to Terra. Blind Terra replacement improved
  166/200 fields to 171/200 but also regressed five previously correct fields.
  Requiring a Terra result to pass all deterministic checks before replacement
  eliminated those observed regressions and reached 176/200. The remaining 22
  flagged fields stay unresolved: they may contribute an explicitly estimated
  priority value but cannot support an eligibility Block.
- 2026-09-04: The escalation trigger caught 3/3 known graduation errors, 11/14
  YOE errors, 0/4 degree errors, and 10/13 compensation errors. This invalidates
  a validator-only degree strategy: missed degree semantics need an independent
  semantic review path or substantially better validators before production use.
- 2026-09-04: The semantic-labeling set was expanded to 500 unique frozen JDs
  from 212 companies and 18 hosts. It contains 120 graduation, 100 YOE, 120
  degree, 100 compensation, and 60 no-signal primary samples; every JD is still
  labeled for all four fields. Sources are company-owned career pages or
  official Greenhouse/Ashby/Lever postings, excluding aggregator summaries.
- 2026-09-04: Sol-A and Terra-B independently blind-labeled all 2,000 fields.
  State agreement was 1,960/2,000; decision-fact agreement was 1,488/2,000;
  138 additional fields differed only in representation, and 512 remain in the
  adjudication queue. Agreement is not accuracy, so the 512 disagreements are
  not automatically resolved in favor of either model.

## Verification record

- Task 1: `.venv/bin/pytest` over prompt rendering, jobs view, user decisions,
  company intelligence, and seniority behavior: 72 passed. Read-only SQLite
  inspection: 111,728 jobs; 23,006 evaluations; 13,791 Stage B rows skipped by
  the old threshold; legacy verdicts include 1,652 Consider and 723 Skip rows.
- Tasks 2-5: focused backend contracts passed; frontend Vitest passed 159/159;
  Ruff and strict mypy passed 315 source files; production UI build passed.
- Task 3: two live model calls and two persisted Luna Evidence Fit rows verified.
  Twilio job 440981 persisted score 66; the detail API returned 15 evidence items
  and 10 gaps without a legacy verdict.
- Semantic parsing demo: 50/50 unique official URLs across 12 company-owned
  career hosts were evaluated. Verified Luna-to-Terra arbitration accepted 11
  field replacements (2 graduation, 2 YOE, 1 degree, 6 compensation), retained
  Luna and marked 22 escalations unresolved, and improved the independent Sol
  audit from 166/200 to 176/200 without an observed correct-to-incorrect
  transition among accepted replacements. This is experiment evidence, not a
  production parser acceptance result.
- Double-blind expansion: both 500-row annotator artifacts passed manifest/hash,
  2,000-field completeness, and normalized contiguous-evidence grounding. Sol-A
  produced 1,372 grounded claims and Terra-B 1,363. The comparison split is 465
  graduation, 417 YOE, 306 degree, and 300 compensation decision agreements;
  the remaining 35/83/194/200 fields respectively require adjudication.
- Task 4: current fast Triage API returned 50/50 tier-0 Apply rows at the top,
  including Palantir FDE, Sierra/Netic New Grad, AI, Product, Infrastructure,
  and ordinary Software Engineer I roles.
- Task 5: a real in-app browser pass showed Results/Applied/Ignored plus
  Eligibility, Priority, Evidence fit, and Posted; browser console had no errors.
- Task 6: the exact current Triage request returned 7,366 deduplicated rows;
  its first 50 were 50/50 Apply and 50/50 tier 0, including HPE AI, Palantir
  Forward Deployed New Grad, General Dynamics Entry Level, General Motors,
  American Express campus AI/Data Engineer, and ordinary full-stack/product
  roles. The fast first page returned in under one second while the exact
  background reconciliation completed in 30.6 seconds.
- Final checks: backend `2063 passed, 418 deselected`; frontend `159 passed`;
  production TypeScript/Vite build passed; Ruff, Ruff format, strict mypy over
  315 source files, and `git diff --check` passed. The live Jobright bridge
  status endpoint returned `connected: true`; both persisted new-schema
  Evidence Fit rows use `jd_quality=full`.
