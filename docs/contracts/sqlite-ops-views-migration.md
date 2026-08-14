# SQLite Ops, Views, Performance, and Migration Contract

**Status:** Task 0 contract draft, implementation-blocking

**Scope:** Ops, Views, Performance, source-enrichment lookup, and migration-only
PostgresStore capabilities

**Verified against:** `main` at `606c4a4`, 2026-08-11

**Source of truth:** This contract refines Task 0 of
`docs/plans/2026-08-11-jobfeed-sqlite-cutover-impact-plan.md`. The approved plan
wins for overall scope; this file wins for the method-level behavior in its
declared slice.

## Purpose and constraints

This document freezes the observable PostgreSQL behavior that the SQLite
implementation must preserve. It does not prescribe production code bodies and
does not authorize a database cutover.

Constraints:

- Do not change domain DTOs, service behavior, Web response shapes, or CLI output.
- Runtime SQLite remains the only final backend; PostgreSQL access exists only in
  transition migration and rollback tools.
- External fetch, subprocess, and LLM awaits never occur inside a DB transaction.
- UTC, JSON, Unicode search, NULL ordering, pagination, and percentile behavior
  below are acceptance contracts, not implementation suggestions.
- The existing legacy SQLite-v16 importer remains a separate compatibility path.
- All user-facing execution and evidence commands use `./bin/jobfeed`.

Acceptance for this slice requires tests covering every applicable row below,
the PostgreSQL snapshot/benchmark evidence described here, and a reviewed
disposition for every recorded gap. No SQLite production adapter is part of this
task.

## Surface accounting and actual callers

The three assigned store capability files expose 31 methods:

| Capability | Methods |
|---|---:|
| `StoreOpsMixin` | 20 |
| `StoreViewsMixin` | 5 |
| `StorePerfMixin` | 6 |
| Total in this primary slice | 31 |

`PostgresStore` also exposes 20 public methods outside the 90 methods declared in
`ports/store*.py`:

- 18 migration/parity methods: transaction lifecycle, trigger control, sequence
  reset, ten legacy bulk inserts, and two raw parity reads.
- 2 live runtime methods required by `ports/source.py`:
  `get_enrichment` and `get_closed_canonical_ids`.

Therefore the existing runtime contract is **92 methods**, not 90: 90 store-port
methods plus 2 source lookup methods. With the approved 3 run-lease operations,
the target runtime contract is **95 methods**, unless a later evidence-backed
retirement is explicitly approved. The 18 migration methods do not belong on the
new runtime `SQLiteStore` facade; migration adapters own them.

Direct production call-site audit found four methods with no direct call under
`src/jobfeed/`: `record_llm_usage`, `record_step_timings`, `get_cost_range`, and
`digest_stats`. They remain contract-covered because current tests and public
ports use them. Static absence is not approval to retire them.

The two source lookup callers are live and cannot be omitted:

- LinkedIn freshness calls `get_enrichment` from
  `adapters/sources/_linkedin_enrich.py`.
- SpeedyApply liveness calls `get_closed_canonical_ids` from
  `adapters/sources/speedyapply.py`.

### Runtime surface disposition

The project target is **78 runtime public methods**, down from the corrected
current count of 92. This slice classifies all 33 behaviors it owns. The four
`retire` rows below are candidates only: removal occurs in the task that first
migrates their remaining tests and proves no dynamic/user contract depends on
them. `wrapper` means a meaningful public workflow remains while delegating to a
single internal kernel. `merge-internal` removes duplicate implementation, not a
public operation. Semantically different reads/writes are not collapsed merely
to hit the LOC or method-count target.

