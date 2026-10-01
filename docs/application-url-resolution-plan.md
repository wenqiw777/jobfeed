# Resolve application links to canonical ATS identities

Status: local implementation, regression verification, and live Equifax route
verification complete in the isolated `codex/application-route-dedup` worktree.
The installed Jobfeed worker still runs the previous version; see live scope below.
No live data changed or shipping performed.

## Outcome and current evidence

Resolve an observed source application link through a company job page to the
specific ATS requisition. Keep each source posting, original JD, and original
application URL; associate proven aliases through the existing real-job resolver.
The resolver must observe an actual Apply destination without an Equifax-only
URL mapping. The same flow covers supported Greenhouse, Lever, Ashby, and Workday
job URLs. An unobservable Equifax destination remains unresolved.

Baseline gaps, verified before implementation:

- `_linkedin_enrich.py` reads the displayed external Apply href, but stops there.
- `jobboard-batch.js` / `jobboard_extension.py` do not carry application URLs into
  source postings. Guest enrichment currently extracts JD and date only.
- `EnrichResult` exposes `apply_url`, but no final identity-evidence URL.
- `observed_identifiers()` already accepts listing, Apply, and identity-evidence
  URLs. SQLite source saves resolve and synchronize canonical identity inside
  their transaction. This existing mechanism remains the merge authority.

## Boundaries and assumptions

- Source-native IDs and URLs remain source identities. A discovered ATS identity
  is additional observed evidence, never a replacement LinkedIn ID.
- Resolve actual redirects and links in the target job's Apply region. Do not
  infer an ATS URL from a title, company name, requisition-looking token, or an
  arbitrary ATS link elsewhere on the page.
- HTTP/HTML is the cheap path; dynamic rendering uses the existing Chrome
  extension bridge. No Playwright or DevTools substitute for that fallback.
- Read pages and links only. Do not fill forms, submit, or invoke arbitrary Apply
  actions. Unsupported JS-only actions remain unresolved until a dedicated,
  read-only observer can identify their destination.
- Supported ATS contracts remain those recognized by the existing identity
  parser. Custom careers pages exposing those ATS links/embedded job URLs are
  supported. A custom ATS with no recognized ID remains a future adapter task.
- Resolution failure does not discard a valid source JD or make a job closed.
  Existing intermediary attribution/evaluation rules continue to apply.
- No new hash fields, calculations, dependencies, or verification.
- Existing checkout changes are outside this plan. No shipping, migration,
  automatic broad historical scan, or live-data repair is included.

## Resolution contract

Input: source posting facts, original observed application URL, run fence, and
optional Chrome extension page reader.

Result: `status` (`resolved`, `unresolved`, `ambiguous`, `blocked`, or `failed`),
optional `ats_url`, ordered observed hops with observation kind (HTTP redirect,
target Apply link, or target embedded job URL), reason, and observation time.
Only a resolved result contributes `identity_evidence_url` to source saving.

1. A supported direct ATS job URL takes the existing identity path without an
   extra request just for recognition.
2. For a wrapper/careers URL, follow at most five observed navigation edges with
   cycle detection, a 20-second HTTP budget, and bounded page sizes. Resolve
   relative URLs against the actual page/base URL.
3. On a job page, select links/embedded URLs owned by the target job's Apply
   region. Reject generic careers boards, recommendation links, and multiple
   distinct requisitions. An iframe alone is not identity proof.
4. Fetch the newly discovered ATS job destination and verify employer and target
   job ownership. When both pages expose requisition IDs, require agreement.
   Use title/JD facts as corroboration; a loose title match is insufficient.
   Missing final-page evidence remains unresolved rather than forcing a merge.
5. If HTTP cannot observe a dynamic page, request a read-only snapshot through
   the existing extension bridge, then apply the same candidate and ownership
   rules. Bound the fallback to 30 seconds; absence of the extension is blocked.
