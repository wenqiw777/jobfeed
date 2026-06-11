# LinkedIn Guest Self-Scraper — Design & Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

- **Date:** 2026-06-10
- **Status:** Design + plan merged into this single doc. Review round 1 fixes: posted-at capture at enrich, dedupe rank transfer, additive task sequencing, `cli/scan.py` token, `_parse_linkedin_qs` not reused. Review round 2 (independent, no blockers) fixes: standalone `enrich-linkedin-guest` command (no group conversion), complete Task 9 excision file list, `cli/scan.py` in Task 8 files, `quality.value` for `record_enrichment`, named pagination helper, test-file targets corrected. Ready to implement.
- **Scope:** LinkedIn source only. Indeed unchanged.

**Goal:** Replace the JobSpy LinkedIn source with a self-written anonymous guest scraper that discovers the full result set (correct pagination, ~1000/query) and enriches JD from the lightweight guest endpoint at a token-bucket-safe rate.

**Architecture:** Two phases within the existing hexagonal layout. A `SimpleSource` (`LinkedInGuestSource`) discovers listings with `jd_text=None` (persisted by the normal scan path). A separate `JobEnricher` adapter + `EnrichService` then fills JD for un-enriched rows, paced to ~1 req/s with adaptive backoff and resumable across runs. No login, no Playwright, no `SessionSource`. JobSpy stays for Indeed.

**Platform tag:** all rows from this anonymous source use `platform="linkedin_guest"` — deliberately distinct from the authenticated Playwright source's `platform="linkedin"` so the two never collide (same reason the old JobSpy source used `linkedin_jobspy`). `enrich_source="linkedin_guest"`.

**Tech Stack:** Python 3.12, async, `httpx` (async client), `beautifulsoup4` (already present via jobspy), `asyncpg` store, Click CLI. Reuses `domain/quality.assess_quality`, `ports/source.EnrichResult`, `store.record_enrichment`.

---

# Part I — Design

## 1. Problem

The current LinkedIn source (`adapters/sources/linkedin_jobspy.py`) is a `SimpleSource`
that delegates to `python-jobspy`. It "抓不满" — it cannot return the full set of
LinkedIn postings for a query. Root cause, confirmed at the code level:

**JobSpy's LinkedIn pagination is buggy.** In
`jobspy/linkedin/__init__.py` it advances the page offset with
`start += len(job_list)` (the *accumulated* count) instead of the page size. The
offset therefore grows quadratically and crosses the endpoint's `start < 1000`
guard after ~14 requests, so **every query is hard-capped at ~137 sparse
results, independent of `results_wanted` and of how many postings actually
exist.** Measured: `results_wanted=150 → 139`, `results_wanted=500 → 137/139`.

This is the *only* real defect. JobSpy's JD fetching itself works fine (it pages
~10 detail fetches then sleeps 3–7s, which keeps it under the rate limit) — but
it fetches JD from the heavy `/jobs/view/{id}` page and is still bounded by the
137 cap.

## 2. Decision (TL;DR)

Replace JobSpy on the LinkedIn path with a small self-written scraper:

1. **Discover** — call the same guest endpoint
   (`/jobs-guest/jobs/api/seeMoreJobPostings/search`) with **correct
   pagination** (`start += page_size`) → reaches the real ~1000-deep result set
   per query.
2. **Enrich** — fetch each JD from the **lightweight guest endpoint**
   (`/jobs-guest/jobs/api/jobPosting/{bare_id}`), not `/jobs/view`.
3. **JobSpy stays for Indeed** (its Indeed scraper uses correct cursor
   pagination and has no equivalent bug).

Everything is anonymous (no login, no Playwright, no cookies). This is distinct
from and does not touch the authenticated Playwright LinkedIn `SessionSource`.

## 3. What we verified (empirical, 2026-06-10 session)

All figures are live measurements against LinkedIn's public guest endpoints.

| Claim | Evidence |
| --- | --- |
| JobSpy LinkedIn caps at ~137/query | `rw=150→139`, `rw=500→137` (multiple runs) + code (`start += len(job_list)`) |
| Self-written correct pagination reaches full depth | **993 unique ids in one pass**, 101 pages, ~3.4 min, 0×429 (`software engineer`/US/24h) |
| Indeed has no such bug | Code: cursor-based (`nextCursor`), 100/page — keep JobSpy |
| Guest JD endpoint returns the **complete** JD | 6/6 ids: `jobPosting` text **byte-identical** to `/jobs/view` (lengths 3632–9097) |
| Guest JD endpoint is ~10× lighter | 34 KB vs 340 KB per fetch; needs **bare numeric id** (the `li-` prefix → 400) |
| Enrich is rate-limited per IP (token bucket) | see §6 — burst ~50 then ~1/s; concurrency does **not** escape it |
| Discover is fast/cheap | ~5–10 ids/s; not the bottleneck |
| Cooldown is IP-bound | a throttled IP recovers; a fresh IP starts clean |
| Datacenter/VPN IPs are unreliable | GSL/F.N.S./Clouvider/Latitude all flaky vs the residential home IP |

