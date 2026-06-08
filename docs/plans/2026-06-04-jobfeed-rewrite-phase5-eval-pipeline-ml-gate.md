# Phase 5: Eval Funnel Front-End — Hard Filter + Dedupe + ML Gate (inference) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Revision history:** Revised twice after two 6-reviewer passes (5 specialist Claude agents + independent Codex multi-lens review each round). Round 1 confirmed the architecture split and the no-scikit-learn XGBoost approach (Booster vs XGBClassifier measured **bit-identical, delta 0.0**, on the real model). Round 2 confirmed all round-1 blockers closed and surfaced the **gate→scoring handoff hole** + the **GateCandidate DTO incompatibility** + the **HardFilters field gap**; all are fixed below. Location *normalization* is split into a deferred follow-up plan (`docs/plans/2026-06-04-jobfeed-hard-filter-location-normalization.md`); Phase 5 ships legacy-parity (crude-but-functional) location matching.

> **Revision 3 (2026-06-04, pre-execution simplification — OVERRIDES any conflicting Decision/Task text below):** Locked with the project owner at execution kickoff. jobfeed is an independent **open-source** project that must run self-contained on any machine. (1) The trained XGBoost model is a **REQUIRED** asset **committed in-repo** under `models/ml_gate/` (latest version only); `[ml_gate].model_dir` defaults to that in-repo path. Overrides **Decision 5** (`~/.cache` + one-time copy) — no copy, `git clone` is self-contained. (2) **No model-missing fallback**: the model is always present, so a missing/unloadable model raises a plain error and stops. Overrides **Decision 6** (rules-only degrade) and **removes `ModelNotFoundError`**. (3) **Parity simplified**: replace the legacy bit-for-bit parity (pinned torch/ST versions, pinned batch size, perturbation-stable near-threshold fixture, cross-machine score band) with **ONE complete end-to-end `@mlmodel` test** — real model + real embedder over a handful of known should-pass (entry-level SWE) / should-block (senior, clearance, non-SWE) jobs, asserting the gate's pass/fail decisions. Overrides **Decision 13 + Task 9**. (4) **Funnel always runs**: load → hard-filter → dedupe → (gate if `ml_gate_enabled`) → claim-survivors-by-id is the sole Stage-A path; `claim_pending_stage_a` is retired from evaluate. (5) **`evaluate.py` stays ≤300** by folding the survivor-id claim into `_evaluate_claims.py`. (6) The `xgboost.Booster` objective check (**Decision 4**) is kept. Governance is recorded in `CLAUDE.md` (read-only `~/.jobfeed/`, in-repo model, scoped HuggingFace `all-MiniLM` download exemption — all approved 2026-06-04).

> **Revision 4 (2026-06-07 hardcoded-audit cleanup — OVERRIDES any conflicting Decision/Task text below):** `[ml_gate].model_version` is explicit (`v20260601T170453Z`) and runtime wiring must pass it into `XGBoostGate`; do not rely on lexicographic "latest" selection outside direct adapter tests. The required `.meta.json` sidecar must exist and include `threshold`, `embedding_model`, and `embedding_dim`; missing metadata is a hard failure, never a threshold-0.5 fallback. The inference embedder is `fastembed`/ONNX with the committed model metadata declaring `sentence-transformers/all-MiniLM-L6-v2` and `384`; stale instructions below that mention `sentence-transformers >= 3.0`, `torch`, `~/.cache/jobfeed/ml_gate_models`, model-copy fallback, or rules-only degrade are historical context only and must not be implemented.

**Goal:** Build the evaluation funnel's **front end** — wire the hard filter (extended to legacy parity), fold twins via the Phase 4 dedupe primitive so only one representative per cluster is considered, add the ML Gate as a cheap local pre-filter (loading the *already-trained* XGBoost model), and hand the surviving representatives' job-ids to the **existing** Stage A/B scoring path. Lock the funnel's persisted shape with an evaluation-persistence contract test. **Phase 5 does NOT build or modify Stage A/B scoring (Phase 3 already did).**

**Architecture:** Same hexagonal structure from Phases 0–4. Pure regex feature extraction + deterministic hard-fail rules live in `domain/ml_features.py` (stdlib only). A single predict-only `MLGate` port (`ports/ml_gate.py`) is what `services/` depends on. The ML stack (numpy vectorization, sentence-transformer embedding, XGBoost prediction) lives behind one adapter family (`adapters/ml/`), with the embedder a constructor-injected internal collaborator (NOT a port) and a `MockGate` first-class adapter for fast unit/CI. **Gating is a NON-CLAIMING pass:** it loads ungated/unscored candidates (no claim), hard-filters + dedupes + gates them in-service, persists each gate decision, and produces a **survivor job-id list**. Stage A then claims **exactly those ids** (`claim_stage_a_by_ids`, mirroring legacy "score the queued list"), so no ungated / gate-failed / non-representative row can be scored, and nothing is marked `in_progress` until it survives the funnel.

**Tech Stack additions:** `xgboost >= 3.2.0` (native `Booster` JSON load — NO scikit-learn; artifact saved by xgboost 3.2.0), `numpy >= 1.26`, `fastembed`/ONNX for `all-MiniLM-L6-v2` embeddings (384-dim). Inference-only. `scikit-learn` deliberately NOT added.

**Spec reference (REWRITE repo):** `/Users/wenqiwang/wwq/jobfeed/docs/specs/2026-05-20-jobfeed-rewrite-design.md` — §14 (ML Gate), §7 (EvaluateWorkflow), §9 (config/preferences), §11 (Testing). Bare File Map paths are rooted in the rewrite repo `/Users/wenqiwang/wwq/jobfeed`.

