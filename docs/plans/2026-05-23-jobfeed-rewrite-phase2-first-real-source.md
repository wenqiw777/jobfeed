# Phase 2: First Real Source (ATS + MockLLM) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the first real external data source (ATS: Greenhouse / Ashby / Lever) into the rewritten jobfeed, end-to-end: companies seed → auto-probe → HTTP fetch → parse → PostgresStore → CLI output. LLM remains MockLLM. This phase proves the hexagonal boundary holds when one mock is replaced with real IO.

**Architecture:** Same hexagonal structure from Phase 0/1. Phase 2 introduces real HTTP adapters behind the existing `SimpleSource` Protocol. No new ports or protocol changes. ATSSource is a SimpleSource that internally manages company listing, vendor probing, and per-company fetching.

**Tech Stack additions:** httpx (async HTTP client), beautifulsoup4 (HTML-to-text for Greenhouse/Ashby/Lever JD content), respx (httpx mock for tests)

**Spec reference:** `docs/specs/2026-05-20-jobfeed-rewrite-design.md` (Sections 6, 8, 9)

**Legacy behavioral reference:** `/Users/wenqiwang/wwq/job-apply/src/jobfeed/sources/` (greenhouse.py, ashby.py, lever.py, ats_base.py) and `/Users/wenqiwang/wwq/job-apply/src/jobfeed/companies.py` (auto-probe). Phase 2 adapters must produce the same JobPosting field values for the same API responses, but the internal code structure is a clean rewrite (no inheritance-based ats_base, no ThreadPoolExecutor).

**Plan path:** `docs/plans/2026-05-23-jobfeed-rewrite-phase2-first-real-source.md`

**Prerequisite:** Phase 1 store hardening substantially complete. Required: PostgresStore with `save_job`, `upsert_company`, `list_companies`, `get_company`, `mark_company_removed`, `bump_discover_failure`, and `reset_discover_failures` implemented and passing contract tests. Companies table schema in place.

**New variables in this phase:** 1 (real outbound HTTP to ATS vendor APIs). Everything else stays mocked or uses existing proven infrastructure (PostgresStore, MockLLM).

**Implementation repo:** `/Users/wenqiwang/wwq/jobfeed`. Do not implement Phase 2 tasks in the legacy repo.

**Precedence:** This phase plan is the source of truth when it conflicts with the architecture spec.

**Commit strategy:** Commit each task separately with a task-sized conventional commit.

**Execution mode:** Run Phase 2 tasks sequentially.

---

## Implementation Decisions

**Decision 1: PG-only — no SQLite fallback.**
Phase 1 removed SQLiteStore. All Phase 2 dev/test requires Docker PG (via testcontainers or Docker Compose). Adapter-level unit tests bypass PG by mocking the store; integration tests use real PG.

**Decision 2: All three ATS vendors ship together.**
Greenhouse, Ashby, and Lever are all implemented in Phase 2. Their combined adapter code is ~200-300 lines. The real complexity is in shared infrastructure (HTTP client, auto-probe, error handling) which must be written regardless of vendor count. Shipping all three validates that the SimpleSource abstraction handles diverse API shapes (REST JSON envelope, object envelope with different fields, raw JSON array).

**Decision 3: SpeedyApply deferred to Phase 4.**
SpeedyApply adds GitHub markdown parsing + multi-vendor routing + vendors beyond the ATS three (Workday, SmartRecruiters, iCIMS). It is a separate variable and ships with Source Expansion.

**Decision 4: Companies CLI deferred to Phase 7.**
Phase 1 PostgresStore already implements the company CRUD methods declared in `StoreOpsMixin` (`ports/store_ops.py`). Phase 2 seeds companies from config at scan startup. The `companies add/list/remove` and `bootstrap-companies` CLI commands ship with Full CLI Parity.

**Decision 5: ATSSource depends on `StoreOpsMixin`, not `JobStore`.**
Company methods (`upsert_company`, `list_companies`, `mark_company_removed`, `bump_discover_failure`, `reset_discover_failures`) live on `StoreOpsMixin` in `ports/store_ops.py`, not on the core `JobStore` Protocol. ATSSource's constructor takes `store: StoreOpsMixin` — the DI factory passes the concrete PostgresStore which implements both protocols. ATSSource internally reads companies from store, runs probe-if-needed, persists probe results, then fetches jobs. The `SimpleSource.fetch_jobs(config: dict[str, object])` protocol is satisfied, but the `config` parameter is a **protocol-satisfying no-op** — ATSSource's real configuration comes from its constructor-injected `SourcesATSConfig` and store. The `config` dict passed by ScanService is `{}`.

**Decision 6: ATSSource is built lazily in the CLI scan command, not in `create_app()`.**
ATSSource needs an `httpx.AsyncClient` which must be created in an async context and closed after use. `create_app()` is sync and side-effect-free — it returns config + store + services but does not create IO-bound resources. The CLI scan command creates the httpx client, builds ATSSource, and passes it to ScanService. This matches Phase 0's pattern where `create_app()` builds but does not `connect()` the store.

**Decision 7: Composition over inheritance for vendor adapters.**
Legacy uses an ABC base class (`ats_base.py`). Phase 2 uses private vendor modules (`_ats_greenhouse.py`, `_ats_ashby.py`, `_ats_lever.py`) that export standalone async functions. No class hierarchy. The ATSSource facade calls vendor functions directly based on `company.ats_vendor`.

**Decision 8: async concurrency replaces ThreadPoolExecutor.**
Legacy runs vendors in ThreadPoolExecutor(3). Phase 2 uses one ATSSource-level
`asyncio.gather` over per-company workers plus a single
`asyncio.Semaphore(max_concurrent)` across all ATS companies. This keeps
Greenhouse/Ashby/Lever behind the same SimpleSource call and avoids a second
vendor-group concurrency layer that is not present in the new facade design.

---

## ATS Vendor API Reference

Documented here for implementer reference. These are the external contracts Phase 2 adapters must conform to.

### Greenhouse

| Field | Value |
|-------|-------|
| Jobs endpoint | `GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true` |
| Probe endpoint | `HEAD https://boards-api.greenhouse.io/v1/boards/{slug}` |
| Response shape | `{"jobs": [...]}` (object envelope) |
| Pagination | None — single request returns all jobs |
| Probe hit | HTTP 2xx (some tenants return 200, some 204) |

**Field mapping:**

| JobPosting field | Greenhouse JSON path |
|---|---|
| `platform` | `"greenhouse"` (literal) |
| `canonical_id` | `str(job["id"])` |
| `title` | `job["title"]` |
| `url` | `job["absolute_url"]` |
| `location` | `job["location"]["name"]` |
| `company` | `job.get("company_name") or slug` |
| `jd_text` | `html.unescape(job["content"])` → BeautifulSoup `.get_text()` |
| `posted_at` | `job["updated_at"]` (ISO 8601) |

### Ashby

| Field | Value |
|-------|-------|
| Jobs endpoint | `GET https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true` |
| Probe endpoint | Same as jobs endpoint (HEAD unreliable) |
| Response shape | `{"jobs": [...], "apiVersion": "v1"}` (object envelope) |
| Pagination | None — single request returns all jobs |
| Probe hit | HTTP 2xx; empty `{"jobs": []}` counts as hit and probe does not require parsing jobs |

**Field mapping:**