### Walked-back claims (recorded so they are not re-litigated)
- ❌ "JobSpy's JD bursting causes 429 → 抓不满." **False** — JobSpy got 100/100 JDs
  cleanly. The only defect is the 137 pagination cap.
- ❌ "Guest endpoint does 35 req/s sustained." **False** — that was the burst.
- ❌ "Concurrency breaks the bottleneck." **False** — concurrency only spends the
  burst faster; sustained rate is unchanged.

## 4. Architecture / fit

Stays within the existing hexagonal layout. No Playwright, no `SessionSource`.

- **Discover** produces `JobPosting` rows with `jd_text=None`
  (`enriched_at=None`, `enrich_source=None`) — the store already accepts
  un-enriched rows.
- **Enrich** fills JD via `store.record_enrichment(...)` (existing
  `StoreOpsMixin` method) with `enrich_source="linkedin_guest"`.
- Hard filter (`domain/filtering.py:apply_hard_filters`) is structural
  (company/location/freshness) and reads no `jd_text`; it and dedupe run as today
  in the evaluate funnel. The ML gate + Stage A need `jd_text`, so enrichment
  must complete before evaluation of a row.

### Components (new — see Part II File Structure for the file split)
- `adapters/sources/_linkedin_guest_parse.py` — pure HTML parsing (cards, JD
  text, posted-at), no network.
- `adapters/sources/_linkedin_guest_http.py` — httpx client, endpoint URL
  builders, search-URL param extraction, transient-retry GET.
- `adapters/sources/linkedin_guest.py` — `LinkedInGuestSource` (discover,
  correct `start += len(cards)` pagination, dedupe by bare numeric id, honours
  `f_TPR`) + `LinkedInGuestEnricher` (guest `jobPosting/{bare_id}` fetch,
  `assess_quality`).
- `ports/enrich.py` — narrow `JobEnricher` Protocol
  (`async def enrich(canonical_id, url) -> EnrichOutcome`). **Not**
  `SessionSource` (no lock/browser to hold across phases).
- `services/enrich.py` — thin pass: select un-enriched `linkedin_guest` rows →
  enrich with the token-bucket-aware scheduler (§6) → `record_enrichment`.
  Idempotent + resumable.
- `cli/enrich.py` — `enrich-linkedin-guest` entry point (a standalone command,
  sibling of the existing `enrich-paste`; `cli/enrich.py` has no command group
  and `enrich-paste` stays untouched).
- Config gains a `proxies`/IP-pool hook (default empty) on the LinkedIn-guest
  source.

## 5. Two-phase flow

```
discover (correct pagination, f_TPR window)   → listings, jd_text=None, persisted
        ↓
(evaluate funnel: hard filter + dedupe run as today)
        ↓
enrich pass (this design): un-enriched linkedin_guest rows → guest JD endpoint
        ↓  record_enrichment, enrich_source="linkedin_guest", posted_at backfill
ML gate + Stage A (need jd_text) proceed
```

Discovery being cheap and the search being keyword-targeted means the discovered
set is already ~SDE; we do not add a title pre-filter (verified: the ML gate is
the authoritative relevance cut and needs the JD anyway; a title-only cut either
duplicates the search or risks false-negatives).

## 6. Enrich rate model (the load-bearing constraint)

LinkedIn rate-limits the guest endpoints per IP as a **token bucket**:

- **Bucket capacity ≈ 50** requests (a one-time burst).
- **Refill ≈ 1 request/second.**
- A request with a token succeeds; an empty bucket → 429/999.

Consequences (all measured):
- **Concurrency does not help.** conc≥2 on `/jobs/view`, and conc=5/10 on the
  guest endpoint, only drain the bucket faster, then collapse. Burst size is
  what varies (≈10 on `/jobs/view`, ≈50 on the guest endpoint), not the
  sustainable rate.
- **Sustained ≈ 1 successful fetch/s per IP.** Pushing 1.5/2/5.7 req/s just adds
  429 waste and nets the same ~1/s of successes, plus heats the IP toward
  cooldown.

**Enricher behaviour:** serial-ish, paced to the refill rate, may spend the
initial burst, **adaptive backoff** the moment 429s appear (the IP is entering
cooldown — slow down or rotate, do not hammer), **resumable** (a cooldown defers
work; persisted listings are never lost). The lighter guest endpoint is chosen
for bandwidth + complete JD, **not** for speed.

**Throughput / scaling:**
- Single IP: ~1/s → 200 JDs ≈ ~3.5 min, 1000 ≈ ~17 min.
- Faster only via **multiple residential IPs**, each its own ~1/s serial stream
  (N IPs ≈ N/s). The `proxies` config hook enables this without code change.
- Default posture: **single IP + short `f_TPR` windows run frequently** (e.g.
  hourly `r3600`) so each batch is tiny and single-IP ~1/s is never a problem.
  No proxy required for normal use.

