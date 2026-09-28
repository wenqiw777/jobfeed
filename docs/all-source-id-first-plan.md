# ID-first discovery across the four enabled sources

Status: code implemented and local verification complete; activation and live
browser acceptance deferred by the user to preserve the running evaluate task.

Contract: discover source/native IDs, deduplicate before detail/enrichment work,
probe stored nonempty GOOD/FULL JDs, and request details only for missing or
incomplete jobs. IDs are scoped by platform; titles/company similarity is not
identity. Preserve existing cooldowns and source rows; introduce no hashes.

- LinkedIn: split browser listing discovery from details, gate each page through
  the backend, and reuse full stored postings. Deduplicate across search URLs.
- Handshake: its one GraphQL listing call already includes descriptions, so
  keep that efficient bulk operation. Gate returned IDs before accepting or
  performing any further work; never introduce unnecessary per-job requests.
- Jobright: recommendation responses already contain summaries; enforce native
  ID deduplication before official-JD enrichment and stored-JD reuse.
- SpeedyApply: preserve existing cross-list native/exact-ATS identity grouping,
  DB reuse and cooldowns; verify with existing and strengthened tests.

The browser protocol gains a versioned capability. An old extension must fail
with a reload instruction, never silently bypass the lookup gate. Preserve
Redis partial-batch recovery, run limits and metadata during cached reuse.

Verification: red then green Python/Node checks for old-full/new/partial jobs,
overlapping searches/pages, lookup failure, capability mismatch, and Redis
recovery; focused regression and live extension flow after user reload. Do not
interrupt the currently running scan or reload the extension on the user's behalf.

## Evidence and disposition (2026-09-18)

- Red checks reproduced LinkedIn detail requests before the lookup gate,
  duplicate Jobright native-ID enrichment, and missed stored-JD reuse without an
  HTTP client. Checks now pass after the bounded fixes.
- Focused Python regression: 116 passed, 1 deselected. Includes real SQLite
  platform-scoped batch lookup and real Redis interrupted browser recovery with
  the discovery callback and cached completed rows.
- Extension Node regression: 35 passed, 0 failed. Includes discovery-only
  listing, cached-ID detail suppression, duplicate-only page continuation,
  lookup errors, correlated replies and cancellation.
- Ruff passed for the eight changed source modules and new discovery/Redis
  tests; mypy passed for all eight modules. `git diff --check` passed.
- Empty, whitespace-only and incomplete stored JDs remain eligible. Lookup
  failures stop enrichment rather than silently bypassing reuse. Existing
  SpeedyApply retry cooldowns and exact-ATS grouping remain unchanged.
- Handshake listing already includes JD bodies; preventing those payload bytes
  is not part of this implementation and no additional per-job call was added.
- User explicitly chose to keep evaluate running. No backend restart or extension
  reload was performed. Restart the updated backend, then manually reload
  extension 0.8.0 before live scan verification. No live speedup claim yet.
- No commit, push or merge. Existing unrelated checkout changes preserved.