| JobPosting field | Ashby JSON path |
|---|---|
| `platform` | `"ashby"` (literal) |
| `canonical_id` | `str(job["id"])` |
| `title` | `job["title"]` |
| `url` | `job["jobUrl"]` |
| `location` | `job["location"]` |
| `company` | slug (API does not return company name) |
| `jd_text` | `job["descriptionPlain"]` preferred; fallback `job["descriptionHtml"]` → BS4 |
| `posted_at` | `job["publishedAt"]` (ISO 8601) |

### Lever

| Field | Value |
|-------|-------|
| Jobs endpoint | `GET https://api.lever.co/v0/postings/{slug}?mode=json` |
| Probe endpoint | Same as jobs endpoint |
| Response shape | Top-level array `[...]` (no envelope — differs from Greenhouse/Ashby) |
| Pagination | None — single request returns all jobs |
| Probe hit | HTTP 2xx and `response.json()` is parseable (normally a top-level list) |

**Field mapping:**

| JobPosting field | Lever JSON path |
|---|---|
| `platform` | `"lever"` (literal) |
| `canonical_id` | `str(job["id"])` |
| `title` | `job["text"]` |
| `url` | `job["hostedUrl"]` |
| `location` | `job["categories"]["location"]` |
| `company` | slug |
| `jd_text` | `job["descriptionPlain"]` + `job["lists"][*].text/content` (HTML segments → BS4) |
| `posted_at` | `datetime.fromtimestamp(job["createdAt"] / 1000, tz=UTC)` (epoch ms) |

### Timeout Constants

| Context | Timeout | Rationale |
|---------|---------|-----------|
| Probe | 5s | Quick yes/no; failure = try next vendor |
| Scan (per-company fetch) | 30s | Large tenants (Stripe, Anduril) return 100+ jobs |

### Shared JobPosting Field Rules

Vendor adapters do not call the store. They transform one vendor API response into
`list[JobPosting]` only. ATSSource owns scan-level context and passes a single
`discovered_at` timestamp into each vendor fetch so every posting observed in one
scan batch has consistent freshness semantics.

For every valid returned posting:
- `discovered_at` is the ATSSource scan-start timestamp, created once via `datetime.now(UTC)`
- `jd_quality = assess_quality(jd_text)`
- `enriched_at = discovered_at`
- `enrich_source = "api-{vendor}"` (`api-greenhouse`, `api-ashby`, `api-lever`)
- adapters must satisfy the current Postgres `jobs` table NOT NULL contract before returning a `JobPosting`: `platform`, `canonical_id`, `url`, `title`, `company`, `location`, and `discovered_at` are never `None`
- `location` is normalized to `""` when the vendor field is absent/null/malformed; this matches the Phase 1 decision that location is non-null in the new schema
- `company` falls back to the tracked slug when the vendor does not return a usable company name
- `posted_at` is optional; invalid/missing vendor timestamps become `None`, not a skipped job
- required field failures (`id`, title, URL, or JD text missing/blank after string normalization) skip that job object only

---

## File Map

```
jobfeed/                              # repo root (/Users/wenqiwang/wwq/jobfeed)
├── src/jobfeed/
│   ├── adapters/sources/
│   │   ├── mock.py                   # EXISTING — unchanged
│   │   ├── ats.py                    # CREATE — ATSSource facade (SimpleSource)
│   │   ├── _ats_greenhouse.py        # CREATE — Greenhouse fetch + parse
│   │   ├── _ats_ashby.py             # CREATE — Ashby fetch + parse
│   │   ├── _ats_lever.py             # CREATE — Lever fetch + parse
│   │   ├── _ats_probe.py             # CREATE — auto-probe detection
│   │   └── _http.py                  # CREATE — shared httpx client factory + helpers
│   │
│   ├── config.py                     # MODIFY — add SourcesATSConfig
│   ├── cli/
│   │   ├── __init__.py               # MODIFY — config wiring only (ATSSource built lazily in scan cmd)
│   │   └── scan.py                   # MODIFY — add --source ats flag, build ATSSource + httpx client
│   └── services/
│       └── scan.py                   # UNCHANGED — ScanService already generic
│
├── docker-compose.yml                # MODIFY — merge PG into base
├── docker-compose.postgres.yml       # DELETE — absorbed into base
├── bin/jobfeed                       # MODIFY — remove PG override flag
├── config.example.toml               # MODIFY — add [sources.ats] section
├── pyproject.toml                    # MODIFY — add httpx, beautifulsoup4, respx
│
├── tests/
│   ├── unit/
│   │   └── test_ats_adapters.py      # CREATE — respx mock, mock store
│   ├── contract/
│   │   └── test_ats_dto_contract.py  # CREATE — frozen fixture golden-file tests
│   ├── integration/
│   │   └── test_ats_scan_chain.py    # CREATE — respx + real PG
│   ├── live/
│   │   └── test_ats_live_smoke.py    # CREATE — @pytest.mark.live, real HTTP
│   └── fixtures/
│       ├── ats_greenhouse_response.json  # CREATE — frozen API response
│       ├── ats_ashby_response.json       # CREATE — frozen API response
│       └── ats_lever_response.json       # CREATE — frozen API response
```

---

## Task 0: Docker Compose PG Consolidation

**Files:**
- Modify: `docker-compose.yml`
- Delete: `docker-compose.postgres.yml`
- Modify: `bin/jobfeed`
- Modify: `bin/jobfeed.ps1` (if exists)
- Modify: `.github/workflows/ci.yml`

**What to build:**
SQLite was removed in Phase 1. PG is the only backend. The current two-file compose setup (`docker-compose.yml` base + `docker-compose.postgres.yml` PG override) no longer makes sense — PG is mandatory, not optional.

Merge the PG service definition from `docker-compose.postgres.yml` into the base `docker-compose.yml`. The resulting compose file defines two services: `postgres` (with healthcheck) and `jobfeed-cli` (depends_on postgres healthy, has `JOBFEED_DB_URL` env var pointing to postgres service).

Update `bin/jobfeed` launcher: remove any `-f docker-compose.postgres.yml` override logic if it exists. If the current launcher already just does `docker compose run --rm jobfeed-cli jobfeed "$@"`, leave it unchanged — PG starts automatically via depends_on after compose consolidation.

Delete `docker-compose.postgres.yml`.

Update `.github/workflows/ci.yml`: remove any `-f docker-compose.postgres.yml` references. CI jobs that previously used the two-file compose invocation now use the single consolidated `docker-compose.yml`.

**Acceptance criteria:**
- [ ] `docker-compose.yml` defines `postgres` + `jobfeed-cli` services
- [ ] `docker-compose.postgres.yml` deleted
- [ ] `docker compose up postgres -d` starts PG with healthcheck
- [ ] `./bin/jobfeed --help` works (PG starts automatically via depends_on)
- [ ] `./bin/jobfeed scan --source mock` still works (Phase 0 mock chain through PG)
- [ ] `.github/workflows/ci.yml` has no references to `docker-compose.postgres.yml`
- [ ] `make docker-quality` passes
- [ ] All committed

---