## 7. Freshness / "up to date"

- "新贴 = within 24h." Use short `f_TPR` windows (`r86400`/`r3600`) — filters at
  the source.
- With a freshness window set, the listing cards' `date_posted` often comes back
  empty; capture the real posted date during **enrich** (the JD fragment carries
  a relative posted-time marker) and backfill the row's `posted_at` when it is
  NULL, so `posted_within_days` keeps working (Tasks 1/4/5/6).
- **Do not** parse a "No longer accepting applications" banner — it is
  JS-rendered and absent from the raw HTML (a 231-day posting was not flagged).
  Rely on `f_TPR` + `posted_within_days` + 404/410 → `closed_at` (Task 5's
  `mark_job_closed`).

## 8. Non-goals / dropped
- No login, no Playwright, no cookies (authenticated path is separate).
- No title/SWE pre-enrich cut (search keywords + ML gate already cover it;
  `is_swe_role` on title-only drops 37% of real SWE).
- No proxy purchase now (hook only). Free proxy lists / Tor / datacenter VPS do
  not work for LinkedIn.
- JobSpy LinkedIn path removed; JobSpy Indeed path untouched.

## 9. Open items → resolutions
1. **Sustainable-rate calibration** on the *actual* production IP (residential):
   treat ~1/s + burst-50 as the conservative default; the enricher's adaptive
   backoff self-tunes regardless. Live smoke = Task 10.
2. Guest `jobPosting` fragment parse across edge layouts (missing description,
   non-English): covered by Task 1 fixtures (absent markup → `""`/`None`) +
   Task 10 live smoke.
3. Store query for un-enriched `linkedin_guest` rows: **resolved** — Task 5
   (`list_unenriched_jobs`).
4. Enrich trigger: **resolved** — standalone `enrich-linkedin-guest` CLI
   command (Task 8), not a scan phase.
5. `proxies` config + per-IP serial-stream scheduler: config hook only
   (Task 7); the pool scheduler is deferred until a proxy pool actually exists
   (default posture needs none, §6).

---

# Part II — Implementation Plan

## File Structure

| File | Responsibility |
| --- | --- |
| `src/jobfeed/adapters/sources/_linkedin_guest_parse.py` | Pure parsing: search cards → fields; detail fragment → JD text + posted-at. No network. |
| `src/jobfeed/adapters/sources/_linkedin_guest_http.py` | Async httpx client (browser UA, proxy hook), endpoint URL builders + search-URL param extraction, transient-retry GET returning `(status, text)`. |
| `src/jobfeed/adapters/sources/linkedin_guest.py` | `LinkedInGuestSource` (SimpleSource, discover) + `LinkedInGuestEnricher` (JobEnricher). |
| `src/jobfeed/ports/enrich.py` | `JobEnricher` Protocol + `EnrichOutcome` (wraps `EnrichResult` + blocked/gone signals). |
| `src/jobfeed/services/enrich.py` | `EnrichService`: select un-enriched rows → paced enrich + adaptive backoff → `record_enrichment`. |
| `src/jobfeed/config_sources.py` | `SourcesLinkedInGuestConfig` added (Task 7); `SourcesLinkedInJobSpyConfig` removed (Task 9). |
| `src/jobfeed/cli/_scan_sources.py` | Register `linkedin-guest` source builder (Task 8); retire `linkedin-jobspy` (Task 9). |
| `src/jobfeed/cli/scan.py` | `--source` choices tuple + help text: drop `linkedin-jobspy` (Task 9). |
| `src/jobfeed/cli/enrich.py` | New standalone `enrich-linkedin-guest` command (sibling of `enrich-paste`) driving `EnrichService`. |
| `src/jobfeed/ports/store_ops.py` + `adapters/store/postgres.py` | `list_unenriched_jobs(platform, limit)` query, new `mark_job_closed`, `record_enrichment` gains optional `posted_at`. |
| `src/jobfeed/domain/dedupe.py` | `_PLATFORM_RANK`: `linkedin_jobspy` slot (3) → `linkedin_guest` (Task 9). |

**Sequencing note:** Tasks 1–8 are purely additive — every commit stays green
while the old JobSpy LinkedIn path keeps working. Task 9 is the single excision
commit that removes that path everywhere at once.

---

## Task 1: Guest HTML parsing (pure, no network)

**Files:**
- Create: `src/jobfeed/adapters/sources/_linkedin_guest_parse.py`
- Test: `tests/unit/test_linkedin_guest_parse.py`

