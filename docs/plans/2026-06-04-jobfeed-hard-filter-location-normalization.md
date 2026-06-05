# Follow-up: Hard-Filter Location Normalization — Implementation Plan (DEFERRED)

> **Status: DEFERRED follow-up.** Not part of Phase 5. Picked up AFTER Phase 5 ships the legacy-parity hard filter (`domain/filtering.py` with `company_blocklist` + `location_allowlist`/`location_blocklist` + date/big-company tiering, crude substring matching). This plan strengthens ONLY the location-matching mechanism.

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Replace the crude substring location matching in the hard filter with deterministic **location normalization** — so "SF" / "San Francisco" / "South San Francisco" / "Bay Area" / "Fremont" all resolve to one canonical metro, and "Remote (US)" / "US Remote" / "WFH" parse into a work-arrangement axis — without introducing any fuzzy probability threshold into a hard constraint.

**Why deferred (not in Phase 5):** Phase 5's single new variable is the ML Gate. Location normalization is a separable, non-trivial enrichment that serves TWO call sites (scan-time `location_blocked()` and evaluate-time `apply_hard_filters`), so it gets its own slice. Phase 5 ships functional-but-crude location matching (legacy parity) with the safety valve (empty/unparseable location → not blocked, falls through to the LLM), which bounds the downside until this lands.

**Design principle (locked during Phase 5 design):** location is a deterministic *personal-policy* hard constraint (work authorization / relocation willingness / remote-only), NOT a fuzzy fit signal. So the strengthening MUST stay deterministic: normalize messy strings to canonical form, then match exactly. Do NOT move the location decision into the ML gate (location is not a gate feature and would require retraining) and do NOT make the hard block depend on an embedding/similarity threshold. Embedding/LLM may ONLY power a *soft suggestion* layer (propose new aliases for human confirmation), never an automatic block. See the Phase 5 plan's location discussion for the full rationale.

**Architecture:** A pure `normalize_location()` in `domain/normalize.py` (sibling to the existing company/title normalizers) splits a raw location string into two orthogonal axes — work arrangement (`remote`/`hybrid`/`onsite`, with country scope for remote) and a canonical geographic key (metro/state/country) — via a curated alias map plus an optional offline US gazetteer for the city long tail. `HardFilters` allowlist/blocklist entries are expressed in canonical terms; `apply_hard_filters` and scan-time `location_blocked()` match on the normalized form. Everything is deterministic, local, and explainable.

**Implementation repo:** `/Users/wenqiwang/wwq/jobfeed`.

---

## Implementation Decisions

**D1: Two orthogonal axes.** Parse work-arrangement (`remote`/`hybrid`/`onsite` + optional country for remote) separately from geography. "Hybrid - 3 days SF" → `(hybrid, metro=SF_BAY)`; "Remote (US)" → `(remote, country=US)`.

**D2: Alias map + offline gazetteer, in that priority.** A hand-curated alias map covers the high-frequency colloquial/abbreviated forms (SF, NYC, Bay Area). An offline US gazetteer (e.g. `geonamescache` / `us` / a vendored CBSA city→metro table — pick one with no network dependency) resolves the city long tail (Fremont, Berkeley, Daly City → SF_BAY metro). Alias map wins on conflict.

**D3: Deterministic only on the hard path.** No embedding/LLM/trigram in the block decision. `pg_trgm` is explicitly rejected for the block (it catches typos, not synonyms — "SF" vs "San Francisco" have near-zero trigram overlap).

**D4: Safety valve preserved.** Empty / unparseable / unresolvable location → return "passes" (do NOT block); the LLM Stage B reads nuanced location from JD text. (Legacy `filters.py:164` behavior.)

**D5: Optional soft suggestion layer (separate, non-blocking).** A `suggest_location_aliases()` utility may use the existing local embedder (all-MiniLM, already in the system) to flag location strings that are semantically close to an allowlist entry but did NOT normalize to it — surfaced to the user as "consider adding alias X → CANON" — never auto-applied. This keeps the hard decision deterministic while using semantics to grow the alias map.