## Task 1: Dependencies + Config Expansion

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/jobfeed/config.py`
- Modify: `config.example.toml`
- Test: `tests/unit/test_config.py` (expand)

**What to build:**

**pyproject.toml:** Add runtime dependencies: `httpx >= 0.28`, `beautifulsoup4 >= 4.12`. Add dev dependency: `respx >= 0.22`.

**config.py:** Add `SourcesATSConfig` model:

```python
class SourcesATSConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_concurrent: int = Field(default=10, ge=1)
    probe_ttl_days: int = Field(default=7, ge=0)
    failure_threshold: int = Field(default=3, ge=1)
    probe_timeout_s: float = Field(default=5.0, gt=0)
    scan_timeout_s: float = Field(default=30.0, gt=0)
    seed_companies: list[str] = Field(default_factory=list)
    # NOTE: seed_companies is TOML-only — Pydantic model_validate cannot coerce
    # a bare env var string "anthropic,openai" into list[str]. Scalar fields
    # (enabled, max_concurrent, etc.) work via env var string→type coercion.
```

Add `SourcesConfig` container:

```python
class SourcesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ats: SourcesATSConfig = Field(default_factory=SourcesATSConfig)
```

Add `sources: SourcesConfig` to the root `Settings` model.

**config.example.toml:** Add:

```toml
[sources.ats]
enabled = true
max_concurrent = 10
probe_ttl_days = 7
failure_threshold = 3
seed_companies = ["anthropic", "openai", "palantir"]
```

**Acceptance criteria:**
- [ ] `httpx`, `beautifulsoup4`, `respx` installable
- [ ] `Settings` model has `sources: SourcesConfig` field with `SourcesATSConfig` nested model
- [ ] `load_settings()` parses `[sources.ats]` section with defaults
- [ ] `load_settings(config_with_ats)` populates `settings.sources.ats.seed_companies`
- [ ] Env var `JOBFEED_SOURCES__ATS__MAX_CONCURRENT=5` overrides config (requires Settings model updated first)
- [ ] Invalid ATS config is rejected before runtime: `max_concurrent=0`, `failure_threshold=0`, negative `probe_ttl_days`, and non-positive timeouts all raise validation errors
- [ ] All tests pass, committed

---

## Task 2: Shared HTTP Client Infrastructure

**Files:**
- Create: `src/jobfeed/adapters/sources/_http.py`
- Test: `tests/unit/test_http_client.py`

**What to build:**
Shared async httpx client factory and helpers used by all ATS vendor adapters.

`create_http_client(timeout: float = 30.0) -> httpx.AsyncClient` — creates an `httpx.AsyncClient` with:
- `timeout=httpx.Timeout(timeout, connect=10.0)` (per-request timeout with separate connect timeout)
- `follow_redirects=True`
- User-Agent header: `"jobfeed/1.0"` (polite bot identification)
- No cookies, no auth (ATS APIs are public)

`async def fetch_json(client: httpx.AsyncClient, url: str, *, slug: str, vendor: str, timeout: float | None = None) -> dict | list` — GET request, raises `ATSFetchError` on non-2xx status or JSON decode failure. Returns parsed JSON.

`async def probe_url(client: httpx.AsyncClient, url: str, *, slug: str, vendor: str, timeout: float = 5.0, method: str = "HEAD") -> bool` — returns True if HTTP 2xx, False only on definitive dead-board statuses 404/410, raises `ProbeNetworkError` on network/timeout errors, and raises `ProbeIndeterminateError` on reached-but-not-classifiable statuses such as 401/403, 429, 5xx, 405, or any other non-2xx/non-404/non-410 response. This distinction is required because current `PostgresStore.mark_company_removed()` persists `ats_vendor='removed'`, so ambiguous probe responses must never be treated as removal evidence.

`def html_to_text(html: str) -> str` — BeautifulSoup `.get_text(separator="\n", strip=True)` after `html.unescape()`. Shared across Greenhouse (always) and Ashby/Lever (fallback).

Exception classes:
- `ATSFetchError(Exception)` — base, carries `slug`, `vendor`, `status_code` (optional), `message`. Every call site passes slug/vendor into the shared helpers so per-company logs and failure handling never have to infer context from the URL.
- `ATSParseError(ATSFetchError)` — malformed top-level response schema after JSON parsing (for example Greenhouse without a `jobs` list, Ashby with non-list `jobs`, Lever with a non-list top-level JSON value). This is an expected per-company failure that ATSSource logs and skips; it must not escape as a source-level failure.
- `ProbeNetworkError(ATSFetchError)` — raised only on network-level probe failures (timeout, DNS). This is an **expected non-fatal signal** within `probe_company()` — the probe loop catches it per-vendor and continues to the next vendor. It means "this vendor's endpoint was unreachable, try next"; it becomes fatal only when all vendor probes fail at the network level.
- `ProbeIndeterminateError(ATSFetchError)` — raised when a probe received a response but cannot safely classify the slug as a vendor miss (for example 429, 5xx, 403, or Lever 2xx with invalid JSON). It is also an expected non-fatal signal inside the probe loop. If no vendor hits and any vendor was indeterminate, callers must leave company cache/removal state unchanged.

**Acceptance criteria:**
- [ ] `create_http_client()` returns configured AsyncClient
- [ ] `fetch_json` returns parsed JSON on 200
- [ ] `fetch_json` raises `ATSFetchError` on 404 with status_code, slug, and vendor populated
- [ ] `fetch_json` raises `ATSFetchError` on timeout with status_code=None plus slug/vendor
- [ ] `probe_url` returns True on 200, False on 404
- [ ] `probe_url` returns False on 410
- [ ] `probe_url` raises `ProbeNetworkError` on network error (timeout, DNS) with slug/vendor populated
- [ ] `probe_url` raises `ProbeIndeterminateError` on 403, 429, 5xx, 405, and other non-definitive statuses
- [ ] `ATSParseError` carries slug/vendor/status context and is treated as an expected adapter error, not a programmer crash
- [ ] `html_to_text` extracts text from HTML, handles `&amp;` entities
- [ ] Tests use respx to mock httpx (no real HTTP)
- [ ] All committed

---

## Task 3: Vendor Adapters (Greenhouse / Ashby / Lever)

**Files:**
- Create: `src/jobfeed/adapters/sources/_ats_greenhouse.py`
- Create: `src/jobfeed/adapters/sources/_ats_ashby.py`
- Create: `src/jobfeed/adapters/sources/_ats_lever.py`
- Test: `tests/unit/test_ats_adapters.py`

**What to build:**
Three private modules, each exporting two async functions: `fetch_jobs` and `probe`. No class hierarchy. Each module is self-contained and uses helpers from `_http.py`.

**`_ats_greenhouse.py`:**

```python
JOBS_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
PROBE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}"

async def probe(client: httpx.AsyncClient, slug: str, *, timeout: float = 5.0) -> bool:
    """HEAD request to board URL.
    Returns True on 2xx, False on definitive 404/410 misses."""

async def fetch_jobs(
    client: httpx.AsyncClient,
    slug: str,
    *,
    discovered_at: datetime,
    timeout: float = 30.0,
) -> list[JobPosting]:
    """GET jobs endpoint, parse each job object into JobPosting."""
```

Parse logic: extract `job["id"]`, `job["title"]`, `job["absolute_url"]`, `job["location"]["name"]`, `job["content"]` → `html_to_text()`, `job["updated_at"]`. Platform = `"greenhouse"`. Company = `job.get("company_name") or slug` to preserve legacy parity. Handle missing/null/malformed `location` gracefully (default to `""`). Invalid `updated_at` becomes `posted_at=None`.

**`_ats_ashby.py`:**

```python
JOBS_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"

