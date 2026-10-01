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