6. Validate each network destination as a public HTTP(S) URL, including redirect
   destinations, before requesting it. Reuse existing request pacing and run
   cancellation/fencing; perform network reads outside database transactions.

Persist the bounded outcome/hop evidence using existing state storage keyed by
source platform and native ID. Associate it with the observed original Apply URL
and run ID. Reuse a successful outcome only when that URL is unchanged; expire
it after seven days. Failed/blocked outcomes retry after 24 hours. A changed
Apply URL bypasses the prior outcome. Within a run, share identical URL work.

## Overlap scanning and identity resolution

User direction: start asynchronous deduplication while scanning continues, rather
than waiting for all sources to finish before beginning route resolution.
Confirmed browser capacity: three additional, dedicated deduplication tabs,
independent of the source scan's tabs and existing enrichment capacity.

- Browser batch callbacks enqueue a bounded saving queue (ten batches). One
  independent save consumer takes those batches while discovery continues.
  Saving need not finish before the browser returns the next batch. On success
  drain accepted source batches before reporting the source complete.
- After each source batch commits successfully, enqueue its source IDs and
  observed Apply URLs. Never resolve an uncommitted source or enqueue a batch
  before its durable write receipt exists.
- Use a bounded queue with three route workers and two concurrent HTTP reads.
  Queue saturation may
  apply backpressure after source saving; do not spawn an unbounded task per job.
  HTTP work overlaps source discovery. The existing extension supports separate
  source lanes, and `github-jd-worker.js` already demonstrates independent
  inactive tabs. Add one dedicated application-resolution lane in both bridge
  and extension, with one coordinated queue and up to three inactive worker tabs
  total for deduplication. This is additional capacity, not a three-tab combined
  limit for scan plus deduplication, and does not borrow the GitHub/TikTok lane's
  slots. Do not launch a separate three-tab pool per batch or per request.
  Preserve source-scan concurrency. Isolate tab IDs/task IDs and close only the
  deduplication-owned tabs on its completion/cancellation. Keep per-host pacing;
  measure throughput, rate limits, browser CPU/memory, and source-scan latency.
- Route fetches happen outside database transactions. Source saves and identity
  updates share the existing run's writer serialization/fence; workers do not
  hold write locks while waiting for websites. Reuse the pipeline writer lock
  when present, and provide one run-local writer lock for the ordinary path.
- Identity writes reread the source by its source ID inside the write boundary,
  confirm its Apply URL still matches the work item, and resolve against current
  facts. Do not resave a stale captured posting over a newer JD/date. Resolve
  current parent IDs at commit time; parents may already have merged.
- Record durable route outcomes only after evidence application commits. Failed
  or uncertain routes produce their ordinary outcome without losing scan data.
  All database evidence/state writes use the same fence and writer discipline.
- Workers belong to the scan's structured task lifetime. Ordinary route errors
  are contained per item; cancellation or lease loss stops workers and prevents
  subsequent writes. A resumed run can reuse committed evidence or retry missing
  outcomes; no detached tasks survive a completed/stopped scan.
- Deduplication is global across batches. Same-URL pending work shares its fetch
  result, but applies evidence separately to every saved source ID. Different
  wrapper URLs may resolve concurrently to the same ATS identity: each commit
  queries the current `real_job_identifiers` ownership, never a batch-local
  snapshot. Its existing `(provider, scope, native_id)` unique constraint plus
  transactional resolution joins the later source to the existing parent when
  compatible. SQLite uses short serialized writes; PostgreSQL retains its row
  locking, stale-parent detection, and bounded transaction retry. Source rows
  stay separate, and unresolved/pending records may temporarily have distinct
  parents. Different requisitions or conflicting facts follow existing review
  policy; similar JD text alone does not establish a shared ATS identity.
- When discovery ends, drain the bounded queue and await workers before declaring
  the scan complete or starting canonical evaluation. Each route has its own
  time budget; unresolved jobs retain existing eligibility rules. This prevents
  evaluation racing still-pending identity writes, but does not promise dedupe of
  unobservable links or unsupported ATS contracts.