async def probe(client: httpx.AsyncClient, slug: str, *, timeout: float = 5.0) -> bool:
    """GET jobs endpoint (HEAD unreliable for Ashby).
    Returns True on 2xx, False on definitive 404/410 misses.
    Does not require job parsing for probe parity with the legacy source."""

async def fetch_jobs(
    client: httpx.AsyncClient,
    slug: str,
    *,
    discovered_at: datetime,
    timeout: float = 30.0,
) -> list[JobPosting]:
    """GET jobs endpoint, parse each job object into JobPosting."""
```

Parse logic: `job["id"]`, `job["title"]`, `job["jobUrl"]`, `job["location"]`, `job["descriptionPlain"]` preferred (skip BS4), fallback `job["descriptionHtml"]` → `html_to_text()`, `job["publishedAt"]`. Platform = `"ashby"`. Company = slug. Handle missing/null/malformed `location` gracefully (default to `""`). Invalid `publishedAt` becomes `posted_at=None`.

**`_ats_lever.py`:**

```python
JOBS_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"

async def probe(client: httpx.AsyncClient, slug: str, *, timeout: float = 5.0) -> bool:
    """GET jobs endpoint. Returns True on 2xx + parseable JSON.
    Returns False on definitive 404/410 misses.
    Fetch parsing still validates that the jobs response is a top-level list."""

async def fetch_jobs(
    client: httpx.AsyncClient,
    slug: str,
    *,
    discovered_at: datetime,
    timeout: float = 30.0,
) -> list[JobPosting]:
    """GET jobs endpoint (response is top-level array, no envelope), parse each."""
```

Parse logic: response is `list[dict]` (no `{"jobs": [...]}` wrapper). `job["id"]`, `job["text"]` (not `title`), `job["hostedUrl"]`, `job["categories"]["location"]`, `job["descriptionPlain"]` + `job["lists"]` content → `html_to_text()` for HTML segments, `datetime.fromtimestamp(job["createdAt"] / 1000, tz=UTC)`. Platform = `"lever"`. Company = slug. Handle missing/null/malformed `categories.location` gracefully (default to `""`). Invalid/missing `createdAt` becomes `posted_at=None`.

**Shared per-job error isolation:** Each vendor's `fetch_jobs` wraps individual job parsing in try/except. A single malformed job object logs a warning and is skipped; the remaining jobs are still returned. This matches legacy behavior. **Required field validation:** before `str()` conversion, check that `job["id"]` is not None/missing — `str(None)` silently produces `"None"` as a canonical_id. After conversion, strip string fields and skip the job if `canonical_id`, `title`, `url`, or `jd_text` is blank. Jobs with missing IDs must be skipped, not silently accepted.

**Shared probe error contract:** Vendor `probe()` functions return `True` only
for a hit and `False` only for definitive vendor misses (404/410). Network,
timeout, and DNS errors raise `ProbeNetworkError`. Reached-but-ambiguous
responses (403, 429, 5xx, 405, or Lever 2xx with invalid JSON) raise
`ProbeIndeterminateError`. Do not let generic `ATSFetchError(status_code=None)`
leak from a probe function; `_ats_probe.probe_company()` depends on typed probe
errors to distinguish transport failures, ambiguous vendor behavior, and
definitive slug misses.

**Acceptance criteria:**
- [ ] Greenhouse `fetch_jobs` with respx mock returns correct JobPostings (title, url, location, jd_text, posted_at)
- [ ] Greenhouse `probe` returns True on 200, False on 404
- [ ] Ashby `fetch_jobs` prefers `descriptionPlain`, falls back to HTML → text
- [ ] Ashby `probe` uses GET (not HEAD), returns True on 2xx without requiring job parsing
- [ ] Lever `fetch_jobs` handles top-level array (no envelope), parses epoch ms timestamps
- [ ] Lever `probe` validates response JSON is parseable; invalid 2xx JSON raises `ProbeIndeterminateError`, not False
- [ ] All three probe functions raise `ProbeNetworkError` for network/timeout/DNS, raise `ProbeIndeterminateError` for 403/429/5xx/other ambiguous responses, and return False only for definitive 404/410 misses
- [ ] All three: malformed single job is skipped, rest returned (per-job error isolation)
- [ ] All three: platform field is the vendor name string
- [ ] Ashby/Lever: company field is the slug; Greenhouse: company_name when present, otherwise slug
- [ ] All three: canonical_id is `str(job["id"])`
- [ ] All three: DB-required string fields are non-None; missing location becomes `""`; malformed optional timestamps become `posted_at=None`
- [ ] All three: discovered_at/enriched_at/enrich_source/jd_quality follow the shared JobPosting field rules
- [ ] No store dependency in vendor modules (pure HTTP → domain model)
- [ ] All tests pass, committed

---

## Task 4: Auto-probe Mechanism

**Files:**
- Create: `src/jobfeed/adapters/sources/_ats_probe.py`
- Test: `tests/unit/test_ats_probe.py`

**What to build:**
Auto-probe detects which ATS vendor a company uses by trying vendor probe endpoints in order.

```python
PROBE_ORDER: list[tuple[str, ProbeFunc]] = [
    ("greenhouse", greenhouse.probe),
    ("ashby", ashby.probe),
    ("lever", lever.probe),
]

async def probe_company(
    client: httpx.AsyncClient,
    slug: str,
    *,
    timeout: float = 5.0,
) -> str | None:
    """Try each vendor probe in order. Returns vendor name or None."""
```

Probe order: Greenhouse → Ashby → Lever (by market share: ~70/15/10% among YC companies).

Return values:
- Vendor name string (`"greenhouse"`, `"ashby"`, `"lever"`) on first hit
- `None` only when every supported vendor returned a definitive miss (404/410)
- Raises `ProbeNetworkError` when all three vendor probes fail at the network level (timeout, DNS)
- Raises `ProbeIndeterminateError` when no vendor hits and at least one vendor was unresolved/ambiguous (partial network failure, 403, 429, 5xx, invalid Lever 2xx JSON, etc.). This prevents ambiguous public API behavior from being cached as "unknown" or escalated to `mark_company_removed()`.
- Partial failures are swallowed only when another vendor hits (for example Greenhouse times out but Ashby returns 2xx → return `"ashby"`)

**TTL + caching logic is NOT in probe module.** The caller (ATSSource facade) checks `company.last_verified_at` against `probe_ttl_days` and decides whether to call `probe_company`. Probe module is stateless.

**Dead slug resolution logic:**

```python
async def resolve_dead_slug(
    client: httpx.AsyncClient,
    slug: str,
    *,
    timeout: float = 5.0,
) -> str | None:
    """Re-probe a slug that repeatedly returned 404/410 on its cached vendor.
    Returns same/new vendor, or None if truly dead."""
