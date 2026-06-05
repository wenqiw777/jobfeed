# Phase 4: Source Expansion (SpeedyApply + JobSpy + LinkedIn Playwright) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring every remaining job source into the rewritten jobfeed — SpeedyApply (GitHub markdown + multi-vendor JD routing), Indeed & LinkedIn via JobSpy, and LinkedIn via Playwright — teaching `ScanService` the two-phase `SessionSource` lifecycle it doesn't yet drive, and ship a reusable twin/dedupe primitive so the same real job scraped from N sources is scored once.

**Architecture:** Same hexagonal structure from Phases 0–3. Three new `SimpleSource` adapters (SpeedyApply, Indeed JobSpy, LinkedIn JobSpy) plug into the existing `ScanService.run` path unchanged. One new `SessionSource` adapter (LinkedIn Playwright) requires extending `ScanService` to orchestrate `discover() → enrich_session() → per-posting enrich()`. A new `domain/dedupe.py` pure function clusters twins by the already-persisted `(company_norm, title_norm)` keys and picks a representative. No new ports — `SimpleSource`, `SessionSource`, and `EnrichSession` protocols already exist in `ports/source.py`.

**Tech Stack additions:** `python-jobspy` (Indeed/LinkedIn scraping, lazy-imported), `pandas` (transitively via JobSpy; isolated behind `_jobspy.py`), `playwright` (async API for LinkedIn). `beautifulsoup4`, `httpx`, `respx` already present from Phase 2.

**Spec reference (in the REWRITE repo, where Phase 4 is implemented):** `/Users/wenqiwang/wwq/jobfeed/docs/specs/2026-05-20-jobfeed-rewrite-design.md` — §4 "Dedupe / Twin Semantics" subsection (heading at spec line 299, under §4 Domain Models), §8 (JobSource Abstraction: two-tier protocol, LinkedIn behavioral contract, JobSpy integration, five source types). NOTE: the spec says only that a representative is "selected" — it does NOT define the selection rule; Decision 8 below authors that new rule. (This plan file lives in the legacy repo under `docs/superpowers/specs/`, but ALL implementation paths and the spec above are in the rewrite repo `/Users/wenqiwang/wwq/jobfeed`; bare File Map paths are rooted there.)

**Legacy behavioral reference (READ THE CODE, NOT THE DOCSTRINGS — they have drifted):**
- `/Users/wenqiwang/wwq/job-apply/src/jobfeed/sources/linkedin.py` — discover (scroll-harvest + inline card-click JD at `:621-647`, multi-URL with group budgets at `:1239`, pagination `&start=N` at `:356`), enrich_session (cross-process PID lock `:1458`/`:171`), enrich Tier 1/Tier 2 (`:1575`/`:1617`), auth (`open_login_browser :322`), anti-bot constants (`:89-129`).
- `/Users/wenqiwang/wwq/job-apply/src/jobfeed/sources/speedyapply.py` — markdown parse + apply-URL host routing to GH/Ashby/Lever/SmartRecruiters/iCIMS/Workday with per-slug cache.
- `/Users/wenqiwang/wwq/job-apply/src/jobfeed/sources/indeed.py` + `_jobspy_patches.py` — JobSpy call shape, DataFrame→posting, Indeed `dateOnIndeed` date patch.
- `/Users/wenqiwang/wwq/job-apply/src/jobfeed/main.py:44` (`_enrich_priority`), `:419` (enrich-queue sort).

**Plan path:** `docs/superpowers/plans/2026-05-29-jobfeed-rewrite-phase4-source-expansion.md`

**⚠️ Governance prerequisite (4b only — amendment already DRAFTED, pending commit):** The rewrite repo's `CLAUDE.md` "Constraints" originally stated **"No browser automation, Temporal, or frontend code"** and **"No reads/writes to `~/.jobfeed/`"**. As of 2026-05-29 the **working-tree `CLAUDE.md` ALREADY contains** the "Phase 4 amendment, approved 2026-05-29" permitting Playwright for the LinkedIn SessionSource (`adapters/sources/linkedin.py`) + its `cli/login.py`, with the cookie profile + lock under `~/.cache/jobfeed/` (never `~/.jobfeed/`), and the Phase 4 plan is in the enumerated phase-plan source-of-truth list. **It is UNCOMMITTED (`git status` shows `M CLAUDE.md`); the only remaining action is to COMMIT it** — otherwise an agentic executor on a clean checkout reads the old HEAD constraint and refuses Task 5. This is a **Phase 4b prerequisite**; all of Phase 4a (no browser, no `~/.jobfeed`) is unaffected and may proceed now. (The `~/.jobfeed/` ban itself needed no amendment — the cookie profile + lock relocate to `~/.cache/jobfeed/`, see Decision 4b.)

**Prerequisite:** Phase 2 (ATS source + `_http.py` + `_ats_*` vendor adapters) and Phase 3 (real LLM) substantially complete. Required: `ScanService.run`, `SimpleSource`/`SessionSource`/`EnrichSession` protocols in `ports/source.py`, PostgresStore `save_job` (already persists `company_norm`/`title_norm`/`location_norm`), `_ats_greenhouse`/`_ats_ashby`/`_ats_lever` `fetch_jobs()` functions.

**New variables in this phase:** 3 — (1) JobSpy/pandas/tls-client outbound scraping, (2) Playwright browser automation with a persistent auth profile, (3) the two-phase SessionSource orchestration in ScanService. This is deliberately the largest phase; the risk-ordered task sequence (below) isolates each new variable. (Plus one orthogonal, low-risk piece — the `openai-compat` LLM API backend, Decision 9 / Task 11 — standard async HTTP behind the proven `LLMClient` Protocol, no source/browser coupling; it can land in 4a.)

**Implementation repo:** `/Users/wenqiwang/wwq/jobfeed`. Do NOT implement Phase 4 tasks in the legacy repo.

**Precedence:** This phase plan is the source of truth when it conflicts with the architecture spec.

**Commit strategy:** Commit each task separately with a task-sized conventional commit.

**Execution mode:** Run Phase 4 tasks sequentially in the order below — each task lands on a base proven by the previous one.

---

## Phase Split: 4a (no browser) / 4b (LinkedIn Playwright)

**Decided 2026-05-29 (plan review).** Phase 4 is split into two independently shippable phases so the LinkedIn Playwright variable (browser automation, anti-bot pacing, live-DOM fidelity, the 2015-line legacy port, the Chromium CI lane) is fully isolated from the four low-risk pieces.

```
Phase 4a  (zero browser, zero SessionSource — lands entirely on the EXISTING SimpleSource path)
  ├─ Task 0a  deps/config: speedyapply / indeed / linkedin_jobspy
  ├─ Task 1   SpeedyApply (SimpleSource)
  ├─ Task 2   JobSpy shared module + Indeed
  ├─ Task 3   LinkedIn JobSpy
  ├─ Task 6   dedupe primitive (cross-source twins come from SpeedyApply × ATS)
  └─ Task 7a/8a/9a  CLI + integration tests + CI for these three sources

Phase 4b  (browser / anti-bot / live-DOM / Chromium CI lane)
  ├─ Task 0b  config: linkedin (Playwright) + COMMIT the CLAUDE.md amendment (already drafted — see Governance prerequisite)
  ├─ Task 4   ScanService SessionSource orchestration (fake-tested) ← MOVED here, beside its only consumer
  ├─ Task 5   LinkedIn Playwright (pre-designed module split — see Task 5 "Module layout")
  └─ Task 7b/8b/9b  login command + LinkedIn integration scenarios + browser CI lane
```

**Why Task 4 is in 4b, not 4a:** the three 4a sources are all `SimpleSource` and use the EXISTING `ScanService` path unchanged; the SessionSource orchestration (Task 4) has exactly ONE consumer — LinkedIn (Task 5). Shipping it in 4a would land an orchestration branch with no production caller (premature). It goes with Task 5.

**4a is independently shippable** without Playwright/Chromium in CI. The CLAUDE.md "Phase 4 amendment" is a 4b prerequisite only.

---

## Implementation Decisions

**Decision 1: Risk-ordered incremental build (now split across 4a/4b — see "Phase Split" above).**
Order: SpeedyApply (reuses Phase 2 HTTP + ATS adapters, lowest risk) → JobSpy shared module + Indeed → LinkedIn JobSpy → dedupe primitive **[end of Phase 4a]** → ScanService SessionSource orchestration → LinkedIn Playwright (highest risk, lands on a proven base) **[Phase 4b]**. Cross-source twins appear from the first source (SpeedyApply × ATS), so the dedupe contract test has real data WITHOUT needing LinkedIn.

**Decision 2: ScanService stays source-agnostic.**
For SessionSource, ScanService calls `enrich()` on *every* discovered posting; the source's `EnrichSession` decides internally whether to skip (already-fresh JD), run Tier 1, or escalate to Tier 2. ScanService must NOT contain LinkedIn-specific freshness/tier logic. This matches how legacy's `main.py` drives `sess.enrich(posting)` for every posting while the session short-circuits fresh ones (`is_posting_fresh`).

**Decision 3: LinkedIn Playwright uses the async Playwright API, not sync-in-a-thread.**
Legacy is fully synchronous (`sync_playwright` + `ThreadPoolExecutor`). The rewrite's `ScanService` is async, and Playwright sync objects are thread-affine, so wrapping sync code is awkward. Rewriting against `async_playwright` fits the async `SessionSource` protocol natively (`async discover` / `async enrich_session` / `async enrich`) with no thread marshalling. The selector/scroll/click logic is a port, but it is NOT a mechanical add-`await` — the right-pane wait gating (legacy waits on a `/jobs/view/<id>` link appearing OR a right-pane innerText length delta after each click) and the search-pane re-click flow must be re-implemented and re-validated against live LinkedIn DOM.

**Decision 3a: source_search_url provenance lives inside the LinkedIn source, NOT on JobPosting.**
Legacy threads the originating search-page URL via `posting.raw["source_search_url"]`, but the rewrite's `JobPosting` has no `raw` field and the spec deliberately omits one (do not add it). The LinkedIn source keeps an internal `{canonical_id -> source_search_url}` map populated during `discover()` and consulted in `EnrichSession` Tier 1 and in the priority sort. This keeps the domain DTO clean and matches Decision 2 (source owns its own internals).

