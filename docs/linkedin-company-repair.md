# LinkedIn company recovery — 2026-09-17

Scope: repair missing LinkedIn company extraction and safely recover existing
Unknown values. No JD, evaluation, application-state, or unrelated source changes.

## Implemented

- When the signed-in payload and search card lack a named employer, request the
  public header of that exact native LinkedIn job ID and parse only
  `.topcard__org-name-link`. Decode HTML through DOMParser, not text guesses.
- Company lookup uses one-second pacing by default; authentication/rate-limit
  responses stop further company lookups in that batch. Failed lookup retains JD.
- SQLite rescans cannot replace an existing LinkedIn company with Unknown/blank.
  A later nonempty company remains eligible to update the record.
- Maintenance script records original company/normalized company and source
  evidence before each guarded update. No title-only or JD inference used.

## Verification and remaining work

- Two new extension tests failed before implementation; all 30 extension tests
  passed afterward. A new SQLite protection test failed before implementation;
  relevant backend/partial-scan tests then passed (14 total).
- Public posting 4467961547 returned Capital One in its company header.
- Rehearsal recovered 654 records using exact-ID local matches and public headers.
  HTTP 429 stopped further public requests; no bypass or immediate retry used.
- Applied rehearsal evidence to live DB without repeating network requests;
  additionally restored job 467488 from its previously verified public header.
  Total: 655 repaired; 2,351 remain unresolved.
- Reversible audit: `/Users/wenqiwang/wwq/jobfeed-rollback-l6LQQn/company-live.sqlite`.
  Earlier complete DB backup: same directory, `jobfeed-before.sqlite`.
- Live API `/api/jobs/467488` returns Capital One and the expected program title.
- Local backend uses uvicorn auto-reload. Installed Chrome extension still needs
  reload before claiming the new extractor is active in a real signed-in scan.
- This is partial data recovery, not a claim that all Unknown records are fixed.
  Remaining records need a later rate-limit-free retrieval. No scheduled retry,
  push, merge, or new evaluation was started.
