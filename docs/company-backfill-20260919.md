# Company backfill, 2026-09-19

User authorized immediate backfill of missing LinkedIn company names. Existing
repair script executed without production code changes. Only company and
company_norm updated; known company values guarded against overwrite.

- Backup: artifacts/pre-company-backfill-20260919.sqlite.
- Per-row old values and evidence: artifacts/company-backfill-20260919.sqlite.
- Initial missing: 3,442. Updated and independently read back: 118.
- Remaining missing: 3,324. Public endpoint returned HTTP 429; network lookups
  stopped. No guesses or attempts to bypass the limit.
- Live SQLite PRAGMA quick_check: ok.
- Partial completion only. Remaining records need verified evidence after the
  public endpoint becomes available or an authorized alternative evidence source.
