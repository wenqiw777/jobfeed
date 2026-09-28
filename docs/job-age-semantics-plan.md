# Job age semantics

Scope approved: effective job date uses valid posted_at, otherwise persisted
first discovery. Repeated scans must not overwrite first discovery. No archive
threshold, archive operation, candidate selection change, or historical date
reconstruction. No new columns or identity mechanisms.

Use one domain helper for existing hard-filter age and priority freshness.
Timezone-naive dates follow existing UTC convention; future posting timestamps
are invalid for age and fall back to discovery. Preserve current stored discovery
on both SQLite and PostgreSQL upserts, including repeated batch writes.

Acceptance: failing then passing age tests, real SQLite update/readback, adapter
contract tests as available. Preserve active evaluate; do not restart it.

## Result and verification

- Implemented shared effective date selection for freshness filtering and priority.
- SQLite and PostgreSQL upserts retain the stored discovery timestamp.
- Red checks reproduced future posting dates bypassing freshness and SQLite
  rescans overwriting first discovery. A naive-clock regression check also failed
  before normalizing the comparison clock to UTC.
- Green: 109 passed, 16 deselected across job-age, filtering, priority, SQLite
  jobs capability and default store contract tests. SQLite was exercised through
  actual temporary databases and readback; no live PostgreSQL test was run.
- Targeted Ruff and git diff --check passed.
- Backend activation pending a later restart; the active evaluate was not
  interrupted. Historical overwritten discovery dates were not reconstructed.
