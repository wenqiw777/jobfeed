# Phase 8: Web API + Web UI — Design & Implementation Plan (merged)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement Part B task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the rewrite's Web UI: a six-zone local web app (Triage / Pipeline / Library / Insights / Runs / Sources) over a FastAPI thin shell, so daily triage, application tracking, and ATS-company expansion all happen in the browser — while scan/evaluate execution stays in the CLI until Phase 9.

**Architecture:** Hexagonal, same as Phases 0–7. New `src/jobfeed/web/` (FastAPI thin shell: routes parse/format only, services injected at the boundary, config never crosses into services) + new `web-ui/` React SPA (full rewrite; legacy `job-apply/web-ui/` is a **semantic reference only** — no code migration). One new service (`jobs_view`), one new store query surface (jobs view + insights + run listing), one domain addition (status-priority display fold layered onto `domain/dedupe.py`).

**Tech Stack:** Backend: FastAPI + uvicorn + python-multipart over the existing asyncpg/Postgres store. Frontend: React 19 + Vite + TypeScript + Tailwind + TanStack Query + TanStack Virtual + react-router SPA + shadcn/ui (vendored) + recharts; types generated from a committed OpenAPI snapshot via openapi-typescript; Geist/Geist Mono self-hosted via fontsource.

**Implementation repo:** `/Users/wenqiwang/wwq/jobfeed` (NOT legacy `job-apply`). Bare paths below are rooted there. Mirror a copy into `docs/plans/` after saving; `PRODUCT.md` + `DESIGN.md` (product/brand + visual tokens) are copied to the repo root and are the design source of truth for Part B.
**Commit strategy:** one conventional commit per task, no AI attribution. Two PR slices: **PR-A = Tasks 1–7 (backend)**, **PR-B = Tasks 8–12 (frontend)**.
**Execution mode:** Sequential.

> **History:** Designed 2026-06-11 via brainstorm (visual directions + IA explored in-browser with the user; all decisions user-confirmed). Code verification on 2026-06-11 corrected the standalone spec in two places (see D6, A2); this file supersedes the deleted standalone spec (`docs/superpowers/specs/2026-06-11-jobfeed-rewrite-phase8-web-api-ui-design.md`). Companion product/visual docs: `PRODUCT.md`, `DESIGN.md`.

---

# PART A — DESIGN

## A1. Positioning