- Measure source collection duration, time spent waiting for queue capacity,
  resolution wall time, end-of-scan drain time, browser-resource wait, and route
  outcomes. Under ideal independent resources, total duration approaches the
  larger of source collection and resolution durations, plus the final tail;
  queue, website, browser, and database contention can reduce this benefit.

## Independently verifiable tasks

### 1. Carry observed application links through active source paths

Result: extension and guest paths preserve an application URL when it is actually
present in their existing response/page; SessionSource continues to preserve it.
Do not add one LinkedIn detail request per job just to find a missing href.
Extend enrichment metadata and its persistence boundary as required, including
the separate guest `EnrichService` path; do not only change `scan.py`.

Evidence: failing then passing fixtures for extension raw payload -> JobPosting,
guest page -> enrichment -> stored posting, and session enrichment -> posting.
Missing/LinkedIn-internal links stay absent. First inspect a live read-only
extension response to establish the actual Apply field contract; do not guess it.

### 2. Resolve bounded application routes independently of the source

Prerequisite: task 1 supplies real observed links.
Result: one injected resolver, with HTTP and Chrome extension readers, returns
the contract above for supported ATS destinations behind careers job pages.
Parsing and network readers remain separate from canonical merge policy.

Evidence: failing then passing cases for direct ATS, HTTP redirects, relative
Apply links, a second intermediate page, target embedded Greenhouse job, dynamic
extension snapshot, tracking parameters, and Workday/Greenhouse/Lever/Ashby.
Negative cases cover recommendations, careers home/list pages, wrong employer,
different requisitions, ambiguous candidates, loops, size/time limits, blocked
pages, unsupported endpoints, and cancellation. No LLM-based URL invention.

### 3. Feed evidence into canonical resolution and durable scan flow

Prerequisite: task 2's result contract.
Result: shared asynchronous identity queue handles committed source batches
while later batches/sources continue scanning, and drains before evaluation.
It also runs for later guest enrichment after application metadata is saved.
Pass a verified final ATS URL through the existing identity-evidence mechanism;
do not overwrite the source URL, JD, or original Apply URL. Preserve scan insert
and update counts and queue receipts; identity updates use fenced writes.
Persist route outcomes even when no merge is possible. Existing canonical input,
requirements/status/evaluation conflict handling remains authoritative.

Evidence: SQLite and PostgreSQL integration checks show two source rows, one
real_job_id, retained original links and JD, and durable ATS identifier/route
evidence. Repeated processing and source arrival order do not create new source
rows or parents. Different requisitions remain distinct. Existing conflicts
remain reviewable; interrupted runs cannot commit stale evidence.
Deterministic concurrency checks prove resolution starts before scanning ends,
scan advances while a website request is blocked, evaluation waits for the final
identity commit, queue size stays bounded, and stopping/lease loss leaves no
worker or late write. A concurrent JD update survives an identity result, and
source/identity writes do not hold a database lock across network waits.
Add cross-batch cases where both resolutions are pending and finish in either
order, two distinct wrapper URLs resolve to one requisition, an ATS source arrives
during resolution, and same-URL shared fetching still links every source ID. Check
one final parent and retained source rows/metadata directly in both databases.
Extension checks prove scan and deduplication can run together, no more than
three deduplication tabs are open across batches, and completion/cancellation
does not close or navigate source-scan/user tabs. The dedicated lane must avoid
per-job lane-busy failures or multiplying the tab limit.

### 4. Verify real routes and make historical coverage measurable

Prerequisite: task 3.
Result: capture a read-only Equifax chain and at least one real Greenhouse or
Lever careers-wrapper chain using the Chrome extension. Rehearse identity writes
only in an isolated test database populated from those observed facts.
Produce a read-only inventory of existing sources with missing Apply metadata,
unrecognized Apply URLs, resolved ATS routes, and confirmed aliases. Report
unknowns separately; missing links alone are not confirmed missed duplicates.