```

Called after a cached-vendor fetch returns definitive 404/410 often enough to
cross the caller's failure threshold. Re-probes all three supported vendors,
including the originally cached vendor because same-vendor recovery is possible.
If any vendor hits, returns that vendor name. Returning the same vendor means
the original errors may have been a transient board hiccup; returning a
different vendor means the company may have migrated ATS. The caller still must
retry the fetch before resetting the failure counter. Returning `None` means
all vendors returned definitive 404/410 misses and the slug is truly dead. If
no vendor hits and any probe is unresolved/ambiguous, raises
`ProbeNetworkError` or `ProbeIndeterminateError`; callers treat that as
unresolved and must not mark the company removed.

**Acceptance criteria:**
- [ ] `probe_company` returns `"greenhouse"` when Greenhouse probe succeeds
- [ ] `probe_company` returns `"ashby"` when only Ashby probe succeeds
- [ ] `probe_company` returns `None` only when all supported vendors return definitive False (404/410)
- [ ] `probe_company` tolerates partial network failure only when another vendor succeeds
- [ ] `probe_company` raises `ProbeNetworkError` when all vendor probes fail at the network level
- [ ] `probe_company` raises `ProbeIndeterminateError` when no vendor succeeds and any probe is ambiguous/unresolved
- [ ] `resolve_dead_slug` probes all supported vendors, including the originally cached vendor
- [ ] `resolve_dead_slug` returns the same vendor when same-vendor reprobe succeeds
- [ ] `resolve_dead_slug` returns new vendor on ATS migration
- [ ] `resolve_dead_slug` returns None only when slug is truly dead (all supported vendors return definitive 404/410)
- [ ] `resolve_dead_slug` raises `ProbeNetworkError` when all vendor probes fail at the network level
- [ ] `resolve_dead_slug` raises `ProbeIndeterminateError` when no vendor succeeds and any probe is ambiguous/unresolved
- [ ] All tests use respx mock, no real HTTP
- [ ] All committed

---

## Task 5: ATSSource Facade

**Files:**
- Create: `src/jobfeed/adapters/sources/ats.py`
- Test: `tests/unit/test_ats_source.py`

**What to build:**
`ATSSource` implements `SimpleSource` Protocol. It is the public-facing adapter; vendor modules and probe logic are private implementation details. Its logger dependency uses the existing `JobfeedLogger` protocol from `src/jobfeed/observability.py`, not a concrete `structlog` type; `ats.py` imports that protocol directly.

```python
SUPPORTED_VENDORS: frozenset[str] = frozenset({"greenhouse", "ashby", "lever"})