Phases 0–7 built the pipeline, the status/apply slice, and full CLI parity (PR #15, merged 2026-06-11). Phase 8 is the **human-interface** slice: the web app becomes the daily driver for deciding and tracking, and — the user's stated pain point — for **expanding ATS company coverage without CLI ceremony** (paste a list → probe → confirm). Running the pipeline stays in the CLI; Phase 9 (Temporal) adds web triggering on top of this phase's read-only Runs zone.

## A2. Current State (verified in code 2026-06-11)

**Already implemented — REUSE, do not recreate:**
- Phase 7 CLI surface is merged and registered: `followup`, `companies` (add/list/remove), `bootstrap_companies`, `enrich_paste`, `interview`, `snapshots`, plus list/digest/apply upgrades (`cli/__init__.py:212-231`).
- App assembly is reusable: `create_app(config_path) -> AppContext` (`cli/__init__.py:35`) and `_create_store(settings)` (`cli/__init__.py:191`) do config→adapters→services wiring; the web app factory wraps the same path.
- Companies store layer complete incl. bulk: `upsert_company/get_company/list_companies/mark_company_removed` + `bulk_insert_companies` (`postgres.py:1676,4279-4365`). ATS probe shared at `adapters/sources/_ats_probe.py` (`probe_company`, PROBE_ORDER greenhouse/ashby/lever).
- Workflow/application services cover every write the web needs: `transition/transition_bulk/restore/note/set_followup/add_interview_round/list_interview_rounds/complete_interview_round/attention/auto_decay` (`services/workflow.py`), `apply/apply_history/stats/snapshots_*` (`services/application.py`). `ApplyRequest` fields are **content-based strings** (master/tailored resume, cover letter — `services/application.py`), so multipart uploads map directly; the web route mirrors `cli/apply.py`'s request assembly (incl. verdict/fit/hooks snapshots, reapply notice).
- Twin primitives: `domain/dedupe.py` (`twin_key`, `cluster_twins`, `pick_representatives`, `_representative_sort_key`) — the Phase 4/5 scoring-side fold. `domain/filtering.py` (hard filters), `domain/quality.py` (JD quality), `normalize.py`, `company_norm/title_norm` columns all exist.
- `get_pipeline_run(run_id)` / `record_pipeline_run` exist (`postgres.py:2772-2803`); `PipelineRun` is a **flat counters object** (`domain/models.py:184`: run_id, started_at, source, jobs_discovered/inserted/updated/filtered/ml_gated, stage_a_scored, stage_b_scored, …).
- Attention trio wired by Phase 7 with CLI callers: `workflow_attention` (`ports/store_status.py:115`), `needs_attention` (`ports/store_ops.py:215`), `compute_reapply_notice`.
- Legacy `job-apply/web-ui/` carries the semantic reference + vitest suites for triage filtering, auto-advance, bulk feedback, selection (`Today.tsx`, `use-selection.ts`, `AllPostings.test.tsx` et al.).

**Corrections vs the standalone spec (code wins):**
- **No `pipeline_steps` or `source_health_daily` tables exist** (design-spec aspiration, never built). Runs zone scope corrected in D6.
- Jobs listing today is thin (`list_jobs(limit)`, `list_evaluated_jobs(limit)`, `list_statuses(StatusFilter)`); the web's filtered/folded/paginated jobs view is **net-new** (D10).

**Net-new (CREATE):** `src/jobfeed/web/` package + `cli/serve.py`; `services/jobs_view.py` + `services/insights.py`; ports/store: `query_jobs_view`, `list_pipeline_runs`, `insights_overview`; domain: `pick_display_representatives` (status-priority fold), `JobsViewQuery/Page` + `InsightsOverview` models; `web-ui/` SPA; OpenAPI snapshot + contract test; fastapi/uvicorn/python-multipart deps.

## A3. Decisions (all user-confirmed during brainstorm)

- **D1 — Boundary is "方案 2.5": Sources full, Runs read-only, no web triggering.** Web = decide + track + manage sources. scan/evaluate stay CLI; the Runs zone shows history only. Web triggering + live progress arrive with Phase 9 Temporal (no 202/poll protocol stub needed now). Motivation: the user's real pain is ATS-company expansion, which needs no background runner — probe is fast HTTP.
- **D2 — No auth; loopback only.** `serve` binds `127.0.0.1` and **refuses** a non-loopback `--host` with an error naming the future network-mode/auth work. Zero auth code this phase.
- **D3 — Evolve the stack, don't replace it.** Keep React 19 + Vite + Tailwind + TanStack Query + react-router SPA. Add shadcn/ui (vendored, restyled to DESIGN.md tokens), TanStack Virtual, recharts. NO Next.js (no SSR need, would duplicate FastAPI), NO global state library (TanStack Query + local hooks suffice).
- **D4 — Visual system = `DESIGN.md` (graphite & cobalt).** Geist single family + Geist Mono for data; compact 32px single-line rows default with a persisted comfortable 46px toggle; impeccable absolute bans enforced (no side-stripes, no gradients, no warm-cream bg, contrast ≥4.5:1); light theme only, tokens structured as semantic CSS variables for a future dark theme.
- **D5 — IA is the two-loop six-zone layout.** Triage (Queue + Pending JD) / Pipeline (Applied+Follow-ups merged, attention bar on top) / Library (former All) / Insights (former Stats) / Runs / Sources. Every rewrite-spec §15 product semantic is preserved; only page organization changes (jobfeed-repo spec §15 gets a small docs amendment with the zone mapping — fold into the PR-A docs commit).
- **D6 — Runs zone = `pipeline_runs` counters only (spec drift corrected).** List + detail of recorded runs. "Source health" is served by companies failure counts (Sources zone) — no steps table, no health rollup table, nothing invented. Platform login status (LinkedIn cookie age) is **cut** (belongs with CLI `login`, A5).
- **D7 — Type contract = committed OpenAPI snapshot.** `make web-schema` dumps the FastAPI schema to `src/jobfeed/web/openapi.json` (committed). `tests/contract/test_web_openapi.py` asserts the live app schema equals the snapshot (failure message says how to regenerate). The frontend generates `types.gen.ts` from the committed snapshot via `openapi-typescript` in predev/prebuild. (Supersedes the spec's pydantic2ts suggestion: same lock, maintained tooling, no extra Python dep.)
- **D8 — Apply = multipart upload → content strings.** Route reads uploaded files to UTF-8 strings, builds `ApplyRequest` exactly as `cli/apply.py` does (master resume from settings-resolved content at the boundary; evaluation-derived snapshots; method/notes/variant passthrough; reapply notice in the response).
- **D9 — Display fold = status-priority key layered AHEAD of the existing representative key.** New pure `pick_display_representatives(jobs, status_by_id)` in `domain/dedupe.py`: priority class ({applied, interviewing, offer} > shortlisted > everything else), tie-break = existing `_representative_sort_key`. Scoring-side `pick_representatives` is untouched (Phase 5 contract test keeps guarding it). Fold defaults: ON for Triage queue, OFF for Library.
- **D10 — Jobs view = bounded SQL prefilter, then domain post-processing.** SQL narrows by status set / freshness / search / pending-JD predicate; service applies hard filters (`domain/filtering.py`) + fold (D9) + verdict-group sort, then paginates in memory with true totals + tab counts. Justified: triage corpora are 10²-scale. Library's unfolded "All" tab paginates in SQL.
- **D11 — Fonts self-hosted** (fontsource Geist + Geist Mono). Local-first tool: no runtime Google CDN dependency.
- **D12 — Followup API takes an ISO datetime.** The web sends a computed timestamp (frontend offers 7d/2w presets); `cli/_window.py` stays CLI-owned.

## A4. Surface Delta

**New CLI:** `serve [--host 127.0.0.1] [--port 7654]` — uvicorn, serves `/api/*` + the built SPA (`web-ui/dist`) with SPA fallback.

**HTTP API (all under `/api`, JSON; errors are `{error:{code,message,request_id}}`):**

| Route | Backs | Notes |
|---|---|---|
| `GET /health` | liveness | store ping |
| `GET /jobs` | Triage/Library lists | params: statuses, tab(`queue\|pending_jd\|all\|scored\|shortlisted\|archived`), search, posted_within_days, require_verdict, apply_hard_filters, dedupe, sort, limit, offset → `{jobs, total, tab_counts}` |
| `GET /jobs/{id}` | detail pane/drawer | job + evaluation (Stage A/B/final + Stage B blocks) + status/history/notes + twins(platform+status) + interviews + snapshot refs |
| `POST /jobs/{id}/transition` | status actions | `{to, note?, force?}`; illegal transition → 409 |
| `POST /jobs/bulk/transition` | bulk bar | `{items:[{id,to}], force?}` → `{succeeded, skipped, failed, cascaded}` |
| `POST /jobs/{id}/note` · `POST /jobs/{id}/followup` | notes, follow-up | followup `{at: ISO}` (D12) |
| `POST /jobs/{id}/jd` | Pending JD paste | `{text}` → `enrich_paste`; returns assessed quality |
| `POST /jobs/{id}/apply` | apply dialog | multipart: tailored?, cover_letter?, variant?, method?, notes? (D8) |
| `GET /jobs/{id}/interviews` · `POST …/interviews` · `PATCH …/interviews/{index}` | interview rounds | add `{label, scheduled_at?}` / complete `{notes?}` |
| `GET /attention` | Pipeline action bar | workflow_attention buckets + needs_attention |
| `GET /applications` | apply history | limit param |
| `GET /insights/overview?window=N` | Insights | KPIs, verdict & status distributions, daily series, by-resume table |
| `GET /runs` · `GET /runs/{run_id}` | Runs zone | newest-first run counters (D6) |
| `GET /companies` · `POST /companies` · `POST /companies/bulk` · `DELETE /companies/{slug}` | Sources | list incl. failure counts / add / confirmed bulk insert / soft remove |
| `POST /companies/probe` | Sources add flow | `{entries:[str]}` → per-entry `{input, slug, vendor\|null, error\|null}`; bounded concurrency; **no writes** |

**Web zones (semantics inherited from rewrite-spec §15):** Triage·Queue = legacy `/today` semantics (status new/scored/shortlisted, hard filters, freshness incl. tier-aware non-apply gate, verdict sort, fold, decide→auto-advance). Triage·Pending JD = legacy Pending-JD tab semantics (inadequate/missing JD, no Stage A score, excl. archived/ignored, hard filters + freshness; paste returns row to the scored flow). Pipeline = `/applied` ∪ `/followups` + attention bar + interview rounds. Library = `/all` minus Pending JD, tabs All/Scored/Shortlisted/Archived, search/sort/pagination, counts sync. Insights = `/stats` views (KPI, funnel, donut, timeline, by-resume). Runs/Sources per D6/D1.

## A5. Out of Scope (deferred)

Web triggering of scan/evaluate + live progress (Phase 9); LinkedIn anything-web (login/scan/cookie status stay CLI); dark theme (tokens ready, not built); network mode + auth; command palette; digest page (digest remains a CLI/file artifact; Triage is its live form); full WCAG audit (contrast + keyboard main path ARE in scope per PRODUCT.md); `mark <url>` token resolution and other Phase-7 A5 leftovers; migrating legacy web-ui code.

---

# PART B — IMPLEMENTATION PLAN

## File Structure

```
src/jobfeed/
├── web/ (NEW)
│   ├── __init__.py · app.py (factory; reuses create_app assembly) · deps.py (DI accessors)
│   ├── errors.py (error shape + request_id middleware) · schemas.py (Pydantic DTOs)
│   ├── openapi.json (committed snapshot, T7)
│   └── routes/{__init__,health,jobs,workflow,applications,insights,runs,companies}.py
├── cli/serve.py (NEW) + cli/__init__.py (MOD: register serve)
├── services/{jobs_view.py (NEW), insights.py (NEW)}
├── domain/{dedupe.py (MOD: display fold), models_views.py (NEW: JobsViewQuery/Row/Page, InsightsOverview)}
├── ports/store_views.py (NEW: query_jobs_view, list_pipeline_runs, insights_overview)
└── adapters/store/postgres.py (MOD: implement the three queries)
scripts/dump_openapi.py (NEW) · Makefile (MOD: web-schema) · pyproject.toml (MOD: fastapi, uvicorn, python-multipart)
tests/web/ (NEW: route tests, ASGI client, @postgres) · tests/unit/test_display_fold.py · tests/contract/test_web_openapi.py
web-ui/ (NEW SPA)
├── package.json · vite.config.ts (proxy /api→:7654) · tailwind.config.ts (DESIGN.md tokens) · index.html
└── src/
    ├── main.tsx · App.tsx (routes: /triage /pipeline /library /insights /runs /sources)
    ├── api/{client.ts, types.gen.ts (generated), queries.ts (typed hooks + param builders)}
    ├── lib/{density.tsx, use-selection.ts, keyboard.ts, dates.ts}
    ├── components/{shell/*, jobs/* (JobRow, JobList, DetailPane, ApplyDialog, BulkBar, VerdictPill), ui/* (shadcn vendored)}
    └── routes/{triage,pipeline,library,insights,runs,sources}.tsx (+ colocated *.test.tsx)
```

## Task 1: Web skeleton + `serve`
**Files:** Create `web/{__init__,app,deps,errors}.py`, `web/routes/{__init__,health}.py`, `cli/serve.py`; Modify `cli/__init__.py`, `pyproject.toml`; Test `tests/web/test_app_skeleton.py`.
**What to build:** FastAPI factory `create_web_app(config_path=None)` reusing the `create_app` assembly (store/services built once at startup via lifespan, mirroring `run_with_store`'s store lifecycle). `errors.py`: middleware assigning a per-request `request_id` (logged via structlog on every request) and handlers converting expected exceptions + 404/422 into `{error:{code,message,request_id}}` — no HTML error pages, no tracebacks in responses. `GET /api/health` returns `{status, db}` with a store roundtrip. `serve` command: `--host` (default 127.0.0.1, non-loopback values rejected with a clear error, exit nonzero), `--port` (default 7654); registered in the CLI group.
**Acceptance:**
- [ ] `/api/health` 200 with db ok against ephemeral PG (`@postgres`)
- [ ] Unknown `/api/*` path → JSON error shape carrying a request_id that also appears in logs
- [ ] `serve --host 0.0.0.0` exits nonzero naming the loopback-only policy; default binds 127.0.0.1
- [ ] Services are constructed once per process, not per request
- [ ] All tests pass, committed

## Task 2: Jobs view query + display fold
**Files:** Create `domain/models_views.py`, `ports/store_views.py`, `tests/unit/test_display_fold.py`, `tests/integration/test_jobs_view_store.py` (`@postgres`); Modify `domain/dedupe.py`, `adapters/store/postgres.py`.
**What to build:** (1) `JobsViewQuery` (statuses, tab, search, posted_within_days, require_verdict, limit, offset) / `JobsViewRow` (job core + company_norm/title_norm + status + verdict + stage scores + posted/scraped + jd_quality) / `JobsViewPage` (rows, total, tab_counts). (2) `query_jobs_view` in postgres: one SELECT joining jobs+evaluations+job_status with SQL predicates for statuses / freshness / case-insensitive search on company+title / the pending-JD predicate (JD quality inadequate per `domain/quality.py` constants AND no Stage A score AND status ∉ {archived, ignored}); returns bounded row sets plus per-tab counts. (3) `pick_display_representatives(rows) -> list` in `domain/dedupe.py` per D9 — pure, status-priority class ahead of the existing `_representative_sort_key`; `pick_representatives` untouched.
**Acceptance:**
- [ ] Pending-JD predicate pinned with one fixture row per exclusion reason (adequate JD / has Stage A / archived / ignored)
- [ ] Search matches "stripe" against company "Stripe"; statuses + posted_within AND-compose
- [ ] Fold: an `applied` twin beats a higher-`_representative_sort_key` twin; with equal status class the existing key decides (both directions pinned)
- [ ] `tab_counts` reflect the same predicates as their tabs
- [ ] All tests pass, committed

## Task 3: Jobs routes + jobs_view service
**Files:** Create `services/jobs_view.py`, `web/schemas.py`, `web/routes/jobs.py`; Modify `web/app.py`; Test `tests/web/test_jobs_routes.py` (`@postgres`).
**What to build:** Service composes: store `query_jobs_view` → optional hard filters (`domain/filtering.py`) → optional fold (D9) → verdict-group sort (apply → consider → skip → below_threshold, Stage-B/final score desc within groups, unscored last) → in-memory pagination with true totals (D10). `GET /api/jobs` maps query params to `JobsViewQuery` + flags (`apply_hard_filters`, `dedupe`). `GET /api/jobs/{id}` aggregates detail: job, evaluation (incl. Stage B blocks: one-liner / strengths / gaps / cover-letter hooks / supporting points / avoid-mentioning — field names follow the domain evaluation model), status + history + notes, twins (same company_norm+title_norm, each with platform + status), interview rounds, snapshot refs from the application record. DTOs in `schemas.py` (split into a package if >300 lines).
**Acceptance:**
- [ ] Queue-tab request (statuses new/scored/shortlisted + hard filters + fold + require_verdict) returns the legacy-Today-equivalent set for a seeded fixture matrix
- [ ] Ordering pinned: verdict groups in order, score-desc inside
- [ ] Detail carries Stage B blocks, twins with platform+status, interviews, snapshot refs; unknown id → 404 in the error shape
- [ ] Routes contain no business logic (composition lives in `services/jobs_view.py`)
- [ ] All tests pass, committed

## Task 4: Workflow + application routes
**Files:** Create `web/routes/workflow.py`, `web/routes/applications.py`; Modify `web/schemas.py`, `web/app.py`; Test `tests/web/test_workflow_routes.py`, `tests/web/test_apply_routes.py` (`@postgres`).
**What to build:** Transition (single: `{to, note?, force?}` → `WorkflowService.transition`; illegal per `ALLOWED_TRANSITIONS` → 409 with code `illegal_transition`). Bulk: `{items, force?}` → `transition_bulk` → the four counters verbatim. Note, followup (ISO datetime, D12; missing status row → 404). JD paste: `{text}` → `enrich_paste` keyed by the job's platform+canonical_id (route resolves them via `get_job`); empty text → 422; response carries assessed quality. Interviews: GET list / POST add `{label, scheduled_at?}` / PATCH `{index}` complete `{notes?}`. Apply: multipart per D8 — uploads decoded UTF-8, master resume content resolved at the boundary from settings (same source as `cli/apply.py`), evaluation-derived snapshot fields, `{variant, method, notes}` form fields; response includes the reapply notice when non-None; re-applying an applied job returns the existing no-op parity result.
**Acceptance:**
- [ ] Illegal transition → 409 + code; legal one persists and shows in `GET /jobs/{id}` history
- [ ] Bulk over a twin cluster returns non-zero `cascaded` and the twin's status actually changed (`@postgres`)
- [ ] JD paste on a Pending-JD fixture removes it from the pending_jd tab on the next jobs query
- [ ] Apply multipart persists snapshots + audit + transition in one transaction (verified via detail + applications endpoints); second apply → no-op parity response
- [ ] Interview add/complete round-trips; completing an unknown index → 404
- [ ] All tests pass, committed

## Task 5: Insights + attention + runs (read-only)
**Files:** Create `services/insights.py`, `web/routes/{insights,runs}.py`, `tests/web/test_insights_runs_routes.py` (`@postgres`); Modify `domain/models_views.py` (InsightsOverview), `ports/store_views.py`, `adapters/store/postgres.py`, `web/schemas.py`, `web/app.py`.
**What to build:** Store: `list_pipeline_runs(limit, offset) -> (list[PipelineRun], total)` newest-first; `insights_overview(window_days) -> InsightsOverview` (totals: jobs/evaluated/applied; verdict distribution; status distribution; daily UTC series of scraped/evaluated/applied counts over the window). Service composes overview + `ApplicationService.stats(by_resume=True)` for the by-resume table. Routes: `GET /api/insights/overview?window=30`, `GET /api/attention` (`workflow_attention` buckets + `needs_attention`, store-default thresholds — same sources as the Phase 7 digest footer), `GET /api/runs` + `GET /api/runs/{run_id}` (404 unknown).
**Acceptance:**
- [ ] Overview math pinned against a seeded fixture (incl. day-bucket boundaries in UTC)
- [ ] Attention payload mirrors the digest footer's three buckets + needs_attention for the same fixture
- [ ] Runs list is newest-first with counters; pagination totals correct
- [ ] All tests pass, committed

## Task 6: Companies + probe routes
**Files:** Create `web/routes/companies.py`; Modify `web/schemas.py`, `web/app.py`; Test `tests/web/test_companies_routes.py`.
**What to build:** `GET /api/companies` (`?vendor`, `?include_removed`; rows carry slug/vendor/failure count/removed flag). `POST /api/companies` `{slug, vendor}` (vendor ∈ greenhouse/ashby/lever, else 422) → upsert. `POST /api/companies/bulk` `{rows:[{slug, vendor}]}` → `bulk_insert_companies`, returns inserted count. `DELETE /api/companies/{slug}` → soft remove, 404 when absent. `POST /api/companies/probe` `{entries:[str]}`: normalize each entry to a candidate slug (lowercase trim; extract the slug from greenhouse/ashby/lever board URLs when an URL is pasted), probe via `probe_company` under an `asyncio.Semaphore` (default 5), one entry's failure never aborts the batch; per-entry result `{input, slug, vendor|null, error|null}`; endpoint performs **no writes**. Transport behavior follows the probe module as-is (no new retry layer).
**Acceptance:**
- [ ] Probe over a mocked transport: mixed batch returns per-entry vendor/null+error; concurrency bound asserted via an instrumented mock; no DB writes occur
- [ ] URL-shaped entries for all three vendors resolve to slugs; junk entries come back with errors, not 500s
- [ ] bulk insert → list round-trip; `DELETE` then default list hides the row, `include_removed` shows it
- [ ] Invalid vendor → 422 in the error shape
- [ ] All tests pass, committed

## Task 7: OpenAPI contract + SPA static serving (closes PR-A)
**Files:** Create `scripts/dump_openapi.py`, `src/jobfeed/web/openapi.json`, `tests/contract/test_web_openapi.py`; Modify `Makefile` (target `web-schema`), `cli/serve.py` or `web/app.py` (static mounting); Test the contract test itself + `tests/web/test_static_serving.py`.
**What to build:** Per D7. Dump script renders the factory's OpenAPI schema deterministically (sorted keys) to the committed snapshot; contract test compares live vs committed and fails with "run `make web-schema` and review the diff". Static serving: when `web-ui/dist/` exists, serve it at `/` with SPA fallback (unknown non-`/api` paths → `index.html`); `/api/*` 404s stay JSON. Also fold the rewrite-spec §15 zone-mapping amendment (A4 table) into `docs/` in this commit.
**Acceptance:**
- [ ] Adding a field to any DTO without regenerating fails the contract test with the regeneration hint
- [ ] With a dist fixture: `/` serves index.html, `/triage` falls back to index.html, `/api/nope` stays JSON 404
- [ ] `make quality` green across PR-A
- [ ] All tests pass, committed

## Task 8: Frontend scaffold + app shell
**Files:** Create `web-ui/` (package.json, vite.config.ts with `/api` proxy to 127.0.0.1:7654, tsconfig, tailwind.config.ts, index.html, src/main.tsx, src/App.tsx, src/styles.css, src/api/{client,queries}.ts + generated types.gen.ts, src/lib/density.tsx, src/components/shell/*, src/components/ui/* vendored shadcn, vitest setup); Test `src/components/shell/Shell.test.tsx`, `src/lib/density.test.tsx`.
**What to build:** Vite React-TS scaffold with DESIGN.md tokens as Tailwind theme + semantic CSS variables; fontsource Geist + Geist Mono (no CDN); shadcn/ui vendored set (button, input, select, checkbox, tabs, dialog, sheet, dropdown-menu, tooltip, toast, skeleton) restyled to tokens; `openapi-typescript` generation from `../src/jobfeed/web/openapi.json` wired into predev/prebuild; typed API client honoring the error shape + TanStack Query hooks with **typed param builders** (no string-concatenated filters); app shell: sidebar (6 zones + counts from tab_counts/attention), top bar (title, meta, keyboard hints in mono), router with `/` → `/triage`; density provider (compact default, localStorage persistence, view-menu toggle).
**Acceptance:**
- [ ] Build green with generated types; type-check fails if types.gen.ts is stale vs a changed snapshot
- [ ] Shell renders zones + counts against a mocked API; density toggle switches row-height class and survives reload
- [ ] No external font/CDN requests (assets resolve locally)
- [ ] All tests pass, committed

## Task 9: Triage zone
**Files:** Create `src/routes/triage.tsx`, `src/components/jobs/{JobRow,JobList,DetailPane,ApplyDialog,BulkBar,VerdictPill,JdPasteCard}.tsx`, `src/lib/{use-selection.ts,keyboard.ts}` (+ colocated tests).
**What to build:** Queue + Pending JD tabs (Queue params per A4 zone semantics; Pending JD shows the paste card in detail). Split pane: virtualized list (TanStack Virtual) + persistent right detail. JobRow per DESIGN.md both densities (verdict pill, mono score/age; selection = bg tint + inset ring, **no side-stripes**). DetailPane sections: status actions, "also seen on" twins (platform + status), Stage A/B/final scores, Stage B blocks, notes, followup presets (7d/2w/custom date → ISO), ApplyDialog (file inputs tailored/cover letter + variant/method/notes → multipart; success toast incl. reapply notice when present). Keyboard (`keyboard.ts` hook): ↑↓ + j/k move, Enter focus detail, a=apply dialog, h=shortlist, s=skip(archive), n=note, f=followup, o=open posting URL; decided rows collapse (~180ms; instant under `prefers-reduced-motion`) and selection auto-advances to the next row in displayed order. Selection + BulkBar: per-row toggle, select-page, select-all-matching; bulk actions → bulk endpoint; toast renders all four counters. Mutations invalidate the jobs query; auto-advance re-seeds after refetch (legacy `onBulkCleared` semantics).
**Acceptance:**
- [ ] Rendered order mirrors API order; after a single-row decision the next row is selected (pinned like legacy `Today.test.tsx`)
- [ ] Bulk toast shows succeeded/skipped/failed/cascaded from a mocked response
- [ ] Keyboard map fires each action (hook unit tests); collapse animation disabled under reduced motion
- [ ] Pending JD: paste success removes the row (mocked refetch) and shows assessed quality
- [ ] Densities: 32px single-line and 46px two-line rows both render per tokens
- [ ] All tests pass, committed

## Task 10: Pipeline zone
**Files:** Create `src/routes/pipeline.tsx`, `src/components/jobs/{AttentionBar,InterviewPanel,StatusGroups}.tsx` (+ tests).
**What to build:** Attention bar from `GET /api/attention` (follow-up due / interview prep / going ghosted; amber family per DESIGN.md; each chip filters the list below; empty buckets hidden). Status-grouped list (applied / interviewing / offer / closed groups, collapsible, counts) reusing JobRow + DetailPane. InterviewPanel inside detail for interviewing jobs: list rounds, add (label + optional datetime), complete with notes. Followup picker and ghosted-restore (transition) wired in detail.
**Acceptance:**
- [ ] Groups + counts match fixture; chips filter and clear
- [ ] Interview add → appears in list; complete → marked done with notes (mocked API)
- [ ] A ghosted fixture exposes restore and fires the transition mutation
- [ ] All tests pass, committed

## Task 11: Library + Insights zones
**Files:** Create `src/routes/library.tsx`, `src/routes/insights.tsx`, `src/components/jobs/LibraryTable.tsx`, `src/components/insights/{KpiCards,SankeyFunnel,StatusDonut,DailyTimeline,ByResumeTable}.tsx` (+ tests).
**What to build:** Library: full-width virtualized table, tabs All/Scored/Shortlisted/Archived with live counts, debounced search, sort dropdown, server-side pagination (typed param builders); row click opens a Sheet drawer hosting the same DetailPane. Insights: recharts components styled from tokens over `GET /api/insights/overview` — KPI cards, funnel (discovered → gated → Stage A → Stage B → apply-verdict → applied), status donut, daily timeline, by-resume table.
**Acceptance:**
- [ ] Tab switch + search + sort + page compose into the expected query params (asserted via mock)
- [ ] Counts update with filters; drawer renders DetailPane for the clicked row
- [ ] Each chart renders from a fixture overview; empty window renders empty states, not crashes
- [ ] All tests pass, committed

## Task 12: Runs + Sources zones + final polish (closes PR-B)
**Files:** Create `src/routes/runs.tsx`, `src/routes/sources.tsx`, `src/components/sources/{CompaniesTable,ProbeFlow}.tsx` (+ tests); Modify empty/loading states across zones as found.
**What to build:** Runs: newest-first table (started_at mono, source, counter chips), expandable row for full counters; read-only, with a quiet hint that triggering arrives with Phase 9. Sources: CompaniesTable (slug/vendor/failure count/removed badge; disable-remove behind a danger dialog); single add (slug+vendor, or probe-one); ProbeFlow: textarea paste (one entry per line) → `POST /probe` → results table (resolved rows pre-checked, errors flagged) → confirm → `POST /companies/bulk` → toast with inserted count. Polish pass: every zone has a teaching empty state and skeleton loading per DESIGN.md (no content-area spinners); global `prefers-reduced-motion` honored; run `npx impeccable detect web-ui/src` and fix all absolute-ban hits; sweep the spec acceptance list (below).
**Acceptance:**
- [ ] Paste a 20-line fixture list → probe results render per-entry status → confirm inserts only checked rows (mocked) → toast count
- [ ] Destructive remove requires the danger dialog
- [ ] `impeccable detect` reports zero absolute-ban findings on `web-ui/src`
- [ ] Phase acceptance sweep all green: (1) `serve` + six zones live against the dev DB; (2) full keyboard-only triage round; (3) probe→bulk-add→CLI `scan --ats`→new jobs visible in Triage; (4) bulk cascade counters correct; (5) attention bar numbers match `digest` footer; (6) stale types fail the build; (7) detector clean
- [ ] All tests pass; `make quality` + frontend build/test green; committed

---

## Self-Review
- **Spec coverage:** D1/D6 scope → T5 (runs read-only) + T6/T12 (sources full, probe flow); D2 → T1 (loopback guard); D3/D11 → T8; D4 → T8/T9 tokens + T12 detector; D5 zone semantics → T2 (predicates) / T3 (queue parity) / T9–T11 (zone UIs) + §15 docs amendment in T7; D7 → T7/T8; D8 → T4; D9/D10 → T2/T3; D12 → T4/T9. Phase 4/5 display-fold debt → T2; Phase 6/7 attention debt → T5/T10. All seven spec acceptance items land in T12's sweep.
- **Placeholders:** none; every criterion test-verifiable; the only intentionally-open implementation details (exact Stage-B block field names, quality enum constants) are pinned to existing domain modules by name.
- **Type consistency:** `query_jobs_view`/`JobsViewQuery/Page` (T2→T3), `pick_display_representatives` (T2→T3), `list_pipeline_runs`/`insights_overview`/`InsightsOverview` (T5), error shape `{error:{code,message,request_id}}` (T1→T3/T4/T6), four bulk counters (T4→T9), ISO followup (T4→T9), openapi.json path (T7→T8), density 32/46px (T8→T9).
- **Corrections honored:** no pipeline_steps/source_health invented (D6); ApplyRequest content-based confirmed before T4 was written; scoring-side `pick_representatives` untouched (Phase 5 contract guard).