| # | Runtime behavior | Disposition | Public-surface result and reason |
|---:|---|---|---|
| 1 | `upsert_company` | retain, merge-internal | Keep public upsert semantics; share company row codec only. It is not equivalent to get/list/remove. |
| 2 | `get_company` | retain | Live ATS freshness lookup; distinct optional single-row result. |
| 3 | `list_companies` | retain | Live CLI/Web/ATS filtered ordered collection. |
| 4 | `mark_company_removed` | retain | Live idempotent soft-delete command with boolean result. |
| 5 | `bump_discover_failure` | retain | Live atomic increment-and-return; cannot merge with reset. |
| 6 | `reset_discover_failures` | retain | Live unconditional zeroing; different result and concurrency semantics from bump. |
| 7 | `record_enrichment` | retain, merge-internal | Canonical persisted-enrichment write; share the enrichment update kernel with the paste wrapper. |
| 8 | `list_unenriched_jobs` | retain | Live bounded source queue with frozen ordering. |
| 9 | `mark_job_closed` | retain | Live liveness write with reason-preservation semantics. |
| 10 | `enrich_paste` | wrapper | Keep public CLI/Web workflow; delegate exact-key lookup, quality assessment, and canonical enrichment write. |
| 11 | `get_state` | retain | Live digest cutoff read. |
| 12 | `set_state` | retain | Live exact-key upsert; not interchangeable with get. |
| 13 | `record_cost` | retain, merge-internal | Live budget attempt ledger; share atomic ledger-delta helper with usage+cost. |
| 14 | `get_cost` | retain | Live budget guard single-day read. |
| 15 | `record_llm_usage` | retire | No production caller. Tests must seed through migration fixtures or the combined live write before removal. |
| 16 | `record_llm_usage_with_cost` | retain, merge-internal | Live paid-call audit; its atomic two-table transaction is not replaceable by two public calls. |
| 17 | `get_cost_range` | retire | No production caller or user-facing path; preserve only until contract retirement review. |
| 18 | `digest_stats` | retire | Digest currently derives stats from evaluations and does not call this method. |
| 19 | `needs_attention` | retain | Live digest and `/attention` aggregate with three independently capped buckets. |
| 20 | `mark_stale_jobs_closed` | retain | Live maintenance command with dry-run/write parity. |
| 21 | `get_enrichment` | retain | Live `EnrichmentLookup` source port; previously omitted from the 90-method accounting. |
| 22 | `get_closed_canonical_ids` | retain | Live `ClosedJobLookup` source port with stale-marker exclusion. |
| 23 | `query_jobs_view` | retain, merge-internal | Main Web listing primitive; row/total/tab-count statements share one filter builder but remain one public workflow. |
| 24 | `list_twin_rows_by_status` | retain | Display-fold corpus expansion; not equivalent to detail twin status. |
| 25 | `list_twin_statuses` | retain | Detail-view lookup with self exclusion and a different DTO/order. |
| 26 | `list_pipeline_runs` | retain | Live paginated Runs API with total count. |
| 27 | `insights_overview` | retain | Live multi-aggregate Insights API. It cannot merge with performance overview without changing DTO/window semantics. |
| 28 | `record_step_timing` | retain | Live best-effort single timing write used by `StepTimer`. |
| 29 | `record_step_timings` | retire | No production caller. Atomic batch semantics remain tested until explicit removal. |
| 30 | `get_performance_overview` | retain | Live performance overview with previous-period deltas. |
| 31 | `get_step_timings` | retain | Live ordered raw timing series. |
| 32 | `get_llm_daily_stats` | retain | Live interpolated percentile/token aggregate. |
| 33 | `get_funnel_stats` | retain | Live run-level funnel with 0008 fallback rules. |

This slice therefore proposes **29 retained runtime methods and 4 retirements**.
Together with the independently audited core and business dispositions, this
contributes to the exact 78-operation target. Wrapper/internal-merge rows still
count as public when calculating that target.

## Shared representation and query rules

### Time

- Persist aware datetimes as UTC text with six fractional digits:
  `YYYY-MM-DDTHH:MM:SS.ffffffZ`.
- Parse persisted values back into aware UTC `datetime` values before creating
  domain DTOs.
- A method using `now` obtains one aware UTC timestamp at method entry and binds
  all derived boundaries from it. It must not evaluate different clocks in
  separate statements of one logical aggregate.
- `>= cutoff` is inclusive; `< cutoff` is exclusive. The method matrix calls out
  exceptions.
- UTC calendar-day operations use `[00:00:00Z, next 00:00:00Z)`.

### JSON

- Canonical JSON is UTF-8, sorted-key, compact JSON with `ensure_ascii=false`.
- Raw JSON is parsed with arbitrary-precision `Decimal` for every token that has
  a fraction or exponent. It never passes through binary float first: literals
  `0.1` and `0.10000000000000001` remain distinct.
- JSON integers emit as base-10 integers. Decimal/Python-float values form a
  separate category and emit as exact normalized scientific JSON numbers:
  one coefficient digit, `.`, remaining digits (or `0`), `e`, and a base-10
  exponent. Thus integer `1` differs from decimal `1.0`, while `1.0`, `1.00`,
  and `1e0` converge. Python floats enter through their shortest `str` form.
- NaN and Infinity are rejected at any nesting depth. JSON number, bool, null,
  string, list, and object types remain those types, not quoted stand-ins.
- At every array/object depth, JSON decimal/float `-0.0` canonicalizes to decimal
  `0.0e0`. Integer `0` remains an integer and is not collapsed with decimal zero.
- `stage_b_fit_json.score_0_100` is nullable. Missing JSON, JSON null, missing key,
  or a null key maps to a NULL score and follows the method's NULL ordering.

### Backend scalar canonicalization

- A `bool` column accepts a PostgreSQL/Python boolean or the exact SQLite
  integers `0` and `1`; both encode with the same boolean tag. Any other integer
  fails closed instead of being treated as truthy.
- A finite SQL floating `-0.0` and `+0.0` use the same positive-zero encoding.
  SQLite `REAL` does not preserve the sign on round-trip, and no frozen product
  behavior observes it. Other finite floats retain their exact hexadecimal value;
  non-finite floats fail closed.