**What to build:**
Three pure functions over HTML strings (BeautifulSoup allowed; no network, no jobfeed imports beyond stdlib/bs4). `parse_search_cards(html) -> list[ParsedCard]` finds every `div.base-search-card`, and for each extracts: bare numeric `job_id` (from the `a.base-card__full-link` href, taking the last `-`-delimited numeric segment of the path), `title` (`h3`), `company` (`h4` link text), `location` (`.job-search-card__location`), `posted_at` (the `time` tag's `datetime` attr → aware-UTC `datetime` or `None`), and `url` (`https://www.linkedin.com/jobs/view/{job_id}`). Cards without a numeric id are skipped. `parse_jd(html) -> str` returns the cleaned text of the `div.show-more-less-html__markup` element (`get_text(" ", strip=True)`), or `""` when absent. `parse_posting_posted_at(html, *, now: datetime) -> datetime | None` reads the posting fragment's relative posted-time marker (the `posted-time-ago__text` element, e.g. `"2 weeks ago"`) and converts it to an aware-UTC datetime by subtracting the parsed delta from the injected `now` (minutes/hours/days; weeks ×7 days; months ×30 days); returns `None` when the marker is absent or the text is unparseable. This is the enrich-time date capture required by design §7 (with `f_TPR` set, search cards may omit the date). `ParsedCard` is a frozen dataclass.

**Acceptance criteria:**
- [ ] `parse_search_cards` returns one `ParsedCard` per valid card; a 3-card fixture yields 3 with correct id/title/company/location/url.
- [ ] A card whose href has a non-numeric trailing segment is skipped (not returned).
- [ ] `job_id` is bare digits only (no `li-` prefix); `url` is `.../jobs/view/{job_id}`.
- [ ] `posted_at` parses `<time datetime="2026-06-09">` to an aware-UTC datetime; missing/blank `time` → `None`.
- [ ] `parse_jd` returns the markup text for a fixture containing `show-more-less-html__markup` (len > 0) and `""` for HTML without it.
- [ ] `parse_posting_posted_at` maps `"3 days ago"` → `now - 3 days` and `"2 weeks ago"` → `now - 14 days` (aware UTC, deterministic via injected `now`); HTML without the marker or with unparseable text → `None`.
- [ ] All tests pass, committed.

---

## Task 2: Guest HTTP client + endpoints

**Files:**
- Create: `src/jobfeed/adapters/sources/_linkedin_guest_http.py`
- Test: `tests/unit/test_linkedin_guest_http.py`

**What to build:**
An async helper module owning all HTTP concerns. `create_client(proxies: str | None, timeout: float) -> httpx.AsyncClient` builds an `httpx.AsyncClient` with a real-Chrome `User-Agent` header, `follow_redirects=True`, and the `proxy=` set when `proxies` is non-empty (the proxy hook). Two URL builders: `search_url(keywords, location, f_tpr, start) -> str` for `/jobs-guest/jobs/api/seeMoreJobPostings/search`, and `posting_url(job_id) -> str` for `/jobs-guest/jobs/api/jobPosting/{job_id}` (bare id). A search-URL param extractor `parse_search_params(url) -> SearchParams` (frozen dataclass: `keywords: str | None`, `location: str | None`, `f_tpr: str | None`) pulls the **raw** `keywords`/`location`/`f_TPR` query values with stdlib `parse_qs` — raw passthrough, `f_TPR` stays the `r<seconds>` string so it can be fed back to `search_url` verbatim. Deliberately NOT a reuse of `_jobspy_url._parse_linkedin_qs`: that maps `f_TPR` → JobSpy `hours_old` (the wrong shape here) and is deleted in Task 9. `async fetch(client, url, *, retries=2) -> GuestResponse` performs the GET, retries only on transport errors / 5xx with a short backoff, and returns a `GuestResponse(status: int, text: str)` (frozen dataclass) — it never raises for HTTP status, so callers classify 200/429/999/4xx themselves.

**Acceptance criteria:**
- [ ] `create_client(None, ...)` sets a Chrome-like `User-Agent` (not `jobfeed/1.0`) and no proxy; `create_client("http://u:p@h:1", ...)` passes that proxy to httpx.
- [ ] `search_url(...)` includes `keywords`, `location`, `f_TPR`, `start` query params; `posting_url("123")` ends with `/jobPosting/123`.
- [ ] `parse_search_params` extracts raw values from a pasted LinkedIn search URL (`f_TPR` stays `"r86400"`, not hours); absent params → `None` fields.
- [ ] `fetch` returns `GuestResponse(status=200, text=...)` on success (mock transport) without raising.
- [ ] `fetch` retries on a 503 then succeeds on 200 (mock transport returns 503 then 200); a persistent transport error / 5xx after `retries` returns the sentinel `GuestResponse(status=0, text="")`, never an unhandled raise to the caller.
- [ ] `fetch` does NOT retry a 429 (returns it as-is for the caller to handle).
- [ ] All tests pass, committed.

---

## Task 3: Discover source (correct pagination)

**Files:**
- Create/extend: `src/jobfeed/adapters/sources/linkedin_guest.py` (the `LinkedInGuestSource` class)
- Test: `tests/unit/test_linkedin_guest_source.py`

**What to build:**
`LinkedInGuestSource` implements `SimpleSource` (`ports/source.py`). `fetch_jobs(config)` iterates every configured search URL; for each it extracts `keywords`/`location`/`f_TPR` via `parse_search_params` (Task 2) — a configured URL with no `keywords` is skipped with a logged warning, never fatal — and paginates the list endpoint with **correct** pagination: `start` begins at 0 and advances by the number of cards returned on the page (`start += len(cards)`), looping while cards are non-empty, `start < 1000`, and total unique < `max_jobs`. Dedupe by bare `job_id` across pages. A 429/empty/non-2xx page ends that URL's pagination (return what was collected so far — never abort the whole scan). Each `ParsedCard` becomes a `JobPosting` with `platform="linkedin_guest"`, `jd_text=None`, `enriched_at=None`, `enrich_source=None`, `posted_at` from the card, `discovered_at=now`. The HTTP fetcher is injected (constructor takes a fetch callable) so tests run without network. A per-page sleep (`pacing_s`, default ~1.0) is applied between page fetches via an injected async-sleep so the list endpoint is not hammered (the list endpoint shares the per-IP token bucket). Structure note: do NOT inline the per-URL loop and the per-page pagination loop together in `fetch_jobs` — extract pagination into a named helper (e.g. `_paginate_url`, O(pages), ≤100 pages per URL given the `start < 1000` guard) per the nested-loop rule in `docs/engineering-standards.md`.

**Acceptance criteria:**
- [ ] Given a fake fetcher returning 3 pages of 10 distinct cards then an empty page, `fetch_jobs` returns 30 `JobPosting`s, all `platform="linkedin_guest"`, `jd_text=None`.
- [ ] `start` advances 0 → 10 → 20 → 30 (asserted via the fake fetcher recording requested `start` values) — i.e. `start += len(cards)`, NOT the JobSpy `+= len(accumulated)` bug.
- [ ] Duplicate ids appearing across pages are collapsed (union by id); count reflects unique.
- [ ] Pagination stops at `max_jobs` and at `start >= 1000` and on the first empty page; a mid-stream 429 page ends that URL gracefully and returns the postings gathered so far.
- [ ] A configured search URL without `keywords` is skipped (logged warning); remaining URLs are still processed.
- [ ] The injected sleep is called between page fetches (pacing respected); no sleep needed in tests' fast path (injected as a no-op recorder).
- [ ] All tests pass, committed.

---

## Task 4: JD enricher (port + adapter)

**Files:**
- Create: `src/jobfeed/ports/enrich.py`
- Extend: `src/jobfeed/adapters/sources/linkedin_guest.py` (the `LinkedInGuestEnricher` class)
- Test: `tests/unit/test_linkedin_guest_enricher.py`

**What to build:**
In `ports/enrich.py` define a `JobEnricher` Protocol with `async def enrich(self, *, canonical_id: str, url: str) -> EnrichOutcome` and a frozen `EnrichOutcome` dataclass holding `result: EnrichResult | None`, `is_blocked: bool` (True when the IP was rate-limited — caller should back off), `is_gone: bool` (True on a definitive 404/410 — the posting is removed, caller should mark it closed), and `error: str | None`. Reuse `EnrichResult` from `ports/source.py` (it already carries `enrich_source` and `posted_at`). `LinkedInGuestEnricher` implements it: it derives the bare id from `canonical_id`, GETs `posting_url(bare_id)` via the injected fetch callable, and classifies: 200 with parseable JD (`parse_jd` len > a `MIN_JD_CHARS` threshold, e.g. 200) → `EnrichOutcome(result=EnrichResult(jd_text=..., quality=assess_quality(jd_text), enrich_source="linkedin_guest", posted_at=parse_posting_posted_at(html, now=now())))` where `now` is an injected callable for determinism; 429/999 → `EnrichOutcome(is_blocked=True)`; 404/410 → `EnrichOutcome(is_gone=True)`; 200 without usable JD or any other status → `EnrichOutcome(error=...)` with `result=None`.

**Acceptance criteria:**
- [ ] 200 + a fixture JD (> threshold) → `EnrichOutcome.result.jd_text` set, quality from `assess_quality`, `enrich_source="linkedin_guest"`, `is_blocked=False`, `error=None`.
- [ ] A fixture fragment carrying `"2 weeks ago"` yields `result.posted_at == now - 14 days`; a fragment without the marker yields `result.posted_at is None` (JD still enriched).
- [ ] 429 (and 999) → `is_blocked=True`, `result=None`.
- [ ] 404 (and 410) → `is_gone=True`, `result=None`, `is_blocked=False`.
- [ ] 200 with empty/short JD → `result=None`, `error` set, `is_blocked=False`, `is_gone=False`.
- [ ] The bare numeric id is used in the request URL (no `li-` prefix), asserted via the fake fetcher.
- [ ] All tests pass, committed.

---

## Task 5: Store: un-enriched query, `mark_job_closed`, `posted_at` backfill

**Files:**
- Modify: `src/jobfeed/ports/store_ops.py` (add to `StoreOpsMixin`)
- Modify: `src/jobfeed/adapters/store/postgres.py`
- Test: `tests/store/test_list_unenriched.py` (marker: `postgres`)

**What to build:**
Add `async def list_unenriched_jobs(self, *, platform: str, limit: int) -> list[UnenrichedJob]` to `StoreOpsMixin` and implement it in `PostgresStore`. It returns rows where `platform = $1 AND jd_text IS NULL AND closed_at IS NULL`, newest `discovered_at` first, capped at `limit`, each as a frozen `UnenrichedJob(job_id, canonical_id, url)`. Define `UnenrichedJob` alongside the existing source DTOs (e.g. in `ports/source.py` or a small types module — match where similar read DTOs live). Add `async def mark_job_closed(self, *, job_id: str, closed_at: datetime) -> None` for `is_gone` handling (Task 6) — verified against the codebase: no single-row closed setter exists today (SpeedyApply stamps `closed_at` through the scan upsert path via `RouteResult.closed_at`, and `mark_stale_jobs_closed` is bulk window-based), so this is a new method. Finally, extend `record_enrichment` with an optional keyword `posted_at: datetime | None = None`: when provided and the row's `posted_at` is NULL, the Postgres implementation fills it (COALESCE semantics — an exact card-derived date is never overwritten by an approximate relative-text date); when omitted or `None`, the column is untouched, so existing callers are unaffected.

**Acceptance criteria:**
- [ ] Returns only rows with `jd_text IS NULL` for the given platform; rows with JD or with `closed_at` set are excluded.
- [ ] Respects `limit` and orders newest-first by `discovered_at`.
- [ ] Returns `[]` when none match.
- [ ] `mark_job_closed` sets `closed_at` for a job id; a `list_unenriched_jobs` call no longer returns that row afterward.
- [ ] `record_enrichment(..., posted_at=X)` fills a NULL `posted_at` and leaves a non-NULL `posted_at` unchanged; calls without the kwarg leave the column untouched (existing callers unaffected).
- [ ] Tests run under the `postgres` marker against a seeded schema; pass.
- [ ] Committed.

---

## Task 6: Enrich service (paced + adaptive backoff + resumable)

**Files:**
- Create: `src/jobfeed/services/enrich.py`
- Test: `tests/unit/test_enrich_service.py`

**What to build:**
`EnrichService` depends only on ports: a `JobEnricher`, the store (`StoreOpsMixin` subset: `list_unenriched_jobs`, `record_enrichment`, `mark_job_closed`), a logger, and an injected async-sleep. `async run(platform, batch_limit)`: loads up to `batch_limit` un-enriched rows, then enriches them **sequentially**, sleeping `min_interval_s` (default 1.0) between requests (token-bucket refill ≈ 1/s). On each `EnrichOutcome`: if `result` present → `record_enrichment(job_id=..., jd_text=..., jd_quality=result.quality.value, enriched_at=now, enrich_source="linkedin_guest", posted_at=result.posted_at)` and reset the backoff counter (`assess_quality` returns a `QualityBand` StrEnum; `record_enrichment` takes `jd_quality: str` and every existing caller stores `.value` — match that); if `is_gone` → `mark_job_closed` and continue; if `is_blocked` → exponential backoff sleep (`base_backoff_s` × 2^n, capped at `max_backoff_s`) and **re-queue that row** (do not consume it); after `max_consecutive_blocks` (default 3) blocks in a row, **stop the pass** (the IP is in cooldown; remaining rows resume next run). Errors (non-blocked, non-gone) are logged and the row is skipped (left un-enriched for a future pass). Returns a summary (`enriched`, `closed`, `blocked`, `skipped`, `stopped_early`).

**Acceptance criteria:**
- [ ] With a fake store returning 3 un-enriched rows and an enricher that always succeeds, all 3 are `record_enrichment`-ed with `enrich_source="linkedin_guest"`; summary `enriched=3`.
- [ ] `EnrichOutcome.result.posted_at` is forwarded to `record_enrichment`'s `posted_at` kwarg (asserted via the fake store).
- [ ] Between successful enrichments the injected sleep is called with `min_interval_s` (pacing asserted).
- [ ] When the enricher returns `is_blocked` for a row, the service backs off (sleep called with increasing values) and does NOT `record_enrichment` that row; the row remains un-enriched.
- [ ] After `max_consecutive_blocks` consecutive blocks the pass stops early (`stopped_early=True`) and remaining rows are untouched (resumable).
- [ ] A success after a block resets the consecutive-block counter (does not stop).
- [ ] An `is_gone` outcome calls `mark_job_closed` for that row and continues; summary `closed` counts it; `record_enrichment` is NOT called for it.
- [ ] An enricher `error` (not blocked, not gone) skips the row and continues; summary `skipped` counts it.
- [ ] All tests pass, committed.

---

## Task 7: Config (additive)

**Files:**
- Modify: `src/jobfeed/config_sources.py`
- Modify: `src/jobfeed/config.py` (only if source configs are re-exported/wired there)
- Test: `tests/unit/test_config_sources.py`

**What to build:**
Add `SourcesLinkedInGuestConfig` (Pydantic): `enabled: bool = False`, `search_urls: list[str] = []`, `max_jobs: int = 1000 (ge=1)`, `pacing_s: float = 1.0 (gt=0)` (between list pages and between enrich requests), `enrich_batch_limit: int = 500 (ge=1)`, `proxies: str | None = None`, `timeout_s: float = 15.0 (gt=0)`. A validator: when `enabled` is True, `search_urls` must be non-empty with no blank entries (mirror the existing `_JobSpySourceConfig` validator). Add the `linkedin_guest: SourcesLinkedInGuestConfig` field **alongside** the existing `linkedin_jobspy` field. Do NOT remove `SourcesLinkedInJobSpyConfig` here — `cli/_scan_sources.py` still references `settings.sources.linkedin_jobspy` until Task 9, and deleting the field now would break the build mid-plan. All `linkedin_jobspy` removal happens in Task 9's single excision commit.

**Acceptance criteria:**
- [ ] `SourcesLinkedInGuestConfig()` defaults: `enabled=False`, `max_jobs=1000`, `pacing_s=1.0`, `proxies=None`.
- [ ] Enabled with empty/blank `search_urls` raises a validation error; enabled with a valid URL passes.
- [ ] The sources config exposes `linkedin_guest`; `linkedin_jobspy` is still present and untouched (its removal is Task 9).
- [ ] `ats` default `enabled=True` and other scraper sources default `enabled=False` are unchanged (regression guard).
- [ ] All tests pass, committed.

---

## Task 8: Wiring (source registration + CLI, additive)

**Files:**
- Modify: `src/jobfeed/cli/_scan_sources.py`
- Modify: `src/jobfeed/cli/scan.py` (add `linkedin-guest` to the `--source` choices tuple + help text)
- Modify: `src/jobfeed/cli/enrich.py`
- Modify: `src/jobfeed/cli/__init__.py` (register the new command; further DI wiring only if needed)
- Test: extend `tests/unit/test_cli_enrich.py` and `tests/e2e/test_cli_scan_sources.py` (the existing source-registration coverage lives in the e2e file; there is no `tests/unit/test_scan_sources.py` today)

**What to build:**
In `_scan_sources.py`: add a `_build_linkedin_guest` builder that constructs `LinkedInGuestSource` from `settings.sources.linkedin_guest` (guarded by `_require_enabled`), register it under CLI token `linkedin-guest` in `_BUILDERS` and `_REAL_SOURCES`, and map `"linkedin-guest" -> "linkedin_guest"` in `_CONFIG_FIELDS`. Add the token to the `--source` choices/help in `cli/scan.py`. Leave all `linkedin-jobspy` entries in place — Task 9 removes them. In `cli/enrich.py`: add a standalone `enrich-linkedin-guest` Click command registered on the root group in `cli/__init__.py`, as a sibling of the existing `enrich-paste` command — `cli/enrich.py` has no command group, and converting `enrich-paste` into a group would break its invocation path and tests, so do NOT restructure it. The command builds `LinkedInGuestEnricher` + `EnrichService` (from `settings.sources.linkedin_guest` for pacing/proxies) and runs the pass over `platform="linkedin_guest"`, printing the summary. Reuse the existing async-bridge pattern in the CLI.

**Acceptance criteria:**
- [ ] `scan --source linkedin-guest` resolves to `LinkedInGuestSource` (builder registered, config field mapped, token in `cli/scan.py` choices).
- [ ] `enrich-linkedin-guest` constructs the enricher + service and calls `EnrichService.run(platform="linkedin_guest", ...)`; the summary is printed (assert via a stubbed service).
- [ ] `enrich-paste` still works unchanged (no group conversion).
- [ ] Disabled `linkedin_guest` config makes `scan --source linkedin-guest` fail with the standard "not enabled" message.
- [ ] `linkedin-jobspy` still works in this commit (removal is Task 9).
- [ ] All tests pass, committed.

---

## Task 9: Remove the JobSpy LinkedIn path (single excision commit)

**Files:**
- Delete: `src/jobfeed/adapters/sources/linkedin_jobspy.py`
- Modify: `src/jobfeed/adapters/sources/_jobspy.py` (remove the `linkedin_fetch_description` branch)
- Modify: `src/jobfeed/adapters/sources/_jobspy_url.py` (remove the `site_name == "linkedin"` branch, `_parse_linkedin_qs`, `_tpr_to_hours`, and the LinkedIn key map in the module docstring — the module becomes Indeed-only)
- Modify: `src/jobfeed/config_sources.py` (remove `SourcesLinkedInJobSpyConfig` and the `linkedin_jobspy` field)
- Modify: `src/jobfeed/config.py` (it imports `SourcesLinkedInJobSpyConfig` and re-exports it in `__all__` — both references go)
- Modify: `src/jobfeed/cli/_scan_sources.py` (drop the `LinkedInJobSpySource` import, `_build_linkedin_jobspy`, and the `_BUILDERS`/`_CONFIG_FIELDS`/`_REAL_SOURCES` entries)
- Modify: `src/jobfeed/cli/scan.py` (drop `linkedin-jobspy` from the `--source` choices tuple and the help text)
- Modify: `src/jobfeed/domain/dedupe.py` (`_PLATFORM_RANK`: replace `"linkedin_jobspy": 3` with `"linkedin_guest": 3`; update the priority diagram in the module docstring)
- Modify: `src/jobfeed/adapters/sources/_jobspy_process.py` (docstring still names `linkedin_jobspy` as a caller)
- Delete/Modify tests — complete list; all of these reference `linkedin_jobspy`/`LinkedInJobSpySource` today and would break `make quality` or the grep-clean criterion if missed:
  - `tests/unit/test_jobspy_adapter.py` (drop LinkedIn-JobSpy cases)
  - `tests/contract/test_source_dto_contract.py` (drop/retarget the `jobspy_inline` LinkedIn-frame assertions)
  - `tests/unit/test_config.py` (imports the config class and asserts its defaults)
  - `tests/unit/test_dedupe.py` (`test_source_priority_full_ladder_order` hard-codes `linkedin_jobspy` between `linkedin` and `indeed` — retarget to `linkedin_guest`)
  - `tests/e2e/test_cli_scan_sources.py` (`test_scan_linkedin_jobspy_runs_source`, config writing, skip assertions — retarget to `linkedin-guest`)
  - `tests/integration/test_phase4_scan_chain.py` (imports `LinkedInJobSpySource`; the greenhouse-vs-`linkedin_jobspy` dedupe assertion retargets to `linkedin_guest`)
  - `tests/live/test_phase4_live_smoke.py` (drop `test_linkedin_jobspy_live`)
- Modify: any docs referencing `linkedin_jobspy` as a source.

**What to build:**
Excise the LinkedIn-via-JobSpy path in one commit. The shared `_jobspy.py` / `_jobspy_process.py` stay (Indeed depends on them); `_jobspy_url.py` keeps only its Indeed mapping. The dedupe platform rank that `linkedin_jobspy` held (3 — between the authenticated `linkedin` and `indeed`) transfers to `linkedin_guest`; without this, the new platform falls to `_UNKNOWN_PLATFORM_RANK = 99` and loses every cross-source tie-break, even against `indeed`. Indeed JobSpy behavior must be unchanged.

**Acceptance criteria:**
- [ ] `linkedin_jobspy.py` and `SourcesLinkedInJobSpyConfig` are gone; no remaining references to either (grep clean).
- [ ] `linkedin-jobspy` is no longer a valid `--source` token: gone from `cli/scan.py` choices/help and from `_REAL_SOURCES`/`_BUILDERS`/`_CONFIG_FIELDS` in `_scan_sources.py`.
- [ ] `_jobspy.py` no longer sets `linkedin_fetch_description`; `_jobspy_url.py` has no LinkedIn branch/helpers left; Indeed scrape path and its tests are untouched and still pass.
- [ ] `domain/dedupe.py` ranks `linkedin_guest` at 3 (between `linkedin` and `indeed`); a dedupe test shows a same-quality `linkedin_guest`-vs-`indeed` twin resolving to `linkedin_guest`.
- [ ] Tests asserting old `linkedin_jobspy` / `jobspy_inline`-LinkedIn behavior are removed or retargeted; `make quality` is green.
- [ ] `repeat` default stays `1` for the Indeed JobSpy config (no behavior change — the repeat=N union workaround was a LinkedIn mitigation and is obsolete with correct pagination).
- [ ] Committed.

---

## Task 10: Live smoke test (opt-in, not in `make quality`)

**Files:**
- Create: `tests/live/test_linkedin_guest_live.py` (marker: `live`)

**What to build:**
A `@pytest.mark.live` test (excluded from default `addopts`, like the existing live tests) that, against the real endpoints, asserts the two mechanisms proven in the design: (1) `LinkedInGuestSource` discover for a short `f_TPR` window returns > 137 unique ids for a broad term (proving the pagination cap is beaten), and (2) `LinkedInGuestEnricher` returns a non-empty JD for one discovered id. Keep it small (one short-window query, one enrich) and tolerant of transient blocks (skip with a clear message on 429, do not hard-fail CI — it is opt-in only).

**Acceptance criteria:**
- [ ] Marked `live`; excluded from `make quality` (default addopts).
- [ ] When run with `pytest -m live`, discover returns > 137 unique ids (or skips clearly if the IP is rate-limited).
- [ ] Enrich of one id returns `EnrichResult` with non-empty `jd_text`.
- [ ] Committed.