Evidence: observed page/Apply/ATS links and facts, source counts and parent IDs
queried directly in the test DB, and measurable outcome counts from the audit.
Fixtures establish contracts; only these live reads establish real-site coverage.
Actual historical writes require a separate scoped repair deliverable with a
reviewable candidate list. They are not a startup migration.

## Decision points and progress

Delivery: collect links and implement the shared resolver across the
active LinkedIn paths, with supported ATS destinations and read-only extension
fallback. Do not solve this only with company-specific parser entries.
Use scan-overlapping workers with three dedicated browser tabs, serialized short
writes and a final drain, following the user's proposed direction. Deterministic
tests establish concurrency; no real-site speed improvement has been measured.

Return to design if the active LinkedIn API exposes no observed destination, a
site requires action beyond read-only navigation, or an ATS has no supported
identity contract. Record the affected case as unresolved rather than inventing
a route or silently widening the browser actions.

- Completed: current-source and canonical-resolver inspection.
- Completed: defined source metadata, route result, persistence, failure,
  ordering, and acceptance boundaries.
- Confirmed: three additional browser workers dedicated to deduplication;
  source scanning keeps its own tabs and concurrency.
- Implemented: active/guest Apply metadata, supported route readers, three
  dedicated extension tabs, bounded independent saving consumer, canonical
  identity transactions, scan overlap, and enrichment wiring.
- Verified: red/green fixtures, SQLite/PostgreSQL test-database checks, and final
  regression, recovery, cancellation, and quality checks below.
- Verified live: signed-in LinkedIn DOM Apply contract and the Equifax rendered
  Chrome route, using the actual Chrome extension browser provider. The unified
  browser API exposed that provider; the earlier claim that no callable extension
  connection existed was incorrect. No DevTools was used.
- Pending: active Voyager API response-field observation and a live run of the
  installed Jobfeed application-resolution worker; its current capabilities do
  not advertise application-resolution. No broad source scan was started.
- Deferred: historical coverage inventory and any historical repair. Missing
  Apply fields alone are not counted as confirmed missed duplicates.
- Unmeasured: number of historical duplicate jobs affected.

## Implementation findings and verification evidence

- Streaming correction: batch-return -> bounded save consumer -> committed source
  ID -> dedup queue. A blocked first database save does not prevent the second
  browser batch from returning. Two LinkedIn source IDs resolving in either order
  to one Workday requisition retain both original source rows and one parent.
- Durable identity repair: canonical matching now reloads previously committed
  ATS identity evidence; it no longer treats the second LinkedIn ID as a conflict
  solely because the transient evidence URL was not saved on the jobs row.
- Verification freshness: pending/cache sharing includes normalized company,
  title and JD facts. Transactions reject stale positive evidence; a changed JD
  is reverified once, while a changed Apply URL rejects the old result.
- Extension review blockers fixed: recommendations cannot supply the target
  Apply URL, unrelated iframes cannot end hydration early, and delayed tab creation
  keeps its global slot until the late blank tab is closed. Permission, DNR and
  socket waits respect cancellation. Browser snapshots preserve a bounded ~5.5s
  hydration window; no live browser resource measurements were made.
- Recovery review fixes: duplicate source IDs are saved once per run, immutable
  serialized stream manifests retain original dates and batch generation, and
  completed/abruptly interrupted sources replay their original write receipts.
  This also covers the actual extension adapter remapping timestamps. Ordinary
  source errors preserve accepted pending batches; cancellation/lease loss stop
  them, and writer failures propagate their original error.
- Browser time correction: each whole route shares 30 seconds of cumulative
  rendering budget, separate from 20 seconds for HTTP. A rendered page without
  Apply links is not rendered twice on the same navigation edge.
