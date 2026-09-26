# Jobfeed source extension — batch pilot

## LinkedIn company metadata (0.8.1)

Semantic search now resolves company names from each card's referenced render
state records. Company metadata survives the discovery gate and Redis replay:
stored complete JDs are reused while missing company names can be filled from
the fresh listing. Known names are preserved. The unauthenticated guest-page
fallback has been removed; missing new-posting names use a bounded signed-in
posting-title lookup. Ambiguous or unavailable evidence is not guessed.
Restart the updated backend and reload this extension to activate both halves.

## ID-first integrated Scan (0.8.0)

Integrated LinkedIn and Handshake scans require the `discovery-gate-v1`
capability. LinkedIn sends each page's native IDs to the backend before fetching
details. Nonempty stored GOOD/FULL descriptions are reused; duplicate IDs across
searches and Redis-persisted completed rows do not trigger another detail request.
Missing, blank or incomplete descriptions remain eligible for collection. A failed
database lookup stops the task rather than silently fetching everything again.

Handshake already returns descriptions in its bulk listing response. It uses the
same reuse/deduplication gate after listing, without adding per-job requests; the
gate cannot eliminate description bytes already included by the listing API.
The independent batch pilot below does not use this database gate.

Activation requires restarting the updated backend **then** reloading this
unpacked extension. Defer both while an existing evaluate run must be preserved.
An older extension receives an explicit reload error instead of bypassing the gate.

The existing Jobright Scan bridge is preserved. Version 0.3.0 adds a separate
LinkedIn / Handshake batch-test page; it does not replace production sources.

Reload this unpacked extension in Chrome after updating. The new host access is
limited to LinkedIn and Handshake. Open the extension popup, then **LinkedIn /
Handshake batch test**. Choose the source, query and maximum (1–500), then Start.
The extension opens one search tab in your existing signed-in profile, observes
its job API CSRF header in memory, fetches batches and saves a local JSON result.
No cookies or browser profile files are read. CSRF values are not exported.

- Handshake: GraphQL `jobSearch` returns descriptions in batches of 25. Captures
  employer, location, dates and work-authorization fields alongside source HTML.
- LinkedIn: authenticated Rest.li search in US / past 24 hours, 25 cards per
  request, then direct JSON description requests. Source text and formatting
  attributes are both retained. No guest endpoint or per-job page navigation.
- No LLM is called. No application or save-job action is performed.
- On HTTP/auth/rate-limit or GraphQL failure, stop and preserve collected rows.
- API timing starts after login/search bootstrap. Total timing includes bootstrap.
- Search results can contain promoted or unrelated jobs. These are acquisition
  counts, not counts of confirmed new-grad opportunities.
- The output is a pilot export, not a production Jobfeed database import.

Tests: `node --test tests/extension/*.test.cjs` from the repository root.

## Live evidence, 2026-09-08

The collector was executed in the authenticated Chrome page via the supported
browser developer runtime. The extension's installed-button flow remains a
separate manual smoke check after reload.

| Source/query | Unique jobs with descriptions | Collector time |
| --- | ---: | ---: |
| Handshake / AI Engineer New Grad | 500 | 33.406 s |
| LinkedIn / Software Engineer New Grad, US, past 24h | 500 | 160.319 s |

Both completed with no request failure. Handshake needed 23 pages because some
jobs repeat across pages; LinkedIn needed 20. One expanded page from each source
was compared to the API description: identical after ignoring whitespace.
This supports on-platform body capture, not an audit of employer-original text
or a guarantee that future runs will avoid rate limits.

Artifacts: `data/jobboard-pilot-20260908/batch-scan-summary.json`,
`handshake-batch-speed-500.json`, `linkedin-batch-speed-500.json`.
The final collector also retains LinkedIn company names from search cards; that
metadata mapping has a unit test and does not change the measured request path.

## Integrated Scan (0.4.0)

Reload this same extension after updating. Runs → Start scan now offers Jobright,
LinkedIn · signed in, and Handshake · signed in. The existing local WebSocket
bridge advertises supported sources, so an older worker cannot accidentally run
Jobright when LinkedIn or Handshake was requested.

Enable `sources.linkedin_extension` and `sources.handshake` in config.toml.
Both search Software Engineer New Grad and AI Engineer New Grad by default.
LinkedIn uses the signed-in US / past-24-hours API, with at most 500 jobs shared
between queries. Handshake first walks Most Relevant to exhaustion, then requests
newest (POST_DATE descending). Bootstrap IDs are checked against persisted JDs
before subsequent scans skip Most Relevant. The newest pass is bounded by
max_jobs (500 by default); a query returning fewer jobs is not padded with duplicates.

The worker sends each page to Runs, closes its own tab on completion/cancellation,
and stops immediately on an API error. Successful preceding pages are still
saved by Scan and the run records the failure. A scan that reaches the bootstrap
observation ceiling is explicitly incomplete, never marked as an exhausted search.
No credentials are saved to Jobfeed or exported. The original pilot remains
available for independent bounded 50/500 validation; it does not import to Jobfeed.

Handshake category mode: set `sources.handshake.search_url` to your Handshake
software-role/full-time/job search URL with `sort=posted_date_desc`. This mode
uses the native category filters and no keyword, starts from the first page,
and bypasses the keyword Most Relevant bootstrap. It collects up to 500 jobs.

## GitHub full-description enrichment (0.7.0)

The original extension now accepts `github-jd` targets from **all** GitHub list
rows still missing a description after existing native collectors finish.
It preserves each original job ID and URL and sends bodies back through Scan's
existing persistence path. No company-specific target filter is applied.

`github-jd-hosts.json` records the 400 destination hosts in the current 2,379-row
GitHub input. The manifest also includes exact destination origins added from
subsequent missing-permission reports; the inventory is a historical snapshot.
Reload the extension after manifest permission changes. Unexpected redirects
to unrelated sites must be investigated before granting access.
A newly encountered host without permission produces an explicit per-job error.
Reload this original unpacked extension once to activate the updated worker and
host permissions.

The scan owns at most four real background tabs across all permitted hosts.
Each worker loads one target, waits for page completion, then extracts JobPosting
structured data or description containers (up to 30 attempts, 500 ms apart).
The existing Greenhouse iframe fallback is retained. Each result is sent back
immediately; that worker then waits one second before taking the next target.
Failures receive the same cooldown to avoid rapid navigation loops. Workers do
not wait for one another. Cancellation/disconnection closes all owned tabs.
This scan no longer calls the direct HTTP batch helper; its fetch concurrency,
Workable API shortcut, and HTTP-status host-stop behavior do not apply to this
rendered-page path. Missing bodies remain explicit failures and partial successes
are retained. Reload the unpacked extension to activate this scheduler.

Live collector checks on 2026-09-09: Workable 49/49; www.amazon.jobs 28/29
(one HTTP 404); Jobright sample 23/24 direct; one successful rendered/structured
sample each for Citadel, Akuna, Microsoft and Google. These are collector-level
checks in Chrome, not a claim that every host or the reloaded Scan worker has
been verified. Full artifacts are under `data/github-extension-20260909/`.

`github-jd-entities.js` bundles the installed MIT-licensed `entities` package for
HTML entity decoding without TrustedHTML assignment. Rebuild with:
`web-ui/node_modules/.bin/esbuild web-ui/node_modules/entities/lib/esm/index.js --bundle --format=iife --global-name=GitHubJDEntities --minify --outfile=extensions/jobright-source/github-jd-entities.js`