- JSON signed-zero normalization is recursive because PostgreSQL JSON/JSONB and
  SQLite text round-trips may choose different zero signs. This is the only JSON
  numeric equivalence beyond removal of insignificant decimal trailing zeros;
  integer versus decimal/float remains distinct.
- Codec v1 is pinned by `tests/fixtures/canonical_row_v1_golden.json` to mixed
  literal SHA-256
  `87a0778e5980b4753c1d8c1df69fab0efd35bd3c88b8b3948555e49c602e6e3a`.
  The fixture uses composite primary key `tenant, id`, including two rows with
  the same tenant, so both key position and numeric ordering are exercised.
  A later byte-level semantic change requires a new codec version and manifest,
  not an in-place rewrite of a completed cutover's v1 digest.

### Codec v1 framing

Every frame is exactly `tag || uint64_be(payload_length) || payload`: a one-byte
tag, an unsigned eight-byte big-endian payload length, then that many payload
bytes. Length is never ASCII. Schema frames are `M` magic, `V` version, `S`
schema name, repeated `C` column declarations (nested `n` name, `k` kind, `q`
nullable), then repeated `P` primary-key names. Each row is one `R` frame whose
payload is its declared-column value frames; final row count is an ASCII integer
inside a `Z` frame. Value tags are `N` SQL NULL, `B` bool, `I` integer, `D` SQL
Decimal, `F` float, `T` UTC timestamp, `S` raw text, and `J` canonical JSON.

Scalar payloads also match the implementation exactly: integers use base-10
ASCII; SQL Decimal uses signed coefficient plus colon/exponent (`0:0` for any
zero); float uses `float.hex()` after collapsing both signed zeros to positive
zero; timestamps use six-digit UTC `...Z`; text and JSON use UTF-8. Framing, tags,
schema/column order, and primary-key order are all digest inputs.

### Unicode search

- Each connection registers the approved deterministic Unicode casefold
  function. Both stored value and pattern are casefolded.
- `query_jobs_view(search=...)` is a literal substring over `company OR title`:
  backslash, `%`, and `_` are escaped before adding outer `%` wildcards.
- `list_statuses(notes_contain=...)`, owned by the Status slice but consumed by
  the same query kernel, deliberately retains PostgreSQL wildcard behavior:
  user `%` and `_` remain wildcards and backslash remains the escape character.
- SQLite `NOCASE` is not an acceptable substitute because it is ASCII-only.

### Errors and transactions

- Invalid numeric string identities continue to raise `ValueError` at the adapter
  boundary where current code calls `int(...)`.
- Schema/FK/CHECK violations propagate as store/database errors unless a row below
  defines a domain error or no-op.
- Each writer is one short transaction. `record_llm_usage_with_cost` is explicitly
  all-or-nothing across two tables.
- Empty batch methods are successful no-ops.

## Ops method contract

| Method | Input and output/error | Frozen behavior |
|---|---|---|
| `upsert_company` | `CompanyRecord -> None`; constraint errors propagate | Natural key is exact `slug`. Insert all fields. On conflict, incoming non-NULL vendor/timestamps/notes replace stored values; incoming NULL preserves them. Override, job count, and failure count always replace. No casefold of slug. |
| `get_company` | `slug -> CompanyRecord | None` | Exact slug lookup. Boolean integer hydrates to bool; nullable vendor/timestamps/notes remain NULL. |
| `list_companies` | optional exact `vendor`, `include_removed`; returns list | Default excludes `ats_vendor='removed'` but keeps NULL vendors. Truthy vendor AND-composes an exact equality filter. Ordered by `slug ASC`; empty result is `[]`. |
| `mark_company_removed` | `slug -> bool` | Unknown or already removed returns `False`. Otherwise sets vendor to `removed`, override to false, verified time to NULL, and returns `True`. A later upsert with a non-NULL vendor makes it removable again. |
| `bump_discover_failure` | `slug -> int` | Atomically increments and returns new count. Unknown slug returns `0`; no row is created. |
| `reset_discover_failures` | `slug -> None` | Existing row becomes zero; unknown slug is a no-op. |
| `record_enrichment` | job id plus JD fields; `None` | Unknown numeric id is a no-op; invalid id string raises `ValueError`. Replaces JD/quality/time/source/lang, clears enrich error and `closed_at`, resets all ML-gate verdict fields. `posted_at` fills only when stored value is NULL. |
| `list_unenriched_jobs` | exact platform and limit; list | Requires `jd_text IS NULL` and `closed_at IS NULL`. Ordered `discovered_at DESC, id DESC`; limit zero returns `[]`; NULL fields outside the projected identity/url are irrelevant. |
| `mark_job_closed` | job id, aware time, optional reason; `None` | Unknown numeric id is a no-op. Sets `closed_at` even when already set. NULL reason preserves `enrich_error`; non-NULL reason replaces it. |
| `enrich_paste` | platform, canonical id, JD; returns job id | Exact natural-key lookup. Unknown key raises `ValueError("job not found: platform/id")`. Assesses quality, uses current UTC, source `manual-paste`, then applies all `record_enrichment` reset rules. |
| `get_state` | key -> string or None | Exact key lookup. Missing returns `None`; stored empty string remains empty string. |
| `set_state` | key/value -> None | Atomic exact-key upsert; later value replaces earlier value. |
| `record_cost` | day, spend, calls=1 -> None | Exact day-key upsert; atomically adds both numeric values and updates `last_updated` to one method-entry UTC time. Zero calls is valid. |
| `get_cost` | day -> `CostEntry | None` | Missing returns `None`; preserves day, accumulated floats/ints, and aware UTC update time. |
| `record_llm_usage` | `LLMUsage -> None` | Appends exactly one row. Nullable job/stage/run remain NULL. Missing referenced non-NULL job fails FK. Negative token/cost/latency and invalid stage fail CHECK. |
| `record_llm_usage_with_cost` | day, spend, usage -> None | One transaction: append usage and add spend to ledger with `calls=0`. Either both commit or neither does. This method must not double-count the attempted-call counter already recorded by the budget path. |
| `get_cost_range` | `since_days=30`; list | UTC date cutoff is `today - since_days`, inclusive. Ordered `day DESC`. Empty result is `[]`. Caller currently absent; retain contract. |
| `digest_stats` | threshold=60 -> `DigestStats` | All-time jobs; jobs discovered during current UTC day; all-time completed Stage B; completed Stage B with JSON fit score `>= threshold`; today's cost/calls or zeros. Missing/null fit score does not count. A present non-numeric score currently fails PostgreSQL's integer cast and must raise rather than silently disappear. Caller currently absent. |
| `needs_attention` | days=7, max/category=10 -> report | Three independent lists: recent non-NULL enrich errors; recent stub/partial jobs with completed Stage A; any-age rows at retry cap. Each list is independently capped. Existing SQL has no ordering, so row order is currently unspecified and must not be exposed as a semantic sort. |
| `mark_stale_jobs_closed` | days, dry-run -> count | `days < 1` raises `ValueError`. Match quality NULL/missing/abandoned, `discovered_at < now-days`, and open rows only. Dry-run writes nothing and returns matches. Write stamps one method-entry UTC time and the stale marker. Repeating returns zero. |