---

## Tasks

### Task 1: `normalize_location()` in domain (pure)
**Files:** Modify `src/jobfeed/domain/normalize.py`; Test `tests/unit/test_normalize_location.py`.
**What to build:** `normalize_location(raw: str) -> NormalizedLocation` (frozen dataclass: `arrangement: Literal["remote","hybrid","onsite","unknown"]`, `remote_country: str | None`, `geo_key: str | None` (canonical metro/state/country key), `raw: str`). Work-arrangement regex axis + alias-map geographic axis. Pure stdlib (+ the vendored alias map as a data module). Empty/unresolvable → `geo_key=None`, `arrangement="unknown"`.
**Acceptance:**
- [ ] "SF", "San Francisco, CA", "South San Francisco", "Bay Area", "San Francisco Bay Area" → same `geo_key` (e.g. `SF_BAY`)
- [ ] "Remote (US)", "US Remote", "WFH", "Remote - United States" → `arrangement="remote"`, `remote_country="US"`
- [ ] "Hybrid - 3 days/week SF" → `arrangement="hybrid"`, `geo_key=SF_BAY`
- [ ] "" / "Mars" / unparseable → `geo_key=None`, `arrangement="unknown"`
- [ ] Pure stdlib; domain boundary test stays green; tests pass, committed

### Task 2: Offline gazetteer for the city long tail
**Files:** Create `src/jobfeed/domain/_location_gazetteer.py` (or vendored data + loader); Modify `normalize_location` to consult it after the alias map; Test extends `test_normalize_location.py`.
**What to build:** Resolve arbitrary US city/state strings to the canonical metro/state key using an offline dataset (no network). Alias map takes precedence; gazetteer fills the tail.
**Acceptance:**
- [ ] "Fremont, CA", "Berkeley", "Daly City" → `SF_BAY` metro without being in the hand alias map
- [ ] Resolution is offline (no network); deterministic; tests pass, committed

### Task 3: Wire normalized matching into `apply_hard_filters` + scan-time gate
**Files:** Modify `src/jobfeed/domain/filtering.py` (match on `normalize_location` output), the scan-time location gate; Test `tests/unit/test_filtering_location.py`.
**What to build:** `HardFilters.location_allowlist`/`location_blocklist` entries are canonical keys (or raw strings normalized at load). `apply_hard_filters` normalizes `job.location` and matches on `geo_key`/arrangement; preserve the empty→pass safety valve. Same normalization used by the scan-time `location_blocked` equivalent.
**Acceptance:**
- [ ] A job tagged "Fremont, CA" is blocked by an allowlist of `[SF_BAY]` ... wait — allowlist of `[SF_BAY]` should ADMIT it; a job "Austin, TX" is blocked by an allowlist of `[SF_BAY, REMOTE_US]`
- [ ] "Remote (US)" admitted by an allowlist containing `REMOTE_US`
- [ ] Empty location → not blocked (safety valve)
- [ ] Reason strings remain explainable ("location not in allowlist"); tests pass, committed

### Task 4 (optional): Soft alias-suggestion layer
**Files:** Create `src/jobfeed/adapters/ml/location_suggest.py` (uses the existing embedder); CLI surface (e.g. `jobfeed prefs suggest-locations`); Test `@mlmodel`.
**What to build:** For location strings that did NOT normalize to any allowlist key, compute embedding similarity to allowlist entries and surface high-similarity misses as suggested new aliases — output only, never auto-block.
**Acceptance:**
- [ ] Surfaces a plausible alias suggestion for a near-miss; never mutates filters automatically; `@mlmodel`-marked; committed

---

## Self-Review
Deterministic hard path preserved (D3/D4). Embedding confined to the optional non-blocking suggestion layer (D5). Serves both scan and evaluate call sites (Task 3). Prereq: Phase 5's legacy-parity `HardFilters` (with `location_allowlist`/`location_blocklist`) must exist first.