class ATSSource:
    """ATS job board source (Greenhouse / Ashby / Lever).

    Implements SimpleSource Protocol. Internally manages company listing,
    auto-probe with TTL cache, vendor routing, and per-company error isolation.

    Depends on StoreOpsMixin (ports/store_ops.py) for company CRUD, NOT on
    JobStore. The DI factory passes the concrete PostgresStore which implements
    both protocols.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        store: StoreOpsMixin,
        config: SourcesATSConfig,
        logger: JobfeedLogger,
    ) -> None: ...

    async def fetch_jobs(self, config: dict[str, object]) -> list[JobPosting]:
        """Fetch jobs from all known ATS companies.

        The `config` parameter is a protocol-satisfying no-op — ATSSource's
        real configuration comes from constructor-injected SourcesATSConfig
        and StoreOpsMixin. ScanService passes `{}`.

        Flow:
        1. Read companies from store (exclude removed)
        2. Filter to supported vendors: ats_vendor in SUPPORTED_VENDORS or None (needs probe)
        3. Probe companies needing refresh (last_verified_at older than probe_ttl_days or None)
        4. Persist probe results via read-modify-write company updates
        5. For each company with known supported vendor: fetch_jobs via vendor adapter
        6. Handle per-company errors (definitive 404/410 → thresholded dead-slug resolution)
        7. Track only consecutive definitive discover failures via store.bump_discover_failure()
        8. Return aggregated list[JobPosting]
        """
```

**Concurrency model:**
- All companies fetched concurrently via `asyncio.gather` with `asyncio.Semaphore(config.max_concurrent)` throttle
- Probe calls are also concurrent (within the semaphore)
- Per-company expected failures do not cancel other companies. Implement this by having each per-company worker return a typed internal outcome (`jobs`, `skipped`, `removed`, etc.) after catching expected `ATSFetchError`, `ATSParseError`, `ProbeNetworkError`, and `ProbeIndeterminateError`; do not rely on unhandled exceptions inside `asyncio.gather`.

**Vendor filtering:**
ATSSource only processes companies with `ats_vendor in SUPPORTED_VENDORS` or `ats_vendor is None` (needs probe). Companies with `ats_vendor` set to an unsupported value (e.g., `"workday"`, `"unknown"`) are silently skipped — they may be SpeedyApply targets or legacy entries. This filtering happens in ATSSource, not in the store query.

**Company row update rule:**
The current PostgresStore `upsert_company()` does not behave like the legacy
SQLite helper for every field: it preserves nullable metadata with COALESCE, but
it overwrites `ats_override`, `job_count_last_scan`, and
`consecutive_discover_failures` from the incoming `CompanyRecord`. ATSSource
therefore must not construct fresh partial `CompanyRecord(...)` objects for
updates. For probe cache refreshes, vendor migration, scan-count updates, and
same-vendor recovery:
- read the existing row with `store.get_company(slug)`
- create an updated record preserving all unrelated fields (`ats_override`,
  `notes`, `job_count_last_scan`, `consecutive_discover_failures`, timestamps)
- change only the fields intended by that branch
- call `store.upsert_company(updated_record)`

For confirmed removal, call `store.mark_company_removed(slug)` and then
`store.reset_discover_failures(slug)` so the row matches legacy
`resolve_dead_slug()` behavior.

**Per-company error handling:**
- `ATSFetchError` with `status_code in {404, 410}` is the only branch that bumps `consecutive_discover_failures`.
  - First bump with `store.bump_discover_failure(slug)`.
  - If the new count is below `config.failure_threshold`, log and skip; do **not** re-probe yet.
  - If the new count reaches the threshold, call `resolve_dead_slug()`.
  - Same or new vendor found → preserve existing company metadata, update `ats_vendor`, `last_verified_at`, and `last_probe_attempt_at`, then retry fetch once with the returned vendor.
    - If the retry succeeds, call `store.reset_discover_failures(slug)` and update `job_count_last_scan`.
    - If the retry fails with 404/410, leave the just-bumped failure counter intact and skip; do not reset just because reprobe found a candidate vendor.
    - If the retry fails with timeout/DNS/403/429/5xx/JSON/schema errors, log and skip without an additional bump; leave the just-bumped counter intact.
  - No vendor found → call `store.mark_company_removed(slug)`, then `store.reset_discover_failures(slug)`, log warning.
  - `ProbeNetworkError` or `ProbeIndeterminateError` during dead-slug resolution → log unresolved and leave the just-bumped counter intact, but do **not** mark removed because the slug was not confirmed dead.
- `ATSFetchError` with `status_code is None` (timeout/DNS) → log and skip; do **not** bump. A flaky network must not age a company toward removal.
- `ATSFetchError` with other HTTP status (for example 403, 429, 5xx), JSON decode failure, or `ATSParseError` for malformed response envelope → log and skip; do **not** bump because it is not definitive evidence the board is gone.
- Success (including HTTP 200 with empty job list) → `store.reset_discover_failures(slug)`, then read-modify-write to update `job_count_last_scan`: read current `CompanyRecord` via `store.get_company(slug)`, set `job_count_last_scan = len(jobs)` and `last_verified_at = now`, write back via `store.upsert_company(updated_record)`. Do NOT construct a fresh `CompanyRecord` with default fields — that would clobber `ats_vendor`, `notes`, `ats_override`, etc. An empty job list from a valid 200 response is normal (company temporarily has zero openings), NOT a failure.

**Vendor fetch context:**
ATSSource creates one `scan_started_at = datetime.now(UTC)` at the start of
`fetch_jobs()` and passes it as `discovered_at=scan_started_at` to every vendor
`fetch_jobs()` call. Vendor modules must not choose their own timestamps.

**Companies with `ats_vendor = None`:** Probe when `last_verified_at` is missing or older than `probe_ttl_days`. If probe returns a supported vendor, persist that vendor plus `last_verified_at` / `last_probe_attempt_at`. If probe returns None (all vendors definitive 404/410), preserve `ats_vendor = None`, update `last_verified_at` and `last_probe_attempt_at` via the read-modify-write rule, and skip without counting a failure. This caches the negative probe for the TTL window while allowing a future re-probe if the company later adopts a supported ATS. If probe raises `ProbeNetworkError` or `ProbeIndeterminateError`, update `last_probe_attempt_at` only, leave `last_verified_at` unchanged, and skip; unresolved probes must not create a negative TTL cache.

**Known-vendor TTL refresh:** For a company with cached supported
`ats_vendor` and stale `last_verified_at`, normal TTL probing is opportunistic,
not destructive. If the probe finds the same or a different supported vendor,
update the cached vendor/timestamps and fetch with the returned vendor. If the
probe returns None or raises `ProbeNetworkError`/`ProbeIndeterminateError`, leave the cached vendor
unchanged and still attempt the cached-vendor fetch. Only definitive fetch
404/410s participate in the dead-slug failure threshold and removal path.

**Companies with `ats_override = True`:** Trust cached `ats_vendor` for normal TTL/probe refresh. Do not auto-probe just because the cache is old. If the pinned vendor returns repeated definitive 404/410 and crosses the dead-slug threshold, still run `resolve_dead_slug()` because user-pinned vendors can go stale. Preserve the `ats_override` flag on same/new-vendor recovery; `mark_company_removed()` clears it only when the board is confirmed removed.

**Acceptance criteria:**
- [ ] `ATSSource` satisfies `SimpleSource` Protocol (`isinstance` check)
- [ ] `fetch_jobs` ignores the `config` parameter (protocol no-op)
- [ ] `fetch_jobs` reads companies from store, probes if needed, fetches jobs
- [ ] Probe results persisted via read-modify-write `store.upsert_company()` updates that preserve unrelated fields
- [ ] Only companies with `ats_vendor in SUPPORTED_VENDORS | {None}` are processed
- [ ] Companies with unsupported `ats_vendor` silently skipped
- [ ] `ats_vendor=None` probe miss preserves `ats_vendor=None`, updates probe timestamps, and does not bump failures
- [ ] `ats_vendor=None` unresolved probe (`ProbeNetworkError`/`ProbeIndeterminateError`) updates only `last_probe_attempt_at`; it does not update `last_verified_at` or cache a negative result
- [ ] Stale known-vendor TTL probe miss/network/indeterminate failure does not clear cached vendor and still attempts cached-vendor fetch
- [ ] Per-company 404/410 below threshold bumps once and does not trigger `resolve_dead_slug`
- [ ] Per-company 404/410 at threshold triggers `resolve_dead_slug`
- [ ] Same-vendor dead-slug reprobe retries once and resets failures only after the retry fetch succeeds
- [ ] Different-vendor dead-slug reprobe updates vendor, retries once, and resets failures only after the retry fetch succeeds
- [ ] Same/new-vendor dead-slug retry failure leaves the just-bumped counter intact
- [ ] Confirmed dead slug calls `store.mark_company_removed` and then `store.reset_discover_failures`
- [ ] Timeout/DNS/403/429/5xx do not bump `consecutive_discover_failures`
- [ ] JSON decode or malformed response errors do not bump `consecutive_discover_failures`
- [ ] `ProbeIndeterminateError` never causes `mark_company_removed`
- [ ] Success (including 200 + empty jobs) resets failures via `store.reset_discover_failures`
- [ ] `ats_override` skips normal TTL re-probe but still participates in thresholded dead-slug resolution
- [ ] Concurrency limited by `max_concurrent` semaphore
- [ ] One company failure does not affect others
- [ ] Malformed one-company response produces an expected skip outcome and does not escape to `ScanService` as a source-level failure
- [ ] Structured log entries include `slug`, `vendor`, `job_count`
- [ ] Tests use mock `StoreOpsMixin` + respx mock HTTP
- [ ] All committed

---

## Task 6: DI Factory + CLI Integration + Companies Seed

**Files:**
- Modify: `src/jobfeed/cli/__init__.py`
- Modify: `src/jobfeed/cli/scan.py`
- Modify: `config.example.toml`

**What to build:**

**DI factory (`create_app()`) — no ATSSource construction here.**
`create_app()` remains sync and side-effect-free. It returns the `Settings`, configured `ScanService`, `MockSource`, and store — but does NOT create httpx clients or ATSSource. ATSSource requires an async httpx client that must be created and closed within an async context. Following Phase 0's pattern (where `create_app()` builds but does not `connect()` the store), ATSSource construction is deferred to the CLI scan command.

**CLI scan command — lazy ATSSource construction:**
The async `_run_scan()` function in `cli/scan.py` handles the full lifecycle.
All store reads/writes, including company seeding, happen inside
`run_with_store()` after `store.connect()` and before `store.close()`.

```python
async def _run_scan(app: AppContext, source_name: str) -> PipelineRun:
    async def action() -> PipelineRun:
        sources: list[SourceSpec] = []
        client: httpx.AsyncClient | None = None
        try:
            if source_name in ("mock", "all"):
                sources.append(("mock", app["sources"]["mock"], {}))

            if source_name in ("ats", "all"):
                ats_config = app["settings"].sources.ats
                if not ats_config.enabled:
                    raise click.ClickException("ATS source is disabled in config")
                store_ops = cast(StoreOpsMixin, app["store"])
                await seed_ats_companies(store_ops, ats_config.seed_companies)
                client = create_http_client(ats_config.scan_timeout_s)
                sources.append(
                    (
                        "ats",
                        ATSSource(
                            client=client,
                            store=store_ops,
                            config=ats_config,
                            logger=app["logger"],
                        ),
                        {},
                    )
                )

            return await app["scan_service"].run(sources)
        finally:
            if client is not None:
                await client.aclose()

    return await run_with_store(app, action)
```

**Companies seed on startup:**
Before scan, if `settings.sources.ats.seed_companies` is non-empty:
- For each slug in list: first `await store.get_company(slug)`.
- If no row exists: insert `CompanyRecord(slug=slug)` via `store.upsert_company()`.
- If a row already exists: do nothing.
- COALESCE semantics: existing companies keep their cached `ats_vendor`, `last_verified_at`, etc.
- This is idempotent — re-running seed does not reset probe cache, `job_count_last_scan`, or `consecutive_discover_failures`.

Do **not** blindly call `upsert_company(CompanyRecord(slug=slug))` for existing
rows. The current PostgresStore upsert preserves nullable vendor/timestamp
fields via COALESCE, but it overwrites `job_count_last_scan` and
`consecutive_discover_failures` with the incoming default `0`. Seed logic must
therefore insert only missing rows.

**CLI `scan` command update:**
Current: `--source` with choices `["mock"]`.
New: `--source` with `type=click.Choice(["mock", "ats", "all"])` — Click validates the value before the command body runs, rejecting typos with a clear error message.
- `--source mock` — MockSource only (existing behavior)
- `--source ats` — ATSSource only
- `--source all` — both MockSource and ATSSource (for testing mixed-source scan)

Default: `--source mock` in Phase 2 (changes to `ats` or `all` in later phases when more sources are added).

**Acceptance criteria:**
- [ ] `jobfeed scan --source ats` runs ATS scan (with seed companies from config)
- [ ] `jobfeed scan --source mock` still works (unchanged)
- [ ] `jobfeed scan --source all` runs both mock and ATS
- [ ] Companies from `seed_companies` config are seeded before scan
- [ ] Re-running seed does not reset existing company probe data, scan counts, or failure counters
- [ ] httpx client properly closed after scan
- [ ] `--help` shows updated source choices
- [ ] All committed

---

## Task 7: Frozen Fixtures + DTO Contract Tests

**Files:**
- Create: `tests/fixtures/ats_greenhouse_response.json`
- Create: `tests/fixtures/ats_ashby_response.json`
- Create: `tests/fixtures/ats_lever_response.json`
- Create: `tests/contract/test_ats_dto_contract.py`

**What to build:**

**Frozen fixtures:** Anonymized but structurally accurate API response JSON for each vendor. Each fixture contains 3-5 job objects with representative field variety (different locations, some missing optional fields, varying JD lengths). These are the "golden files" that lock the DTO contract.

Fixture creation: capture real API responses, then anonymize (replace company names, job titles, JD text with realistic but fake content). Preserve structural details: field names, nesting, types, edge cases (null location, HTML entities in JD, epoch ms timestamps for Lever).

**Contract tests (`test_ats_dto_contract.py`):**
For each vendor:
1. Load frozen fixture JSON
2. Call vendor's `fetch_jobs(..., discovered_at=fixed_utc_datetime)` with respx returning the frozen fixture
3. Assert returned `list[JobPosting]` matches expected field values exactly

These tests serve as **regression guards**: if a vendor's API response structure changes, the adapter parse logic may need updating — but the contract test ensures the mapping is deliberate, not accidental.

Also test edge cases embedded in fixtures:
- Job with `null` location → `location = ""`
- Greenhouse JD with HTML entities (`&amp;`, `&lt;`) → correctly unescaped in `jd_text`
- Lever timestamp as epoch ms → correct `posted_at` datetime
- Ashby job with `descriptionPlain = null` → falls back to `descriptionHtml` → text

**Acceptance criteria:**
- [ ] Three fixture files committed, each with 3-5 representative jobs
- [ ] Fixtures are anonymized (no real company data)
- [ ] Contract test for each vendor: frozen fixture → parse → exact field match
- [ ] Contract tests use a fixed `discovered_at` and assert `discovered_at`, `enriched_at`, `enrich_source`, and `jd_quality`
- [ ] Edge cases covered: null location, HTML entities, epoch timestamps, description fallback
- [ ] Tests fail if field mapping is changed (regression guard)
- [ ] Tests use respx (no real HTTP)
- [ ] All committed

---

## Task 8: Integration Tests (respx + real PG)

**Files:**
- Create: `tests/integration/test_ats_scan_chain.py`

**What to build:**
End-to-end integration tests that verify the full ATS scan chain with a real PostgresStore but mocked HTTP responses. **All scenarios run through `ScanService.run()`**, not by calling `ATSSource.fetch_jobs()` directly. This tests the real persistence path: ATSSource returns `list[JobPosting]` → ScanService calls `store.save_job()` per job → PipelineRun recorded. ATSSource's internal store calls (company probe persistence, failure tracking) also go through the real PG.

**Test scenarios:**

**Scenario 1: Happy path scan**
- Seed 2 companies (one Greenhouse, one Ashby) with known `ats_vendor` and recent `last_verified_at`
- Mock both vendor endpoints with fixture responses via respx
- Build ATSSource + ScanService, run `scan_service.run([("ats", ats_source, {})])`
- Assert: jobs from both companies saved to DB via `store.list_jobs()`
- Assert: `company.job_count_last_scan` updated
- Assert: `company.consecutive_discover_failures` is 0
- Assert: PipelineRun.jobs_discovered and jobs_inserted match

**Scenario 2: Probe + scan (unknown vendor)**
- Seed 1 company with `ats_vendor = None`
- Mock probe endpoints: Greenhouse 404, Ashby 200
- Mock Ashby jobs endpoint with fixture
- Run through ScanService
- Assert: company's `ats_vendor` updated to `"ashby"` in DB
- Assert: jobs saved

**Scenario 3: Dead slug resolution**
- Seed 1 company with `ats_vendor = "greenhouse"`, recent `last_verified_at`, and `consecutive_discover_failures = 2`
- Configure ATSSource with `SourcesATSConfig(failure_threshold=3)`
- Mock Greenhouse jobs endpoint → 404
- Mock re-probe: Greenhouse 404, Ashby 200
- Mock Ashby jobs endpoint with fixture
- Run through ScanService
- Assert: company migrated to `ats_vendor = "ashby"`
- Assert: `consecutive_discover_failures` reset to 0 only after the Ashby retry fetch succeeds
- Assert: jobs from Ashby saved

**Scenario 4: Definitive 404 below threshold**
- Seed 1 company with `ats_vendor = "greenhouse"`, recent `last_verified_at`, and `consecutive_discover_failures = 0`
- Configure ATSSource with `SourcesATSConfig(failure_threshold=3)`
- Mock Greenhouse jobs endpoint → 404
- Run through ScanService
- Assert: `consecutive_discover_failures` is 1
- Assert: no re-probe endpoints were called
- Assert: company is not marked removed

**Scenario 5: Company failure threshold removal**
- Seed 1 company with `ats_vendor = "greenhouse"`, recent `last_verified_at`, and `consecutive_discover_failures = 2`
- Configure ATSSource with `SourcesATSConfig(failure_threshold=3)`
- Mock vendor endpoint → 404
- Mock re-probe → all supported vendors return definitive 404/410 misses
- Run through ScanService
- Assert: company marked removed (`ats_vendor = "removed"`)
- Assert: `consecutive_discover_failures` reset to 0 after removal

**Scenario 6: Idempotent re-scan**
- Run ScanService twice with same mock responses
- Assert: job count unchanged (upsert, not duplicate)
- Assert: second run's PipelineRun shows `jobs_updated > 0`, `jobs_inserted = 0`

**Scenario 7: Mixed success/failure (ATSSource error containment)**
- Seed 5 companies with known `ats_vendor` and recent `last_verified_at`: one succeeds, one 404s below threshold, one times out, one returns 5xx, one returns malformed top-level JSON schema
- Run through ScanService
- Assert: successful company's jobs saved, others logged and skipped
- Assert: only the 404 company has `consecutive_discover_failures` incremented in DB
- Assert: timeout, 5xx, and malformed-schema companies keep their prior failure counters unchanged
- Assert: `PipelineRun.errors == 0` — this verifies ATSSource's error containment contract: per-company failures are handled internally (logged, failure counters bumped) and NOT propagated through `fetch_jobs()`. ScanService only increments `errors` if the source itself raises, which ATSSource must not do for individual company failures.
  Per-company failures are intentionally contained inside ATSSource and are not
  exposed through the unchanged `SimpleSource` protocol.

**Scenario 8: Ambiguous dead-slug resolution does not remove**
- Seed 1 company with `ats_vendor = "greenhouse"`, recent `last_verified_at`, and `consecutive_discover_failures = 2`
- Configure ATSSource with `SourcesATSConfig(failure_threshold=3)`
- Mock Greenhouse jobs endpoint → 404
- Mock re-probe: Greenhouse 404, Ashby 429, Lever 404
- Run through ScanService
- Assert: company is not marked removed
- Assert: `consecutive_discover_failures` remains 3 (the just-bumped counter is preserved)
- Assert: `last_verified_at` is not refreshed from the ambiguous probe result

**Infrastructure:** Tests use testcontainers PG (same as Phase 1 contract tests). Each test gets a fresh DB with Alembic migration applied.

**Acceptance criteria:**
- [ ] All 8 scenarios pass with real PG + respx mock HTTP
- [ ] Jobs correctly saved to `jobs` table with all fields populated
- [ ] Company probe results persisted to `companies` table
- [ ] Dead slug resolution triggers vendor migration in DB
- [ ] Below-threshold 404/410 bumps but does not re-probe/remove
- [ ] Failure threshold triggers mark_company_removed and resets the counter
- [ ] Timeout/DNS/403/429/5xx failures do not bump removal counters
- [ ] Ambiguous probe results never trigger `mark_company_removed`
- [ ] Malformed response envelopes are contained as expected per-company skips
- [ ] Idempotent re-scan: upsert, not duplicate
- [ ] PipelineRun recorded with correct counts
- [ ] Per-company failures update company counters/logs without incrementing `PipelineRun.errors`
- [ ] Tests use testcontainers (carry `@pytest.mark.postgres`)
- [ ] All committed

---

## Task 9: Live Smoke Tests + CI Update

**Files:**
- Create: `tests/live/test_ats_live_smoke.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `pyproject.toml` (pytest markers)

**What to build:**

**Live smoke tests:**
Optional tests that make real HTTP requests to known public ATS job boards. These verify that the adapter's parsing logic works against current live API responses — catching API drift that frozen fixtures cannot detect.

```python
from datetime import UTC, datetime

@pytest.mark.live
class TestATSLiveSmoke:
    """Real HTTP tests — not run in CI. Manual: pytest -m live"""

    async def test_greenhouse_live(self):
        """Fetch jobs from a known Greenhouse board."""
        jobs = await greenhouse.fetch_jobs(
            client,
            "anthropic",
            discovered_at=datetime.now(UTC),
        )
        assert len(jobs) > 0
        assert all(j.platform == "greenhouse" for j in jobs)
        assert all(j.jd_text and len(j.jd_text) > 100 for j in jobs)

    async def test_ashby_live(self):
        """Fetch jobs from a known Ashby board."""
        jobs = await ashby.fetch_jobs(client, "openai", discovered_at=datetime.now(UTC))
        ...

    async def test_lever_live(self):
        """Fetch jobs from a known Lever board."""
        jobs = await lever.fetch_jobs(
            client,
            "palantir",
            discovered_at=datetime.now(UTC),
        )
        ...

    async def test_probe_live(self):
        """Probe a known company and verify vendor detection."""
        vendor = await probe_company(client, "anthropic")
        assert vendor == "greenhouse"
```

Test companies chosen from the legacy repo's existing fixture coverage (`anthropic`, `openai`, `palantir`) so frozen tests and optional live smoke cover the same vendor shapes.

**pyproject.toml marker registration:**
```toml
[tool.pytest.ini_options]
markers = [
    "postgres: requires PostgreSQL (Docker)",
    "live: requires real HTTP to external APIs (manual only)",
]
addopts = "-m 'not postgres and not live'"
```

**CI update:**
- Current CI has a single PG-backed `quality` job. Phase 2 changes it to two jobs:
  - `quality-gate`: installs `.[dev]`, runs `make quality`, no Postgres service, no live HTTP
  - `postgres-tests`: starts the Postgres service, sets `PGTEST_DSN` and `PGTEST_REQUIRE=1`, then runs `pytest -m postgres -o "addopts=" -v --tb=short`
- Before adding default `addopts`, mark every existing PG-backed test with `@pytest.mark.postgres` (or module-level `pytestmark = pytest.mark.postgres`). This includes tests that use the `store`, `contract_store`, `pg_url`, or `fresh_pg_dsn` fixtures. Otherwise `make quality` will still attempt Docker/testcontainers despite the default marker filter.
- Add Phase 2 integration tests to the `postgres` marker group.
- Live tests: **never in CI** — external API dependency makes them flaky and rate-limit-prone
- Add `httpx`, `beautifulsoup4`, `respx` to CI install step (if not already via pyproject.toml)

**Acceptance criteria:**
- [ ] Live smoke tests pass when run manually (`pytest -m live`)
- [ ] Live tests skipped by default (`make quality`, bare `pytest`)
- [ ] Live tests excluded from CI
- [ ] Existing PG-backed tests are marked `postgres` before default `addopts` is enabled
- [ ] CI quality-gate runs unit + contract tests (no PG needed)
- [ ] CI postgres-tests job runs integration tests including Phase 2 ATS chain tests
- [ ] `pyproject.toml` has both `postgres` and `live` markers registered
- [ ] All committed

---

## Task 10: Verification + Phase 2 Acceptance

**Files:** None new. Verification-only task.

**What to verify:**

1. **End-to-end ATS chain:** in a PG-backed test harness, run the ATS source with respx-mocked HTTP through `ScanService.run()` → jobs appear in DB → `jobfeed evaluate` (MockLLM) → `jobfeed digest` → digest includes ATS-sourced jobs. Do not describe this as a normal CLI command with respx; respx mocking belongs inside tests. A manual `jobfeed scan --source ats` smoke uses real HTTP and is optional/manual like `pytest -m live`.

2. **Source isolation:** `jobfeed scan --source mock` still works exactly as before. ATSSource introduction does not break MockSource path.

3. **Architecture boundaries:**
   - `domain/` has no new imports (unchanged by Phase 2)
   - `ports/` has no new imports (SimpleSource protocol unchanged)
   - `services/scan.py` has no imports from `adapters/sources/ats*` (uses SimpleSource protocol only)
   - `adapters/sources/ats.py` imports from `_http.py`, `_ats_*.py`, and `ports/store_ops.py` — no domain bypass and no dependency on the core `JobStore` Protocol

4. **Protocol compliance:** `isinstance(ats_source, SimpleSource)` is True.

5. **Config round-trip:** `config.example.toml` → `load_settings()` → `create_app()` returns settings → CLI scan builds ATSSource with correct `SourcesATSConfig` values.

6. **Docker smoke:** `docker compose build && ./bin/jobfeed scan --source mock` succeeds with PG-consolidated compose.

7. **Test coverage:**
   - `make quality` passes (unit + contract, no PG)
   - `pytest -m postgres` passes (integration with PG)
   - `pytest -m live` passes (manual, real HTTP)

8. **DTO contract lock:** Modifying a field mapping in any vendor adapter → contract test fails. This proves the contract tests are effective regression guards.

**Acceptance criteria (Phase 2 milestone):**
- [ ] Full scan → evaluate → digest chain works with ATS source + MockLLM in the PG-backed mocked-HTTP test harness
- [ ] MockSource path unbroken
- [ ] Architecture boundary tests pass
- [ ] SimpleSource protocol compliance verified
- [ ] Docker Compose PG consolidation works
- [ ] `make quality` passes
- [ ] `pytest -m postgres` passes
- [ ] DTO contract tests catch intentional field mapping changes
- [ ] All committed