## Source-enrichment lookup contract

| Method | Input and output/error | Frozen behavior |
|---|---|---|
| `get_enrichment` | platform + canonical id -> `StoredEnrichment | None` | Exact natural-key lookup. Unknown returns `None`. A known row returns a snapshot even when all four enrichment fields are NULL; quality text hydrates to `QualityBand` and invalid stored enum propagates `ValueError`. |
| `get_closed_canonical_ids` | exact platform -> `set[str]` | Returns rows with non-NULL `closed_at`, excluding only rows whose `enrich_error` exactly equals the stale-backfill marker. NULL errors and all other reasons remain included. Unknown platform returns empty set; order is intentionally absent. |

## Views method contract

### Jobs-view tab and sort rules

All shared filters apply identically to rows, active-tab total, and every tab
count. Tab predicates are:

- `queue`: status in new/scored/shortlisted/awaiting_referral and open.
- `pending_jd`: quality NULL/missing/abandoned, no Stage A score,
  status not archived/ignored, and open.
- `all`: all rows, including closed.
- `scored`: status scored.
- `shortlisted`: status shortlisted/awaiting_referral.
- `archived`: status archived/ignored.

Sorts are stable:

- `discovered_desc`: `discovered_at DESC, id DESC`.
- `posted_desc`: posted non-NULL descending, NULL last, then discovered/id DESC.
- `score_desc`: Stage-B fit score with Stage-A fallback, non-NULL descending,
  NULL last, then discovered/id DESC.
- `company_asc`: company norm non-NULL ascending, NULL last, then
  discovered/id DESC.

| Method | Input and output/error | Frozen behavior |
|---|---|---|
| `query_jobs_view` | validated `JobsViewQuery -> JobsViewPage` | Status filter AND-narrows tab. Literal Unicode-casefold search covers company/title. Freshness uses `discovered_at >= now-days`; verdict filter requires non-NULL Stage-B verdict. SQL `limit/offset` occurs before service hard-filter/fold. `total` ignores page; all six tab counts ignore page and share request filters. Negative limits/offsets and unknown tab/sort are rejected by `JobsViewQuery`. |
| `list_twin_rows_by_status` | paired norm keys, statuses, limit -> rows | Empty keys or statuses returns `[]`. Match exact paired `(company_norm,title_norm)`, not cross product. NULL/blank norms never match. Ordered `discovered_at DESC, id DESC`; bounded by limit. The production caller supplies sorted unique keys; duplicate-input behavior is not public contract. |
| `list_twin_statuses` | job id -> rows | Invalid id string raises `ValueError`; missing/blank-norm source returns `[]`. Excludes self. Exact non-blank persisted norm match. LEFT-joined missing status remains NULL at SQL level, though normal schema trigger seeds it. Ordered `twin.id ASC`. |
| `list_pipeline_runs` | limit=50, offset=0, optional days -> `(rows,total)` | Optional inclusive cutoff `started_at >= now-days`; no days means all time. Rows ordered `started_at DESC, run_id DESC`. Total applies days but ignores page. Web callers validate limit 1..200, offset >=0, days 1..365. |
| `insights_overview` | caller-validated window -> overview | The window defines the job discovery-date cohort for totals and distributions. `evaluated_jobs` means non-NULL `stage_a_at`; gate count means result `pass`; applied total means current `job_status.status = 'applied'`; verdict wins, otherwise only skipped-below-threshold enters derived bucket. Daily buckets are UTC and closed on both ends `[now-days, now]`, limited to cohort jobs; applied activity is `job_status_history.to_status = 'applied'` bucketed by `changed_at`. Buckets omit future/NULL timestamps and empty days, merge three measures with zeros, and order day ASC. |