**Legacy behavioral reference (READ THE CODE, NOT DOCSTRINGS — they drifted):**
- `ml_gate/extractor.py` — `extract(title, jd_text) -> dict`, 10 keys (`:399`); `_clearance_required` int (`:260`), `_clearance_status` str (`:264`).
- `ml_gate/features.py` — `featurize`. **Docstring STALE** ("36 techs/`[26:62]`"); real `_TECH_NAMES`=**39**, `STRUCTURED_DIM`=**66** (5+4+1+1+5+1+9+39+1), layout `seniority[0:5] degree[5:9] clearance[9] school[10] role[11:16] yoe[16] domains[17:26] techs[26:65] is_swe[65]`, +384 = **450**. Unknown one-hot → all-zeros (`:79-82`); `yoe_norm=min(yoe/10,1.0)`, 0.0 if None.
- `ml_gate/rules.py` — `yoe_min>=2`→`f"yoe_min >= {int(yoe)}"`; `clearance_status in ("active_required","ambiguous")`→`"active clearance required"`; `not is_swe_role`→`"not software engineering role"`; keys on **`clearance_status`**.
- `ml_gate/predictor.py` — extract → hard-fail short-circuit (`score=0.0`,`result="fail"`, NO embed) → `format_input` → embed → `featurize` → `predict_proba[:,1]` → `pass if score>=threshold`. Batch embeds only non-hard-failed subset, re-aligns by original index (`:122-152`).
- `ml_gate/embedder.py` — `all-MiniLM-L6-v2`, `normalize_embeddings=True`. **Double truncation:** `format_input`=`f"{title} | {jd_text[:2000-len(title)-3]}"` AND `embed` re-slices `[:2000]`.
- `ml_gate/train.py` — `load_model -> (model, threshold)`: model `v{ts}.json` (native), meta `v{ts}.meta.json` (`threshold` key, e.g. 0.19), latest = last `sorted(glob("v*.json"))` excl. meta, default 0.5 if meta missing. **Only the loader half is in scope.**
- `filters.py` — **legacy hard filter division of labor (authoritative):** "Title allowlist/blocklist and JD-text blocklist have moved to the ML gate." Active `apply()` checks ONLY: company_blocklist → location (allowlist/blocklist, empty location passes) → date freshness on `scraped_at` with **big-company tiering** (`big_company_list` → `big_company_days` default 90, else `posted_within_days`). Crude substring matching (`_first_substring`/`_any_substring`). `location_blocked()` is a scan-time subset (out of Phase 5 scope).
- `main.py:967,1000,1004-1085` — legacy evaluate: load `unrated` (no claim, no dedupe) → `hard_filter` (count `hf_blocked`) → `ml_gate.predict_batch` over `ml_gate_result IS NULL` rows (count `ml_gate_blocked`=non-pass) → keep `pass` → **score exactly that list** (`queued`), then `[:limit]`.

**Plan path:** `docs/superpowers/plans/2026-06-04-jobfeed-rewrite-phase5-eval-pipeline-ml-gate.md` (sync a copy into the rewrite repo `docs/plans/`).

**Prerequisite (verified in the rewrite repo):** `EvaluateService` + Stage A/B + budget + claims (`services/evaluate.py` — **299 lines**, Decision 12) + `_evaluate_claims.py` (`load_stage_a_for_run`→`claim_pending_stage_a`); `domain/dedupe.py` (`pick_representatives`, `twin_key` — both take **`JobPosting`** and read raw `.company`/`.title`/`.canonical_id`/`.quality`/`.platform`/`.posted_at`); `domain/filtering.py` (`HardFilters`=ONLY `title_blocklist`+`company_blocklist` today; `apply_hard_filters(job: JobPosting, filters)` reads `.title`/`.company`); `domain/quality.py:quality_rank`; `MLGateResult` (`domain/models.py:133`; `clearance_required`/`school_restricted` are `bool | None`); `JobStore.save_ml_gate_result` (sets `ml_gate_at=now()` server-side, writes 13 columns); `_job_from_record` (`postgres.py` — does NOT hydrate ml_gate columns); `claim_pending_stage_a` (`postgres.py:736-779` — filters corpus/quality/max_days/retry-cap/stale only, NO `ml_gate_result`); `ScoringSettings.ml_gate_enabled` (default False); `EvaluateRuntimeConfig` (has `llm`/`stage_a_threshold`/`resume_text`, NO `ml_gate_enabled`); `PipelineRun.jobs_filtered`/`jobs_ml_gated` (default 0, no current writer); `jobs_scored = stage_a_scored + stage_b_scored` (call-count, not unique jobs).

**Implementation repo:** `/Users/wenqiwang/wwq/jobfeed`. Do NOT implement in the legacy repo.

**⚠️ Governance prerequisite (before Task 6/7):** Add a Phase 5 line to the rewrite `CLAUDE.md` phase-plan list (currently ends at Phase 4, ~line 52). No constraint *amendment* needed: local CPU inference, artifacts under `~/.cache/jobfeed/` (NOT `~/.jobfeed/`); the only network is a one-time HuggingFace model download, reached only by `@pytest.mark.mlmodel` paths (excluded from `make quality`) and real runs.

**Precedence:** This plan overrides the spec where they conflict — esp. §14's literal `domain/ml_gate.py` placement (Decision 1) and the spec field name `ml_gate_hard_fail_reason` (implemented column is `ml_gate_fail_reason`, Decision 11).

**Commit strategy:** One conventional commit per task. No Claude/AI attribution.

**Execution mode:** Sequential.

---

## Implementation Decisions

**Decision 1: ML inference behind a port; `domain/` stays pure (divergence from spec §14).** Pure feature extraction + hard-fail rules + vocab/layout → `domain/ml_features.py`; numpy/xgboost/ST → `adapters/ml/`. Confirmed twice to keep `test_architecture_boundaries.py` green.

**Decision 2: One predict-only `MLGate` port; embedder injected, NOT a port (Approach C).**

