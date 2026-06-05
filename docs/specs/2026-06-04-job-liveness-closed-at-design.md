# Job Liveness: `closed_at` — confirmed-gone marking from JD fetch — Design

**Status:** Approved design (2026-06-04). Branch: `phase4/job-liveness-closed-at`.
**Owner consumer split:** This spec ships the **producer + persistence + backfill** only. The
**candidate-selection consumer** (filtering closed jobs out of scoring) is **deferred to Phase 5**,
which already restructures that load path (see *Phase 5 Handoff*).

## 1. Problem

Aggregator sources (notably SpeedyApply, a curated GitHub list) lag reality: a posting can be
**closed/expired** by the time we scan, yet the list keeps re-listing it, so `discovered_at`
(last-seen) keeps refreshing and the row never ages out of the freshness window. The only current
liveness signal is passive `discovered_at` staleness — worst case **90 days** (the Phase 5
big-company freshness window) before a dead job stops being a scoring candidate.

Empirically (this repo's dev DB, 2026-06-04): of 142 SpeedyApply→Workday "failed-JD" rows, ~99
already had usable JD (preserved by the quality-monotonic upsert across scans); **43 were truly
missing JD; of those only 8 had a JD-bearing cross-source twin; ~33 are permanently JD-less** —
their Workday reqs are closed (manually verified) but Akamai WAF returns 403/404 so no scraper
(httpx, headless Chromium, **headful Chromium** — all tested, all 403/404) can fetch them.

When a per-job JD fetch returns a **definitive "not here" HTTP status (404 / 410 / 403)**, that is a
fast, direct signal the posting is gone — far better than waiting out the passive window.

## 2. Goal / Non-Goals

**Goal:** When a per-job JD fetch returns 404 / 410 / 403, mark the job **closed** immediately and
durably, with a reason that distinguishes confidence; make it **reversible** (a later successful
enrich un-marks it); backfill the existing stock conservatively.

**Non-Goals (explicitly deferred):**
- **Candidate-selection / hard-filter consumer** → **Phase 5** (`load_gate_candidates`,
  `apply_hard_filters`). This spec only requires Phase 5 to add `closed_at IS NULL`; it does not
  modify the eval funnel here.
- **Digest consumer** → follow-up (not Phase 5's surface; small, deferred to keep this PR to one
  concern).
- **LinkedIn / ATS per-job closed marking** → SpeedyApply is the only per-job-JD-fetch source whose
  individual req can return 404/410/403. ATS board-level death is already handled (dead-slug,
  `_ats_probe`). LinkedIn enrich is a later follow-up.
- **WAF bypass / scraping the closed Workday reqs** → proven impossible without evasion; out of
  scope, will not be attempted.

## 3. Key Decisions

### 3.1 Storage — new column `jobs.closed_at` + reuse `jobs.enrich_error`
- **`closed_at TIMESTAMPTZ NULL`** (new): `NULL` = open/unknown; set = first time we confirmed the
  posting unobtainable. Clean, NULL-safe liveness predicate (`closed_at IS NULL`). Records *when*.
- **`enrich_error TEXT`** (exists; **currently not written by `save_job`** — Phase 4 deferred it):
  human-readable reason, also distinguishes confidence:
  - `gone:404:<vendor>` / `gone:410:<vendor>` — server says the req does not exist (**high
    confidence**).
  - `unreachable:403:<vendor>` — server refuses (WAF); the req is *very likely* closed but
    not server-confirmed (**lower confidence**, auditable/reversible separately if 403 ever
    proves a false-positive source).
- One Alembic migration adds `closed_at` (nullable, default NULL → non-breaking, no backfill in the
  migration itself).

### 3.2 Trigger (producer) — status-code branch in SpeedyApply
In `SpeedyApplySource._route`'s `except ATSFetchError`, inspect `exc.status_code`:

| JD fetch outcome        | `closed_at` | `enrich_error`              | `enrich_source` |
|-------------------------|-------------|-----------------------------|-----------------|
| 404 / 410               | `now`       | `gone:<code>:<vendor>`      | `speedyapply-error` (unchanged) |
| 403                     | `now`       | `unreachable:403:<vendor>`  | `speedyapply-error` |
| timeout / 5xx / network | unchanged   | unchanged                   | `speedyapply-error` |
| success (JD obtained)   | **cleared** | cleared                     | `speedyapply-<vendor>` |

- **Only 403/404/410** — explicit "server says not-here / refuses". Timeout/5xx/connection errors are
  **transient**; marking them closed would wrong-kill live jobs.
- `ATSFetchError.status_code` already carries the code (`_http.py:118`); it is currently discarded at
  the catch site. The vendor is derivable from the apply-URL host (already matched in routing).
- The `<vendor>-notfound` labels in the existing DB are **historical** (old code/legacy import);
  current code does not produce them and the backfill must not trust them (see 3.5).

### 3.3 `save_job` upsert — `closed_at` orthogonal to the quality-monotonic gate
`closed_at` gets its own `ON CONFLICT ... SET` rule, **independent** of the `jd_text`/`jd_quality`/
`enrich_source` quality-monotonic `CASE` (which must stay untouched):

```sql
closed_at = CASE
    WHEN EXCLUDED.jd_text IS NOT NULL THEN NULL                       -- job came back with JD → un-mark
    ELSE COALESCE(jobs.closed_at, EXCLUDED.closed_at)                 -- keep EARLIEST confirmed-gone time
END,
enrich_error = CASE
    WHEN EXCLUDED.jd_text IS NOT NULL THEN NULL                       -- cleared on recovery
    ELSE COALESCE(EXCLUDED.enrich_error, jobs.enrich_error)           -- adopt new reason, else keep
END
```

- **Reversible / self-healing:** any future scan that obtains real JD (`EXCLUDED.jd_text IS NOT NULL`)
  clears both columns → a false-positive (e.g. an open-but-WAF-walled 403 job that later enriches)
  recovers automatically.
- **Earliest-wins** for `closed_at` (truthful "gone since"); never overwritten by a later re-confirm.
- Fresh INSERT path writes whatever the posting carries (`closed_at` NULL unless the discovering
  fetch already 403/404/410'd it).

### 3.4 Domain model — `JobPosting`
Add `closed_at: datetime | None = None` and (if absent) `enrich_error: str | None = None` to
`JobPosting` (`domain/models.py`). Pure stdlib; no boundary change. SpeedyApply sets them; `save_job`
persists them; `_job_from_record` hydrates them.

### 3.5 Backfill — one-time, conservative, no network
A guarded maintenance routine marks existing stock that is **confidently** gone. Criterion:

```
(jd_quality IS NULL OR jd_quality IN ('missing','abandoned'))
  AND discovered_at < now() - INTERVAL '30 days'
  AND closed_at IS NULL
→ set closed_at = now(), enrich_error = 'backfill:stale-no-jd'
```

- Pure staleness — **no network, no re-probe**; defensible and reversible.
- **Not** label-based (`enrich_source LIKE '%notfound%'`): those legacy labels mix 403/404 and are
  untrustworthy.
- Delivery: a `jobfeed` maintenance subcommand (preferred) or a one-off `scripts/` script; dry-run by
  default, `--apply` to write; logs counts.

## 4. Data Flow

```
SpeedyApply scan
  └─ _route(row) → route_and_fetch
        ├─ JD obtained        → (jd_text, "speedyapply-<vendor>"),       closed_at=None
        └─ ATSFetchError
              ├─ status 404/410 → ("", "speedyapply-error"), closed_at=now, enrich_error="gone:..."
              ├─ status 403     → ("", "speedyapply-error"), closed_at=now, enrich_error="unreachable:403:..."
              └─ else (timeout) → ("", "speedyapply-error"), closed_at=None
  └─ JobPosting(..., closed_at, enrich_error) → store.save_job (upsert rule 3.3)

[deferred → Phase 5]  load_gate_candidates(...) AND ... AND closed_at IS NULL
[one-time]            backfill maintenance command
```

## 5. Phase 5 Handoff (the consumer)

Phase 5's `load_gate_candidates` (Task 5) and the `_stage_a_pending_filters` eligibility predicate are
the correct home for the consumer. **Requirement added to the Phase 5 plan:** the candidate
eligibility predicate must include `AND jobs.closed_at IS NULL`, so closed jobs never enter the eval
funnel. (This spec amends the Phase 5 doc with that line; it does not implement it here.)

## 6. Testing

- **Unit (SpeedyApply):** 404→`closed_at` set + `gone:404`; 410→`gone:410`; 403→`closed_at` set +
  `unreachable:403`; timeout/5xx→`closed_at` stays None; success→None. (status-code branch)
- **Store (`@pytest.mark.postgres`):** `save_job` sets `closed_at` on a gone posting; preserves the
  **earliest** across re-confirm; **clears** on a later posting carrying JD; the quality-monotonic
  `jd_text`/`jd_quality` gate is **unaffected** by closed_at logic (regression).
- **Backfill (`@pytest.mark.postgres`):** marks stale-no-JD rows; leaves fresh rows and rows with JD
  untouched; `--apply`/dry-run honored; idempotent.
- **Migration:** upgrade adds nullable `closed_at`; downgrade drops it; round-trips on PG.
- **Gates:** architecture boundaries, ≤300 lines/file (store layer exempt), complexity ≤10.

## 7. Risks

- **403 false-positive:** a genuinely-open but WAF-walled job is marked closed and (once Phase 5 lands)
  excluded. **Mitigated:** (a) such a job has no JD so it is barely evaluable anyway; (b) `closed_at`
  is never a delete and **self-heals** on any successful enrich; (c) `enrich_error='unreachable:403'`
  keeps it auditable/recoverable as a class. Accepted per owner's domain knowledge that their 403s are
  predominantly closed reqs.
- **Producer/consumer split:** the signal is populated but inert until Phase 5 wires the filter. This
  is intentional (owner directive: leave Phase-5-owned consumption to Phase 5); the column is queryable
  and the backfill makes it immediately observable.

## 8. Out of plan note

This feature is not in an enumerated phase plan; it is owner-directed, sits between Phase 4 and Phase 5,
and depends on Phase 4 source code (branch cut from the Phase-4 tip, not `main`). A `writing-plans`
implementation plan follows this spec.
