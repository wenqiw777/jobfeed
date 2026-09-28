# Authenticated job-board Scan integration

Outcome: existing extension and local Runs Scan ingest LinkedIn and Handshake bodies through batch APIs, without guest requests or navigating each job page.

Acceptance:
- Preserve Jobright wire compatibility; refuse unsupported new-source commands before sending them to an old extension.
- Batch progress, cancellation, immediate stop on HTTP/GraphQL failure; preserve full description text through normal persistence.
- LinkedIn: up to 500 jobs per search URL independently, two supplied searches, up to 1000 before source-ID deduplication. Handshake remains capped at 500.
- Local UI uses Jobright, LinkedIn signed-in, Handshake. Legacy adapters remain available explicitly; local default sources follow the three-board plan.
- Continuous signed-in limit experiment counts individual list/detail requests and stops at first error. An observation ceiling without a restriction proves only a lower bound, not unlimited access.

Evidence / progress:
- Prior actual extension tests: Handshake 50 bodies / 3.709 seconds; LinkedIn 50 bodies / 20.113 seconds.
- New scanner resume/HTTP telemetry test failed as expected before implementation.
- Integration bridge/mapping tests added before implementation.
- Both continuous API experiments started in Chrome with the same scanner code; sequential requests and normal one-second inter-batch pacing.

Boundaries: no deployment/merge, no additional extension, no credential persistence, no new schema fields, no hashes. Browser tools cannot reload extensions; an updated worker requires the user to reload once after implementation.

User steering:
- Handshake: exhaust Most Relevant first, then fetch newest; subsequent scans start newest after persisted bootstrap verification.
- Stop the rate-limit experiment. Acceptance is stable 500-JD scans; do not increase volume to seek restrictions.
- Handshake newest sort verified from actual JobSearchQuery: DESC / POST_DATE. Actual 50-body scan succeeded in 1.589 seconds.

Latest verification:
- 35 focused backend/contract tests passed; 13 extension tests passed; 21 Runs UI tests passed; TypeScript and changed-file Ruff checks passed.
- Replayed each actual 500-job capture through the new bridge, adapter, ScanService and temporary SQLite: LinkedIn 500/500 stored bodies, Handshake 500/500 stored bodies, zero errors. This is replay evidence, not a live new-extension scan.
- Real Runs UI shows the three authenticated source choices; screenshot in data/jobboard-pilot-20260908/integrated-scan-menu.jpg.
- Handshake newest Software Engineer New Grad: API total=69, 69 bodies in 3.101s. Direct offsets 69 and 75 both returned zero jobs (normal response). UI also says 69 results.
- Newest query totals (one-row probes only): Software Engineer 3685; AI Engineer 1893; AI Engineer New Grad 40. These are reported search counts, not downloaded/unique/relevant counts. No automatic widening implemented pending the user's search-scope preference.
- Live bridge currently connected with supported_sources=[jobright], confirming the old worker is still loaded. Reload original extension 0.4.0 is the remaining prerequisite for live integrated Scan verification.
- Two original continuous-test tab handles became unattached; close commands failed. User was asked to close these; no additional continuous tests were started after cancellation. Their runner has a 30-minute ceiling.

Category search update:
- User-supplied URL with jobRoleGroups=64, employmentTypes=1, jobType=9 and posted_date_desc was verified in the Filters UI: Software Developers and Engineers, Full-Time, Job; keyword is empty.
- Captured JobSearchQuery filter uses jobRoleGroupIds=[64], employmentTypeIds=[1], jobTypeIds=[9] as string arrays, POST_DATE descending.
- Actual shared-scanner run succeeded: 428 unique jobs, 428 descriptions, 22.733 seconds, matching total=428 (max 500; no padding).
- Local Handshake config now uses this category search_url. This mode skips keyword/relevance bootstrap and scans newest to max 500. Existing keyword mode remains available when search_url is omitted.
- Second supplied URL (query=software engineer, newest, page 3) showed 3698 results; the page included mechanical/civil engineering, sales, maintenance and marketing. It was inspected but not substituted as the main source.

LinkedIn search-results update (in progress):
- Both user-supplied search URLs are configured; their keyword, geography and time filters are preserved. Selection/tracking parameters are omitted and each scan starts at offset 0.
- New RSC search-list API is wired into the existing extension. Full descriptions use the existing authenticated job-detail JSON API, without detail-page navigation.
- Per-search budget regression failed at [250, 250], then passed at [500, 500]; overlap is merged by native job ID.
- New-link list pagination test failed before implementation, then passed. 15 extension tests and 7 focused Python tests passed.
- Version 0.5.0 advertises search-results support so the backend rejects outdated workers instead of silently scanning a different search.
- Live 500-per-link extension verification is still pending; browser API reconnaissance is not extension performance evidence.