- Final checks: default full Python suite with disposable Redis: 2,832 passed,
  478 deselected (explicit PostgreSQL/live/browser/ML lanes), 8 expected failures.
  Separate disposable PostgreSQL identity suite: 13 passed. Extension suite:
  110 passed. Ruff checks/format checks passed; full mypy passed for 371 source
  files. Production hygiene passed with existing advisory length warnings.
- The first streaming consumer check failed before implementation; later
  regressions reproduced duplicate counts, missing durable ATS proof, stale JD
  sharing, receipt drift and partial-source loss before their respective fixes.
  All reproductions pass now. Tests inspect temporary DB rows and identifiers;
  no production DB or historical job record was modified.
- Equifax public HTTP observation (2026-10-01 UTC): genuine HTTP200 job page
  contains JSON-LD requisition J00179159 and employer Equifax, but zero observed
  Apply links or controls and zero Workday mentions. Its JS adds tracking to
  existing anchors; no Apply-generation endpoint was observed. Reusing this
  document in a reader fixture returns `unresolved/target_apply_link_missing`;
  no reader returns `blocked/chrome_reader_unavailable`. This is not live Chrome
  evidence and does not prove the linked Equifax job resolves end to end.
- Live production DB writes, scan runs, browser reload, push, and deployment were
  not performed.

## Live verification follow-up, 2026-10-01

- Actual signed-in LinkedIn job 4473979895 exposes the native control
  `a[aria-label="Apply on company website"]`. Its observed `/safety/go/` URL
  contains the exact Equifax careers destination. Original selectors and redirect
  decoding missed this SDUI case. Failing then passing tests now cover the
  extension metadata parser, guest parser, and session enrichment path; recommended
  links remain excluded. Both parsers also passed against the actual captured DOM.
- The real Equifax Chrome page exposes `#js-apply-external` in `.job-sidebar`,
  pointing to Workday J00179159 `/apply?source=Applied_LinkedIn`. The same bounded
  public HTTP page still omits this anchor, so browser observation is required.
- Live Workday confirms title Generative AI Engineer and requisition J00179159.
  Following its Apply path into CXS returned HTTP422 because `/apply` is an action,
  not the job detail API path. The actual corresponding job detail CXS endpoint
  returned HTTP200 and confirmed the same title, JD and requisition. Added two
  failing then passing suffix regressions; the resolver strips only the Workday
  action suffix for its detail request and retains the original observed URL.
- Verification ran the new production resolver and asynchronous identity queue
  with captured public Chrome job facts, actual live HTTP/CXS, and a disposable
  SQLite database. The Chrome reader input is a projection of captured JSON-LD,
  header and sidebar, not an installed Jobfeed-worker snapshot. Three source rows
  produced two parents before resolution, one afterwards; all original URLs,
  Apply URLs and JD strings remained unchanged. All three outcomes resolved.
  Direct SQL confirmed the durable Workday J00179159 identifier.
- Evidence: [public verification report](evidence/equifax-application-resolution.json).

## Mini deployment and historical backfill (2026-10-01)

User explicitly authorized Mini deployment and selected all open candidates.
Deploy the reviewed feature branch without merging main; preserve Mini's existing
non-internship filter at base `732306b` and all private/untracked state.

Additional release checks found and fixed Web factory bridge injection and missing
historical Apply coverage. The explicit `POST /api/runs/application-backfill`
uses the production bridge, scan lease/lock, three bounded workers and durable
per-source receipts. Source URL is used only for rows without an observed Apply URL;
original source fields remain intact. No discovery/enrichment/paid evaluation hook
runs. Existing run APIs expose progress and stop. Explicit retry reuses fresh
receipts; interrupted backfills do not automatically restart as ordinary scans.

Test-first follow-up: missing-Apply queue, Web route/manager ownership, LinkedIn
SDUI header, native ID mismatches, recommendation class/ARIA containers, and stale
source-URL proof failed before their fixes. Independent review closed all three
LinkedIn ownership findings and the source-URL transaction finding.

