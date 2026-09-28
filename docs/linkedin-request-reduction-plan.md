# LinkedIn request reduction — 2026-09-28

Scope: avoid redundant signed-in LinkedIn job-page requests while preserving
missing-company recovery and the existing discovery-gate cache reuse path.

## Result

- [x] When the detail response or search card has a company name, fetch only the
  listing page and JD detail; do not request `/jobs/view/{id}`.
- [x] When the company name is missing, retain the exact-ID signed-in job-page
  fallback. Any explicit repost evidence found by that fallback is still kept.
- [x] Preserve discovery-gate reuse of stored and Redis JDs without refetching
  their details.

## Verification

- Failing check before implementation: the two-job request-count test observed
  5 requests instead of 3 because both jobs fetched an unnecessary HTML page.
- Passing check after implementation: all 79 extension tests passed. The focused
  test now observes 3 requests for two jobs with company metadata and zero
  `/jobs/view/` requests. Missing-company fallback and cache reuse tests pass.
- `git diff --check` passed for the changed source and test files.

## Delivery

The source is updated locally in one focused commit. No push, extension reload,
or running scan restart was performed.
