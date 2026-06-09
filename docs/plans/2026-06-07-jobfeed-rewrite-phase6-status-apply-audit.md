# Phase 6: Status + Apply Audit — Design & Implementation Plan (merged)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement Part B task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the user-facing job-status lifecycle + transactional application audit (service + CLI) on top of the already-built status/apply store+domain primitives, adding an `awaiting_referral` status and multi-round interviews, and retiring `oa/hr_call/second_round/final_round`.

**Architecture:** Hexagonal, same as Phases 0–5. New `services/workflow.py` + `services/application.py` (config injected, never importing `jobfeed.config`); new `cli/{status,apply,interview}.py`. One new table (`interview_rounds`) via an **Alembic** revision. Most status/apply primitives already exist and are *modified*, not created.

**Tech Stack:** Python 3.12, asyncpg + Postgres, Alembic migrations, Click CLI, pytest (`postgres` / `contract` markers).

**Implementation repo:** `/Users/wenqiwang/wwq/jobfeed` (NOT legacy `job-apply`). Bare paths below are rooted there. Mirror a copy into `docs/plans/` after saving.
**Commit strategy:** one conventional commit per task, no AI attribution.
**Execution mode:** Sequential.

> **History:** This merges the original spec + plan after a 5-agent + self review (2026-06-07). The pre-merge review found the plan systematically under-read the store layer and got the migration mechanism wrong; every finding below is folded in. The two source docs are superseded by this file.

---

# PART A — DESIGN

## A1. Positioning

Phase 6 is the first **human-in-the-loop** slice. Phases 0–5 mutate job state only from the pipeline (scan/evaluate). Phase 6 adds user-driven state: the status lifecycle, multi-round interview tracking, and a transactional audit of application materials.

## A2. Current State (verified in code 2026-06-07)

**Already implemented — MODIFY/REUSE, do NOT recreate** (file:line in `adapters/store/postgres.py` unless noted):
- `restore_from_archived` (2896), `auto_decay` (2942, full sweep in one tx, reads `DECAY_SOURCES`), `list_statuses` (3003, supports `statuses`/`needs_followup`/`no_response_days`/`notes_contain`/`days`/`limit`), `append_note` (3082, resets ghost clock), `workflow_attention` (3102, builds all three buckets; `interview_prep` currently keyed off `status='interviewing'`), `compute_reapply_notice` (3171), `record_application` (3227, writes `applied` row + status UPDATE + history INSERT in one tx, returns `False` on re-apply; does NOT write snapshots, does NOT use the transition primitive), `save_resume_snapshot` (3494), `application_stats` (3359, `by_resume` fully implemented). Domain models `StatusInfo/StatusTransition/AutoDecayResult/WorkflowAttention{follow_up_today,interview_prep,going_ghosted}/WorkflowAttentionItem/ApplicationRecord/ResumeSnapshot/ApplicationStats` exist. `transition_status`/`_transition_status_in_tx` exist with `force`/`i_mean_it`. `save_job` DOES populate `company_norm/title_norm` (1766/1806). Twin authority = `domain/dedupe.py:twin_key = (normalize_company(company), normalize(title))`; existing cross-run suppression SQL (postgres.py:957–962) matches on those norms WITH a blank-norm singleton guard.

**Net-new (CREATE):** `interview_rounds` table + `InterviewRound` model + interview CRUD; `awaiting_referral` status; `services/workflow.py`; `services/application.py`; `cli/{status,apply,interview}.py`; the snapshot-in-apply-tx extension; twin-cascade bulk transition; `get_status_history` read; status/bulk/cascade contract test.

## A3. Decisions