Current evidence: full SQLite/default suite 2,903 passed, 478 deselected, 8 xfailed;
latest follow-up regression/hygiene 72 passed; extension 111 passed; Ruff/format,
mypy 373 source files, frontend generated types and TypeScript passed. The real
captured LinkedIn SDUI DOM yields the exact Equifax company Apply URL. Mini online
backup `artifacts/application-backfill-release-20261001/before.sqlite` passed
`PRAGMA quick_check`; baseline has 153,485 source rows and 147,543 source-linked
parents.

Deployment evidence: GitHub CI run `36897977337` passed changes, quality-gate and
browser-tests. Mini now runs release `0059d70` (extension files version 0.8.4),
normal LaunchAgent PID 51207; DB and Redis health are OK, API remains loopback-only.
Scheduler was paused during deployment and restored. Mini's local Chrome PID
78769 owns the bridge; its loaded extension still lacks application-resolution,
so full browser verification is pending an unpacked-extension reload. The user's
active application tab was preserved.

First backfill phase completed: direct-ATS run
`6e04731e-9862-48a6-b84f-111d96010c27` resolved 5,689 sources, errors=0,
LLM cost=0. Direct SQLite comparison against the online backup found:
153,485 source rows retained, zero changed job fields excluding real_job_id,
zero missing/changed/new source evaluations, 337 sources relinked, and
source-linked parents 147,543 -> 147,241 (302 fewer duplicates). quick_check=ok.
Receipts: Mini artifacts/application-backfill-release-20261001/direct-report.json
and direct-db-verification.json.

The complete open-candidate snapshot contains 90,000 rows. An authorized one-off
continuation process (PID 51310, PPID 1) is waiting for the extension capability,
independently of this Air SSH session. After reload it will run the two actual
Equifax source IDs (487275, 487470), verify that their parent IDs match, and only
then start the full API backfill. It saves run IDs/progress under the same Mini
artifact directory; no recurring schedule is added. It stops if the sample fails,
the full run fails/is stopped, or an API error occurs. Already resolved direct ATS
receipts are reused. Current continuation phase: waiting_for_extension;
Equifax's LinkedIn source is not yet reconciled in production. Do not claim the
full historical backfill or the installed browser worker validated yet.
  No private LinkedIn DOM/profile data is committed. Related regression checks:
  127 Python tests passed; all 111 extension tests passed; Ruff/format/mypy passed.
- This proves the reported Equifax route and canonical merge in new code. It
  does not claim the running production service has loaded the new extension or
  that the dedicated three-tab worker pool has been exercised live after reload.

## Live Mini reload follow-up

The user reloaded extension 0.8.4. The real Mini sample
`0e8e6bd5-cb73-4044-af08-30dc55d7715c` processed both Equifax sources but did
not reconcile them: LinkedIn receipt followed the genuine employer wrapper,
then returned `target_apply_link_missing`. The one-off continuation correctly
stopped before launching the 90,000-source bulk run.

Root cause observed in the actual public DOM: `#js-apply-external` is inside
`div.job-sidebar`, itself inside generic `aside.col-lg-4.sidebar`. The parser
accepted the inner current-job region but rejected the outer aside. Earlier
projection evidence omitted that ancestor, so it did not verify this real layout.

Task: preserve recognized current-job region context while walking outer
containers; still reject recommendations, conflicting job IDs, nav and footer.
Test-first evidence: nested-sidebar positive failed before fix; the four
negative ancestor cases passed. After fix, 48 resolver checks and 18 hygiene
checks passed; Ruff and mypy passed. The actual captured public sidebar inserted
into its original ancestor structure yields the genuine J00179159 Apply URL.
Release acceptance remains the real Mini parent-ID comparison, then full open
backfill launch and direct DB source/evaluation preservation checks.