**Decision 3b: discover-time ordering is a deliberate divergence from legacy.**
Legacy `discover()` returns postings unsorted; the intern-first sort (`_enrich_priority`) lives in the ORCHESTRATOR (`main.py:44`, applied at `main.py:419`), not in `linkedin.py`. The rewrite moves that priority ordering INTO `LinkedInSource.discover()` output so `ScanService` stays source-agnostic (Decision 2). This is a deliberate behavioral move, not a port — re-implement the tier logic inside the source: tier 0 = "intern" in title AND ("fall" in title OR **"fall" in the card's source_search_url** OR "2026" in title); tier 1 = "intern" in title; tier 2 = rest; secondary key = the source's own search-url provenance from Decision 3a. (The `"fall" in url` disjunct mirrors legacy `main.py:58-60` — do not drop it; the URL is available via the Decision 3a map.)

**Decision 4: Anti-bot pacing lives inside the LinkedIn source, not a shared framework.**
Sleeps, jitter, Tier 2 caps, viewport rotation, and the cross-process lock stay inside `linkedin.py`. Phase 9 (Temporal) will later reshape pacing/retry into activity-level rate limiters — do NOT pre-build that here.

**Decision 4a: pacing must be injectable so tests don't sleep for minutes.**
Legacy uses inline `time.sleep(random.uniform(...))` with no seam; a faithful port would make discover tests block 45–90s per URL. The rewrite injects a sleeper (an `async def sleep(lo, hi)` dependency, or sleep-range config) defaulting to the production ranges; unit/integration tests pass a no-op sleeper so the click/scroll/pagination logic runs at full speed. `tier2_cap` is already config-injectable.

**Decision 4b: cookie profile + enrich lock live OFF `~/.jobfeed/` (CLAUDE.md:50 compliance).**
Legacy stored the Playwright cookie profile and the cross-process lock under `~/.jobfeed/` (`paths.cookies_dir`, `paths.home()/enrich.lock`), but the rewrite's `CLAUDE.md:50` forbids any `~/.jobfeed/` read/write. Default both to an XDG-style cache dir instead — `profile_dir = "~/.cache/jobfeed/linkedin"`, `lock_path = "~/.cache/jobfeed/enrich.lock"` (config-overridable, expanduser'd). This complies with the constraint without needing an amendment; only the browser-automation ban (Governance prerequisite above) requires the human-approved amendment.

**Decision 4c: the enrich lock is scoped to `enrich_session()` only — `discover()` is deliberately NOT under it (resolved in plan review 2026-05-29).** The PID lock's purpose is anti-bot: never run two concurrent LinkedIn browser sessions ACROSS PROCESSES (legacy `linkedin.py:89-92` — "At most one enrich_session at a time … two concurrent Playwright sessions … looks like account compromise"). Strictly, `discover()` is also a full LinkedIn session, so two concurrent `jobfeed scan` PROCESSES could both run `discover()` unserialized. Legacy itself locks only `enrich_session()`, and the **operating assumption here is one LinkedIn scan at a time** (no cron overlap, no manual double-run) — under which the lock never fires and enrich-only scope is fine. Do NOT extend the lock to cover discover now. REVISIT only if LinkedIn scans ever become concurrent / cron-overlapping (then: acquire before discover, hold across discover→enrich, release on EVERY exit path incl. the `needs_reauth` early return). The lock mechanism is a PID file (`<pid> <iso-ts>`); acquire raises `EnrichLocked` if a <2h lock from another PID exists, takes over a >2h stale lock, else writes own PID; release deletes the file.

**Decision 5: The two LinkedIn sources get distinct platform tags.**
LinkedIn Playwright tags `platform="linkedin"`; LinkedIn JobSpy tags `platform="linkedin_jobspy"`. Their job-id schemes differ, so distinct platforms prevent accidental `(platform, canonical_id)` upsert collisions and keep provenance clear. They become twins folded by the dedupe primitive, not the same DB row. **Operational note:** the two are alternative LinkedIn paths, not complements — running BOTH routinely doubles LinkedIn traffic (raising anti-bot exposure) and produces large twin clusters. Ship both for parity/flexibility, but `config.example.toml` should enable at most one by default and the docs should say "pick one". (Post-split, LinkedIn JobSpy ships in Phase 4a and LinkedIn Playwright in Phase 4b, so the "pick one" guidance spans both phases — call it out in the 4b docs.)

**Decision 5a: new sources reuse the Phase 0 observability baseline.**
Every new source binds the structured logger and emits `run_id`-scoped structlog events (not stdlib `logging`), matching ATSSource. ScanService already binds `run_id`; source-internal logs (LinkedIn anti-bot/tier events, JobSpy challenges, SpeedyApply routing misses) go through the injected structured logger.

**Decision 6: SpeedyApply is a `SimpleSource`; it is a sync→async + JobSource→SimpleSource REWRITE, not a copy.**
Legacy SpeedyApply is a sync `httpx.Client` with a discover/enrich_session lifecycle; the rewrite collapses that into one async `fetch_jobs()` using `_http` (async httpx) and reuses Phase 2 adapters where they actually exist:
- **Greenhouse:** the existing `_ats_greenhouse` exposes only a whole-board `fetch_jobs` — there is NO single-job helper. ADD a new `fetch_job(client, slug, job_id) -> JobPosting | None` to `_ats_greenhouse.py` hitting the per-job endpoint `/v1/boards/{slug}/jobs/{job_id}?content=true` (this is what legacy `speedyapply.py:191-201` does — a targeted GET, NOT fetching the whole board).
- **Ashby / Lever:** call the existing `_ats_*.fetch_jobs(client, slug)` ONCE per slug (cached for the call), then match the target by `canonical_id` in the returned list. NOTE: `fetch_jobs` drops jobs with blank required fields (`_ats_ashby.py:166`, `_ats_lever.py` equivalent), so a target id may be absent — treat "not found" as empty JD (`speedyapply-notfound`), do not crash.
- **Workday / SmartRecruiters / iCIMS:** new helpers (`_ats_workday.py`, `_ats_smartrecruiters.py`, `_ats_icims.py`). Workday/SmartRecruiters return JSON (use `_http.fetch_json`); iCIMS returns HTML, so add a `_http.fetch_text` helper (async `await client.get(...)` + `.text`, same retry/timeout as `fetch_json`) and use it — never the sync `client.get(...).text` idiom (the client is async).
Per-slug HTTP responses are cached within one `fetch_jobs` call so multiple rows from the same board hit memory. SpeedyApply needs no persistent session, so it does not implement `SessionSource`.

**Decision 7: JobSpy is isolated behind `_jobspy.py` and bridged with `asyncio.to_thread`.**
JobSpy is synchronous (tls-client) but a single-shot call with no persistent session and no thread affinity, so `await asyncio.to_thread(...)` is sufficient. pandas / jobspy / tls-client types never leak past `_jobspy.py`; callers see only `list[JobPosting]`. The Indeed `dateOnIndeed` monkeypatch is ported into `_jobspy_patches.py`.

**Decision 8: Dedupe — keep all rows; score one representative.**
The DB stores every source row (no scan-time dedup). The `(company_norm, title_norm)` columns, the `idx_jobs_dedup_softkey` index, and `normalize`/`normalize_company` ALREADY EXIST (migration `0001`, `adapters/store/_normalize.py`, `save_job`). Phase 4 adds only a pure clustering function + contract test.

**Representative selection rule** (ordered; first decisive key wins):
1. **Highest JD quality** — reuse the existing `domain.quality.quality_rank()` (FULL=5 … ABANDONED=0, None=-1) covering ALL SIX `QualityBand` members (do NOT invent a 4-band ladder — STUB/ABANDONED are real and produced by `assess_quality`). `save_job` already uses this same ladder, so dedupe and persistence stay consistent.
2. **Source priority** — by the ACTUAL `platform` values postings carry (ATS rows are tagged with the vendor name, never `"ats"`): treat `{greenhouse, ashby, lever}` as the top "ATS family" tier, then `speedyapply` > `linkedin` > `linkedin_jobspy` > `indeed`. Implement as a `platform -> rank` map in `domain/dedupe.py`.
3. **Recency** — most recent `posted_at` (NULLS-LAST, since `posted_at` is nullable). Do NOT use `discovered_at`: it is the scan-start timestamp, near-constant within a scan, so it is a useless tiebreak. This matches legacy's justified ordering (`web/routes/jobs.py:328` — `posted_at` freshness signals the req is most likely still open).
4. **Stable** — `(platform, canonical_id)` as the final deterministic tiebreak.

**Deliberate divergence to flag:** legacy's display fold (`web/routes/jobs.py:_dedup_rep_order`) puts a STATUS-priority key FIRST (an `applied`/`shortlisted` twin always represents its cluster). Phase 4's primitive operates on `JobPosting` (pre-status) and omits the status key by design; **when Phase 8 wires dedupe into status-aware display, it must layer a status-priority key ahead of rule 1** so an `applied` twin is not displaced by a fresher `new` twin. Note this in Task 6.

**Score-time use** (score only the representative) is a deliberate improvement over legacy — legacy's `list_uneval_jobs` scores every twin separately. Phase 4 ships the primitive; Phase 5 consumes it for candidate selection; Phase 8 consumes it for the status-aware display fold.

**Decision 9: One OpenAI-compatible LLM adapter (`openai-compat`) covers all API providers — supersedes Phase 3 Decision 2.**
Phase 3 Decision 2 deferred "an Anthropic SDK adapter" to Phase 4. Phase 4 generalizes that deferral: instead of one SDK adapter per vendor, ship a SINGLE `openai-compat` adapter parameterized by `base_url` + API-key-env-name + model. OpenAI's `/chat/completions` is the de-facto standard, so this one adapter drives OpenAI, DeepSeek (`https://api.deepseek.com`), MiniMax (`https://api.minimax.io/v1`), OpenRouter/Together/Groq, and local Ollama/vLLM/LM Studio — adding a provider is a config row, not code.