- **D1 — Migrations are Alembic, not `.sql`.** New revision `migrations/versions/0006_phase6_status_apply.py`, `down_revision="0005"`, raw SQL via `op.execute()`, real `upgrade()`/`downgrade()`. (Repo has 0001–0005; tests run `alembic upgrade head` in `tests/conftest.py`.)
- **D2 — `ghosted` stays terminal; recovery is `restore` only.** `ghosted` and `archived` remain in `_TERMINAL`; `is_terminal('ghosted')==True`. They are exited ONLY via `restore` (a force-based recovery), never via the normal graph. `apply` on a ghosted job is blocked by the existing `is_terminal` guard — the user must `restore` (or `--force`) first. One unified `restore` (see A4.4) replaces the archived-only `restore_from_archived`.
- **D3 — Retire the 4 statuses everywhere (full blast radius).** Removing `oa/hr_call/second_round/final_round` touches: `domain/status.py` (sets), `domain/models.py:JobStatus` enum, `adapters/store/parity.py:STATUS_VALUES` (KEEP retired values there — see D7), the migration CHECK constraint, the `idx_job_status_stale` partial index, and existing tests + the legacy fixture generator. All enumerated in Task 3.
- **D4 — Back up before the destructive migration.** Task 2 includes a `pg_dump` step before `alembic upgrade` (Postgres, not `cp`). The backfill is irreversible (`oa→interviewing` loses substage granularity from live state); `downgrade()` drops `interview_rounds` and is documented lossy. [[backup-before-destructive-db]]
- **D5 — Reuse the shipped `auto_decay` and `workflow_attention`; do not build parallel ones.** Evaluate calls `store.auto_decay(...)` directly. `workflow_attention.interview_prep` is REWORKED to source from `interview_rounds.scheduled_at` (not `status='interviewing'`). No `load_decay_candidates` service sweep.
- **D6 — Twin cascade uses the existing norm columns + blank-norm singleton guard, per twin-cluster atomic.** `expand_twin_ids` matches `company_norm = $ AND title_norm = $` with the guard from postgres.py:957–962 (a row with blank company_norm OR title_norm expands to itself only). Bulk transition is **atomic per twin-cluster** (one tx per cluster; a failing cluster is reported in `BulkResult`, others proceed), NOT per-row. IDs are `int` to match the store.
- **D7 — Legacy import stays verbatim; the migration converges data; parity status-set is decoupled.** `legacy_import` does NOT remap statuses (preserves parity checksums). `parity.py:STATUS_VALUES` KEEPS the retired values (it validates legacy/historical source rows), while `domain` STATUS_VALUES drops them (live-state set). The 0006 backfill converges already-imported retired-status rows. (Import must precede the 0006 CHECK swap — true for the already-migrated repo; re-import onto the new schema is a Phase 10 cutover concern, noted as a known limit.)
- **D8 — `apply` is one transaction; resume-variant FK is pre-registered.** Extend `record_application` to take snapshot content and write snapshots inside its existing tx via an `_in_tx` helper. Order: snapshot upsert (`ON CONFLICT DO NOTHING`) → `applied` INSERT → if `"INSERT 0 0"` return `False` (re-apply no-op) → transition. Any path setting `job_status.resume_variant` (`mark --resume`, `apply --variant`) first calls `register_resume_variant(name)` (upsert) to satisfy the FK.
- **D9 — No new "applied source URL" column.** Each twin is a distinct `jobs` row with its own `url`; `applied.job_id` already identifies the exact applied posting. Drop the "pass in source URL" language; assert via `applied.job_id → jobs.url`.
- **D10 — Round activity is activity.** `add_interview_round` / `complete_interview_round` reset `last_status_change_at`, so an `interviewing` job with active rounds is not auto-ghosted out from under a live interview. Leaving `interviewing` (→ offer/rejected/ghosted) marks remaining open rounds `completed_at=now()` so terminal jobs don't linger in `interview_prep`.
- **D11 — Pin reason strings.** Keep the existing `auto_decay` reason text (don't churn its shipped contract). Define literal bulk reason constants in `domain/status.py` (`REASON_BULK_SELECTED="bulk"`, `REASON_BULK_CASCADE="bulk-cascade"`); the contract test asserts these exact strings. `application_stats` historical regression from the slimmed `RESPONSE_STATUSES` is ACCEPTED + documented (user has ~no real retired-substage history); the backfill does NOT inject synthetic history rows (keeps parity row counts).
- **D12 — Contract test mirrors Phase 5's two-layer technique.** (1) `inspect.getsource` the writers and pin literal column names (NO DB — runs under `make quality`); (2) freeze new dataclass `__dataclass_fields__`; (3) `@postgres` row-shape assertions. Domain unit tests + the no-DB introspection layer are the fast lane (no Docker).

## A4. Status Machine

### A4.1 Status set (11, live)
`new, scored, shortlisted, awaiting_referral, applied, interviewing, offer, rejected, ghosted, archived, ignored`. (Removed from live: `oa, hr_call, second_round, final_round`.)

### A4.2 Transition graph
```text
new            → scored
scored         → shortlisted | awaiting_referral | applied | archived | ignored
shortlisted    → awaiting_referral | applied | archived
awaiting_referral → applied | archived
applied        → interviewing | offer | rejected | ghosted
interviewing   → offer | rejected | ghosted
ignored, archived, rejected, offer, ghosted → terminal (graph); ghosted/archived exit via restore only (D2)
```
`awaiting_referral` intentionally has NO edge to `ignored`/`rejected`/`ghosted` (a dead referral is self-applied or archived).

### A4.3 Multi-round interviews
A job in `interviewing` carries N rows in `interview_rounds` (`id`, `job_id` FK CASCADE, `round_index` 1..N unique-per-job, `label`, `scheduled_at?`, `completed_at?`, `notes?`, `created_at`). `PRESET_INTERVIEW_LABELS` are suggestions (any string allowed); `RETIRED_STATUS_LABELS = {oa:"OA", hr_call:"HR Call", second_round:"2nd Round", final_round:"Final Round"}`. "Upcoming" = `scheduled_at` within N days AND `completed_at IS NULL`.

### A4.4 Restore (unified, replaces `restore_from_archived`)
`restore(job_id)`: read history via new `get_status_history`, `pick_restore_target(history)` = most recent `to_status` that is NOT ghosted/archived; transition there (forced). Fallback when `None`: `applied` (a ghosted job always descended from applied/interviewing). The old archived-only `restore_from_archived` is replaced by this unified path; its contract test is updated.

## A5. Data Model & Migration (Alembic 0006)

`upgrade()`:
1. `CREATE TABLE interview_rounds (...)` + indexes: `(job_id)`, partial `(scheduled_at) WHERE completed_at IS NULL`. (The UNIQUE(job_id, round_index) already provides that composite btree.)
2. Backfill: for each `job_status` row with status in the 4 retired values → insert one `interview_rounds` row (`round_index=1`, mapped label, `completed_at=now()`), set `status='interviewing'`, **preserve `last_status_change_at`** and all `job_status_history` rows. Guard the insert with `WHERE NOT EXISTS (...)` for idempotency.
3. `ALTER TABLE job_status DROP CONSTRAINT job_status_status_check`, then add the new 11-value CHECK (incl. `awaiting_referral`). (Must run AFTER step 2 so no row violates it.)
4. `DROP INDEX idx_job_status_stale`; recreate `WHERE status IN ('applied','interviewing')`.

`downgrade()`: drop `interview_rounds`, restore the old CHECK + index; document that `interviewing`→original substage is NOT recovered (lossy).

Pre-migration (Task 2 step): `pg_dump` to a timestamped file; document restore.

## A6. Apply Audit
Per D8/D9. Freezes master (+ optional tailored) resume by hash, optional cover-letter text, and Stage-B `block_a/c/e` from `evaluations` (NULL-safe when no evaluation). Re-apply idempotent. `applied.job_id` is the applied posting identity.

## A7. Automation
- **auto-decay:** reuse shipped `store.auto_decay`; call at the head of `evaluate` (D5). Thresholds via `EvaluateRuntimeConfig` (defaults 30/14), resolved in CLI.
- **attention:** reuse shipped `store.workflow_attention`; rework `interview_prep` to read `interview_rounds` (D5); dedupe multiple rounds per job to one item; decide policy for an `interviewing` job with no upcoming round (include with reason "interviewing, unscheduled"). Surfaced via `list` flags. Digest folding OUT of scope (A8).

## A8. Out of Scope (later phases)
Web UI (Phase 8), Temporal runner (Phase 9), digest overhaul to fold attention (Phase 8+). Concurrent cross-command races (single-user CLI) documented, not engineered.

---

# PART B — IMPLEMENTATION PLAN

## File Structure
```text
src/jobfeed/
├── domain/{status.py(MOD), interview.py(NEW), models.py(MOD: JobStatus enum)}
├── ports/{store.py, store_ext.py}(MOD)
├── adapters/store/{postgres.py(MOD), parity.py(MOD: keep retired in its STATUS_VALUES)}
├── services/{workflow.py(NEW), application.py(NEW), evaluate.py(MOD), evaluate_types.py(MOD)}
├── cli/{status.py(NEW), apply.py(NEW), interview.py(NEW), __init__.py(MOD), evaluate.py(MOD)}
└── migrations/versions/0006_phase6_status_apply.py(NEW)
tests/
├── unit/{test_status_transitions, test_interview_domain, test_status_retirement_cleanup(MOD existing)}
├── integration/{test_interview_rounds_store, test_status_queries_store, test_apply_tx_store, test_twin_cascade_store, test_phase6_chain}(@postgres)
├── contract/test_status_apply_persistence (NEW)
└── (MOD) unit/test_status.py, unit/test_models.py, contract/test_store_contract.py, integration/test_store_pg_behaviors.py, fixtures/generate_legacy_fixture.py + legacy_v16_manifest.json
```

## Task 1: Domain — status set, transitions, enum, interview & reason constants
**Files:** Modify `domain/status.py`, `domain/models.py`; Create `domain/interview.py`; Test `tests/unit/test_status_transitions.py`, `tests/unit/test_interview_domain.py`.
**What to build:** Apply A4.1/A4.2. In `status.py`: add `awaiting_referral`; remove the 4 retired from `STATUS_VALUES`/`ALLOWED_TRANSITIONS`/`_TERMINAL`/`DECAY_SOURCES`/`RESPONSE_STATUSES` (`ACTIVE_APPLICATION_STATUSES` is an alias of `DECAY_SOURCES`, auto-tracks); keep `ghosted`,`archived` in `_TERMINAL` (D2); add `pick_restore_target(history_to_statuses) -> str|None` (latest not-ghosted/not-archived; D2/A4.4); add `REASON_BULK_SELECTED="bulk"`, `REASON_BULK_CASCADE="bulk-cascade"` (D11). In `models.py`: drop `OA/HR_CALL/SECOND_ROUND/FINAL_ROUND` from `JobStatus`, add `AWAITING_REFERRAL="awaiting_referral"`. New `domain/interview.py`: `InterviewRound` dataclass, `PRESET_INTERVIEW_LABELS`, `RETIRED_STATUS_LABELS`.
**Acceptance:**
- [ ] `STATUS_VALUES` == the 11 in A4.1; the 4 retired absent; `JobStatus` mirrors it (no retired members, has `AWAITING_REFERRAL`)
- [ ] `validate_transition` accepts every A4.2 edge; rejects `applied→oa` (unknown) and `awaiting_referral→ghosted` without force; `--force` bypass + `archived→new` needs `i_mean_it` still hold
- [ ] `is_terminal('ghosted')` and `is_terminal('archived')` are `True`; `DECAY_SOURCES=={"applied","interviewing"}`, `RESPONSE_STATUSES=={"interviewing","offer","rejected"}`, `ACTIVE_APPLICATION_STATUSES=={"applied","interviewing"}`
- [ ] `pick_restore_target` returns latest non-ghosted/non-archived `to_status`, else `None`
- [ ] `RETIRED_STATUS_LABELS` maps all 4; reason constants exported
- [ ] All tests pass, committed

## Task 2: Alembic migration 0006 (+ backup)
**Files:** Create `migrations/versions/0006_phase6_status_apply.py`; Test `tests/integration/test_interview_rounds_store.py` (schema/backfill portion, `@postgres`).
**What to build:** Per A5/D1/D4. Pre-step documented: `pg_dump` backup before `alembic upgrade`.
**Acceptance:**
- [ ] `alembic upgrade head` then `downgrade -1` round-trips structurally (table created/dropped, CHECK + index swapped back)
- [ ] On a DB seeded with rows in each retired status: after upgrade none remain in `job_status.status`; each became `interviewing` + exactly one mapped-label round with `completed_at` set; `last_status_change_at` and history rows unchanged
- [ ] New CHECK accepts `awaiting_referral`, rejects the 4 retired; constraint swap ordered after backfill (no violation)
- [ ] `idx_job_status_stale` predicate is now `('applied','interviewing')`
- [ ] Backfill insert is guarded idempotent (`WHERE NOT EXISTS`); `conftest`'s fresh-schema fixture exposes `interview_rounds`
- [ ] Backup step + lossy-downgrade are documented in the revision docstring
- [ ] All tests pass, committed

## Task 3: Retirement blast-radius cleanup (existing tests + parity + fixture)
**Files:** Modify `adapters/store/parity.py`; `tests/unit/test_status.py`, `tests/unit/test_models.py`, `tests/contract/test_store_contract.py`, `tests/integration/test_store_pg_behaviors.py`, `tests/fixtures/generate_legacy_fixture.py`, `tests/fixtures/legacy_v16_manifest.json`.
**What to build:** Make the whole suite green under the new status model (D3/D7). `parity.py:STATUS_VALUES` KEEPS the 4 retired values (legacy-source validation) — add a comment explaining the decoupling from `domain.STATUS_VALUES`. Update `test_status.py` count (14→11) + retired-substage transition cases → `interviewing`/interview-round equivalents; `test_models.py` enum list; `test_store_contract.py` (`test_forward_only_interview_stages`, `test_application_stats_by_resume` use `oa`) → use `interviewing`; `test_store_pg_behaviors.py` (reapply/followup/workflow_attention substage cases) → `interviewing` + rounds. Change `generate_legacy_fixture.py` to emit retired statuses from a LITERAL list (decoupled from `domain.STATUS_VALUES`) so the import path still exercises them; regenerate `legacy_v16_manifest.json`.
**Acceptance:**
- [ ] `grep -rn "oa\|hr_call\|second_round\|final_round" src tests` returns only intentional interview-label strings / parity legacy set / fixture literal list (no live-status uses)
- [ ] Full suite (excluding `@postgres` where unavailable) is green; `@postgres` suite green where DB present
- [ ] Legacy fixture import still produces retired-status source rows, importing verbatim, with parity `--verify` passing (status checksum matches; rows then converged by 0006)
- [ ] All tests pass, committed

## Task 4: Store — interview_rounds CRUD
**Files:** Modify `ports/store_ext.py`, `adapters/store/postgres.py`; Test `tests/integration/test_interview_rounds_store.py` (`@postgres`).
**What to build:** `add_interview_round(job_id, label, scheduled_at=None) -> InterviewRound` assigning `round_index` via single-statement `INSERT ... SELECT COALESCE(MAX(round_index),0)+1 ...` relying on `UNIQUE(job_id,round_index)` (catch+retry on rare conflict); `list_interview_rounds(job_id)`; `complete_interview_round(job_id, round_index=None, notes=None)` (default = highest open round; error if none open); `list_upcoming_interviews(within_days)`. Reset `last_status_change_at` on add/complete (D10). Place on a status/interview mixin per existing style.
**Acceptance:**
- [ ] Sequential `add_interview_round` → round_index 1,2,3; duplicate index rejected by the unique constraint
- [ ] `complete_interview_round()` completes the latest open round; `--round N` targets N; raises a clear error when no open round exists
- [ ] `list_upcoming_interviews(7)` returns only future, not-completed rounds in window
- [ ] add/complete bump `job_status.last_status_change_at`
- [ ] Deleting a job cascades its rounds
- [ ] All tests pass, committed

## Task 5: Store — status queries, bulk cascade, twin expand, history read; attention rework
**Files:** Modify `ports/store_ext.py`, `adapters/store/postgres.py`; Test `tests/integration/test_status_queries_store.py`, `tests/integration/test_twin_cascade_store.py` (`@postgres`).
**What to build:** Extend existing `list_statuses` so `no_response_days` covers `status IN ('applied','interviewing')` (not applied-only). Add `get_status_history(job_id) -> list[str]` (to_status, newest-first) for restore. Add `expand_twin_ids(job_ids: list[int]) -> dict[int, list[int]]` using `company_norm/title_norm` WITH the blank-norm singleton guard (postgres.py:957–962 pattern). Add `transition_status_bulk(items, *, reason_selected, reason_cascade) -> BulkResult` — **atomic per twin-cluster** (one tx/cluster; cluster failure recorded, others proceed). Rework `workflow_attention.interview_prep` to source from `interview_rounds` (upcoming, `completed_at IS NULL`), dedupe to one item per job, and include `interviewing` jobs with no upcoming round tagged accordingly (A7/D5/D10). Do NOT add `load_decay_candidates` (reuse `auto_decay`).
**Acceptance:**
- [ ] `list_statuses(no_response_days=14)` includes both applied AND interviewing silent ≥14d
- [ ] `expand_twin_ids` groups cross-platform rows sharing non-blank `(company_norm,title_norm)`; a blank-norm row expands to itself only (no over-cascade)
- [ ] `transition_status_bulk` cascades each id's cluster with `REASON_BULK_SELECTED`/`REASON_BULK_CASCADE`; a cluster that fails is in `BulkResult.failed`, others still applied; each cluster all-or-nothing
- [ ] `get_status_history` returns to_statuses newest-first
- [ ] `workflow_attention.interview_prep` lists upcoming-round jobs (one item/job) + flags unscheduled interviewing jobs; a job that left `interviewing` does not appear
- [ ] All tests pass, committed

## Task 6: Store — apply in one transaction + variant FK guard
**Files:** Modify `ports/store_ext.py`, `adapters/store/postgres.py`; Test `tests/integration/test_apply_tx_store.py` (`@postgres`).
**What to build:** Per D8. Refactor snapshot writes into an `_in_tx` helper; extend `record_application` (or add `record_application_with_snapshots`) to accept master/optional-tailored snapshot content and write them inside its existing transaction, ordered snapshot-upsert → applied-insert → idempotency check (`"INSERT 0 0"` → return False) → transition. Add `register_resume_variant(name)` upsert; call it before any `resume_variant` write (here and reused by `mark --resume`).
**Acceptance:**
- [ ] Successful apply writes snapshot(s) + applied row + history transition atomically
- [ ] Forced failure after snapshot upsert rolls back the snapshot (assert via a real constraint, e.g. terminal-status guard); zero snapshot rows remain
- [ ] Re-apply returns `False`; original `applied_at`/snapshots unchanged; exactly one `applied` row
- [ ] Same resume content across two jobs → one `resume_snapshots` row
- [ ] Setting `resume_variant` for an unregistered variant auto-registers it (no FK violation)
- [ ] All tests pass, committed

## Task 7: Service — workflow.py (config-injected)
**Files:** Create `services/workflow.py`; Test `tests/unit/` + paths in `tests/integration/test_phase6_chain.py`.
**What to build:** `WorkflowService(store, logger)` (no `jobfeed.config` import — boundary test). `transition`, `transition_bulk` (calls `expand_twin_ids`+`transition_status_bulk` with the reason constants), `restore` (reads `get_status_history`, `pick_restore_target`, fallback `applied`), `note`, `set_followup`, interview ops (`add_round`/`list_rounds`/`complete_round`; `add_round` flips `applied→interviewing`), and thin `attention()`/`auto_decay()` pass-throughs to the shipped store methods. Single-level loops.
**Acceptance:**
- [ ] `transition` rejects illegal w/o force, performs w/ force
- [ ] `transition_bulk` marks each id + its twin cluster with the correct reason constants; single-row (no twin) cluster tags `selected` only
- [ ] `restore` returns a ghosted/archived job to its prior non-terminal status; uses `applied` fallback when history has none; clear behavior either way
- [ ] `add_round` on `applied` → `interviewing`; second `add_round` keeps `interviewing`
- [ ] Service module imports neither `jobfeed.adapters` nor `jobfeed.config` (boundary test green)
- [ ] All tests pass, committed

## Task 8: Service — application.py (config-injected)
**Files:** Create `services/application.py`, modify `services/evaluate_types.py` if a shared deps/config dataclass is reused; Test paths in `tests/integration/test_phase6_chain.py` (`@postgres`).
**What to build:** Per D8/D9. `ApplicationService` taking an injected config (resume/cover-letter base paths) + store (mirror `EvaluateService` deps+config shape; resolve config in CLI). `apply(job_id, *, tailored_path=None, cover_letter_path=None, variant=None)` reads+hashes materials, pulls Stage-B blocks (NULL-safe if no evaluation), calls Task-6 apply; `apply_history`, `stats` (pass-through to `application_stats`, incl. `by_resume`), `snapshots_list/show/diff`.
**Acceptance:**
- [ ] `apply` snapshots master (+tailored if given), captures cover letter, freezes block_a/c/e when present, sets `applied`; succeeds with NULL blocks when the job has no evaluation
- [ ] Missing/unreadable resume or cover-letter file → clear error, NO partial write (no applied row, no snapshot)
- [ ] `apply` first call truthy, re-apply falsy (one audit row)
- [ ] `stats(by_resume=True)` returns per-variant counts; empty DB → zeroed stats, `by_resume=={}` (no crash)
- [ ] `snapshot_diff` returns a unified diff; module imports no `jobfeed.config`
- [ ] All tests pass, committed

## Task 9: Evaluate wiring — auto_decay at head
**Files:** Modify `services/evaluate.py`, `services/evaluate_types.py`, `cli/evaluate.py`; Test in `tests/integration/test_phase6_chain.py`.
**What to build:** Add `ghost_days`/`archive_ignored_days` (defaults 30/14) to `EvaluateRuntimeConfig`; resolve from settings in `cli/evaluate.py`; call `self._deps.store.auto_decay(ghost_days, archive_ignored_days)` at the head of `run()` before candidate loading; log `AutoDecayResult`. (No `workflow` indirection; no config import in the service.)
**Acceptance:**
- [ ] A job both decay-eligible AND Stage-A-pending is ghosted by the head sweep and consequently NOT Stage-A scored that run (ordering proven)
- [ ] Evaluate completes normally when nothing decays; counts logged
- [ ] Thresholds come from `EvaluateRuntimeConfig`, default 30/14; service imports no `jobfeed.config`
- [ ] All tests pass, committed

## Task 10: CLI — status / apply / interview + registration
**Files:** Create `cli/status.py`, `cli/apply.py`, `cli/interview.py`; Modify `cli/__init__.py`; Test in `tests/integration/test_phase6_chain.py`.
**What to build:** Click commands (parse/format only; resolve config + build services here). `mark <id…> <status>` (`--bulk` → twin cascade, `--note`, `--force`, `--i-mean-it`, `--restore`, `--resume`); `archive` alias; `note`; `followup --in`; `list [--status] [--needs-followup] [--no-response N] [--md|--json]`; `stats [--by-resume] [--window N]`; `apply <id> [--tailored] [--cover-letter] [--variant]`; `apply-history`; `snapshots list|show|diff`; `interview add|list|done`. Register all in `cli/__init__.py` (alongside scan/evaluate/…).
**Acceptance:**
- [ ] Every command shows in `--help`; registered
- [ ] `mark --restore` routes to service `restore`; `mark <id> <s> --force` forces; `mark --bulk` triggers twin cascade; `mark archived→new` w/o `--i-mean-it` surfaces the gated error
- [ ] `interview add` → `list` shows the round → `done` completes it; `done` with no open round errors clearly
- [ ] `list --json` output parses via `json.loads` with keys `{id,status,next_followup_at}`; combined `--status`+`--needs-followup`+`--no-response` AND-compose correctly
- [ ] `apply` end-to-end records audit + flips status
- [ ] All tests pass, committed

## Task 11: Contract test + edge cases + fast lane
**Files:** Create `tests/contract/test_status_apply_persistence.py`; add edge-case tests across unit/integration; Test itself.
**What to build:** Per D12, mirror Phase 5's evaluation-persistence contract: (1) NO-DB `inspect.getsource` pins on the writers — `_transition_status_in_tx` (history `reason`/`resume_variant_at_change`), the apply-in-tx method (`applied.master_resume_hash/tailored_resume_hash/cover_letter/verdict_snapshot/fit_snapshot/hooks_snapshot`), `add_interview_round` (`round_index/label/scheduled_at/completed_at/notes`); (2) freeze `InterviewRound.__dataclass_fields__`; (3) `@postgres` row-shape assertions for a single transition, a bulk twin-cascade (each cluster member has a history row with the exact `REASON_BULK_*` strings), and an application audit (re-apply leaves exactly one row). Add the missing edge cases as concrete tests: empty/single-row twin cluster; restore with only ghosted/archived history (fallback); apply with no evaluation; apply with missing files; `interview done` no open round; combined list filters; bulk cascade where a cluster member is terminal (policy: skip terminal, record in `BulkResult.skipped`); stats with zero applications; `awaiting_referral not in DECAY_SOURCES` + an aged `awaiting_referral` survives a decay sweep; auto-decay boundary ages (assert against shipped `auto_decay`, pin the `==N` day rule); note-resets-clock at threshold.
**Acceptance:**
- [ ] NO-DB getsource + dataclass-field assertions run under `make quality` (no `postgres` marker, no Docker) and fail on a column/field rename
- [ ] `@postgres` contract pins the persisted shape incl. exact bulk reason strings; re-apply count stays 1
- [ ] Every listed edge case has a passing test
- [ ] Marker split correct (no-DB half runs under the default skip-postgres invocation)
- [ ] All tests pass, committed

---

## Self-Review
- **Review-finding coverage:** Alembic (D1/T2); existing-methods-reuse (A2/D5/T5/T7/T9); retirement blast radius incl. enum/parity/CHECK/index/tests/fixture (D3/T1/T2/T3); DB backup (D4/T2); ghosted terminal+restore unification (D2/A4.4/T1/T5/T7); twin norm+guard+per-cluster atomic (D6/T5); config-injection boundary (D8.../T7/T8/T9); apply one-tx + variant FK (D8/T6); no URL column (D9/T6); decay-vs-rounds clock + leaving-interviewing cleanup (D10/T4/T5); reason strings + stats-regression accepted (D11/T1/T11); Phase-5-style contract + fast lane + edge cases (D12/T11).
- **Placeholders:** none — criteria are test-verifiable.
- **Type consistency:** `InterviewRound`, `expand_twin_ids(list[int])->dict[int,list[int]]`, `get_status_history`, `pick_restore_target`, `REASON_BULK_SELECTED/CASCADE`, `register_resume_variant`, reused `auto_decay`/`workflow_attention`/`list_statuses`/`record_application` named consistently.
- **Out of scope (A8) honored:** no web/Temporal/digest-fold tasks.
