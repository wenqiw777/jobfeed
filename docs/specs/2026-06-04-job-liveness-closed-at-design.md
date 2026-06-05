# Job Liveness: `closed_at` + Workday JD Recovery — Design

**Status:** Approved design, **revised 2026-06-04** after empirical disproof of the "WAF" hypothesis.
Branch: `phase4/job-liveness-closed-at` / worktree `worktree-job-liveness-closed-at`.
**Consumer split:** ships **producer + persistence + backfill**; the candidate-selection consumer
(filter closed jobs out of scoring) is **deferred to Phase 5** (its plan carries the `closed_at IS NULL`
requirement).

## 1. Problem & root cause (corrected)

Aggregator sources (SpeedyApply) lag reality: a posting can be closed by the time we scan, yet the
list keeps re-listing it, so `discovered_at` (last-seen) never lets it age out.

**Corrected root cause:** the Workday JD failures were NOT an Akamai WAF (earlier hypothesis,
**disproved**). Our `_ats_workday.fetch_jd` does a **bare CXS API GET with no session cookie / CSRF
token**, which Workday rejects. The Workday job HTML carries an inline config with the real signal:

- `postingAvailable: true|false` — the **authoritative open/closed flag**.
- `token: "<uuid>"` — the CSRF token (`X-CALYPSO-CSRF-TOKEN`).

The correct fetch is **two-step**: GET the HTML (seeds session cookies + yields `token` +
`postingAvailable`), then GET the CXS API on the **same client** (cookies) with the CSRF header.

**Measured (25 random previously-failed Workday URLs, 2026-06-04):**
**9/25 (36%) JD recovered** (`postingAvailable=true` → CXS 200, JD 5–11k chars); **16/25 (64%)
confirmed closed** (`postingAvailable=false` → CXS 403, or HTML/CXS 404); **0 WAF / 0 ambiguous.**

So a "403" is simply Workday's response for a **closed** req (`postingAvailable=false`) — not a wall.
This makes `postingAvailable` a clean closed signal AND unlocks recovering JD for the open ~36%.

## 2. Goal / Non-Goals

**Goal (two wins):**
1. **Recover JD** for open Workday postings by fixing the fetch to the two-step cookie+CSRF flow.
2. **Mark `closed_at`** authoritatively when Workday says `postingAvailable=false`, or any vendor
   returns HTTP 404/410 (page gone) — so closed reqs leave the funnel immediately, reversibly.
Conservative backfill of stale stock.

**Non-Goals (deferred):**
- Candidate-selection / hard-filter consumer → **Phase 5** (`closed_at IS NULL` predicate).
- Digest consumer → follow-up.
- Closed marking for LinkedIn/ATS-board per-job (ATS board death is already dead-slug handled).
- No browser, no WAF evasion, no IP rotation — and now **unnecessary** (plain httpx two-step works).

## 3. Key Decisions

### 3.1 Storage — new `jobs.closed_at` + reuse `jobs.enrich_error`
- **`closed_at TIMESTAMPTZ NULL`** (new): NULL = open/unknown; set = first confirmed-closed time.
- **`enrich_error TEXT`** (exists, currently unwritten by `save_job`): reason, e.g.
  `closed:posting-unavailable:workday`, `gone:404:<vendor>`, `gone:410:<vendor>`.
- One Alembic migration adds `closed_at` (nullable, default NULL).

### 3.2 Workday fetch fix (`_ats_workday.fetch_jd`) — recovers JD + emits closed
Replace the bare CXS GET with the two-step flow, returning a result that distinguishes three outcomes:

1. GET the apply HTML (`User-Agent`, follow redirects). HTTP 404/410 → **closed** (`gone:<code>`).
   Any other non-2xx HTML status (403/429/5xx) or empty body → **transient** (do NOT parse the flag).
2. Parse inline config: `postingAvailable: (true|false)` and `token: "<uuid>"`.
3. `postingAvailable=false` → **closed** (`closed:posting-unavailable:workday`), no JD.
4. `postingAvailable=true` **(or absent — best-effort, see below)** → GET the CXS URL with headers
   `X-CALYPSO-CSRF-TOKEN: <token>`, `Accept: application/json`, `Referer: <url>`; require
   `jobPostingInfo.jobDescription` to be a **string** (non-string → no JD) → **JD recovered**. CXS
   404/410 → **closed**; other non-2xx / timeout → **transient** (no JD, no closed mark).

**Missing `postingAvailable` (format drift):** when the flag is absent but a token exists, the CXS
call is still attempted (best-effort). This is safe: `posting-unavailable` closed is set ONLY on an
explicit `false`; a missing flag can only yield JD (200), `gone` (CXS 404/410), or transient.