**Decision 3: Predict-only — load existing artifacts; NO training; NO scikit-learn.** `ml-gate train` deferred; `ml-gate info` included.

**Decision 4: Native `xgboost.Booster` — CONFIRMED bit-identical to legacy `XGBClassifier.predict_proba[:,1]`** (reviewers measured delta 0.0 on the real model: native JSON, `objective.name="binary:logistic"`, `num_feature=450`, no `best_iteration`). Load with `Booster().load_model`; predict the positive-class probability (`output_margin=False`, no `base_margin`). **Fail-fast if objective != `binary:logistic`, read via `json.loads(booster.save_config())["learner"]["objective"]["name"]`** (NOT the model file/meta). Pin `xgboost>=3.2.0`.

**Decision 5: Artifacts under `~/.cache/jobfeed/ml_gate_models/`** (config `[ml_gate].model_dir`, expanduser'd). Not committed; one-time copy documented.

**Decision 6: `ModelNotFoundError` in `ports/ml_gate.py` (core-safe); missing model never crashes.** `services/` may import `ports/` but not `adapters/`, so the exception must live in `ports/`. When `ml_gate_enabled` but no model: degrade to rules-only (hard-fail rules apply; rule-survivors → `result="pass"`, `score=0.0`, `version="rules-only"`, `fail_reason=None`), log once, do NOT abort. When disabled: skip the gate stage entirely.

**Decision 7: Gating is a NON-CLAIMING pass; Stage A claims survivors BY ID (fixes the claim-leak AND the handoff hole).**
The current Stage A path (`claim_pending_stage_a`) atomically marks loaded rows `in_progress` BEFORE any in-service filtering AND has NO `ml_gate_result` predicate — so (a) running filter/dedupe/gate after it leaks dropped rows as `in_progress`, and (b) leaving it "unchanged" would let it re-claim and score every ungated/gate-failed/non-representative row, nullifying the gate. **Fix — uniform claim-by-ids handoff:**
1. **Funnel (no claim):** `load_gate_candidates(...)` (Task 5) loads eligible, **not-yet-Stage-A-scored** rows that are not gate-failed, as `list[JobPosting]`, no claim, no Stage-A limit (capped at `ml_gate.max_candidates`, logged if hit).
2. In-service `apply_hard_filters` (drop → `jobs_filtered++`).
3. In-service `pick_representatives` over the full set (no claim/limit split → representative chosen from the complete cluster).
4. If `ml_gate_enabled` and a gate is injected: send representatives whose `ml_gate_result IS NULL` to `gate.predict_batch`; `save_ml_gate_result` each; `jobs_ml_gated += <non-pass>`. Representatives already `ml_gate_result='pass'` (from a prior crashed run) are kept WITHOUT re-gating. `ModelNotFoundError` → Decision 6.
5. **Survivor job-ids** (hard-filter ∩ representative ∩ gate-pass; or, gate disabled, hard-filter ∩ representative) are handed to `_run_stage_a`, which calls the NEW `claim_stage_a_by_ids(survivor_ids)` (Task 5) — atomic claim (FOR UPDATE SKIP LOCKED + `in_progress` insert + retry-cap/stale semantics) restricted to those ids — then the existing Stage A → Stage B scoring runs UNCHANGED. Empty survivor set → claim nothing, return early.
Because gating never claims and Stage A only claims survivor ids, no leak and no bypass. Two concurrent runs may both gate a row (idempotent UPDATE, same decision); the by-id claim still serializes scoring.

**Decision 8: Idempotency + crash-recovery via the load predicate (no in-service gate-state filter — `JobPosting` carries none).** `load_gate_candidates` selects rows that are eligible (corpus/quality/freshness) AND not-yet-Stage-A-scored AND (when `ml_gate_enabled`) `ml_gate_result IS DISTINCT FROM 'fail'`. Therefore: gate-failed rows are never reconsidered; `NULL` rows get gated; **`'pass'`-but-unscored rows (e.g. crash between gate-write and Stage-A claim) are re-loaded and re-handed to scoring without re-gating** — closing the crash-recovery gap. When `ml_gate_enabled=false`, the gate predicate is dropped (load eligible + unscored regardless of gate state). Non-representative twins stay ungated (folded out each pass by `pick_representatives`); intended — display fold is Phase 8.

**Decision 9: Counters declared authoritatively.** `jobs_filtered` = hard-filter-blocked count; `jobs_ml_gated` = gate-blocked (non-pass) count. `jobs_scored` keeps its existing Stage A + Stage B **call-count** semantic (`evaluate.py:85`); Task 8 asserts it explicitly.

**Decision 10: int→bool coercion at the gate→`MLGateResult` boundary.** `MLGateFeatures.clearance_required`/`school_restricted` are `int` (legacy returns 0/1); `MLGateResult` types them `bool | None`; coerce `int(0/1)→bool` in the gate adapters/MockGate; pinned by the Task 8 contract test.

**Decision 11: Field name = implemented `ml_gate_fail_reason` / `MLGateResult.fail_reason`, NOT spec's `ml_gate_hard_fail_reason`.** Hard-vs-model fail discriminated by `fail_reason` non-null (hard) vs null (model). Adapter sets `fail_reason=None` on model-driven fails.

**Decision 12: Funnel in NEW `services/_evaluate_funnel.py`; `evaluate.py` (299/300) gains ≤ a few call-site lines.** Single-level loops only (nested loops forbidden in `services/`); persist+count via a named single-pass helper or `asyncio.gather` (mirror `_run_stage_a`'s gather+worker). `ml_gate_enabled` is threaded via a NEW field on `EvaluateRuntimeConfig` (services may not import `jobfeed.config`); needed to distinguish "enabled-but-no-model → rules-only" from "disabled → skip".

**Decision 13: Parity asserts the DECISION exactly + a tree-aware score band (measured).** XGBoost is piecewise-constant: same-machine prediction is bit-deterministic (measured delta 0.0); a cross-machine embedding drift that crosses a tree split causes a discrete jump of ~1e-3–1e-2 (NOT ~1e-4). So Task 9: assert `result` (pass/fail) **EXACTLY**; assert score `== 0.0` delta same-machine; cross-machine assert ONLY a wide sanity band `abs(delta) < 0.05` (gross-drift catch: wrong model/layout/objective/truncation move scores >>0.05). Pin embedding **batch size** between fixture-gen and test, `embedding_max_chars=2000`, run on **CPU** with pinned `sentence-transformers`+`torch` versions. The near-threshold (0.19) fixture is chosen by **perturbation-stability** (its decision must not flip under ~1e-5 embedding noise during fixture-gen) so it guards the boundary without being flaky.

**Decision 14: Dry-run previews the FREE funnel front-end.** Dry-run runs `load_gate_candidates` + `apply_hard_filters` + `pick_representatives` + the gate (local inference is free; rules-only if model missing) to preview the TRUE survivor set, but **persists nothing** and calls NO Stage A/B LLM. (Legacy dry-run previews the gated `queued` list, `main.py:1102`.)

**Decision 15: Hard filter = legacy `apply()` parity (company + location + date), title is the gate's job; location matching is crude substring (normalization deferred).** Extend `domain/filtering.py` `HardFilters` to legacy's ACTIVE fields: `company_blocklist`, `location_allowlist`, `location_blocklist`, `posted_within_days`, `big_company_list`, `big_company_days`; extend `apply_hard_filters` to check company → location (empty passes) → date with big-company tiering, matching `scraped_at`/`discovered_at`. **`title_blocklist` is NOT applied in the funnel** (seniority/title filtering is the ML gate's job — legacy moved it there); keep the field for back-compat deserialization only. Location uses crude substring (legacy parity); the deterministic **location normalization** strengthening is the deferred follow-up plan `docs/plans/2026-06-04-jobfeed-hard-filter-location-normalization.md`. Empty location → not blocked (safety valve).

---

## File Map

```
jobfeed/                                  # /Users/wenqiwang/wwq/jobfeed
├── src/jobfeed/
│   ├── domain/
│   │   ├── ml_features.py                # CREATE — pure: extract + hard_fail + MLGateFeatures + vocab/STRUCTURED_DIM
│   │   └── filtering.py                  # MODIFY — extend HardFilters + apply_hard_filters (company+location+date, legacy parity)
│   ├── ports/
│   │   ├── ml_gate.py                    # CREATE — MLGate protocol + GateInput + ModelNotFoundError
│   │   └── store.py                      # MODIFY — add load_gate_candidates + claim_stage_a_by_ids to JobStore
│   ├── adapters/
│   │   ├── ml/                           # CREATE (mirrors llm/, sources/)
│   │   │   ├── __init__.py
│   │   │   ├── _embedder.py              # CREATE — SentenceTransformerEmbedder (injectable) + EmbedderProtocol (NOT a port)
│   │   │   ├── _vectorize.py             # CREATE — featurize(features, embedding)->np.ndarray (66+384 exact)
│   │   │   ├── xgboost_gate.py           # CREATE — MLGate: Booster load + objective check + extract→hardfail→embed→vectorize→predict
│   │   │   └── mock.py                   # CREATE — MockGate (first-class, deterministic, real hard-fail)
│   │   └── store/postgres.py             # MODIFY — load_gate_candidates (no claim; returns JobPosting) + claim_stage_a_by_ids
│   ├── services/
│   │   ├── _evaluate_funnel.py           # CREATE — non-claiming pass: load→hard filter→dedupe→gate→survivor ids
│   │   ├── evaluate.py                   # MODIFY — call funnel; _run_stage_a claims survivor ids; dry-run preview (≤ few net lines)
│   │   └── evaluate_types.py             # MODIFY — EvaluateDependencies += ml_gate/hard_filters; EvaluateRuntimeConfig += ml_gate_enabled
│   ├── config.py                         # MODIFY — MLGateSettings + HardFiltersSettings (preferences surface)
│   ├── config.example.toml               # MODIFY — [ml_gate] + [hard_filters]
│   ├── cli/{__init__.py,evaluate.py}     # MODIFY — build gate + hard filters lazily; thread ml_gate_enabled
│   ├── cli/ml_gate.py                    # CREATE — `jobfeed ml-gate info`
│   └── pyproject.toml                    # MODIFY — xgboost>=3.2.0, numpy, sentence-transformers; `mlmodel` marker
└── tests/
    ├── unit/{test_ml_features,test_ml_vectorize,test_mock_gate,test_hard_filter,test_evaluate_funnel}.py   # CREATE
    ├── contract/test_evaluation_persistence.py                                                            # CREATE
    ├── integration/{test_gate_candidates_query,test_phase5_evaluate_chain}.py                             # CREATE @postgres
    ├── mlmodel/{test_xgboost_gate_load,test_ml_gate_parity}.py                                            # CREATE @mlmodel
    └── fixtures/{ml_gate_tiny_model.json,ml_gate_parity_cases.json}                                       # CREATE
```

---

## Task 0: Dependencies + ML Gate Config
**Files:** `pyproject.toml`, `config.py`, `config.example.toml`; Test `tests/unit/test_config.py`.
**What to build:** Add `xgboost>=3.2.0`, `numpy>=1.26`, `sentence-transformers>=3.0` (NOT scikit-learn); register `mlmodel` marker excluded from default `addopts` (with `postgres`/`live`/`browser`). Add `MLGateSettings` (`extra="forbid"`) on `Settings.ml_gate`: `model_dir="~/.cache/jobfeed/ml_gate_models"`, `embedding_model="all-MiniLM-L6-v2"`, `embedding_max_chars=Field(2000,gt=0)`, `threshold_override: float|None=Field(None,ge=0,le=1)`, `max_candidates=Field(5000,ge=1)`. Keep `scoring.ml_gate_enabled`. `[ml_gate]` block in example with the Decision 5 copy-step comment.
**Acceptance:**
- [ ] deps installable; scikit-learn NOT added; `mlmodel` marker excluded from `make quality` (no ST/xgboost import at collection)
- [ ] `Settings.ml_gate` exposes all five fields; unknown `[ml_gate]` keys raise; `scoring.ml_gate_enabled` defaults false
- [ ] tests pass, committed

## Task 1: Hard Filter — extend to legacy parity (company + location + date)
**Files:** Modify `domain/filtering.py`, `config.py`, `config.example.toml`; Test `tests/unit/test_hard_filter.py`.
**What to build:** Extend `HardFilters` with legacy `apply()`'s ACTIVE fields: `location_allowlist`, `location_blocklist`, `posted_within_days: int|None`, `big_company_list`, `big_company_days: int=90` (keep existing `title_blocklist`/`company_blocklist`). Extend `apply_hard_filters(job, filters)` to the legacy chain: (1) company_blocklist substring → block; (2) if `job.location` non-empty: allowlist miss → block, blocklist hit → block (empty location → pass); (3) freshness on `job.discovered_at`/`scraped_at`: `big_company_days` if company in `big_company_list` else `posted_within_days` (None → no date filter). **Do NOT apply `title_blocklist`** (gate's job; document this). Crude substring matching (legacy parity); location normalization is the deferred follow-up plan. Add `HardFiltersSettings` config surface (spec §9 preferences keys) + a `to_domain()`/builder; `[hard_filters]` example block with empty/safe defaults (no filtering when unset).
**Acceptance:**
- [ ] `HardFilters` has location_allowlist/blocklist, posted_within_days, big_company_list/days; `apply_hard_filters` blocks on company / location allow-miss / location block-hit / older-than-days; empty location passes
- [ ] big-company tiering: a `big_company_list` company uses `big_company_days`, others `posted_within_days`
- [ ] `title_blocklist` is NOT used by `apply_hard_filters` (asserted: a "Senior …" title is NOT blocked here)
- [ ] `[hard_filters]` config parses into `HardFilters`; empty/missing → no-op; unknown keys raise
- [ ] tests pass, committed

## Task 2: Domain Feature Extraction + Hard-Fail Rules (pure)
**Files:** Create `domain/ml_features.py`; Test `tests/unit/test_ml_features.py`.
**What to build:** Pure port of `extractor.py`+`rules.py`+`features.py` constants. `MLGateFeatures` frozen dataclass (`seniority_level:str, degree_required:str, clearance_required:int, clearance_status:str, school_restricted:int, domain_tags:list[str], tech_required:list[str], role_type:str, yoe_min:int|None, is_swe_role:bool`). `extract_features(title, jd_text)`. `hard_fail_reason(features)->str|None` (exact strings; keys on `clearance_status`). Constants: ordered `SENIORITY_LEVELS/DEGREE_LEVELS/ROLE_TYPES/DOMAIN_NAMES(9)/TECH_NAMES(39)` verbatim; `STRUCTURED_DIM=66` asserted in code. No numpy.
**Acceptance:**
- [ ] stdlib-only; boundary test green
- [ ] `extract_features` reproduces legacy field-for-field across a battery (seniority/degree/clearance/yoe/role/each domain/each tech/is_swe)
- [ ] TECH_NAMES=39, DOMAIN_NAMES=9, STRUCTURED_DIM==66
- [ ] `hard_fail_reason` exact strings; `None` for clean entry-level SWE; keys on `clearance_status`
- [ ] tests pass, committed

## Task 3: MLGate Port + GateInput + ModelNotFoundError + MockGate
**Files:** Create `ports/ml_gate.py`, `adapters/ml/__init__.py`, `adapters/ml/mock.py`; Test `tests/unit/test_mock_gate.py`.
**What to build:** `ports/ml_gate.py` (domain only): `@runtime_checkable MLGate` with `async def predict_batch(jobs: list[GateInput]) -> list[MLGateResult]` (ordered); frozen `GateInput(job_id, title, jd_text)`; `class ModelNotFoundError(Exception)`. `MockGate`: runs real `hard_fail_reason`; non-hard-failed → configurable verdict (`default_result="pass"`, optional fail predicate); `score` fixed, `version="mock"`, features via `extract_features` with int→bool coercion.
**Acceptance:**
- [ ] port imports domain only; `MLGate` runtime_checkable; `ModelNotFoundError` defined here
- [ ] `isinstance(MockGate(),MLGate)`; ordered one-per-input; empty→empty
- [ ] yoe≥2 → fail+reason regardless of default; default/override honored otherwise; clearance/school are `bool` on output
- [ ] tests pass, committed

## Task 4: XGBoost Gate Adapter (embedder + vectorize + predict)
**Files:** Create `adapters/ml/_embedder.py`, `adapters/ml/_vectorize.py`, `adapters/ml/xgboost_gate.py`, `tests/fixtures/ml_gate_tiny_model.json`; Test `tests/unit/test_ml_vectorize.py`, `tests/mlmodel/test_xgboost_gate_load.py`.
**What to build:**
- `_embedder.py`: `SentenceTransformerEmbedder` (lazy ST import; `embed_batch` `normalize_embeddings=True`; `format_input(title, jd_text, max_chars)` legacy double-truncation) + structural `EmbedderProtocol` (NOT in ports/).
- `_vectorize.py`: `featurize(MLGateFeatures, embedding)->np.ndarray` exact legacy numeric port (one-hots w/ all-zeros unknown; `yoe_norm=min(yoe/10,1.0)` 0.0 if None at idx16; binary domains/techs; concat `[structured(66),embedding(384)]` float32 (450,)); vocab from `domain.ml_features` (no dup).
- `xgboost_gate.py`: `XGBoostGate(MLGate)`; constructor injects `model_dir`, embedder (default ST), `threshold_override`, `embedding_max_chars`; load latest `v*.json` via `Booster().load_model`; objective check via `save_config()` (fail-fast); threshold from override else meta (0.5 if absent); `version=path.stem`; missing → `ModelNotFoundError`. `predict_batch`: extract→hard_fail short-circuit (no embed) ; rest embedded as batch, vectorized, stacked (n,450), scored (pos prob), `pass if score>=threshold`, `fail_reason=None` on model fail; re-align by original index; int→bool coercion.
**Acceptance:**
- [ ] `featurize`→float32 (450,); structured prefix 66; index positions match domain vocab; yoe None→idx16=0.0, 25→1.0; unknown one-hot all-zeros; no duplicated vocab; runs under `make quality` with a fake 384-vec
- [ ] `XGBoostGate` loads tiny fixture + meta threshold; objective≠binary:logistic → fail-fast; missing model → `ModelNotFoundError`
- [ ] hard-failed skip embed (assert fake embedder NOT called) + correct re-align; `threshold_override` beats meta; model-fail `fail_reason=None`
- [ ] tiny fixture model built via xgboost native API (numpy only); real-artifact test `@mlmodel`
- [ ] tests pass, committed

## Task 5: Store — `load_gate_candidates` + `claim_stage_a_by_ids`
**Files:** Modify `ports/store.py`, `adapters/store/postgres.py`; Test `tests/integration/test_gate_candidates_query.py`.
**What to build:**
- `load_gate_candidates(*, corpus, quality_bands, max_days, limit, exclude_gate_failed: bool) -> list[JobPosting]` — reuse the existing eligibility predicate (`_stage_a_pending_filters`: corpus/quality/freshness on `discovered_at`) AND not-yet-Stage-A-scored AND (when `exclude_gate_failed`) `ml_gate_result IS DISTINCT FROM 'fail'`; **no claim / no `in_progress` insert**; cap at `limit`; build via the existing `_job_from_record` (returns `JobPosting` — reuse, do NOT invent a DTO).
  - **Job-liveness (DONE — `closed_at` feature, see `docs/specs/2026-06-04-job-liveness-closed-at-design.md`):** the shared eligibility predicate (`_stage_a_pending_filters`, consumed by `load_gate_candidates` AND every claim path — `claim_pending_stage_a` and `claim_stage_a_by_ids`) now appends `AND jobs.closed_at IS NULL`, so reqs confirmed-gone by JD fetch (404/410/403) never enter the funnel. The `closed_at` column + producer + backfill shipped on `main` (job-liveness PR); this consumer predicate was added at the Phase-5-onto-main rebase (no longer blocked). Covered by `test_closed_at_row_excluded_from_load_and_claim`.
- `claim_stage_a_by_ids(job_ids: list[str], *, ...) -> list[JobPosting]` — atomic claim restricted to `job_ids` (FOR UPDATE SKIP LOCKED + `in_progress` insert), reusing the existing claim machinery (retry-cap/stale-takeover) but scoped to the id set; empty ids → empty result, no writes.
**Acceptance:**
- [ ] `load_gate_candidates` returns `JobPosting`s with `ml_gate_result IS DISTINCT FROM 'fail'` excluded when `exclude_gate_failed`; includes NULL and 'pass'-unscored rows; excludes already-Stage-A-scored; with `exclude_gate_failed=false` ignores gate state
- [ ] `load_gate_candidates` writes NO `evaluations`/`in_progress` row (assert candidate `stage_a_status` untouched on PG)
- [ ] returns full eligible set up to `limit`; freshness uses `discovered_at`
- [x] eligibility predicate excludes `closed_at IS NOT NULL` rows (job-liveness; a confirmed-gone row is not returned by `load_gate_candidates` NOR claimed by `claim_stage_a_by_ids`) — `test_closed_at_row_excluded_from_load_and_claim`
- [ ] `claim_stage_a_by_ids` claims only the given ids atomically (marks them `in_progress`), leaves others untouched; empty ids → no writes
- [ ] `@postgres`; committed

## Task 6: Funnel Service + Handoff Wiring
**Files:** Create `services/_evaluate_funnel.py`; Modify `services/evaluate.py`, `services/evaluate_types.py`; Test `tests/unit/test_evaluate_funnel.py`.
**What to build:** `_evaluate_funnel.py` runs Decision 7 steps 1–5 and returns survivor job-ids. `EvaluateDependencies += ml_gate: MLGate|None=None, hard_filters: HardFilters|None=None`; `EvaluateRuntimeConfig += ml_gate_enabled: bool=False`. `evaluate.py`: call the funnel before Stage A; `_run_stage_a` claims survivors via `claim_stage_a_by_ids` (instead of `claim_pending_stage_a`) when the funnel ran; dry-run uses the funnel for preview (Decision 14, no persist/LLM). `ModelNotFoundError` → Decision 6 fallback. Single-level loops; persist+count via helper/gather; `evaluate.py` ≤300 lines (Decision 12).
**Acceptance:**
- [ ] hard-filter-blocked candidates not scored, `jobs_filtered++`; only the representative is gated/scored; non-reps never claimed
- [ ] `ml_gate_enabled=true`+MockGate: gate-failed reps persisted + excluded; passed reps' ids handed to `claim_stage_a_by_ids`; `jobs_ml_gated`=non-pass; a pre-existing `'pass'`-unscored rep is scored without re-gating
- [ ] `ml_gate_enabled=false` OR no gate: gate skipped, `jobs_ml_gated=0`, hard-filter+dedupe survivors scored; existing evaluate tests still pass
- [ ] `ModelNotFoundError` → run does not abort (rules-only/skip, logged once)
- [ ] dry-run previews survivors without persisting or calling Stage A/B LLM
- [ ] no nested loops in new `services/` code; `evaluate.py` ≤300 lines
- [ ] tests use MockGate + fake/in-memory store; committed

## Task 7: CLI Wiring + `ml-gate info`
**Files:** Modify `cli/__init__.py`, `cli/evaluate.py`; Create `cli/ml_gate.py`; Test `tests/unit/test_cli_ml_gate.py`.
**What to build:** Lazily build gate + hard filters for `evaluate`; thread `scoring.ml_gate_enabled` into `EvaluateRuntimeConfig.ml_gate_enabled`. `ml_gate_enabled=false` → no gate; true → `XGBoostGate(...)` (Decision 6 fallback if missing). Dev/test escape hatch selects `MockGate` (consistent with MockLLM selection). `jobfeed ml-gate info`: print version, threshold, `model_dir`, real meta keys (`recall_pos`,`precision_pos`,`f1`,`train_size`); no model → "no model; rules-only", exit 0. `ml-gate train` omitted (documented deferral).
**Acceptance:**
- [ ] `ml-gate info` prints version+threshold+real meta metrics; no-model → rules-only message, exit 0
- [ ] `evaluate` injects gate+filters only when configured; `ml_gate_enabled=false` runs without importing xgboost/ST (lazy)
- [ ] `ml-gate train` absent; CLI tests need no download; committed

## Task 8: Evaluation Persistence Contract + Integration (companion)
**Files:** Create `tests/contract/test_evaluation_persistence.py`, `tests/integration/test_phase5_evaluate_chain.py`.
**What to build:** Contract (no PG): assert every persisted field — `ml_gate_score`(float∈[0,1]), `ml_gate_result`(∈{pass,fail}), `ml_gate_fail_reason`(str|None), `ml_gate_version`, the 9 feature columns (incl. `clearance_required`/`school_restricted` **bool**) — plus Stage A (`score`,`one_line`,`timing_eligible`,model,prompt_hash,resume_hash) + Stage B (verdict,jd_summary,fit_analysis,resume_hooks,raw_blocks,model,hashes) against frozen fixtures; renaming any → red; pin column `ml_gate_fail_reason`. Integration (`@postgres`, MockLLM+MockGate): seed twin cluster + hard-fail + gate-fail + pass job; run `evaluate`; assert representative-only scoring, counters, gate persistence, **handoff isolation (gate-fail / never-gated / non-rep rows are NOT claimed or scored)**, dropped rows NOT left `in_progress`, second run re-gates nothing, and a **`'pass'`-but-unscored row left by an interrupted run gets scored on the next run** (crash recovery).
**Acceptance:**
- [ ] contract enumerates+type-asserts every persisted ml_gate (incl. bool) + Stage A + Stage B field; rename → red; pins `ml_gate_fail_reason`
- [ ] integration: only representative survivor Stage-A/B scored; `jobs_filtered`/`jobs_ml_gated` correct; `jobs_scored` matches call-count semantic
- [ ] handoff isolation: gate-fail / ungated / non-rep rows are NOT scored and NOT marked `in_progress`
- [ ] crash recovery: a `'pass'`-unscored row is scored on the next run
- [ ] second `evaluate` re-gates nothing already-gated; `@postgres`; contract in `make quality`; committed

## Task 9: Inference Parity Guard (`mlmodel`)
**Files:** Create `tests/fixtures/ml_gate_parity_cases.json`, `tests/mlmodel/test_ml_gate_parity.py`; Modify README.
**What to build:** Generate `ml_gate_parity_cases.json` from the LEGACY `MLGatePredictor` (real `v20260601T170453Z`) over N≥14 cases incl. intern/senior/clearance/non-SWE/high-yoe/multi-tech + **empty JD**, **>2000-char JD**, **unicode JD**, and a **perturbation-stable near-0.19** case (Decision 13): record `(title, jd_text, score, result)`. The `@mlmodel` test loads the same model via rewrite `XGBoostGate` (real embedder, `embedding_max_chars=2000`, pinned batch size, CPU) and asserts: `result` matches **EXACTLY** every case; score delta `==0.0` same-machine / `<0.05` sanity band cross-machine. README documents the one-time `~/.cache/jobfeed/ml_gate_models/` copy + that the parity gate is a **manual release step** (CPU, pinned ST/torch versions), not CI.
**Acceptance:**
- [ ] ≥14 cases incl. empty/long/unicode/near-threshold, from legacy + real model
- [ ] `@mlmodel` test: pass/fail EXACT every case; score same-machine `==0.0`, cross-machine `<0.05`; `embedding_max_chars=2000`, pinned batch size, CPU
- [ ] README documents copy step + manual release-gate + `pytest -m mlmodel`; excluded from `make quality`; committed

---

## Self-Review
**Spec coverage:** §14 extraction→T2; layout 66+384→T2/T4; embedding→T4; XGBoost+threshold→T4/T6; output fields (mapped, `fail_reason` rename)→T4/T6/T8; persistence→T6/T8; artifacts→T4/T7; fallback→D6/T6/T7; `info`→T7 (`train` deferred D3); §7 funnel order (hard-filter+dedupe prepended, non-claiming + claim-by-ids handoff)→D7/D8/T5/T6; §9 hard-filter parity→D15/T1; §11 mock-first→T3; companion contract→T8. Scope: funnel front-end only; Stage A/B pre-existing. Location normalization → deferred follow-up plan. Non-rep display fold → Phase 8.
**Round-2 fixes applied:** handoff hole → claim-by-survivor-ids (D7) + crash-safe load predicate (D8) + handoff-isolation/crash-recovery tests (T8); GateCandidate DTO incompatibility → `load_gate_candidates` returns `JobPosting` (T5); HardFilters gap → extend to legacy parity, title excluded (D15/T1); parity tolerance → measured scheme (D13/T9); dry-run bypass → D14; corpus="all" re-gating → not-yet-scored predicate (D8/T5); `ml_gate_enabled` carrier → `EvaluateRuntimeConfig` (D12/T6); max_days→discovered_at (T5); xgboost>=3.2.0 (T0); int→bool (D10); field name (D11); objective via save_config (D4).
**Type consistency:** `MLGateFeatures`(T2)→`featurize`/`MockGate`(T3,T4); `MLGate.predict_batch->list[MLGateResult]`,`GateInput`,`ModelNotFoundError`(ports)→T3,T4,T6; `load_gate_candidates`/`claim_stage_a_by_ids` return `JobPosting`(T5)→funnel(T6); `STRUCTURED_DIM=66`(T2)→T4; `EvaluateRuntimeConfig.ml_gate_enabled`(T6); `ml_gate_fail_reason` pinned(T8).
**Placeholder scan:** none; the prior "(if needed)" hedges are now concrete tasks (T1, T5).

## Plan Deviations

### Deviation 1 — 2026-06-05 Funnel dry-run preview drops Stage-A stale-claim recovery (integration test encoded the retired preview path)
**Discovered while:** Task 6 — running `tests/integration/test_services.py::test_evaluate_dry_run_previews_stale_stage_a_claim` after wiring the funnel-based dry-run.
**Plan said:** Decision 14 — "Dry-run runs `load_gate_candidates` + `apply_hard_filters` + `pick_representatives` + the gate to preview the TRUE survivor set"; Rev3 pt 4 — "`claim_pending_stage_a` is retired from evaluate." The funnel (load_gate_candidates) is the sole Stage-A candidate source for both real and dry runs.
**Reality:** The retired `preview_claimable_stage_a` surfaced a stale (aged) `in_progress` Stage-A claim via its stale-takeover status condition. `load_gate_candidates(corpus="unrated")` does NOT — `_pg_corpus_condition("unrated")` admits only `stage_a_status IS NULL / 'error'` rows, so a row stuck `in_progress` by an interrupted Stage-A *scoring* run is not re-surfaced by the funnel. The pre-existing integration test asserts the OLD stale-claim preview contract.
**Type:** [x] Review-deficit  [ ] Essential
**Why this type:** Knowable at plan-time by reading `_pg_corpus_condition` + `_build_claimable_stage_a_query` vs `_build_gate_candidates_query` (all in `postgres.py`) — the plan adopted `load_gate_candidates` as the dry-run source (D14) without reconciling that its corpus predicate is narrower than the retired `preview_claimable_stage_a` for stale `in_progress` rows. Crash-recovery in the plan (D8) targets `'pass'`-but-unscored rows (gate written, `stage_a_status` still NULL — these ARE re-surfaced); it does NOT cover rows already marked `in_progress` by an interrupted scoring run, which under the new sole-path funnel are recovered only by the claim step's stale-takeover on a corpus that admits `in_progress` (e.g. "all"), not "unrated".
**Resolution (superseded — see Follow-up below):** Originally resolved by updating the integration test to the narrower funnel-based dry-run contract: a gated-but-unscored row (gate written, not yet claimed → `stage_a_status` NULL) IS previewed (true crash-recovery per D8); a *claimed-then-stale* `in_progress` row under "unrated" is NOT previewed. Funnel/store predicates were left exactly as Task 5 specified (reuse `_stage_a_pending_filters`); no store change in Task 6.
**Lesson:** The narrower-corpus behavior of `load_gate_candidates` vs the retired claim-preview is a deliberate consequence of the sole-path funnel. If Stage-A *scoring*-interrupted (`in_progress`) recovery under "unrated" is required, that is a Task 5 store-predicate decision (add a stale-`in_progress` arm to the gate-candidates corpus clause), to be raised separately — not silently bolted onto the Task 6 funnel.

**Follow-up (2026-06-05) — stale `in_progress` recovery IS now required; predicate reversed.** Code review judged the "stranded `in_progress` forever" gap unacceptable: under the sole-path funnel a scorer crash leaves `stage_a_status='in_progress'`, `load_gate_candidates(corpus="unrated")` excluded it, so it never became a survivor and `claim_stage_a_by_ids`'s stale-takeover never fired. **Implemented the exact Task 5 store-predicate change the Lesson anticipated:** `_build_gate_candidates_query` now uses `_stage_a_claim_status_condition(corpus, stale_ref)` (the SAME stale-takeover predicate + `_PG_EVALUATION_CLAIM_TTL` the claim builders use) instead of the narrow `_pg_corpus_condition`. A stale `in_progress` row (past the 1h TTL) re-enters the funnel under "unrated", becomes a survivor, and is re-claimed (the claim's own stale-takeover then succeeds); a FRESH `in_progress` row stays excluded (actively owned). The dry-run preview therefore DOES now re-surface a stale `in_progress` row under "unrated". Coupled fix: the funnel now skips re-gating already-`'pass'` reps (surfacing `ml_gate_result` via a `GateCandidate(job, ml_gate_result)` port DTO) so a model/threshold swap cannot flip a persisted `'pass'` to `'fail'` and silently drop it — the true reading of D8's "re-handed to scoring WITHOUT re-gating". Tests: `test_gate_candidates_query` (stale recovered / fresh excluded; gate state surfaced) + `test_phase5_evaluate_chain` (end-to-end stale recovery under default `unrated`; pass-but-unscored scored-not-regated even when MockGate would fail it; dry-run previews exactly `--limit`).
