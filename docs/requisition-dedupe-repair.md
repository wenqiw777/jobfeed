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