Cookie isolation: each fetch uses a fully **independent** `httpx.AsyncClient` (own jar + own
transport — NOT the shared client's `_transport`, which would race-close under concurrent routing),
preserving the shared client's `headers`/`follow_redirects`/`timeout`. The CXS URL transform is the
existing `_build_cxs_url` (unchanged).

### 3.3 Closed-signal contract through routing → SpeedyApply
`_ats_workday.fetch_jd` returns a small result (`jd_text: str`, `is_closed: bool`, `reason: str|None`)
— implementer's choice of shape (dataclass or a typed exception for the closed case). The
`_speedyapply_routing.route_and_fetch` and `SpeedyApplySource._route` propagate it so the built
`JobPosting` carries `closed_at`/`enrich_error`:

| Outcome                                   | `jd_text` | `closed_at` | `enrich_error`                         |
|-------------------------------------------|-----------|-------------|----------------------------------------|
| Workday `postingAvailable=false`          | ""        | `now`       | `closed:posting-unavailable:workday`   |
| HTTP 404 / 410 (HTML or CXS, any vendor)  | ""        | `now`       | `gone:<code>:<vendor>`                 |
| JD recovered (`postingAvailable=true`,200)| `<jd>`    | NULL        | NULL (`enrich_source=speedyapply-workday`) |
| Transient (timeout / 5xx / other)         | ""        | unchanged   | unchanged (`enrich_source=speedyapply-error`) |

**A bare 403 is no longer a standalone closed trigger** — for Workday it is now subsumed by reading
`postingAvailable` (false → closed); for other vendors a 403 is treated as transient (not closed).

### 3.4 `save_job` upsert — `closed_at` orthogonal to the quality-monotonic gate
Independent `ON CONFLICT ... SET`, not touching the `jd_text`/`jd_quality`/`enrich_source` CASE:

```sql
closed_at = CASE WHEN EXCLUDED.jd_text IS NOT NULL THEN NULL
                 ELSE COALESCE(jobs.closed_at, EXCLUDED.closed_at) END,
enrich_error = CASE WHEN EXCLUDED.jd_text IS NOT NULL THEN NULL
                    ELSE COALESCE(EXCLUDED.enrich_error, jobs.enrich_error) END
```
Reversible/self-healing (recovered JD clears it), earliest-confirmed-closed wins.

### 3.5 Domain model — `JobPosting`
Add `closed_at: datetime | None = None`, `enrich_error: str | None = None`. Pure stdlib.

### 3.6 Backfill — one-time, conservative, no network
`(jd_quality IS NULL OR jd_quality IN ('missing','abandoned')) AND discovered_at < now()-30d AND
closed_at IS NULL` → `closed_at=now()`, `enrich_error='backfill:stale-no-jd'`. Dry-run default. Not
label-based. (A re-scan with the fixed fetch will recover ~36% of these before backfill even runs.)

## 4. Phase 5 Handoff (consumer)
Phase 5's eligibility predicate (`_stage_a_pending_filters`, used by `load_gate_candidates` and
`claim_pending_stage_a`) MUST add `AND jobs.closed_at IS NULL`. Recorded in the Phase 5 plan, Task 5.

## 5. Testing
- **Workday fetch (unit, mocked HTTP via respx):** two-step happy path recovers JD; `postingAvailable=false`
  → closed (no CXS call needed past the flag); HTML 404 → closed; CXS 404 → closed; transient 5xx/timeout
  → no JD/no closed; CSRF header + cookies are sent on the CXS call.
- **Routing/producer (unit):** closed outcome → posting `closed_at` set + correct `enrich_error`; JD
  outcome → `closed_at` None; transient → unchanged.
- **Store (`postgres`):** save_job sets/earliest-preserves/clears `closed_at`; quality-monotonic JD gate
  unaffected; `_job_from_record` hydrates.
- **Backfill (`postgres`):** marks stale-no-JD only; dry-run/apply; idempotent.
- **Migration (`postgres`):** upgrade adds nullable column, downgrade drops.
- **Gates:** boundaries, ≤300 lines (store exempt), complexity ≤10. Optional `@pytest.mark.live`
  smoke that hits 1–2 real Workday URLs (manual).

## 6. Risks
- **Workday HTML/token format drift:** the parse (`postingAvailable:`, `token:`) is tied to Workday's
  current inline-config shape; a redesign would break recovery → falls back to "transient" (no JD, no
  false-closed). Covered by the optional live smoke.
- **Closed false-positive:** near-zero now — `postingAvailable=false` is Workday's own authoritative
  flag (not an inferred 403). Still reversible (recovered JD clears `closed_at`).
- **Producer/consumer split:** signal populated but inert until Phase 5 wires the filter (intentional).

## 7. Out-of-plan note
Owner-directed, between Phase 4 and Phase 5; depends on Phase 4 source (branch cut from Phase-4 tip).