- **This is "standardize onto an existing protocol", NOT "build a meta-protocol".** Deliberately rejected: a config-driven mapping layer that templates arbitrary request/response shapes (per-provider auth headers, JSONPath field extraction). That is a framework for providers that don't exist — untestable, perpetually patching edge cases (YAGNI). A provider whose API genuinely diverges from OpenAI-compat earns its OWN thin adapter only when a real need appears — same rule as the existing per-CLI adapters.
- **Lowest-common-denominator wire contract.** Send only `system`/`user`/`assistant` roles and core sampling params; do NOT send the `developer` role or vendor-specific fields. (Real interop bug: MiniMax rejects `developer` role with `invalid role: developer (2013)`.) Staying on the LCD is what makes a single adapter genuinely portable across vendors.
- **CLI vs API is the user's tradeoff, preserved.** `codex-cli`/`claude-cli` reuse flat-rate subscription auth at zero marginal token cost (most developers already have one logged in); `openai-compat` serves users without a subscription, or CI/server contexts. Both sit behind the same `LLMClient`; the choice is the `backend/model` config spec (Phase 3 Decision 3) — no caller changes.
- **Cost estimation degrades gracefully (unlike codex's hard requirement).** The codex path hard-fails when a model is absent from the vendored price table (`_require_codex_pricing`). `openai-compat` CANNOT require pricing for arbitrary providers/models, so it does best-effort lookup in `model_prices.json` by model name: priced → estimate; absent → cost `None` + one warning, call proceeds. The dollar budget gate (Phase 3 Decision 7) therefore degrades to call-count-only for unpriced models; the always-available call-count gate remains the hard runaway-loop guard.
- **Key handling.** The API key is read from an env var whose NAME is given in config (`api_key_env`), never the key value in the config file or repo. Factory validation parallels `_require_executable` → a new `_require_api_key` raising a teaching error (`Set $<VAR>, or use claude-cli if you have the CLI logged in`).
- **Anthropic resolution (closes the deferred Phase 3 Decision 2 Anthropic SDK adapter).** Anthropic's native `/v1/messages` is NOT OpenAI-compatible, but it does NOT need its own adapter: (a) `claude-cli` already gives full-fidelity Anthropic access — exact cost from the JSON envelope (Phase 3 Decision 8), subscription auth; (b) Anthropic ships an OpenAI-compat endpoint at `https://api.anthropic.com/v1/` that the SAME `openai-compat` adapter drives for API-key users. Anthropic documents that compat endpoint as **test-only**: no prompt caching / extended thinking / PDF / citations, many params dropped, system messages hoisted+concatenated — but the job-eval use case (chat-completion scoring) uses none of those, so the lost features mostly don't bite. A native `anthropic` SDK adapter is therefore deferred **indefinitely**; its ONLY trigger is a real need for explicit `cache_control` prompt caching to cut eval cost (per-invocation `claude-cli` and the compat endpoint both lack it).
- **Recommended backends are the low-latency pair: `codex-cli` (subscription auth, zero marginal token cost) and `openai-compat` pointed at a FAST hosted provider (OpenAI / DeepSeek / MiniMax).** Backed by Phase 3 Decision 5's measured P99: codex-cli 28s vs claude-cli 128s (Stage B). `claude-cli` and the Anthropic compat endpoint stay fully supported but are documented as higher-latency alternatives. NB: `openai-compat` latency is the PROVIDER's, not the adapter's — a local Ollama / large model can be slow, so the recommendation is bound to a fast hosted endpoint. `config.example.toml` ships `codex-cli` (or `openai-compat`) as the recommended `stage_a`/`stage_b`, with `claude-cli` + the provider presets as commented alternatives carrying the latency note.
- **Orthogonal to the 4a/4b source split.** This task touches no source and no browser; it can land in Phase 4a alongside the SimpleSources. It is a low-risk variable (standard async HTTP behind a proven Protocol), unlike the three source variables.

---

## Source Behavioral Reference

Documented here for implementer reference. Verify each against the legacy code paths cited — docstrings have drifted.

### SpeedyApply (markdown + multi-vendor routing)

| Aspect | Value |
|--------|-------|
| List source | `GET https://raw.githubusercontent.com/speedyapply/2026-SWE-College-Jobs/main/README.md` (default; config may add NEW_GRAD/INTL files) |
| Row shape | Markdown table `Company \| Position \| Location \| [Salary] \| Posting \| Age`; apply URL in the Posting cell anchor; closed rows carry 🔒 (skip) |
| `canonical_id` | `"sa-" + sha256(apply_url)[:16]` |
| `posted_at` | Age column `Nd` → `now - N days`; else None |

**Apply-URL routing → JD fetch** (host regex → vendor):

| Host | Vendor | JD fetch |
|------|--------|----------|
| `[job-]boards[.region].greenhouse.io/<slug>/jobs/<id>` | greenhouse | NEW `_ats_greenhouse.fetch_job(client, slug, job_id)` → per-job GET `/v1/boards/{slug}/jobs/{job_id}?content=true` (single targeted GET, NOT the whole board) |
| `jobs.ashbyhq.com/<slug>/<uuid>` | ashby | call existing `_ats_ashby.fetch_jobs(client, slug)` once (cached per slug), match by `canonical_id`; not-found → empty JD |
| `jobs.lever.co/<slug>/<uuid>` | lever | call existing `_ats_lever.fetch_jobs(client, slug)` once (cached per slug), match by `canonical_id`; not-found → empty JD |
| `jobs.smartrecruiters.com/<co>/<id>` | smartrecruiters | `GET api.smartrecruiters.com/v1/companies/<co>/postings/<id>` (JSON via `_http.fetch_json`); concat `jobAd.sections.{companyDescription,jobDescription,qualifications,additionalInformation}` (HTML→text) |
| `careers-<tenant>.icims.com/jobs/<id>/...` | icims | append `?in_iframe=1`; this returns HTML, NOT JSON — fetch via a new `_http.fetch_text(client, url, ...)` helper (`resp = await client.get(url); return resp.text`, with the SAME transport retry/timeout wrapping as `fetch_json`). Do NOT write `client.get(url).text` — the rewrite client is `httpx.AsyncClient`, so `.get()` returns a coroutine; it must be `(await client.get(url)).text`. Then extract JSON-LD `JobPosting.description` (HTML→text). |
| `*.myworkdayjobs.com/<lang>/<board>/job/<rest>` or `*.myworkdaysite.com/recruiting/<tenant>/<board>/job/<rest>` | workday | `GET https://<host>/wday/cxs/<tenant>/<board>/job/<rest>`; `jobPostingInfo.jobDescription` (HTML→text) |
| anything else | unrouted | JD stays empty; quality `missing`; row still persisted |

### JobSpy (Indeed + LinkedIn)

| Aspect | Value |
|--------|-------|
| Call | `jobspy.scrape_jobs(site_name=<"indeed"\|"linkedin">, results_wanted=max_jobs, country_indeed="usa", ...)` → pandas DataFrame |
| URL→kwargs parse | Indeed (legacy `indeed.py:109-135`): `q`→`search_term`, `l`→`location`, `fromage=N`→`hours_old=N*24`, `radius`→`distance`. Parsing ONLY `fromage` drops the search term/location → JobSpy scrapes the wrong jobs. The explicit `hours_old` kwarg OVERRIDES a URL `fromage` value (legacy `indeed.py:80-81`). LinkedIn search URLs use different query keys, so the parser must be **site-aware** (or each shell passes its own params). |
| Per row | `id`, `title`, `company`, `location`, `job_url`, `description` (inline JD → `enrich_source="jobspy_inline"`), `date_posted` |
| Indeed date patch | JobSpy maps `date_posted` from `datePublished` (employer original); patch swaps to `dateOnIndeed` (when indexed) so freshness reflects "new on Indeed". Port `apply_indeed_date_patch` into `_jobspy_patches.py`. |
| Enrich | pass-through — JD already inline; `SimpleSource.fetch_jobs` returns fully-populated postings |
| `needs_reauth` | N/A (no session/cookies) |
| Challenge | Indeed may hit Cloudflare → raise/log a contained error, return what was scraped |

### LinkedIn Playwright (SessionSource)

**discover(config)** — returns postings MOSTLY WITH JD already filled:
- `search_urls` entries are plain strings OR `{url, max_jobs, group, group_max_jobs}`; same `group` pools URLs under one shared budget consumed in declaration order.
- Per URL: paginate via `&start=N` (PAGE_SIZE 25, cap 20 pages), scroll the virtual list to hydrate cards, harvest card metadata, THEN click each card and read the right-pane JD inline (`enrich_source="discover-inline"`). The right-pane read waits for a `/jobs/view/<id>` link to appear OR the right-pane innerText length to change after the click — a JS-driven DOM swap, not a static read.
- Returns postings ordered by the in-source priority logic (Decision 3b): tier 0 = title has "intern" AND ("fall" in title OR "fall" in the card's source_search_url OR "2026" in title); tier 1 = "intern"; tier 2 = rest; secondary key = the source's own `{canonical_id -> source_search_url}` map (Decision 3a) for locality.
- The source records each posting's originating search URL in its internal map (Decision 3a). The `JobPosting` DTO is NOT extended.
- Auth wall / checkpoint redirect (`CHECKPOINT_HINTS`) → `DiscoverResult(needs_reauth=True, error=...)`.
- Anti-bot pacing is INJECTABLE (see Decision 4a): production defaults 45–90s sleep between URLs, 0.8–2.0s jitter between card clicks, 1.5–2.5s between scrolls; tests pass a zero/fake sleeper.

**enrich_session()** — `async def` returning an async context manager (per `ports/source.py`); callers MUST `async with await source.enrich_session() as sess`:
- Acquire cross-process PID-file lock (`lock_path`, default `~/.cache/jobfeed/enrich.lock` — NOT `~/.jobfeed/`, per CLAUDE.md:50 / Decision 4b; stale after 2h → take over). Another fresh lock → raise `EnrichLocked` BEFORE opening a browser. (ScanService must contain this — see Task 4.)
- Open ONE `async_playwright` persistent context at the configured cookie profile dir (`SourcesLinkedInConfig.profile_dir`, Task 0), random viewport, pinned UA, `locale="en-US"`. Release lock on exit.

**EnrichSession.enrich(posting)** — gap-fill only:
- Fresh-skip: posting already has acceptable JD (quality ≥ good, or long partial) → return cached (`enrich_source="cached-fresh"`), no fetch.
- Tier 1: reload the posting's search URL (from the source's internal `{canonical_id -> source_search_url}` map, Decision 3a) once per session, click the card, read right pane. Quality ≥ good → done (`enrich_source="search-pane"`).
- Tier 2: only if Tier 1 < good → `goto(posting.url)` detail page. Capped at 30 per session; 5–30s sleep before each goto. Counted pre-goto.
- Per-posting errors are contained (return `EnrichResult` with `error`, never raise); auth wall → `EnrichResult(error="needs_reauth: ...")`.

---

## File Map

```
jobfeed/                                  # repo root (/Users/wenqiwang/wwq/jobfeed)
├── src/jobfeed/
│   ├── adapters/sources/
│   │   ├── ats.py, _ats_ashby.py, _ats_lever.py, mock.py  # EXISTING — _ats_* reused by SpeedyApply
│   │   ├── _http.py                      # MODIFY — ADD fetch_text(client, url) (async, retry/timeout) for iCIMS HTML
│   │   ├── _ats_greenhouse.py            # MODIFY — ADD fetch_job(slug, job_id) single-job helper
│   │   ├── speedyapply.py                # CREATE — SimpleSource: markdown + routing facade
│   │   ├── _speedyapply_markdown.py      # CREATE — markdown table → rows (no HTTP)
│   │   ├── _speedyapply_routing.py       # CREATE — apply-URL host → vendor + JD fetch (per-call cache)
│   │   ├── _ats_workday.py               # CREATE — Workday JD fetch helper (JSON)
│   │   ├── _ats_smartrecruiters.py       # CREATE — SmartRecruiters JD fetch helper (JSON)
│   │   ├── _ats_icims.py                 # CREATE — iCIMS JSON-LD JD fetch helper (raw HTML, not fetch_json)
│   │   ├── _jobspy.py                    # CREATE — lazy jobspy/pandas, site-aware URL parse, scrape→JobPosting boundary
│   │   ├── _jobspy_patches.py            # CREATE — Indeed dateOnIndeed monkeypatch
│   │   ├── indeed_jobspy.py              # CREATE — SimpleSource (JobSpy, site_name="indeed") [spec-aligned name]
│   │   ├── linkedin_jobspy.py            # CREATE — SimpleSource (JobSpy, site_name="linkedin")
│   │   ├── linkedin.py                   # CREATE — SessionSource FACADE (async_playwright); thin, delegates to the modules below
│   │   ├── _linkedin_discover.py         # CREATE — discover(): pagination + scroll-harvest + card-click right-pane JD + priority order
│   │   ├── _linkedin_enrich.py           # CREATE — EnrichSession.enrich(): fresh-skip / Tier 1 / Tier 2
│   │   ├── _linkedin_dom.py              # CREATE — selectors + anti-bot constants (viewports, sleep ranges, checkpoint hints)
│   │   └── _linkedin_lock.py             # CREATE — cross-process PID-file enrich lock
│   ├── domain/
│   │   ├── normalize.py                  # CREATE — relocate normalize()/normalize_company() here
│   │   └── dedupe.py                     # CREATE — twin clustering + representative selection
│   ├── services/
│   │   └── scan.py                       # MODIFY — drive SessionSource (discover→enrich loop)
│   ├── adapters/store/
│   │   ├── _normalize.py                 # MODIFY — re-export from domain.normalize (keep import site stable)
│   │   └── postgres.py                   # UNCHANGED — already writes *_norm columns
│   ├── config.py                         # MODIFY — add per-source config models
│   ├── cli/
│   │   ├── __init__.py                   # MODIFY — wiring only
│   │   ├── scan.py                       # MODIFY — --source choices + lazy source construction
│   │   └── login.py                      # CREATE — `jobfeed login linkedin` (headed one-time login)
│   ├── config.example.toml               # MODIFY — [sources.speedyapply|indeed|linkedin_jobspy|linkedin]
│   └── pyproject.toml                    # MODIFY — add python-jobspy, playwright
└── tests/
    ├── unit/
    │   ├── test_speedyapply_markdown.py        # CREATE
    │   ├── test_speedyapply_routing.py         # CREATE (respx)
    │   ├── test_jobspy_adapter.py              # CREATE (monkeypatch jobspy)
    │   ├── test_jobspy_indeed_patch.py         # CREATE
    │   ├── test_linkedin_discover.py           # CREATE (Playwright route-mock / fixture HTML)
    │   ├── test_linkedin_enrich.py             # CREATE
    │   ├── test_scan_session_source.py         # CREATE (fake SessionSource)
    │   └── test_dedupe.py                       # CREATE
    ├── contract/
    │   ├── test_dedupe_contract.py             # CREATE — twin semantics + representative rule
    │   └── test_source_dto_contract.py         # CREATE — frozen fixtures per new source
    ├── integration/
    │   └── test_phase4_scan_chain.py           # CREATE — PG + mocked IO, incl. twin scenario
    ├── live/
    │   └── test_phase4_live_smoke.py           # CREATE — @pytest.mark.live
    └── fixtures/
        ├── speedyapply_readme.md               # CREATE
        ├── jobspy_indeed_rows.json             # CREATE
        ├── jobspy_linkedin_rows.json           # CREATE
        ├── linkedin_search_page.html           # CREATE
        └── linkedin_detail_page.html           # CREATE
```

---

## Task 0: Dependencies + Config Expansion

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/jobfeed/config.py`
- Modify: `config.example.toml`
- Test: `tests/unit/test_config.py` (expand)

**What to build:**
Add runtime deps `python-jobspy >= 1.1` and `playwright >= 1.48` (pandas arrives transitively via jobspy). Document `playwright install chromium` as a post-install step in the README/quickstart.

Add four Pydantic config models under `SourcesConfig` (all `extra="forbid"`):
- `SourcesSpeedyApplyConfig`: `enabled: bool = False`, `search_urls: list[str] = []` (empty → use the built-in default README URL), `max_concurrent: int = Field(10, ge=1)`, `fetch_timeout_s: float = Field(30.0, gt=0)`.
- Two JobSpy models, `SourcesIndeedConfig` and `SourcesLinkedInJobSpyConfig` (kept separate so each site's URLs/limits are independent), each: `enabled: bool = False`, `search_urls: list[str] = []`, `max_jobs: int = Field(100, ge=1)`, `hours_old: int | None = None`.
- `SourcesLinkedInConfig`: `enabled: bool = False`, `search_urls: list[dict | str] = []`, `max_jobs: int = Field(100, ge=1)`, `headless: bool = True`, `parallel_workers: int = Field(3, ge=1)`, `tier2_cap: int = Field(30, ge=0)`, `profile_dir: str = "~/.cache/jobfeed/linkedin"` (expanduser'd; the persistent Chromium cookie profile — BOTH `jobfeed login linkedin` and the SessionSource read this same field so the logged-in session loads for headless scans; NOT under `~/.jobfeed/` per CLAUDE.md:50, Decision 4b), `lock_path: str = "~/.cache/jobfeed/enrich.lock"`. The anti-bot sleep ranges (Decision 4a) default in code to the production values and are overridable for tests via the injected sleeper, not config.

Extend `SourcesConfig` with `speedyapply`, `indeed`, `linkedin_jobspy`, `linkedin` fields (all `default_factory`). Add matching `[sources.*]` blocks to `config.example.toml` with `enabled = false` defaults so a fresh config scans nothing new until opted in.

**Acceptance criteria:**
- [ ] `python-jobspy` and `playwright` are installable; `pyproject.toml` updated
- [ ] `Settings.sources` exposes `speedyapply`, `indeed`, `linkedin_jobspy`, `linkedin` with documented defaults
- [ ] `load_settings()` parses each `[sources.*]` block; unknown keys raise (extra="forbid")
- [ ] Env override works for a SCALAR (e.g. `JOBFEED_SOURCES__LINKEDIN__HEADLESS=false`). NOTE: list/dict fields (`search_urls`, etc.) are TOML-only — `_set_nested_value` stores a bare string at the leaf and Pydantic won't coerce it into a list, so `JOBFEED_SOURCES__*__SEARCH_URLS` is unsupported by design. Document this in `config.example.toml`; do not add an acceptance criterion expecting list env override.
- [ ] All four sources default to `enabled = false`
- [ ] All tests pass, committed

---

## Task 1: SpeedyApply Source (markdown + multi-vendor routing)

**Files:**
- Create: `src/jobfeed/adapters/sources/_speedyapply_markdown.py`
- Create: `src/jobfeed/adapters/sources/_speedyapply_routing.py`
- Modify: `src/jobfeed/adapters/sources/_ats_greenhouse.py` (ADD a single-job helper)
- Modify: `src/jobfeed/adapters/sources/_http.py` (ADD `fetch_text` helper with retry/timeout, for iCIMS HTML)
- Create: `src/jobfeed/adapters/sources/_ats_workday.py`
- Create: `src/jobfeed/adapters/sources/_ats_smartrecruiters.py`
- Create: `src/jobfeed/adapters/sources/_ats_icims.py`
- Create: `src/jobfeed/adapters/sources/speedyapply.py`
- Test: `tests/unit/test_speedyapply_markdown.py`, `tests/unit/test_speedyapply_routing.py`

**What to build:**
`_speedyapply_markdown.parse_rows(markdown: str, *, now: datetime) -> list[SpeedyRow]` — walk the table top-down, tracking column count (5-col vs 6-col with Salary), extract company/title/location/apply_url/age. Skip 🔒 closed rows and continuation rows (empty company). `canonical_id = "sa-" + sha256(apply_url)[:16]`. No HTTP. Behavioral parity with legacy `_parse_markdown_rows`.

Add `_ats_greenhouse.fetch_job(client, slug, job_id, *, discovered_at) -> JobPosting | None` — single-job GET `/v1/boards/{slug}/jobs/{job_id}?content=true` (NOT the board fetch). Reuse the module's existing `_build_posting`/parse helpers. Export it in `__all__`.

`_speedyapply_routing.route_and_fetch(client, apply_url, *, slug_cache) -> tuple[str, str]` returning `(jd_text, enrich_source)` — match the apply URL against the host table in the Source Behavioral Reference; dispatch to the right vendor helper. Greenhouse → the NEW `fetch_job` (single GET). Ashby/Lever → call the existing `_ats_*.fetch_jobs(client, slug)` once, cache the returned list in `slug_cache`, match by `canonical_id`; if the id is absent (e.g. `fetch_jobs` dropped it on a blank-field guard), return `("", "speedyapply-notfound")`. New `_ats_workday`/`_ats_smartrecruiters`/`_ats_icims` helpers each expose `async def fetch_jd(client, ...) -> str` (Workday/SmartRecruiters use `_http.fetch_json`; iCIMS uses the new `_http.fetch_text` — `(await client.get(url)).text`, NOT the sync `client.get(url).text`, since the client is `httpx.AsyncClient`). Unrouted hosts return `("", "speedyapply-unrouted")`.

`speedyapply.SpeedyApplySource` implements `SimpleSource`; its `SourcesSpeedyApplyConfig` (incl. `fetch_timeout_s`) is injected via the constructor (the `fetch_jobs(config)` dict arg is a protocol no-op — same convention as `ATSSource`/`IndeedSource`; pass `{}`). `fetch_jobs(config)`: fetch each configured markdown URL (default built-in), parse rows, in-source dedupe by `canonical_id`, then route+fetch JD per row with a shared per-call `slug_cache`; build `JobPosting(platform="speedyapply", jd_text=..., jd_quality=assess_quality(jd_text), enrich_source=...)`. `route_and_fetch` returns `(jd_text, enrich_source)`; for the Greenhouse path call `_ats_greenhouse.fetch_job(...)` and take `.jd_text` off the returned `JobPosting`. Every per-row vendor fetch goes through `_http` (`fetch_json` for JSON vendors, the new `fetch_text` for iCIMS HTML) so all inherit the same transport retry; each must pass `fetch_timeout_s` so one slow/degraded vendor (cf. the Ashby degradation that motivated `_http`'s retry) can't stall the batch. Per-row fetch errors are contained (log, JD empty, row still returned). Concurrency bounded by `max_concurrent`.

**Acceptance criteria:**
- [ ] Markdown parser handles 5-col and 6-col tables, skips 🔒 and continuation rows, derives `posted_at` from `Nd` age
- [ ] `canonical_id` is `"sa-" + sha256(apply_url)[:16]`
- [ ] New `_ats_greenhouse.fetch_job(slug, job_id)` hits the single-job endpoint and returns one posting (respx-mocked); exported in `__all__`
- [ ] Ashby/Lever routing calls `_ats_*.fetch_jobs` once per slug and matches by `canonical_id`; a job id missing from the returned list → `("", "speedyapply-notfound")` (no crash)
- [ ] Workday, SmartRecruiters, iCIMS helpers each fetch + HTML-strip JD from the documented endpoints (respx-mocked); iCIMS path uses raw-text fetch, not `fetch_json`
- [ ] Ashby/Lever per-slug board fetched once per `fetch_jobs` call even with multiple rows from that slug (cache asserted)
- [ ] Unrouted host → JD empty, `enrich_source="speedyapply-unrouted"`, row still persisted
- [ ] One row's fetch failure does not drop other rows
- [ ] `isinstance(SpeedyApplySource(...), SimpleSource)` is True
- [ ] All tests use respx (no real HTTP); committed

---

## Task 2: JobSpy Shared Module + Indeed Source

**Files:**
- Create: `src/jobfeed/adapters/sources/_jobspy.py`
- Create: `src/jobfeed/adapters/sources/_jobspy_patches.py`
- Create: `src/jobfeed/adapters/sources/indeed_jobspy.py` (spec-aligned name, symmetric with `linkedin_jobspy.py`)
- Test: `tests/unit/test_jobspy_adapter.py`, `tests/unit/test_jobspy_indeed_patch.py`

**What to build:**
`_jobspy.scrape(*, site_name, search_url, max_jobs, hours_old) -> list[JobPosting]` — lazy-import `jobspy` and `pandas` inside the function; parse the FULL set of JobSpy kwargs out of the search URL, site-aware. For Indeed (legacy `indeed.py:109-135`): `q`→`search_term`, `l`→`location`, `fromage=N`→`hours_old=N*24`, `radius`→`distance`. Parsing only `fromage` (dropping `q`/`l`) makes JobSpy scrape the wrong jobs. When the `hours_old` argument is not None it OVERRIDES the URL's `fromage`. LinkedIn uses different query keys, so the parser branches on `site_name`. Call `scrape_jobs(...)`; convert each DataFrame row to `JobPosting` with `enrich_source="jobspy_inline"`, coercing pandas NaN/NaT to `None` and parsing `date_posted` to aware UTC. pandas/jobspy types never escape this module. Raise a contained `JobSpyError` on Cloudflare/challenge responses.

ALSO expose `async def scrape_urls(*, site_name, search_urls, max_jobs, hours_old, logger) -> list[JobPosting]` — the shared per-URL loop (`await asyncio.to_thread(scrape, ...)` per URL, aggregate, contain per-URL errors). **Both `IndeedSource` and `LinkedInJobSpySource` call THIS** (DRY — the `to_thread` + per-URL containment loop is byte-identical between the two sources; only `site_name` and Indeed's date patch differ), so neither source re-implements the loop. The `platform` re-tag (`"indeed"` vs `"linkedin_jobspy"`, Decision 5) is the source's job, applied to `scrape_urls`'s output.

`_jobspy_patches.apply_indeed_date_patch()` — port the legacy monkeypatch that swaps Indeed's `datePublished` for `dateOnIndeed` in `_process_job` input, so `posted_at` reflects "new on Indeed". Idempotent; raises a clear error if JobSpy renamed the patched symbol.

`indeed_jobspy.IndeedSource` implements `SimpleSource`. Its `SourcesIndeedConfig` is injected via the constructor (the `SimpleSource.fetch_jobs(config)` dict arg is a protocol no-op, same convention as Phase 2's `ATSSource`; pass `{}`). `fetch_jobs(config)`: apply the date patch once, then **delegate to `_jobspy.scrape_urls(site_name="indeed", search_urls=..., ...)`** (the shared loop — do NOT re-implement the `to_thread`/containment loop here); tag `platform="indeed"`.

**Acceptance criteria:**
- [ ] `_jobspy.scrape` converts a fixture DataFrame to `JobPosting`s with inline JD and aware-UTC `posted_at`; NaN→None
- [ ] URL parsing maps Indeed `q`/`l`/`fromage`/`radius` to `search_term`/`location`/`hours_old`/`distance` (asserted on a crafted URL); explicit `hours_old` arg overrides URL `fromage`
- [ ] pandas/jobspy imported lazily (module import works without them at import time; assert via import-order test or mocked absence)
- [ ] Indeed date patch makes `posted_at` follow `dateOnIndeed`, not `datePublished` (behavioral test on a crafted input)
- [ ] `IndeedSource.fetch_jobs` runs the sync scrape via `asyncio.to_thread` (does not block the loop — asserted by running concurrently with another coroutine)
- [ ] Cloudflare/challenge response → contained error, partial results returned, run not aborted
- [ ] `isinstance(IndeedSource(...), SimpleSource)` is True
- [ ] Tests mock `jobspy.scrape_jobs` (no real HTTP); committed

---

## Task 3: LinkedIn JobSpy Source

**Files:**
- Create: `src/jobfeed/adapters/sources/linkedin_jobspy.py`
- Test: `tests/unit/test_jobspy_adapter.py` (extend)

**What to build:**
`linkedin_jobspy.LinkedInJobSpySource` implements `SimpleSource` — a thin shell delegating to `_jobspy.scrape_urls(site_name="linkedin", ...)` (the SAME shared loop Indeed uses — NO duplicated `to_thread`/containment code), then tagging `platform="linkedin_jobspy"` (Decision 5). No date patch (Indeed-specific).

**Acceptance criteria:**
- [ ] `fetch_jobs` returns postings with `platform="linkedin_jobspy"` and inline JD
- [ ] Reuses `_jobspy.scrape_urls` (no duplicated DataFrame OR per-URL fetch-loop logic — same shared loop as Indeed)
- [ ] `isinstance(LinkedInJobSpySource(...), SimpleSource)` is True
- [ ] Tests mock `jobspy.scrape_jobs`; committed

---

## Task 4: ScanService SessionSource Orchestration (Phase 4b — moved beside its only consumer, Task 5)

**Files:**
- Modify: `src/jobfeed/services/scan.py`
- Modify: `src/jobfeed/services/error_handler.py` (no new method needed — see contract below)
- Test: `tests/unit/test_scan_session_source.py`

**What to build:**
Widen `SourceSpec` to `tuple[str, SimpleSource | SessionSource, dict[str, object]]`. Also widen `_scan_one_source`'s `source` parameter annotation to `SimpleSource | SessionSource` (not just the alias) — under `mypy --strict` (enforced by `make lint`) leaving the param as `SimpleSource` fails with an arg-type error at the `run()` call site; `fetch_jobs` is only reached inside the SimpleSource isinstance branch, `discover`/`enrich_session` inside the SessionSource branch. Branch on `isinstance(source, SessionSource)` (runtime-checkable). **Wrap the ENTIRE SessionSource branch (discover, session-open, enrich loop) in the same try/except the SimpleSource branch uses**, so discover exceptions, `enrich_session()` failures (notably `EnrichLocked` raised at lock acquisition, and browser-launch errors), and any non-contained error are routed through `handle_source_fetch_error` and the source contributes 0 jobs WITHOUT aborting sibling sources. (`run()`'s `asyncio.gather` has no `return_exceptions=True`, so an uncaught exception here WOULD abort the whole run — containment must live in `_scan_one_source`.)

- SimpleSource: unchanged (`fetch_jobs` → save).
- SessionSource: `result = await source.discover(config)`. **needs_reauth contract:** `result.needs_reauth=True` is treated as a recoverable source failure routed through `handle_source_fetch_error` — it DOES increment `PipelineRun.errors` (consistent with the existing handler and the locked test `test_scan_service_continues_on_source_failure` asserting `errors==1`; do not invent a non-counting path). No session is opened, the source contributes 0 jobs, `run()` does not raise, sibling sources are unaffected. Else open the session — note the protocol method is `async def`, so the correct form is `async with await source.enrich_session() as sess:` — and iterate `result.postings` **sequentially**, calling `await sess.enrich(posting)` for EVERY posting (Decision 2 — the session decides skip/Tier1/Tier2). Merge each `EnrichResult` back into the posting (`jd_text`, `jd_quality`←`quality`, `enrich_source`, and `posted_at` when present), then save. **On a successful enrich (`enrich_source != "error"`), stamp `enriched_at = now`** — legacy does this (`main.py:432-435`) so the DB row's freshness/observability stays consistent; `save_job` persists it via `COALESCE(EXCLUDED.enriched_at, ...)`. On hard failure leave `enriched_at` unchanged so the next scan retries. **Freshness scope (read carefully — legacy is INTRA-scan, not cross-scan):** the session's fresh-skip (`is_posting_fresh`) inspects the IN-MEMORY posting handed in by THIS run's `discover()`; it does NOT read the prior scan's persisted `enriched_at` back from the store. So fresh-skip only fires when `discover()` itself already stamped `enriched_at` on a card it read inline this run (see Task 5) — it lets the enrich loop skip re-fetching cards whose right-pane JD discover already captured. A fresh `discover()` re-scrapes every card next scan, so the persisted `enriched_at` does NOT make a later scan skip enrichment (that would require a discover-time store hydration step, which legacy does not have and Phase 4 does not add). A per-posting `EnrichResult.error` is logged (Phase 4 = log-only; the rewrite already has a `jobs.enrich_error` column + an attention-report consumer, but wiring the per-posting error into `save_job` is deferred — state this explicitly) and the posting saved with whatever JD it has; it does NOT abort the loop and does NOT increment `PipelineRun.errors`.

Cross-source `asyncio.gather` in `run()` is unchanged structurally (SimpleSources and the SessionSource's discover run concurrently); only the SessionSource's enrich loop is sequential within its session.

**Acceptance criteria:**
- [ ] `SourceSpec` accepts both source kinds; `isinstance` dispatch chooses the right path
- [ ] SimpleSource path behavior is byte-for-byte unchanged (existing scan tests still pass)
- [ ] SessionSource path opens the session via `async with await source.enrich_session() as sess` (the protocol method is `async def`); a fake whose `enrich_session` is `async def` works with the chosen form
- [ ] SessionSource path: discover → enrich every posting → save; merged fields persisted
- [ ] Successful enrich stamps `enriched_at = now` (persisted via `save_job`)
- [ ] INTRA-scan fresh-skip: within ONE run, a discovered posting that already carries acceptable inline JD (its `enriched_at` set by `discover()`, Task 5) is NOT re-fetched by the enrich loop — the session returns `cached-fresh` (asserted with a fake session). Do NOT assert cross-scan skip (legacy re-scrapes every card each scan; persisted `enriched_at` does not feed a later scan's fresh-skip).
- [ ] `needs_reauth=True` → no session opened, 0 jobs, `PipelineRun.errors` incremented by 1 via `handle_source_fetch_error`, other sources still saved, `run()` does not raise
- [ ] `enrich_session()` raising `EnrichLocked` (or a browser-launch error) → that source contributes 0, is recorded via the error handler, and sibling sources in the same `gather` still complete and save (run not aborted)
- [ ] A per-posting enrich error does not abort the loop and does not bump `PipelineRun.errors`
- [ ] enrich is called for every discovered posting (fresh-skip is the session's responsibility, asserted with a fake SessionSource)
- [ ] Tests use a fake in-memory SessionSource + EnrichSession (no Playwright); committed

---

## Task 5: LinkedIn Playwright SessionSource (Phase 4b)

**Files:**
- Create: `src/jobfeed/adapters/sources/_linkedin_lock.py`
- Create: `src/jobfeed/adapters/sources/linkedin.py` (SessionSource facade)
- Create: `src/jobfeed/adapters/sources/_linkedin_discover.py`
- Create: `src/jobfeed/adapters/sources/_linkedin_enrich.py`
- Create: `src/jobfeed/adapters/sources/_linkedin_dom.py` (selectors + anti-bot constants)
- Test: `tests/unit/test_linkedin_discover.py`, `tests/unit/test_linkedin_enrich.py`

**Module layout (pre-designed to satisfy the ≤300-line `adapters/sources/` gate, which is BLOCKING and does NOT exempt sources; legacy `linkedin.py` is 2015 lines and cannot be ported into one or two files):** `linkedin.py` = the `SessionSource` facade (constructor, holds `SourcesLinkedInConfig` + injected sleeper + the `{canonical_id -> source_search_url}` map, and delegates); `_linkedin_discover.py` = the discover pipeline; `_linkedin_enrich.py` = the `EnrichSession` (fresh-skip / Tier 1 / Tier 2); `_linkedin_dom.py` = selectors + anti-bot constants (viewports, sleep ranges, `CHECKPOINT_HINTS`); `_linkedin_lock.py` = the lock. Each file must stay ≤300 lines — if discover or enrich still overflows, split further (e.g. `_linkedin_scroll.py`).

**What to build:**
`_linkedin_lock`: cross-process PID-file lock (`acquire`/`release`, path from `lock_path` config, default `~/.cache/jobfeed/enrich.lock` — NOT `~/.jobfeed/` per CLAUDE.md:50; stale-takeover after 2h, raise `EnrichLocked` if a fresh lock is held). Port the lock LOGIC from legacy `acquire_enrich_lock`/`release_enrich_lock` but change the default path.

`linkedin.LinkedInSource` implements `SessionSource` using `async_playwright` (Decision 3). The constructor takes the injectable sleeper (Decision 4a) and reads `profile_dir`/`lock_path`/`tier2_cap` from `SourcesLinkedInConfig`:
- `discover(config)`: resolve `search_urls` (strings or `{url,max_jobs,group,group_max_jobs}`) into a plan with group budgets; per URL paginate `&start=N` (25/page, ≤20 pages), scroll to hydrate, harvest card metadata, then click each card to read right-pane JD inline (the right-pane read waits for a `/jobs/view/<id>` link OR an innerText length delta after the click — a JS DOM swap). **Stamp `enriched_at = now` on a card that got acceptable inline JD this pass, and leave `enriched_at = None` on cards-only postings** (mirror legacy `linkedin.py:1234`) — this is what lets the enrich loop's intra-scan fresh-skip (Task 4) skip re-fetching the inline-JD cards. Populate the source's internal `{canonical_id -> source_search_url}` map (Decision 3a). Return `DiscoverResult` with postings ordered by the in-source priority logic (Decision 3b — a deliberate divergence from legacy, which sorts in the orchestrator, NOT in `linkedin.py`). Checkpoint/authwall → `needs_reauth=True`. Pace via the injected sleeper.
- `enrich_session()`: `async def` that acquires the PID lock (raising `EnrichLocked` BEFORE any browser cost), opens ONE async persistent context (`profile_dir`, random viewport, pinned UA), and returns an async-context-manager yielding an `EnrichSession`; release lock + close context on exit. Callers use `async with await source.enrich_session() as sess`.
- `EnrichSession.enrich(posting)`: fresh-skip (acceptable cached JD) → Tier 1 (reload the search URL from the source's `{canonical_id -> source_search_url}` map once/session, click card, read pane; ≥good wins) → Tier 2 (`goto(posting.url)`, capped at `tier2_cap`/session, pre-goto sleep via the injected sleeper). Contain per-posting errors; authwall → `EnrichResult(error="needs_reauth: ...")`.

**Test fixture note:** static captured HTML cannot reproduce the click-driven right-pane JD read (no voyager JS runs, so the click is a no-op and the wait times out). The unit tests must use a PURPOSE-BUILT interactive fixture (a real Chromium runs its JS — so these tests carry `@pytest.mark.browser`, Task 9). DOM contract to document and build:
- **Cards:** `li[data-occludable-job-id="<id>"]` each containing `a[href*='/jobs/view/<id>']` (title link) + company/location sub-elements.
- **Click → JD:** clicking a card's JS injects/updates a click-reactive right-pane body (`#job-details` / `[data-test-job-description]`) containing the matching `/jobs/view/<id>` link and the JD text, so the discover/Tier-1 wait (link-appears OR innerText-delta) resolves.
- **Scroll → more cards:** a fixed-height `overflow:auto` list container whose scroll event appends the next batch of `li[data-occludable-job-id]` nodes, so the async scroll-harvest + stuck-rounds termination is exercised deterministically (not just the already-present cards).
- `&start=N` pagination uses fresh `page.goto`, so it is route-mockable separately.

**Fidelity caveat:** the fixture validates the orchestration/scroll/click/parse logic ONLY; it cannot prove the selectors match TODAY's live LinkedIn DOM. The Task 9 live smoke is the SOLE selector-fidelity check and must be run manually after any LinkedIn DOM drift — state this in the plan so green fixture tests are not mistaken for "discover works against real LinkedIn".

**Acceptance criteria:**
- [ ] `_linkedin_lock`: acquires, blocks a second fresh acquire (`EnrichLocked`), takes over a >2h-stale lock, releases on exit
- [ ] `discover` returns postings with inline JD and `enrich_source="discover-inline"` against the interactive fixture (click injects the right-pane JD); inline-JD cards carry `enriched_at=now`, cards-only carry `enriched_at=None`
- [ ] `discover` orders postings intern-first per the in-source priority logic: tier 0 = title has "intern" AND ("fall" in title OR "fall" in the card's source_search_url OR "2026" in title); tier 1 = "intern"; tier 2 = rest; secondary key = search-url. Reads the URL from the source's internal map (NOT `JobPosting.raw`). (Restores legacy's `"fall" in url` disjunct — `main.py:58-60`.)
- [ ] `discover` paginates via `&start=N` and respects per-URL / group budgets
- [ ] Checkpoint/authwall HTML → `DiscoverResult(needs_reauth=True)`
- [ ] `enrich_session` is `async def`; `async with await source.enrich_session() as sess` works and raises `EnrichLocked` before opening a browser when a fresh lock is held
- [ ] `enrich` fresh-skip returns `cached-fresh` without navigating
- [ ] `enrich` Tier 1 ≥good short-circuits; Tier 1 <good escalates to Tier 2
- [ ] Tier 2 respects `tier2_cap` per session (cap-exceeded → skip with diagnostic, JD left empty)
- [ ] Injected no-op sleeper makes the discover/enrich tests run without real anti-bot sleeps (no multi-second waits)
- [ ] `isinstance(LinkedInSource(...), SessionSource)` is True
- [ ] Tests drive Playwright against the interactive fixture / route interception (no live LinkedIn), carry `@pytest.mark.browser`, and are excluded from `make quality`; committed
- [ ] LinkedIn source is decomposed into `linkedin.py` (facade) + `_linkedin_discover.py` + `_linkedin_enrich.py` + `_linkedin_dom.py` + `_linkedin_lock.py`, EACH ≤300 lines — `make quality`'s code-hygiene gate passes (the `adapters/sources/` 300-line limit is blocking and unexempted)

---

## Task 6: Dedupe Primitive + Twin Contract Test

**Files:**
- Create: `src/jobfeed/domain/normalize.py`
- Modify: `src/jobfeed/adapters/store/_normalize.py` (re-export from `domain.normalize`)
- Modify: `tests/unit/test_architecture_boundaries.py` (add `"re"` to `DOMAIN_ALLOWED_IMPORTS`)
- Create: `src/jobfeed/domain/dedupe.py`
- Test: `tests/unit/test_dedupe.py`, `tests/contract/test_dedupe_contract.py`

**What to build:**
Relocate `normalize()` and `normalize_company()` into `domain/normalize.py` (domain owns the rules; adapters depend on domain, not vice-versa). Keep `adapters/store/_normalize.py` as a thin re-export so `postgres.py`'s import site and behavior are unchanged. **`normalize` uses `import re`, which is NOT in `test_architecture_boundaries.py`'s `DOMAIN_ALLOWED_IMPORTS` set** — add `"re"` to that allowlist (stdlib, legitimately domain-pure) in the same task, or the relocation lands with a red boundary test (contradicting Task 10's "boundary checks pass").

`domain/dedupe.py`:
- `twin_key(job: JobPosting) -> tuple[str, str]` = `(normalize_company(job.company), normalize(job.title))`.
- `cluster_twins(jobs: Iterable[JobPosting]) -> list[TwinCluster]` — group by `twin_key`, EXCEPT: any job whose normalized company OR title is empty (`""`) must form its OWN singleton cluster, never folded with other blank-norm rows. Mirror legacy's guard (`web/routes/jobs.py`: `PARTITION BY COALESCE(company_norm, '__row_'||id), ...` and `if not company_norm or not title_norm: skip`). Without this, two unrelated rows that both normalize to a blank company (e.g. symbol-only names, or a JobSpy row with a null company) collapse into one bogus cluster with one arbitrary representative. Each cluster lists its members and a chosen `representative`.
- Representative rule (Decision 8), keys in order: (1) **highest JD quality via the existing `domain.quality.quality_rank`** (covers all six bands incl. STUB/ABANDONED + None — do NOT hand-roll a 4-band ladder); (2) **source priority** via a `platform -> rank` map using REAL platform values — `{greenhouse, ashby, lever}` (the ATS family) rank highest, then `speedyapply` > `linkedin` > `linkedin_jobspy` > `indeed` (there is no `"ats"` platform value); (3) **most recent `posted_at`, NULLS-LAST** (NOT `discovered_at`, which is the near-constant scan-start time); (4) stable `(platform, canonical_id)`.
- `pick_representatives(jobs) -> list[JobPosting]` — convenience returning one representative per cluster (the input Phase 5 candidate selection will filter to).
- **Status-priority is deliberately NOT a key here** (the primitive is pre-status); add a doc comment noting Phase 8's status-aware display fold must layer a status key (applied/shortlisted twin wins) ahead of quality, per legacy `web/routes/jobs.py:_dedup_rep_order`.

`cluster_twins` and the normalization must be pure (no IO). The contract test is the Phase 4 companion ("twin/dedupe contract test").

**Acceptance criteria:**
- [ ] `normalize`/`normalize_company` live in `domain/normalize.py`; `adapters/store/_normalize.py` re-exports them; `postgres.py` unchanged and its tests still pass
- [ ] `"re"` added to `DOMAIN_ALLOWED_IMPORTS`; `test_architecture_boundaries.py` passes with `domain/normalize.py` present
- [ ] `twin_key` folds "Stripe" / "Stripe, Inc." / "Stripe Technologies" to the same company key (corporate-suffix stripping preserved)
- [ ] N postings of the same job across different platforms produce ONE cluster with N members
- [ ] Empty-norm guard: two UNRELATED postings that both normalize to a blank company (or blank title) do NOT fold together — each is its own singleton cluster (contract-test case)
- [ ] Representative quality key uses `domain.quality.quality_rank` and ranks all six bands (a STUB twin beats a MISSING twin; covered by a test)
- [ ] Source-priority tiebreak uses real platform values (a `greenhouse` twin beats a `linkedin` twin at equal quality; an `"ats"` literal appears nowhere)
- [ ] Recency tiebreak uses `posted_at` NULLS-LAST, not `discovered_at` (covered by a test where discovered_at is equal but posted_at differs)
- [ ] Distinct jobs (different title or company) stay in separate clusters
- [ ] `pick_representatives` returns exactly one posting per cluster
- [ ] `domain/dedupe.py` has no imports from `adapters/` or `ports/store*` (imports `domain.quality.quality_rank` and `domain.normalize`)
- [ ] Contract test locks twin-key + representative semantics (changing any key fails it)
- [ ] All committed

---

## Task 7: CLI Integration + LinkedIn Login Command

**Files:**
- Modify: `src/jobfeed/cli/scan.py`
- Modify: `src/jobfeed/cli/__init__.py`
- Create: `src/jobfeed/cli/login.py`
- Modify: `config.example.toml`

**What to build:**
Extend `scan --source` choices to `["mock", "ats", "speedyapply", "indeed", "linkedin-jobspy", "linkedin", "all"]` (hyphenated CLI token, dispatched via explicit branches like the existing `ats` path — maps to the `linkedin_jobspy` config field / platform tag; no programmatic name derivation). Following the existing `_build_ats` pattern, construct each enabled source LOCALLY (append to the local `list[SourceSpec]`) — do NOT store new sources into `app["sources"]` (that registry stays the mock seed). Use a single `contextlib.AsyncExitStack` to own ALL created resources so `--source all` with both ATS and SpeedyApply does not overwrite/leak a shared client variable:
- SpeedyApply: create an httpx client via `create_http_client`, register it on the exit stack (NOT a single `client` var).
- Indeed / LinkedIn JobSpy: construct directly (scrape bridged via `to_thread`).
- LinkedIn Playwright: construct the `SessionSource` (pass config `profile_dir`/`lock_path`/`tier2_cap` + a real sleeper); ScanService drives discover+enrich.
- `--source all` includes only sources with `enabled = true` in config (skip disabled, log which were skipped — no silent omission).
- The `--source` DEFAULT stays `"mock"` (unchanged from Phase 2/3); Phase 4 only adds choices. Switching the default to `ats`/`all` is deferred to a later phase.

`cli/login.py`: register a new `login` command in `cli/__init__.py` (`cli.add_command(login)`). `jobfeed login linkedin` opens a headed `async_playwright` persistent context at `SourcesLinkedInConfig.profile_dir` (the SAME field the SessionSource reads, so the session loads for headless scans), navigates to the LinkedIn login page, and waits for the user to press Enter before closing. Since `_run` is async, do NOT call blocking `input()` directly on the event loop — use `await asyncio.to_thread(input, prompt)` (or `loop.run_in_executor`). Mirrors legacy `open_login_browser`.

**Acceptance criteria:**
- [ ] `jobfeed scan --source speedyapply|indeed|linkedin-jobspy|linkedin` each runs that source
- [ ] `jobfeed scan --source all` runs all `enabled=true` sources and logs skipped (disabled) ones
- [ ] `jobfeed scan --source mock|ats` unchanged
- [ ] `--source all` with ATS + SpeedyApply both enabled closes BOTH httpx clients (AsyncExitStack owns all resources; asserted no leak/overwrite)
- [ ] `login` is registered in the cli group; `jobfeed login linkedin` opens a headed browser at the configured `profile_dir` and persists the profile on exit
- [ ] login's Enter-wait does not block the event loop (uses `to_thread`/executor)
- [ ] `--help` shows updated choices
- [ ] All committed

---

## Task 8: Integration Tests (PG + mocked IO, incl. twin scenario)

**Files:**
- Create: `tests/integration/test_phase4_scan_chain.py`
- Create: `tests/contract/test_source_dto_contract.py`
- Create fixtures: `tests/fixtures/speedyapply_readme.md`, `jobspy_indeed_rows.json`, `jobspy_linkedin_rows.json`, `linkedin_search_page.html` (INTERACTIVE — embedded JS that injects the right-pane JD on card click and appends cards on scroll, per Task 5's DOM contract; NOT a static capture), `linkedin_detail_page.html`

**What to build:**
All scenarios run through `ScanService.run()` against a real PG (testcontainers, `@pytest.mark.postgres`) with HTTP/JobSpy/Playwright mocked.

- **Scenario 1 — SpeedyApply happy path:** mock README + vendor JD endpoints (respx) → rows saved with JD, correct `platform`/`enrich_source`.
- **Scenario 2 — Indeed JobSpy:** mock `jobspy.scrape_jobs` → rows saved, `posted_at` from `dateOnIndeed`.
- **Scenario 3 — LinkedIn SessionSource happy path** (`@pytest.mark.postgres` AND `@pytest.mark.browser` — drives the real async_playwright LinkedInSource): interactive fixture pages → discover-inline JD saved; a gap posting enriched via Tier 1.
- **Scenario 4 — needs_reauth:** LinkedIn discover returns `needs_reauth=True` → 0 LinkedIn jobs, `PipelineRun.errors` incremented by 1 (via `handle_source_fetch_error`), other sources in the same run still saved, `run()` does not raise.
- **Scenario 4b — session-open failure:** LinkedIn `enrich_session()` raises `EnrichLocked` → 0 LinkedIn jobs, recorded via the error handler, sibling sources still complete and save, `run()` does not raise.
- **Scenario 5 — cross-source twin:** seed/scan the SAME job via a `greenhouse` (full JD) row and a `speedyapply` (unrouted → MISSING JD) row → both rows persisted (no scan-time dedup) AND `cluster_twins` over `store.list_jobs()` yields ONE cluster whose representative is the `greenhouse` full-JD row (exercises the quality key with a real quality differential). Add a second case where both have equal quality but different platforms to pin the source-priority tiebreak.
- **Scenario 6 — idempotent re-scan:** second run upserts (no duplicate rows; `jobs_updated > 0`).
- **Scenario 7 — mixed success/failure containment:** one source raises internally per-item, others succeed; `PipelineRun.errors == 0` for contained PER-ITEM failures (distinct from Scenario 4's source-level reauth which DOES increment errors).

Contract tests: each new source parses its frozen fixture to exact `JobPosting` field values (regression guard), using a fixed `discovered_at`.

**Acceptance criteria:**
- [ ] All 8 scenarios (1–7 + 4b) pass with real PG + mocked IO
- [ ] Twin scenario: all source rows persisted; `cluster_twins` folds to one cluster with the correct representative (quality case + source-priority case)
- [ ] DTO contract tests fail if a source's field mapping changes
- [ ] Idempotent re-scan upserts, no duplicates
- [ ] `needs_reauth` and `EnrichLocked` are contained at source level (run not aborted, siblings saved) and DO increment `PipelineRun.errors`; per-ITEM enrich failures are contained and do NOT increment `errors`
- [ ] Tests carry `@pytest.mark.postgres`; the LinkedIn scenarios (3, and 4's authwall discover) also carry `@pytest.mark.browser` and run in the CI browser lane (Chromium installed); committed

---

## Task 9: Live Smoke Tests + CI Update

**Files:**
- Create: `tests/live/test_phase4_live_smoke.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `pyproject.toml` (pytest markers / Playwright install in CI live lane only)

**What to build:**
`@pytest.mark.live` smoke tests (manual only, never in CI): SpeedyApply fetches the real README and routes ≥1 JD; Indeed/LinkedIn JobSpy scrape a small real query; LinkedIn Playwright `discover` against a real search URL returns ≥1 posting with inline JD (requires a logged-in profile; skip with a clear message if absent).

**`browser` marker (REQUIRED — the Playwright tests genuinely launch Chromium).** Any test that drives the real `LinkedInSource` runs a real browser: the LinkedIn unit tests (`test_linkedin_discover.py`, `test_linkedin_enrich.py`) and the integration LinkedIn scenarios (Task 8 Scenario 3, and 4's discover/authwall — 4b's `EnrichLocked` fires pre-browser so it alone does not). Mark all of these `@pytest.mark.browser` (the integration ones are `postgres` AND `browser`). Set `addopts = "-m 'not postgres and not live and not browser'"` so `make quality` collects NONE of them and stays browser-free. Register `browser` (and `live`) markers in `pyproject.toml`.

CI: three lanes — (1) `quality-gate` = `make quality` (no PG, no net, no browser); (2) `postgres-tests` = `pytest -m 'postgres and not browser'` (PG, no browser); (3) NEW `browser-tests` = `playwright install chromium` + start PG + `pytest -m browser` (covers LinkedIn unit + LinkedIn integration scenarios). `python-jobspy` installs in all lanes (it's a runtime dep, no browser). Live tests stay excluded from every CI lane.

**Acceptance criteria:**
- [ ] Live smoke tests pass when run manually (`pytest -m live`) with a logged-in LinkedIn profile
- [ ] Live tests skipped by default and excluded from CI
- [ ] LinkedIn Playwright tests carry `@pytest.mark.browser`; `make quality` and `pytest -m 'postgres and not browser'` collect NO browser test and run with NO Chromium installed
- [ ] `browser`/`live` markers registered in `pyproject.toml`; `addopts` excludes `postgres`, `live`, AND `browser`
- [ ] CI `browser-tests` lane installs Chromium (`playwright install chromium`) + PG and runs `pytest -m browser` green
- [ ] All committed

---

## Task 10: Verification + Phase 4 Acceptance

**Files:** None new. Verification-only.

**What to verify:**
1. **Five sources scan:** `mock`, `ats`, `speedyapply`, `indeed`, `linkedin-jobspy`, `linkedin` each run via `jobfeed scan --source <name>` (LinkedIn live is manual); `--source all` runs the enabled subset.
2. **SessionSource orchestration:** ScanService drives discover→enrich for LinkedIn; SimpleSource path unchanged.
3. **Architecture boundaries:** `domain/dedupe.py` and `domain/normalize.py` import nothing from `adapters/`/`ports/store*`; `services/scan.py` depends only on the `SimpleSource`/`SessionSource` protocols; pandas/jobspy/tls-client confined to `_jobspy.py`; Playwright confined to the `linkedin.py`/`_linkedin_*` module set + `cli/login.py`.
4. **Twin/dedupe:** same job from N sources → N DB rows; `cluster_twins` folds to one cluster; representative is highest-JD-quality.
5. **Protocol compliance:** the three SimpleSources satisfy `SimpleSource`; LinkedIn satisfies `SessionSource`.
6. **Test coverage:** `make quality` (unit+contract, no PG/net/browser), `pytest -m 'postgres and not browser'` (non-browser integration), `pytest -m browser` (LinkedIn unit + integration, needs Chromium + PG), `pytest -m live` (manual) all pass.
7. **LLM backend choice (Decision 9 / Task 11):** `codex-cli`, `claude-cli`, `openai-compat`, and `mock` all build via `build_llm_client`; `openai-compat` drives ≥2 mocked providers by `base_url` alone in tests, and missing `api_key_env` raises `LLMRuntimeUnavailable` before any network call.

**Acceptance criteria (Phase 4 milestone):**
- [ ] `CLAUDE.md` Constraints amended (human-approved "Phase 4 amendment") to permit Playwright browser automation for the LinkedIn SessionSource AND the amendment is COMMITTED (not left as an uncommitted working-tree change); Phase 4 plan added to the enumerated phase-plan source-of-truth list; no source reads/writes `~/.jobfeed/` (cookie profile + lock default under `~/.cache/jobfeed/`)
- [ ] All new sources scan end-to-end into PG via `ScanService.run()` with mocked IO in tests
- [ ] SessionSource orchestration works; MockSource/ATS paths unbroken
- [ ] Architecture boundary checks pass (no layer/dependency leaks)
- [ ] Twin clustering + representative selection verified on cross-source data
- [ ] `make quality` (browser-free), `pytest -m 'postgres and not browser'`, and the CI `browser-tests` lane (`pytest -m browser` with Chromium) all pass; live smoke documented as manual
- [ ] `openai-compat` LLM backend builds and runs against a mocked provider; CLI and API LLM paths are both selectable via `backend/model` config (Decision 9 / Task 11)
- [ ] All committed

---

## Task 11: OpenAI-Compatible LLM Backend (`openai-compat`) — independent of the 4a/4b source split

**Files:**
- Create: `src/jobfeed/adapters/llm/openai_compat.py` — `OpenAiCompatLLM(LLMClient)`
- Modify: `src/jobfeed/adapters/llm/_factory.py` — add the `backend == "openai-compat"` branch + a new `_require_api_key`
- Modify: `src/jobfeed/config.py` — `LLMSettings`: add `openai_compat_base_url`, `openai_compat_api_key_env`, `openai_compat_timeout_s` (mirror the existing `codex_timeout_s` / `claude_timeout_s`)
- Modify: `pyproject.toml` — add the `openai` SDK runtime dep (lazy-imported inside the adapter)
- Modify: `config.example.toml` — commented `openai-compat` presets (deepseek / minimax / openai / local Ollama) + document env-var key handling
- Test: `tests/unit/test_openai_compat_llm.py` (create), `tests/unit/test_llm_factory.py` (extend)

**What to build:**
`OpenAiCompatLLM` implements `LLMClient.complete(request)` and takes an INJECTED async client: `def __init__(self, *, client, model, price_table, logger)` — 4 args, within the ≤5 max-args gate; the SDK client is built by the factory (below), not here. The adapter module imports `openai` ONLY under `if TYPE_CHECKING:` (annotate `client: AsyncOpenAI`), so `import openai_compat` succeeds with the SDK absent AND tests inject a fake client exposing an async `chat.completions.create` (no monkeypatching the SDK). This injection design is what keeps the adapter SDK-free, ≤5 args, and trivially mockable. **The SDK's built-in `timeout`/`max_retries` are configured at client-construction time in the factory; do NOT wrap with the subprocess `run_with_retry` helper** — that is CLI-only.

In `complete`: map `request.messages` straight to OpenAI messages — `[{"role": m.role, "content": m.content} for m in request.messages]` — because `Message.role` is ALREADY LCD-constrained to `system`/`user`/`assistant` at the domain layer (`models_llm.py`), and the eval emits `[system, user]` exactly like the CLI adapters (which hard-index `messages[0]`/`messages[1]`). Pass `model=request.model`, `temperature=request.temperature`, `max_tokens=request.max_tokens` straight through (works for the recommended chat models — gpt-4o-class / deepseek / minimax). NB: OpenAI reasoning models (o1/o3) reject `temperature != 1` and require `max_completion_tokens`, but those go through `codex-cli`, not `openai-compat` — do NOT add per-model param remapping here (out of LCD scope; documented, not built). **Do NOT translate `request.response_schema` into OpenAI `response_format`/`json_schema`** — neither CLI adapter enforces the schema natively; both rely on prompt-instructed JSON + the `ScoringParseError` retry (Phase 3 Decision 6). Emitting `response_format` is vendor-specific and breaks LCD portability across DeepSeek/MiniMax/local, so it is forbidden here (the schema stays advisory, parsed/retried downstream as today).

Call `chat.completions.create(...)`, measure wall-clock latency with `time.monotonic()`, parse `choices[0].message.content` → `content`; `usage.prompt_tokens`/`usage.completion_tokens` → `input_tokens`/`output_tokens`, **guarding omitted `usage` with `0`** (some local providers, e.g. Ollama, return none — mirror codex's `int(usage.get(..., 0))`); set `cached` best-effort from `usage.prompt_tokens_details.cached_tokens > 0` when present, else `False`. Cost: reuse `_pricing.estimate_cost` / `TokenUsage` (same as codex), but **guard `request.model not in price_table` → `cost_usd=None` BEFORE calling `estimate_cost`**. Rationale: `estimate_cost` returns `0.0` (+ a warning) on a miss — passing that straight through would misrepresent *unknown* cost as *free* and silently fool the dollar budget gate; the adapter instead sets `cost_usd=None` so "unknown" is distinguishable from "zero". The vendored LiteLLM table is OpenAI/Anthropic-centric, so non-OpenAI models (deepseek/minimax/local) typically miss → cost `None`; that is the expected Decision 9 behavior, not an error. The `openai` SDK and its types never escape this module.

`_factory.build_llm_client`: add `if backend == "openai-compat":` → call `_require_api_key(settings.openai_compat_api_key_env)`, then lazy-import `from openai import AsyncOpenAI` INSIDE the branch (`# noqa: PLC0415` — keeps the factory module import SDK-free, like the existing CLI adapter imports), build `client = AsyncOpenAI(base_url=settings.openai_compat_base_url, api_key=os.environ[settings.openai_compat_api_key_env], timeout=_resolve_timeout(settings.openai_compat_timeout_s, opts), max_retries=opts.max_retries)`, and return `OpenAiCompatLLM(client=client, model=model_name, price_table=price_table, logger=logger)`. `_require_api_key(env_name)` raises `LLMRuntimeUnavailable` with a teaching message when `os.environ.get(env_name)` is empty — parallel to `_require_executable`, raised BEFORE the SDK import / any network call. Do NOT add an `openai-compat` analogue of `_require_codex_pricing` (Decision 9: pricing is best-effort, not required).

**Acceptance criteria:**
- [ ] `openai-compat/<model>` spec builds `OpenAiCompatLLM`; an empty/absent `api_key_env` → `LLMRuntimeUnavailable` with a teaching message (parallels `_require_executable`), raised BEFORE any network call
- [ ] One adapter drives ≥2 mocked providers by `base_url` alone (e.g. OpenAI + DeepSeek + MiniMax endpoints) — no per-provider code path
- [ ] `request.messages` mapped directly to OpenAI messages (roles already LCD-constrained by `Message`); `request.response_schema` is NOT translated into `response_format`/`json_schema`, and NO vendor-specific fields appear on the captured request (asserted)
- [ ] Uses the `openai` SDK's built-in `timeout`/`max_retries`; the subprocess `run_with_retry` helper is NOT used
- [ ] `usage` parsed into the `LLMResponse`; cost estimated when the model is in `model_prices.json`, and `None` + one logged warning when absent — the call still returns (does NOT hard-fail like codex)
- [ ] Omitted `usage` (provider returns none, e.g. local Ollama) → `input_tokens`/`output_tokens` default to `0` and the call still returns (no crash); `cached` best-effort from `prompt_tokens_details.cached_tokens`
- [ ] Price lookup guards `model not in price_table` → `cost_usd=None` (NOT `estimate_cost`'s `0.0`-on-miss, which would misrepresent unknown cost as free); priced model → the `estimate_cost` value (regression test covers both)
- [ ] Dollar budget gate (Phase 3 Decision 7) degrades to call-count-only for an unpriced model; the call-count gate still trips (regression test)
- [ ] Single `openai_compat_base_url`: all `openai-compat/*` specs (both stages) target ONE provider; cross-provider `stage_a`/`stage_b` over `openai-compat` is documented as out of Phase 4 scope (not built)
- [ ] API key read from `api_key_env`; the key value never appears in config files or logs
- [ ] `openai` imported only in the factory branch + under `TYPE_CHECKING` — `import openai_compat` and `import _factory` both succeed with the SDK absent (mocked-absence test); the adapter takes an INJECTED client (`__init__(*, client, model, price_table, logger)`, ≤5 args), and the unit tests pass a fake client (no real SDK, no monkeypatch)
- [ ] `config.example.toml` ships commented `openai-compat` presets for deepseek / minimax / openai / local (Ollama), and documents that only the env-var NAME goes in config
- [ ] `config.example.toml` recommends the low-latency pair (`codex-cli` / `openai-compat` on a fast hosted provider) as the default `stage_a`/`stage_b`, with `claude-cli` + the Anthropic compat endpoint shown as commented higher-latency alternatives (per Decision 9; cites the Phase 3 Decision 5 P99 gap)
- [ ] `isinstance(OpenAiCompatLLM(...), LLMClient)` is True
- [ ] `openai_compat.py` is ≤300 lines (the `adapters/llm/` layer is NOT exempt from the code-hygiene gate)
- [ ] Tests mock the SDK/HTTP (no real API calls); committed
```
