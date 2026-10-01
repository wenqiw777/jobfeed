# Block repeated third-party publisher listings

## Accepted scope

User authorized blocking Yara AI, RemoteHunter / Remote Hunter, and Torentify to avoid spending evaluation tokens on their listings. Match full publisher names, ignoring case and repeated whitespace. Keep independently collected employer postings eligible.

## Implementation and acceptance

- LinkedIn discovery skips matching cards before detail/cache work.
- Session scans skip known publishers before enrichment; every scan source drops matching postings before persistence and intermediary resolution.
- The resolver itself skips blocked publishers without internal candidate lookup or external search.
- Existing listings are excluded by the shared intermediary policy from canonical scoring input and Results. Historical source rows are preserved.
- Similar employer names remain eligible.

## Verification evidence

- Initial new coverage: 11 failures, 45 passes; implementation made those 56 checks pass.
- Direct resolver checks reproduced 3 external searches before the resolver guard; all 3 now make no search or writes.
- SQLite integration checks: existing blocked source rows remain stored, cannot claim Stage A, and are absent from Results; an official Acme posting remains visible and claimable.
- Final targeted suite: 139 passed. Ruff, mypy for four changed source modules, and git diff --check passed.

## Findings and limits

- Canonical backlog selection is deliberately broad; the canonical claim/input check is the boundary that prevents scoring. Tests verify claim rejection rather than asserting that every ID is removed from the broad selector.
- No deletion or production database migration is required.
- Name matching does not identify publishers using unknown aliases; domains and heuristic detection are outside this change.
- User explicitly authorized Mini deployment. Deployed only the eight scoped files, leaving unrelated checkout changes untouched; no GitHub push was required.
- Mini had no active runs before restart. LaunchAgent server restarted successfully; API, DB and Redis health all returned ok.
- Live canonical Results queries before/after: Yara AI 3 -> 0; RemoteHunter 91 -> 0; Torentify 49 -> 0. Results still contains 13,111 other canonical jobs.
- Direct read-only production SQLite check: 49 Yara AI, 133 RemoteHunter, and 68 Torentify source records remain stored; all 250 fail the intermediary eligibility predicate.
- No paid scan/evaluation was triggered for deployment verification. Future runtime behavior is covered by the local 139-test suite and Mini policy smoke checks.

## Approved audit extension (2026-10-01)

User requested processing the 16 confirmed publisher groups from the audit: TalentHop, Sundayy, Netrolynx AI, FetchJobs.co, Ladders / The Ladders, Jobgether, Wiraa, Jack & Jill, Underdog.io (two observed display names and exact domain name), Dex, TalentAlly, CodeRound AI, Haystack, hackajob, Hire Feed, Jobverse.io. Extend the existing shared policy; retain raw history and employer postings. Jobright mixed listings, recruiting agencies and unverified publishers remain outside this extension.

Acceptance: all audited aliases skip LinkedIn discovery detail/cache work, source persistence, resolver searches, scoring claims and Results. Underdog sports company, Haystack Oncology, Dex Imaging and official Jobright.ai employer postings retain scoring eligibility.

Test-first reproduction: 91 failed, 70 passed with the extended tests against the previous policy. Final targeted suite: 238 passed; Ruff, mypy and git diff --check passed. Mini pre-deploy read-only baseline: 1,238 historical source rows and 392 affected Results among 13,109 total. Deployed to Mini with only these five scoped files. No active runs at restart; API, DB and Redis health all ok. Live Results: 392 affected -> 0, total 13,109 -> 12,717. All 1,238 historical rows remain stored and now fail scoring eligibility. Mini smoke passed all 19 aliases with case/whitespace variants and five similar employer names. No paid scans or evaluations were triggered.

Deployment reconciliation: another release temporarily replaced Mini policy code after initial verification. Reapplied the publisher guards to that release without altering application-route logic. A subsequent checkout restored the original publisher-block release (c41ffc3). Final live verification on the running server again confirms 392 affected Results -> 0, 1,238 historical rows preserved and healthy API/DB/Redis. Current source smoke confirms all 23 aliases. Evidence: artifacts/publisher-audit-20261001/deployment-verification.json.