## Performance method contract

| Method | Input and output/error | Frozen behavior |
|---|---|---|
| `record_step_timing` | `StepTiming -> None` | Appends one row. DB generates UTC `created_at`; input `created_at` is currently ignored. Missing run id fails FK. `StepTimer` treats this write as best-effort and swallows/logs failures outside the store. |
| `record_step_timings` | list -> None | Empty list is no-op. Non-empty batch preserves input insertion order through generated ids. Batch is one atomic write unit. Caller currently absent. |
| `get_performance_overview` | window days -> overview | Uses completed runs only. Current window is `started_at >= now-window`; previous is `[now-2*window, now-window)`. Scan is exact source not equal to `evaluate`; eval is case-insensitive source matching PostgreSQL `ILIKE '%evaluat%'` (for example evaluate/evaluation/evaluating). Empty current values are zero. A delta is `(current-previous)/previous` only when previous window has rows and the specific previous metric is >0; otherwise NULL. Error rate is runs with `errors>0` divided by completed runs. |
| `get_step_timings` | window, optional exact type -> list | Inclusive cutoff on `created_at`; exact step type when supplied. Ordered `created_at ASC, id ASC`; empty is `[]`. Boolean and timestamps hydrate to bool and aware UTC datetime. |
| `get_llm_daily_stats` | window -> list | Inclusive timestamp cutoff; UTC day buckets; only days with rows; day ASC. Token values are arithmetic means. P50/P95 use PostgreSQL continuous percentile: sort, `h=(n-1)*p`, linearly interpolate floor/ceil. Empty is `[]`. |
| `get_funnel_stats` | window -> list | Only exact source `evaluate`, inclusive start cutoff. `after_gate=max(jobs_gate_passed,stage_a_scored,stage_b_scored)`; `scored=max(stage_a_scored,stage_b_scored)`; add gated/filtered counts as current formulas do. Ordered started time DESC; equal-time ordering is currently unspecified. Scan-only window returns `[]`. |

Web performance and insights callers validate windows to 1..365. Store methods do
not currently repeat that validation; SQLite must not invent different results
for invalid direct calls. Contract tests should exercise valid values and leave
invalid-value validation at the existing caller boundary.

## Migration-only public method disposition

The following 18 current public methods are not part of the target runtime
facade. They remain supported by the legacy v16 compatibility path until Task 8,
while the new cutover tooling uses dedicated migration adapters.

| Methods | Current contract | Target disposition |
|---|---|---|
| `begin_import_transaction`, `commit_import_transaction`, `rollback_import_transaction` | One dedicated connection spans all bulk calls; commit/rollback without an active transaction is a no-op | Private migration-adapter transaction context; failure rolls back the whole import |
| `disable_triggers`, `enable_triggers` | Disable/re-enable jobs status seed trigger | New PG snapshot export never disables source triggers. SQLite temporary import creates seed/status rows explicitly and installs triggers after data load. SQLite→PG rollback uses a private, transaction-scoped control for the single named `trg_jobs_seed_status`; it never disables all triggers. |
| `reset_sequences` | Reset jobs and status-history serial ids beyond preserved ids | PG rollback resets every serial identity table represented in 0008; SQLite preserves integer ids and validates next generated ids |
| ten `bulk_insert_*` methods | Empty list returns 0; otherwise insert exact typed rows and return input length; calls require active import transaction | Keep only for legacy-v16 path. New bidirectional migration covers all 14 tables, including pipeline runs, LLM usage, interview rounds, and step timings |
| `read_all_rows`, `count_rows` | Allowlisted table only; unknown table raises `ValueError` | Replace all-row memory loading with ordered/chunked migration reads; parity allowlist is exactly the 14 migrated tables |

The legacy methods cover only ten imported tables and cannot satisfy this
cutover. Current `read_all_rows` also loads an entire table, which is unsuitable
as the primary checksum path for a 375 MB jobs table.

## PostgreSQL 0008 baseline and 14-to-15 table contract

