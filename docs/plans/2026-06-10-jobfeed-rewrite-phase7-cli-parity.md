# Phase 7: Full CLI Parity — Design & Implementation Plan (merged)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement Part B task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every daily-use CLI gap between the legacy `job-apply` CLI and the rewrite, so the user can dual-run the rewrite daily ahead of the Phase 10 cutover. Parity is **trimmed**: port real capabilities, drop legacy baggage, adapt interfaces where the new architecture obsoletes the old shape.

**Architecture:** Hexagonal, same as Phases 0–6. New `cli/{companies,enrich}.py`; modifications to `cli/{status,status_query,apply,digest}.py`, `services/{workflow,application,digest}.py`, status/apply store mixins. No Alembic migration anticipated (`job_status.next_followup_at` and the store KV table already exist).

**Tech Stack:** Python 3.12, asyncpg + Postgres, Click CLI, httpx (bootstrap fetch), pytest (`postgres` marker e2e).

**Implementation repo:** `/Users/wenqiwang/wwq/jobfeed` (NOT legacy `job-apply`). Bare paths below are rooted there. Mirror a copy into `docs/plans/` after saving.
**Commit strategy:** one conventional commit per task, no AI attribution.
**Execution mode:** Sequential.

> **History:** Design drafted 2026-06-10, revised after 1 independent agent review against both codebases. The review corrected the effort map (bootstrap-companies is ~220 lines with ONE generic parser, not 7; the ATS probe is already a shared module) and surfaced the two real MUSTs the draft missed: digest file output + auto-cutoff persistence, and `list` output/default parity. This file supersedes the deleted standalone spec.

---

# PART A — DESIGN

## A1. Positioning

Phases 0–6 built the pipeline and the human-in-the-loop status/apply slice. Phase 7 is the **cutover-readiness** slice: after it, every workflow the user runs daily in legacy has a rewrite equivalent, and the rewrite CLI becomes the primary daily driver (legacy kept as fallback until Phase 10).

## A2. Current State (verified in code 2026-06-10)

**Already implemented — REUSE, do not recreate:**
- Companies store layer is complete: `upsert_company/get_company/list_companies/mark_company_removed` (`ports/store_ops.py:21-57`, implemented in `postgres.py`).
- ATS vendor probe is already standalone+shared: `probe_company` in `adapters/sources/_ats_probe.py:58`, `PROBE_ORDER` = greenhouse/ashby/lever. Zero extraction work.
- `enrich_paste(platform, canonical_id, jd_text)` exists end-to-end (`ports/store_ops.py:109-126`, `postgres.py:4298`): assesses quality, stamps `enrich_source='manual-paste'`, clears `enrich_error`. CLI shell only.
- `StatusFilter` already supports `days/notes_contain/limit` (`domain/models_status.py:108-116`; SQL at `postgres.py:3036-3070`) — but `notes_contain` is case-sensitive `LIKE` (legacy is case-insensitive) and the SQL selects no company/title.
- `ApplyRequest.application_method` is plumbed through service→record (`services/application.py:25`); only the CLI flag is missing. `ApplicationRecord.notes` field exists but `ApplyRequest` has no notes and the service builds the record without it (`application.py:74-84`).
- Store KV `get_state/set_state` exists (`ports/store_ops.py:128-145`) — the vehicle for digest auto-cutoff.
- Built-but-unexposed (zero CLI callers): `WorkflowService.attention` (`services/workflow.py:236`), `store.needs_attention` (`postgres.py:4480`), `compute_reapply_notice` (`postgres.py:3197`).
- Phase 6 planned `set_followup`/`followup --in` but they did NOT ship: no `set_followup` in `ports/store_status.py` or `postgres.py`, no `followup` command registered. (Legacy has no store method either — it inlines a raw UPDATE, legacy `main.py:1712-1716`.)

**Net-new (CREATE):** `set_followup` port+store method; `list_resume_snapshots` + hash-prefix snapshot lookup; `list_applications` resume-hash filter; shared `Nd/Nw/YYYY-MM-DD` parser; digest file output + KV cutoff; `cli/companies.py` (incl. bootstrap), `cli/enrich.py`; `LIST_DEFAULT_STATUSES` domain constant.

