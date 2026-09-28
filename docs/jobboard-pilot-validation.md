# Job board pilot — 2026-09-08

## Actual Scan speed test (subsequent verification)

The real Scan backend was invoked with `source=jobright`, using its existing
connected Chrome extension and unchanged configuration (200 jobs, batches of
20, 1-second pacing). Run: `14bb629b-95d6-4571-bc0c-54002ece8d93`.

- Start: 21:51:52.085701 UTC.
- First 20 jobs returned: 21:52:20.768546 UTC (28.683 seconds from start).
- All 200 returned: 21:52:32.891542 UTC (40.806 seconds from start).
- The remaining 180 after the first batch took 12.123 seconds.
- Persisted `source_fetch/jobright` timer: 68.413 seconds, no error. This
  includes the existing employer enrichment and ingestion, not only the API.
- Database counters: 200 fetched, 200 with JD text; 59 marked full and 141
  partial by existing provenance rules; 26 inserted, 174 updated; LLM cost $0.
- Full run finalization was still pending at the last check. The code awaits
  a post-scan priority rebuild, so the source timer is not total UI completion
  latency. The first-batch delay is observed; its cause is not established.

This supersedes the earlier statement that batch speed was unverified. The
raw API field/fulltext coverage question is still separate. Evidence is in
`data/jobboard-pilot-20260908/scan-speed-live.json` and
`scan-speed-events.txt` (all ten batch progress events).

Original page-audit scope: authenticated Chrome, Jobright / LinkedIn / Handshake,
capture validation only. The subsequent user-authorized actual Scan above ran
the existing employer enrichment and inserted/updated job records. No source
implementation changes or job applications were made.

## Verified

- Jobright has a public `script#job-posting` JSON-LD `description`. Five live
  samples contain 2,801–4,065 HTML characters. All 129 job-specific list items
  occur in the corresponding visible responsibilities, qualifications, and
  benefits sections. This is a one-way containment check, not proof that every
  visible detail or the employer's original text is retained.
- All five pages distinguish Required and Preferred, while their JSON-LD
  combines the qualifications into Skills. Preserve the page distinction for
  downstream application preparation. Company overview and historical H1B
  sponsorship can also appear in the description; neither is a role requirement.
- Existing code already reads this description in
  `_speedyapply_routing._fetch_jobright_result`. The authenticated Jobright source
  instead builds a description from five recommendation fields and optionally
  visits employer URLs. These are different existing paths.
- LinkedIn authenticated Wayve detail yielded 2,794 characters including the
  salary-ending paragraph. A DOM “more” control remains; its role locator did
  not resolve, so expansion/completeness is not certified. The AI search detail
  yielded 8,579 characters ending in a screening/privacy paragraph.
- LinkedIn AI Engineer New Grad search visibly confirms Past 24 hours and Most
  recent. Results are broad (1,000+), not 1,000 verified new-grad matches.
- Handshake Salesforce expanded Job description yielded 6,730 characters,
  ending in base-salary exclusions (plus the Less control). Its separate job
  preferences include graduation information, outside the description.
- Handshake searches display 582 Software Engineer New Grad results and 574 AI
  Engineer New Grad results. These include broad/promoted matches and older jobs.
- No JSON-LD was present in the inspected authenticated LinkedIn and Handshake
  detail DOMs. This does not prove their internal APIs lack full descriptions.

## Performance and remaining evidence

The second five-page Jobright comparison took 2.7–3.6 seconds per page. This is
page-navigation time, not the speed of the existing batch API. The earlier
five-page pass took about 11.6 seconds total. Neither extrapolates reliably to
500 jobs or proves freedom from rate limits.

The repository extension calls `/swan/recommend/list/jobs` with authenticated
Chrome credentials and offset/count pagination (default 20). Direct navigation
to that known endpoint in the browser tool failed with `net::ERR_BLOCKED_BY_CLIENT`.
No HTTP response or raw recommendation payload was obtained. Classify this as
a browser-client navigation failure, not demonstrated Jobright anti-bot behavior.
Do not substitute page reading for a claimed batch-API benchmark.

Still required: export a fresh raw recommendation batch through the existing
extension, compare every returned field against the same job's displayed JD,
and measure batch throughput separately from optional employer enrichment.
LinkedIn and Handshake batch/fulltext API capability remains unverified as well.
No 500-job collection is claimed complete.

## Reproduction artifacts

Captures: `data/jobboard-pilot-20260908/`. HTML/text are preserved for downstream
inspection, not summarized by an LLM. `jobright-structured-*.json` contains both
the public JSON-LD and the visible JD sections. `content-audit.json` contains
per-job comparisons and limitations.

Run the offline content comparison:

```sh
python3 scripts/audit_jobboard_pilot.py data/jobboard-pilot-20260908
```

The script can also inventory all string paths and lengths in a supplied raw
export using `--raw-recommendations PATH`. That option has not been run against
a fresh raw API response; the page captures are not represented as API captures.

## LinkedIn / Handshake batch implementation and live test

Implemented the separate extension pilot in `jobboard-batch.js`,
`pilot-worker.js`, `pilot.html`, and `pilot.js`. Original Jobright bridge remains
available. Branch: `codex/jobboard-batch-pilot`; no commit or production pipeline
replacement. Extension version 0.3.0 needs reload after the new files are present.

Acceptance evidence:

- Handshake live GraphQL search returns the job `description` with the list.
  Implemented 25-row pagination and ID deduplication. 500 unique descriptions
  in 23 batches, 33.406 seconds including 1-second batch pacing.
- LinkedIn uses the signed-in Rest.li list and JSON job detail API. 500 unique
  descriptions in 20 batches, 160.319 seconds. No guest requests or per-job
  webpage visits. Earlier 25/100 pilots took 9.867/37.186 seconds.
- Corrected a live HTTP 400 caused by double-encoding Rest.li variables, with a
  failing regression test followed by a passing test. Matched the site's
  normalized Accept media type after an initial response-shape mismatch.
- Actual page comparisons: Handshake 11347404 and LinkedIn 4464693506 match their
  API descriptions exactly when whitespace is ignored. Keep LinkedIn formatting
  attributes because its raw text can concatenate adjacent list items.
- 9 Node tests pass, including pagination/deduplication, failure retention,
  GraphQL failures, source text, company mapping, and extension worker cleanup.
- Benchmark credentials came from already-observed job requests and stayed in
  memory. Collector results whitelist job fields rather than exporting account
  details from the original responses. Temporary page benchmark state removed.

Remaining: the installed extension popup-to-export flow is not certified. The
browser tool explicitly blocked `chrome://extensions/`; user reloaded once before
new files were complete and was asked to reload the completed version. That UI
limitation does not invalidate the real in-page API benchmarks, but they must not
be presented as a verified extension-button benchmark. API timings exclude page
bootstrap and export; the worker additionally records total elapsed time.

Full exports and summary are under `data/jobboard-pilot-20260908/`.