The source and rollback target must report Alembic revision exactly `0008`.
Revision 0008 adds non-null `pipeline_runs.jobs_gate_passed DEFAULT 0`. Revision
0007 is rejected rather than silently filling the missing column.

The 14 migrated tables are:

`jobs`, `evaluations`, `pipeline_runs`, `resume_variants`, `job_status`,
`job_status_history`, `applied`, `resume_snapshots`, `companies`, `cost_ledger`,
`state`, `llm_usage`, `interview_rounds`, and `step_timings`.

The fifteenth SQLite table is `run_leases`. It is not migrated from PG. The new
database contains exactly two seed rows, `scan` and `evaluate`, each with
`generation=0` and NULL owner/run/heartbeat/expiry fields.

Export is permitted only after all formal writers stop. Remaining historical
`running` pipeline rows are finalized with the approved cutover-recovery reason
before the consistent snapshot begins.

## Snapshot manifest

Every rehearsal and cutover produces a UTF-8 JSON manifest. It contains no DSN,
password, token, environment dump, or absolute home-directory secret. The
illustrative shape below is not an accepted artifact: placeholders must be
replaced by values satisfying the executable exact validator. Required fields
are:

```json
{
  "format_version": 1,
  "created_at_utc": "2026-08-11T00:00:00.000000Z",
  "git_commit": "full-sha",
  "schema_registry": {
    "manifest_version": 1,
    "canonical_row_codec_version": "jobfeed-canonical-row-v1",
    "alembic_revision": "0008",
    "tables": "the exact ordered 14-table registry object described below"
  },
  "source": {
    "backend": "postgresql",
    "alembic_revision": "0008",
    "server_version": "16.x",
    "database_size_bytes": 0,
    "jobs_size_bytes": 0,
    "consistent_snapshot_id": "pgdump-sha256:<sha256>",
    "source_dump_sha256": "sha256",
    "source_dump_size_bytes": 0
  },
  "restore_attestations": {
    "source": {
      "attestation_version": 1,
      "dump_sha256": "sha256",
      "container_id": "isolated-source-container",
      "database_identity": "sha256",
      "restore_tool": "pg_restore",
      "restore_tool_version": "16.x",
      "restore_command_sha256": "sha256",
      "pre_upgrade_revision": "0007",
      "post_upgrade_revision": "0008"
    },
    "scratch": "same exact keys; distinct container/database identity"
  },
  "writer_quiescence": {
    "checked_at_utc": "...",
    "active_jobfeed_writers": 0,
    "historical_running_runs": 0
  },
  "tables": {
    "jobs": {
      "row_count": 0,
      "primary_key": ["id"],
      "max_identity": 0,
      "canonical_sha256": "sha256"
    }
  },
  "aggregates": {
    "as_of_utc": "2026-08-11T00:00:00.000000Z",
    "window_days": 30,
    "pending_stage_a": 0,
    "pending_stage_b": 0,
    "needs_attention_sha256": "sha256",
    "funnel_sha256": "sha256",
    "daily_cost_sha256": "sha256",
    "llm_percentiles_sha256": "sha256"
  },
  "target": {
    "status": "not_applicable_postgres_baseline",
    "backend": "sqlite",
    "sqlite_schema_version": 1,
    "minimum_sqlite_version": "3.35.0",
    "migrated_table_count": 14,
    "total_table_count": 15,
    "sqlite_file_sha256": null
  }
}
```

The manifest never contains its benchmark hash. The benchmark points one-way to
the manifest SHA and workload SHA. A final `evidence-index.json` points to the
dump, manifest, benchmark, and workload hashes. Exact validators reject unknown,
missing, or extra fields, avoiding a manifest/benchmark hash cycle. Restore
provenance is orchestrator evidence: both attestation files name the same dump
SHA and 0007→0008 upgrade, while their container and live database identities
must differ. This is a restored-dump identity, not `pg_export_snapshot` proof.

The example expands the complete 0008 `jobs` schema. The executable source of
truth for all 14 tables and 153 columns is
`src/jobfeed/adapters/migration/canonical_schema_manifest_v1.json`, loaded as
immutable `CANONICAL_SCHEMA_MANIFEST_V1` and `CANONICAL_ROW_SCHEMAS_V1` by
`canonical_schema_manifest.py`. A real snapshot copies that registry structure
for every table in exact order and adds row count, ordered canonical checksum,
and max generated integer id when applicable.

The importer and verifier fail closed on an unknown manifest/codec/revision;
missing, extra, duplicate, or reordered tables/columns; or any source type,
target type, codec kind, nullability, or primary-key mismatch. They never infer a
replacement mapping.

Codec v1 follows the exact tags, uint64 framing, scalar payloads, JSON numeric
rules, schema order, and primary-key order frozen in **Codec v1 framing** above.
Rows stream in full primary-key order and SHA-256 consumes fixed-size ordered
chunks; chunk size cannot change the digest. Raw TEXT is never parsed as JSON.