## A3. Decisions

- **D1 — Trimmed parity; five drops are final.** `init` (covered by `./setup`: config + venv/Docker + PG + Alembic), `login indeed` (rewrite indeed is JobSpy, no browser session), `upgrade-blocks` (legacy tiered-blocks repair; concept doesn't exist here), `ml-gate train` (Phase 5: training is offline; CLI keeps `info`/`fetch`), `serve` (Phase 8).
- **D2 — `digest --top N` adapts to verdict semantics; NO `--threshold`.** The rewrite digest groups by Stage-B verdict (`domain/digest.py:38`); `--top` caps each verdict group at N rows (header shows `(N/total)`). Numeric-score thresholds are a legacy scoring-model leftover and do not return. (User-approved.)
- **D3 — Digest cutoff persists in the store KV, not file mtime.** Key `digest.last_rendered_at` (ISO-8601 UTC). When `--cutoff-at` is absent, the service reads the key for the new/seen split and writes `now()` after a successful render. Works whether or not file output is configured — strictly better than legacy's digest-file-mtime hack.
- **D4 — Digest file output is config-driven; services stay config-free.** New `DigestSettings.output_dir: str | None = None` in `config.py` (`[digest]` TOML section). `None` → stdout only (current behavior). When set, the CLI resolves the dir and passes it to the service, which writes `today.md` + `YYYY-MM-DD.md` and still echoes unless `--quiet`. `--quiet` without a configured `output_dir` is a usage error.
- **D5 — `list` defaults to the actionable middle, with legacy's exit-code contract.** `LIST_DEFAULT_STATUSES = STATUS_VALUES - {"new","archived"}` (mirrors legacy `store.py:1791`) lives in `domain/status.py` and is applied at the CLI layer when `--status` is absent (the store keeps `None` = no filter for other callers). Empty result exits 1 unless `--allow-empty` — a deliberate change from today's always-0.
- **D6 — `notes_contain` becomes ILIKE.** Legacy is case-insensitive; the e2e test pins it.
- **D7 — Snapshot identity supports hash prefixes; global list derives source from `applied` joins.** `get_resume_snapshot_by_prefix(prefix)` errors on ambiguity (≥2 matches) and on no-match. `list_resume_snapshots()` LEFT JOINs `applied` on master/tailored hash columns to derive source (`master`/`tailored`/`-` when unreferenced) and a usage count; `--source` filters on the derived value.
- **D8 — Bootstrap parsing is a pure module; CLI owns IO.** Port legacy's single generic slug-regex extractor + age-aware variant + the 7-entry source URL dict (legacy `main.py:2137-2260`) into `adapters/sources/_bootstrap_aggregators.py` as pure functions over markdown text. The CLI fetches READMEs with a lazily-built httpx client (Phase 2 pattern) and upserts via the existing store methods. Dry-run by default; `--apply` writes. Keep all 7 sources (user runs new-grad + intern tracks).
- **D9 — Attention ships inside digest; reapply notice ships inside `apply`.** Digest gains a footer: workflow attention (follow-up due / interview prep / going-ghosted) + pipeline `needs_attention`. `apply` prints `compute_reapply_notice` when non-None. This supersedes Phase 6 A8's "digest fold = Phase 8+" deferral — the data methods exist with zero callers; the wiring is small and is exactly what daily dual-running needs.
- **D10 — One shared window parser.** `parse_window("7d"|"2w"|"YYYY-MM-DD")` with mandatory unit suffix (legacy convention, prevents bare-int typos), used by `followup --in` and `list --days`. `stats --since` stays int `--window` (deferred, A5).
- **D11 — Companion = CLI golden-path e2e, NOT `e2e_smoke.sh`.** New `tests/e2e/test_cli_parity.py` modeled on `tests/e2e/test_cli_skeleton.py` (CliRunner + `postgres` testcontainers marker, CI-runnable). `scripts/e2e_smoke.sh` is manual-only by design (real outbound scrapes). The parity behaviors decided here (ILIKE, default status set, exit codes, output columns, `--top` capping, KV cutoff) are pinned there.

## A4. CLI Surface Delta (complete list)

| Command | Change |
|---|---|
| `companies add <slug> [--ats greenhouse\|ashby\|lever]` | NEW; probes via `_ats_probe` when `--ats` absent |
| `companies list [--vendor ...] [--include-removed]` | NEW |
| `companies remove <slug>` | NEW; soft delete |
| `bootstrap-companies [--source <7 choices>\|all] [--apply] [--max-age-days N]` | NEW; dry-run default |
| `followup <job_id> --in 7d` | NEW; sets `next_followup_at` |
| `enrich-paste <canonical_id> --platform X [--from-file PATH]` | NEW; stdin when no file |
| `list` | +`--days`, `--notes-contain` (ILIKE), `--limit`, `--allow-empty`; output gains company/title/last_change; default = actionable middle; empty→exit 1 |
| `digest` | +`--top N` (per-group cap), `--quiet`; writes `today.md`+dated file when configured; KV auto-cutoff; attention footer |
| `apply` | +`--method (web\|referral\|email)`, `--notes`; prints reapply notice |
| `apply-history` | +`--resume <hash-prefix>` |
| `snapshots list` | job_id becomes optional → global list w/ usage counts + `--source (master\|tailored)` |
| `snapshots show/diff` | accept hash prefixes |

## A5. Out of Scope (deferred / later phases)

`mark <url>` token resolution; `archive --reason`; `stats --since Nd/Nw/date` (keeps int `--window`); `./setup` seeding resume templates (NICE); legacy digest-time hard-filter re-application (rewrite applies hard filters in the eval funnel — if Task 4 finds digest-visible drift, document, don't build); Web UI (Phase 8); Temporal (Phase 9); cutover (Phase 10).

---

# PART B — IMPLEMENTATION PLAN

## File Structure
```
src/jobfeed/
├── domain/{status.py(MOD: LIST_DEFAULT_STATUSES), models_status.py(MOD: StatusInfo fields),
│           digest.py(MOD: per-group top cap), models_application.py(read-only reference)}
├── ports/{store_status.py(MOD: set_followup), store_ext.py(MOD: list_applications filter,
│           snapshot list/prefix)}
├── adapters/store/postgres.py(MOD: set_followup, ILIKE, list_statuses select, snapshot
│           list/prefix, list_applications filter)
├── adapters/sources/_bootstrap_aggregators.py(NEW: pure parsers + source dict)
├── services/{workflow.py(MOD: set_followup), application.py(MOD: notes, reapply notice),
│           digest.py(MOD: top/files/cutoff/attention)}
├── cli/{_window.py(NEW), companies.py(NEW), enrich.py(NEW), status.py(MOD: followup),
│        status_query.py(MOD: list flags/output/exit), apply.py(MOD: flags, snapshots),
│        digest.py(MOD: flags), __init__.py(MOD: registration)}
└── config.py(MOD: DigestSettings) + config.example.toml(MOD: [digest])
tests/
├── unit/{test_window_parser(NEW), test_digest_top(NEW), test_bootstrap_parsers(NEW)}
├── integration/{test_followup_store, test_snapshot_lookup_store, test_list_output_store}(@postgres)
└── e2e/test_cli_parity.py(NEW, @postgres — the companion)
```

## Task 1: Shared window parser
**Files:** Create `cli/_window.py`; Test `tests/unit/test_window_parser.py`.
**What to build:** `parse_window(value: str) -> datetime` returning an aware UTC datetime: `Nd`/`Nw` = now+N days/weeks (for `followup --in`) and a companion `parse_window_back(value) -> datetime` = now−N (for `list --days`); bare `YYYY-MM-DD` = that date at midnight UTC. Mandatory unit suffix on numerics; bare ints, zero/negative N, and other strings raise `click.BadParameter` naming the accepted forms.
**Acceptance:**
- [ ] `7d`/`2w`/`2026-07-01` parse; `7`, `0d`, `-3d`, `7x`, `""` raise BadParameter with the accepted-forms message
- [ ] Returned datetimes are timezone-aware UTC
- [ ] All tests pass, committed

## Task 2: Store — followup, ILIKE, list output fields
**Files:** Modify `ports/store_status.py`, `domain/models_status.py`, `domain/status.py`, `adapters/store/postgres.py`; Test `tests/integration/test_followup_store.py`, `tests/integration/test_list_output_store.py` (`@postgres`).
**What to build:** (1) `set_followup(job_id: int, at: datetime) -> bool` — UPDATE `job_status.next_followup_at`; returns False when no `job_status` row exists. (2) Switch `notes_contain` to `ILIKE` (D6). (3) Add `company`, `title`, `last_status_change_at` to `StatusInfo`; extend the `list_statuses` SELECT (it already JOINs `jobs`, `postgres.py:3061`) to populate them. (4) Add `LIST_DEFAULT_STATUSES = STATUS_VALUES - {"new","archived"}` to `domain/status.py` (D5; store semantics unchanged — `None` still means no filter).
**Acceptance:**
- [ ] `set_followup` round-trips via `list_statuses(needs_followup=True)` once the date passes; returns False for an id with no status row
- [ ] `notes_contain="ACME"` matches a note saved as "acme" (ILIKE pinned)
- [ ] `StatusInfo` rows carry non-empty company/title/last_status_change_at for a seeded job
- [ ] `LIST_DEFAULT_STATUSES` == the 11-value live set minus `new`/`archived`
- [ ] All tests pass, committed

## Task 3: Store — snapshot prefix/global list, apply-history filter
**Files:** Modify `ports/store_ext.py`, `adapters/store/postgres.py`; Test `tests/integration/test_snapshot_lookup_store.py` (`@postgres`).
**What to build:** Per D7. (1) `get_resume_snapshot_by_prefix(prefix: str) -> ResumeSnapshot` — `WHERE resume_hash LIKE prefix || '%'`; raise a domain-level `LookupError` subclass distinguishing no-match from ambiguous (≥2). (2) `list_resume_snapshots(source: str | None = None) -> list[...]` — every snapshot with created_at, derived source (`master`/`tailored` via LEFT JOIN on `applied.master_resume_hash`/`tailored_resume_hash`, `-` when unreferenced), and usage count (number of referencing applied rows); `source` filters on the derived value. (3) `list_applications(limit, resume_hash_prefix: str | None = None)` — filter on either hash column matching the prefix.
**Acceptance:**
- [ ] Unique prefix resolves; ambiguous prefix and unknown prefix raise the two distinct errors
- [ ] A snapshot used as master by 2 jobs lists usage 2 / source `master`; tailored derives `tailored`; an orphan snapshot lists `-` and appears only when `source=None`
- [ ] `list_applications(resume_hash_prefix=...)` matches on master OR tailored hash
- [ ] All tests pass, committed

## Task 4: Digest — top cap, file output, KV cutoff, attention footer
**Files:** Modify `domain/digest.py`, `services/digest.py`, `cli/digest.py`, `config.py`, `config.example.toml`; Test `tests/unit/test_digest_top.py` + e2e coverage in Task 8.
**What to build:** Per D2/D3/D4/D9. Domain: render accepts `top: int | None`, caps each verdict group at N with `(shown/total)` in the group header. Service: when `cutoff_at` is None read KV `digest.last_rendered_at` as the cutoff; after a successful render write `now()` to that key. Accept an optional `output_dir: Path | None`; when set, write `today.md` (overwrite) and `YYYY-MM-DD.md` (today's date, overwrite). Append a footer section rendering `store.workflow_attention()` (three buckets, store defaults) and `store.needs_attention()` (existing defaults), each omitted when empty. Config: `DigestSettings.output_dir: str | None = None`, `[digest]` section in `config.example.toml` (commented example). CLI: `--top N` (≥1), `--quiet` (suppress stdout echo; usage error without configured output_dir); resolve output_dir from settings (service stays config-free).
**Acceptance:**
- [ ] `--top 2` shows ≤2 rows/group with `(2/total)` headers; without it, full groups unchanged
- [ ] First run with empty KV: no new/seen split crash; second run splits on the stored timestamp; explicit `--cutoff-at` overrides and still updates the key
- [ ] With output_dir set: both files written, stdout still echoes; `--quiet` writes silently; `--quiet` without output_dir → usage error; without output_dir behavior is today's stdout-only
- [ ] Footer lists a seeded follow-up-due job and omits empty buckets; service imports no `jobfeed.config`
- [ ] All tests pass, committed

## Task 5: Companies CLI (add/list/remove)
**Files:** Create `cli/companies.py`; Modify `cli/__init__.py`; Test via Task 8 e2e (+ CliRunner unit for probe-skip path).
**What to build:** Click group `companies`. `add <slug> [--ats ...]`: with `--ats`, upsert directly; without, build an httpx AsyncClient in-command (Phase 2 pattern) and call `probe_company`; unresolvable probe → clear error, no row written. `list [--vendor greenhouse|ashby|lever|removed] [--include-removed]` printing slug/vendor/failure-count. `remove <slug>` → `mark_company_removed`; report when slug didn't exist (method returns bool).
**Acceptance:**
- [ ] `add --ats greenhouse` writes without network; probe path resolves a vendor or errors cleanly without writing
- [ ] `list` hides removed by default; `--include-removed` shows; `--vendor` filters
- [ ] `remove` is idempotent-safe (second call reports not-found, exits nonzero)
- [ ] All commands registered in `--help`; all tests pass, committed

## Task 6: bootstrap-companies
**Files:** Create `adapters/sources/_bootstrap_aggregators.py`; Modify `cli/companies.py`, `cli/__init__.py`; Test `tests/unit/test_bootstrap_parsers.py`.
**What to build:** Per D8. Pure module: `BOOTSTRAP_SOURCES` (port the 7-entry name→raw-README-URL dict from legacy `main.py:2137-2186`), `extract_ats_slugs(markdown) -> set[tuple[slug, vendor]]` (generic regex over boards.greenhouse.io / jobs.ashbyhq.com / jobs.lever.co URLs), `extract_ats_slugs_with_age(markdown, max_age_days)` (age-aware variant, ports legacy `main.py:2238-2260` row-date parsing). CLI command `bootstrap-companies [--source <name>|all] [--apply] [--max-age-days N]`: fetch via lazily-built httpx client, parse, diff against `list_companies`, print add/skip summary; write upserts only with `--apply`.
**Acceptance:**
- [ ] Parser unit tests cover all three vendor URL shapes + a no-match document, from fixture markdown (no network)
- [ ] `--max-age-days` excludes rows older than N in the age-aware fixture
- [ ] Dry-run prints the plan and writes nothing; `--apply` upserts; re-run reports all-skipped (idempotent)
- [ ] Unknown `--source` lists valid names; all tests pass, committed

## Task 7: Workflow CLI gaps — followup, enrich-paste, list rework, apply flags
**Files:** Modify `cli/status.py` (followup), `cli/status_query.py` (list), `cli/apply.py` (flags + snapshots), `services/workflow.py` (set_followup), `services/application.py` (notes + reapply notice), `cli/__init__.py`; Create `cli/enrich.py`; Test via Task 8 e2e (+ service unit for notes plumbing).
**What to build:** (1) `followup <job_id> --in 7d` → Task 1 parser → `WorkflowService.set_followup` (new passthrough) → Task 2 store method; not-found exits nonzero. (2) `enrich-paste <canonical_id> --platform <choice> [--from-file PATH]`; no file → read stdin; empty text → usage error; calls `store.enrich_paste`; prints resulting job id + assessed quality. (3) `list`: add `--days` (Task 1 back-parser), `--notes-contain`, `--limit`, `--allow-empty`; default statuses = `LIST_DEFAULT_STATUSES` when `--status` absent; output columns id/status/company/title/last_change/followup (plain, `--md`, `--json` all carry the new fields); empty result exits 1 unless `--allow-empty` (D5). (4) `apply`: `--method` choice flag mapping to the existing `ApplyRequest.application_method`; `--notes` — add field to `ApplyRequest`, plumb into `ApplicationRecord.notes`; after a successful apply, print `compute_reapply_notice` when non-None (D9). (5) `apply-history --resume <prefix>` → Task 3 filter.
**Acceptance:**
- [ ] `followup 12 --in 7d` then `list --needs-followup` excludes it today; with `--in 2026-06-01` (past) includes it
- [ ] `enrich-paste` works via stdin AND `--from-file`; unknown canonical_id → clean nonzero exit
- [ ] `list` default omits `new`/`archived` rows; `--status new` opts in; `--days 7` + `--notes-contain` + `--limit` AND-compose; empty exits 1, `--allow-empty` exits 0; `--json` rows carry company/title
- [ ] `apply --method referral --notes "via Sam"` persists both (visible in `apply-history`); a second active same-company application prints the reapply notice
- [ ] All tests pass, committed

## Task 8: Snapshots CLI + companion e2e parity suite
**Files:** Modify `cli/apply.py` (snapshots group); Create `tests/e2e/test_cli_parity.py` (`@postgres`); Test itself.
**What to build:** (1) Snapshots CLI per A4: `snapshots list [JOB_ID] [--source master|tailored]` (job_id now optional → global list with usage counts via Task 3); `show`/`diff` resolve hash prefixes via `get_resume_snapshot_by_prefix`, surfacing the ambiguous-vs-unknown distinction as different messages. (2) The companion suite per D11, modeled on `tests/e2e/test_cli_skeleton.py`: one golden-path test per command in A4's table plus the pre-existing commands' new flags, against ephemeral PG with seeded fixtures. Must pin: ILIKE case-insensitivity, `LIST_DEFAULT_STATUSES` filtering, empty-list exit codes, list output columns, digest `--top` capping + KV cutoff key + file writes (tmp output_dir), apply `--method/--notes` persistence, snapshot prefix resolution, companies add/list/remove + bootstrap dry-run (fixture markdown, no network).
**Acceptance:**
- [ ] `snapshots list` (no arg) shows usage counts; `--source tailored` filters; per-job mode still works
- [ ] `show`/`diff` accept unique prefixes; ambiguous and unknown prefixes produce distinct errors
- [ ] Every A4-table command has a passing golden-path e2e; the seven pinned behaviors above each have an explicit assertion
- [ ] Suite runs under the `postgres` marker (CI-runnable, no manual smoke dependency)
- [ ] All tests pass, committed

---

## Self-Review
- **Spec coverage:** Block 1 companies (T5/T6); Block 2 followup/enrich-paste/parser (T1/T2/T7); Block 3 list+digest daily parity (T2/T4/T7); Block 4 apply/snapshots (T3/T7/T8); Block 5 attention+reapply (T4/T7); companion (T8); drops + deferrals recorded in D1/A5, no tasks built for them.
- **Placeholders:** none; every criterion is test-verifiable.
- **Type consistency:** `set_followup(int, datetime)->bool` (T2→T7), `get_resume_snapshot_by_prefix`/`list_resume_snapshots(source)`/`list_applications(limit, resume_hash_prefix)` (T3→T7/T8), `parse_window`/`parse_window_back` (T1→T7), `LIST_DEFAULT_STATUSES` (T2→T7/T8), digest `top`/`output_dir`/KV key `digest.last_rendered_at` (T4→T8).
- **Review-finding coverage:** sizing fix (A2, D8); probe-already-shared (A2/T5); set_followup gap incl. Phase 6 drift note (A2/T2); ILIKE (D6/T2); list output+default+exit codes (D5/T2/T7); digest file/cutoff (D3/D4/T4); apply notes-vs-method split (A2/T7); apply-history + snapshots store work (T3); attention zero-callers wired (D9/T4/T7); companion vehicle corrected to pytest e2e (D11/T8).
