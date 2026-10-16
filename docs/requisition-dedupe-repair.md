# Requisition identity repair

Scope: repair ambiguous identity resolution, verified Workday requisition aliases, and historical-application exclusion. Deploy to Mini, backfill with backup and reviewable disposition list, push main and watch CI. Preserve unrelated dirty changes and user application/status history; add no hashes.

Confirmed diagnosis: JR355250-1 new parents own zero identity rows because identifiers are already globally owned by older parents. INSERT OR IGNORE is expected for that uniqueness conflict. Resolver then records multiple_identity_candidates/explicit_status_conflict and returns without setting an identity hold. This permits scoring unresolved duplicates. Salesforce official CXS external_career_site endpoint reports jobReqId JR355250 for URL suffix JR355250-1. Do not strip suffixes without authoritative evidence.

Acceptance: same scoped requisitions join; mutually incompatible candidates stay separate and held; distinct requisitions, companies/tenants and unverified suffixes do not merge. Verified Workday alias uses official CXS evidence. Application records suppress fresh recommendations regardless of ghosted/archived status. Backfill preserves applications and explicit statuses, with conflicts held rather than overwritten.

Verification and deployment evidence: pending.

## Implemented and verified

- Identity collisions now distinguish compatible multiple owners from contradictory evidence. Compatible exact-identity bridges merge transitively; every conflicting candidate is held, rather than logging one pair and leaving others eligible.
- SQLite identifier inserts ignore only the intended unique-key collision, not unrelated constraint violations.
- Workday URL suffix aliases require the same posting's official CXS jobReqId. State stores the verified tenant-scoped mapping and observed URL remains intact. Unverified suffixes and distinct tenant/requisition combinations remain separate.
- Existing application-route receipt transactions persist verified aliases; direct suffixed Workday links now read official posting facts rather than assuming the URL token is the requisition.
- Results and automatic evaluation selection exclude recorded applications independently of current workflow status. Identity conflicts are excluded from Results and scoring. Explicit user statuses are never overwritten to force a merge.
- Full local tracked suite: 3336 passed, 19 skipped, 479 deselected, 8 xfailed. Subsequent multi-candidate hold and cross-tenant regressions pass; Ruff, formatting, mypy and hygiene checks pass. PostgreSQL code mirrors the changes; no live PostgreSQL service was used locally.
- Live Salesforce route verification: resolved verified_application_route, workday_requisition JR355250 for the JR355250-1 URL.
- Official CXS reads for 49 observed base/suffix pairs: 28 verified, 21 unverified (HTTP/access/availability failures). Only verified pairs are eligible for alias backfill; no suffix-only merges.

## Mini release and production backfill — 2026-10-16 Detroit

Deployed source commit 6c09433, plus the existing main LinkedIn application/Guest shared modules required by Mini's older checkout. All deployed modules imported successfully before restart. No unrelated Mini dirty files were overwritten.

Idle preflight, scheduler suspension/resumption, full SQLite backup and service restart completed. Health/db/Redis all OK. Production backfill: 1,286 source targets, 423 duplicate parents merged, 1,829 source rows affected; 28 official Workday aliases, 21 unverified pairs left separate. All 296 applications and 174,461 source statuses preserved; foreign-key check clean. Remaining 173 parents without exclusive identifiers are all identity_conflict, none clear.

Salesforce Results 29 -> 27; new duplicates 168507 and 168357 excluded. Original September 7 application remains on 57649. Conflicting ghosted/archived histories are held, not force-merged. There are 547 historical Apply/Consider rows covered by the new exclusion predicates, including already archived records; this is not a count of newly removed visible cards.

Mini rollback backup: data/deployment-backups/requisition-20261016-release/before.sqlite, prior source under source/. Preserve concurrent user writes when considering restoration. Local detailed CSV/Markdown report: artifacts/requisition-dedupe-repair/report.md.