Task 0's mixed-type golden fixture covers NULL/empty, Unicode, delimiter-like
text, timezone offsets, signed zero, JSON key order, nested JSON signed zero, and
multi-column PK order. Its literal SHA-256 is consumed by backend-neutral tests;
SQLite in-memory round trips additionally prove REAL signed-zero and BOOLEAN
integer representations normalize to the same bytes. Boundary-framing tests
prove different row boundaries cannot collide.

`test_canonical_schema_manifest.py` independently derives
the 0008 CREATE/ALTER column surface from Alembic and verifies every registry
field. Canonical checksums normalize timestamps, booleans, floats, and declared
JSON fields before hashing and are computed in ordered chunks rather than by
loading an entire table. Raw-text fields such as the three application snapshots
remain `text` and are never parsed or reordered as JSON.

## Command contract and metric reproduction

Task 4 must expose these canonical Docker CLI shapes; DSNs are read from named
environment variables so shell history does not contain credentials:

```bash
./bin/jobfeed migrate pg-to-sqlite \
  --source-dsn-env JOBFEED_MIGRATION_PG_URL \
  --target /data/jobfeed.next.sqlite \
  --manifest /data/jobfeed.snapshot.json \
  --verify

./bin/jobfeed migrate verify-cutover \
  --source-dsn-env JOBFEED_MIGRATION_PG_URL \
  --sqlite /data/jobfeed.next.sqlite \
  --manifest /data/jobfeed.snapshot.json

./bin/jobfeed migrate sqlite-to-pg \
  --source /data/jobfeed.sqlite \
  --target-dsn-env JOBFEED_MIGRATION_PG_URL \
  --cutover-manifest /data/jobfeed.snapshot.json \
  --verify
```

Task 0/3 benchmark tooling must expose one reproducible command with the same
workload manifest for both backends:

```bash
./bin/jobfeed migrate benchmark-store \
  --backend postgres \
  --dsn-env JOBFEED_MIGRATION_PG_URL \
  --scratch-dsn-env JOBFEED_MIGRATION_SCRATCH_PG_URL \
  --machine-token-env JOBFEED_BENCH_MACHINE_TOKEN \
  --source-dump artifacts/jobfeed-0007.dump \
  --source-restore-attestation artifacts/source-restore.json \
  --scratch-restore-attestation artifacts/scratch-restore.json \
  --workload docs/contracts/fixtures/sqlite-store-benchmark-v1.json \
  --artifact-dir artifacts/postgres-store-baseline

# Task 4 pending: the same command gains --backend sqlite and --path only after
# the SQLite adapter and scratch-copy lifecycle exist.
```

The report records the hash of an explicit shared machine token plus CPU
identity, git SHA, snapshot manifest SHA, warmup count, sample count, per-query
P50/P95/max, and contention outcomes. It covers list/detail/status hot paths, all
five Views methods, all four Performance read methods, insights, DB-only read
proxies, and the approved two-process contention workload. P95 uses at least 30
measured samples. Text primary keys use PostgreSQL `COLLATE "C"` and SQLite byte
order. Only explicitly unordered `needs_attention` buckets are stable-sorted;
funnel primary order, cost day-desc order, and LLM day-asc order remain digest
inputs. Every rolling aggregate binds the same database-derived `as_of_utc` and
records it with the 30-day window. The source is freshly gated and fully rehashed
after read benchmarks. Both contention processes connect before a shared start
event; each must claim successfully, and the final IDs/statuses must exactly
match the reported claims, including legitimate stale-claim recovery.

Restore provenance remains **OPEN** in this bounded slice. The current two
attestation-file options are validation plumbing, not proof: user-authored JSON
can still satisfy their shape. Baseline capture is prohibited until the canonical
restore orchestrator is integrated as the only CLI entry, generates both
attestations from executed restore commands, validates live container/database
identity, and prevents manually supplied provenance from reaching capture.

The disposable scratch benchmark now measures both real mutation workloads.
Scan times one `save_job` insert plus a quality upgrade on the same natural key,
then verifies one FULL row. Evaluate creates explicit fixtures outside the timed
path and records at least 30 samples each for claim+release, claim+result, and
claim+error through production store methods without external LLM calls. A fresh
raw read verifies every final status, error, and score before reporting success.

Real-data metrics are rechecked from the stopped 0008 source, never copied from a
document. The evidence bundle includes:

- `SELECT version_num FROM alembic_version` equals `0008`.
- `SELECT pg_database_size(current_database())`.
- Exact row counts for all 14 tables.
- `pg_total_relation_size('jobs')`.
- Maximum activity timestamps from jobs, pipeline runs, LLM usage, step timings,
  applications, status history, and interviews.
- Counts of `pipeline_runs.status='running'` and active Jobfeed writer processes.
- The manifest and every benchmark output SHA-256.

The currently observed 413 MB / 56,507-job numbers are planning context only.
They are not accepted as cutover evidence until reproduced after the source is
upgraded to 0008.

## Configuration, Docker, and CI compatibility

