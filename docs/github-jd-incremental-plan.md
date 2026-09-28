# Incremental GitHub JD collection

Scope: reuse persisted SpeedyApply/GitHub descriptions before native routing or
browser enrichment. Keep refreshing configured markdown lists and return current
listing metadata through the existing save path. Use the existing platform and
canonical ID lookup; no new identity fields, schema, or dependencies.

Policy: reuse nonblank stored GOOD/FULL descriptions regardless of age, retaining
their original enrichment time/source/quality. New, blank, STUB/PARTIAL entries
still follow the existing fetch path. Lookup errors fail open. Existing definitive
closed-job filtering remains ahead of reuse.

Evidence (2026-09-17):
- [x] Red: 7 new tests failed because the enrichment lookup was not wired.
- [x] Implemented the optional existing EnrichmentLookup capability in the source
  and wired the runtime store in the source builder.
- [x] 53 tests passed across incremental/closed/routing/CLI/partial-scan tests.
- [x] Temporary real SQLite database: save, close/reconnect, build runtime source,
  reuse without native/browser calls, save again with the same ID and unchanged
  body/enrichment time. Production database was not modified by this validation.
- [x] Ruff check passed for the three changed Python files.

Boundaries / review:
- No running server restart, scan cancellation, browser changes, or shipping.
- Existing unrelated changes in the two production files were preserved.
- Running server already imported the old adapter; activation requires restarting
  the backend after the current scan finishes. Reloading the extension is not
  sufficient and is not required for this backend-only change.
- Reused jobs are not probed for changed text or newly closed status. Automatic
  expiry and a new forced-refresh interface are intentionally outside this task.
- Native collectors' existing incomplete-body fallback policy is unchanged.