- Final runtime config is `[db].path` with flat `JOBFEED_DB_PATH` and nested
  `JOBFEED_DB__PATH`; nested form wins when both are set.
- Normal runtime rejects `[db].url`, `JOBFEED_DB_URL`, and `JOBFEED_DB__URL` with
  an actionable migration message. They are never silently ignored.
- Migration commands alone accept a source/target DSN via the explicitly named
  environment option above. This is not a backend selector.
- Docker mounts one named data volume at the path containing `[db].path`; every
  transient `docker compose run --rm` invocation and `serve` uses the same file.
- PostgreSQL `depends_on`, PG port, and Alembic smoke remain during rollback soak
  and are deleted only in separately approved Task 8.
- CI first adds SQLite lanes and bidirectional migration/parity coverage. PG lanes
  remain until rollback soak passes; removing them early is a release blocker.

## Lossless rollback preconditions

SQLite-to-PostgreSQL rollback is allowed only when all conditions hold:

1. All SQLite Web/CLI writers are stopped and writer quiescence is recorded.
2. The source SQLite has the exact supported schema version, exact 15-table set,
   and exact per-table column/type manifest recorded at cutover. The schema is
   frozen throughout soak; any unknown version, extra/missing table, or
   extra/missing/changed column fails closed before reading rows.
3. The rollback PG target is revision 0008 and was restored from the exact
   cutover snapshot identified by the manifest.
4. Target PG canonical checksums still match that cutover snapshot. Any target-side
   insert, update, delete, or unknown divergence fails closed.
5. A final consistent SQLite backup and manifest are created before rollback.
6. Before replay, the target must prove the exact `trg_jobs_seed_status` trigger
   exists and is enabled. On one dedicated connection and inside the same
   all-or-nothing transaction, rollback tooling records the original trigger
   state, disables only that named trigger, replays all 14 tables in FK-safe
   order, resets every represented sequence, re-enables the trigger, and proves
   it is enabled before commit. This prevents jobs inserts from synthesizing
   duplicate status/history while preserved rows are replayed. Disabling on a
   separate pooled connection is forbidden. Never use `DISABLE TRIGGER ALL` or
   `DISABLE TRIGGER USER`, and never leave trigger state to an
   out-of-transaction cleanup. Deletes as well as inserts/updates are represented.
7. Conflicts are detected; there is no last-write-wins merge.
8. PG sequences are reset for every generated integer identity within the same
   transaction described above.
9. Reverse row/PK/FK/JSON/checksum/business parity reaches 100%, then CLI/API smoke
   passes before traffic switches.
10. On any failure, the import transaction rolls back all data, sequence, and
    trigger-state changes; a `finally` path verifies the original enabled state
    before releasing the connection, and SQLite remains the formal source of
    truth. Fault-injection tests interrupt after trigger disable/`jobs`, during
    replay, after sequence reset, and before trigger re-enable. Every case proves
    no partial rows, no seeded extras, the trigger enabled, and a normal
    post-failure job insert seeding exactly one status and one history row.

## Evidence gaps and owner tasks

These are implementation blockers for later tasks, not reasons to weaken the
contract:

| Gap | Owner/evidence required |
|---|---|
| Formal data is at 0007, so no valid 0008 PG baseline exists yet | Cutover owner upgrades a backup to 0008, runs smoke, then captures manifest and benchmark |
| Existing jobs-view tests cover ASCII case and literal `%`, not deterministic Unicode casefold, `_`, or backslash | Task 3C adds PG golden plus SQLite contract fixtures |
| Notes search tests cover ASCII case only, not intentional wildcard behavior | Status/Task 3C adds `%`, `_`, backslash, and Unicode fixtures without changing wildcard semantics |
| LLM percentile tests only assert P95 >= P50 | Task 3D adds fixed odd/even/singleton datasets proving continuous interpolation |
| `needs_attention` and equal-time funnel ordering are unspecified today | Preserve unordered semantics in APIs/tests, or return to design before introducing a user-visible order |
| Legacy parity covers ten tables and excludes current Phase 3/6/9 data | Task 4 replaces it for cutover with all-14-table chunked parity |
| Four port methods have no direct production caller | Retain and test until a separately reviewed surface-retirement decision |
| Main plan originally omitted two live source lookup methods | Resolved by plan commit `d6565c3`: total accounting is now 92 existing + 3 lease runtime methods |

## Completion evidence for this document

- 31 assigned port methods mapped to inputs, outputs/errors, NULL/time/JSON/search,
  ordering, idempotency, and transaction behavior where applicable.
- 2 live source lookup methods mapped and tied to actual production callers.
- 18 migration/parity public methods inventoried with target disposition.
- Executable v1 registry covers all 14 Alembic-0008 tables and 153 ordered
  columns, with exact PK/source type/target type/codec kind/nullability checks.
- 0008 source gate, 14-to-15 table mapping, manifest, benchmark command shapes,
  config compatibility, and lossless rollback preconditions recorded.
- `git diff --check` must pass before commit.
