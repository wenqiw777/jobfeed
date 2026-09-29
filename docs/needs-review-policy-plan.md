# Legacy review policy reconciliation

User-approved scope: reuse old scores despite enrichment timestamp uncertainty; choose the highest complete historical evaluation as one unit; prefer a trusted official ATS JD over lower-trust disagreements. Keep missing JD holds, true conflicting official inputs, source audit rows and live canonical input invalidation. No new hash. No production database writes in this implementation step.

## Policy
- Historical winner: highest Stage A score, then latest Stage A timestamp, then highest source job ID. Stage B, explanation, model and cost stay with that row.
- Input: select among complete trusted ATS sources when present, otherwise require existing cross-source agreement. Contradictory trusted ATS sources stay held.
- Legacy scores retain absent historical model/policy metadata; existing views already accept this absence. Candidate SQL and claim checks now agree, while explicitly known policy changes still invalidate. Record adoption source and chosen input in revision-keyed existing state plus reconciliation output, not a fabricated scoring event.
- Existing canonical scores are not overwritten with legacy scores by reconciliation; real input changes still archive/invalidate through existing sync logic.
- Missing input requires a separate decision and remains held.

## Tasks and acceptance
- [x] Failing tests for timestamp reuse, highest whole evaluation, official priority and conflicting official hold.
- [x] Shared selection policy and SQLite/Postgres legacy adoption parity.
- [x] Explicit SQLite dry-run/apply reconciliation tool, default read-only, report IDs/actions/provenance; repeated apply idempotent.
- [x] Air-copy test validates preservation of source/evaluation rows, missing holds and current canonical invalidation.
- [x] Root reviews and independently verifies before any production apply.

## Evidence
- Red: official preference and timestamp reuse unit tests failed (2 failures/6 passes). Reconciliation test initially failed on missing API. Added configured-policy acceptance then reproduced immediate requeue (`real_id` unexpectedly in candidate query).
- Green: 90 tests passed, 23 deselected across source selection, reconciliation, SQLite evaluation, candidate queries, canonical service, resolver/workflow/migration and web reader policy behavior.
- Mypy: all 352 source files pass. Scoped Ruff and `git diff --check` pass.
- Real PostgreSQL on Air: 32 integration tests passed. Old expectations now assert official input revision invalidation and history preservation, and authorized missing-policy reuse.
- Reconciliation tests cover highest-score answer integrity (A/B/explanation from one row), ties by date then ID, repeated apply/scan, source evaluation preservation, missing JD, contradictory official input, unrelated review conflict, and existing canonical input invalidation.
- Root is independently rehearsing the CLI against `artifacts/canonical-backlog-performance/mini-policy-rehearsal.sqlite`. Initial dry-run: 4,613 legacy adoptions, 49 existing-canonical clears, 11 pending clears, 705 holds retained. Existing-canonical clears are now reported separately if actual input changes will invalidate a score. No production writes by this implementation agent.

## Operations

Default read-only preview:

```sh
.venv/bin/python scripts/reconcile_legacy_reviews.py --db /absolute/path/copy.sqlite --report /absolute/path/preview.json
```

Explicit apply to a verified copy uses the same command with `--apply`. Stop writers first. The command refuses active claims, creates a private timestamped SQLite backup, and writes a private per-ID report. It never initializes schema, calls an LLM or fetches JD. Root must verify the Air copy and approve deployment before running against Mini.

Historical adoption records use `real-job-legacy-adoption:<real_id>:1` in existing `state`: score source ID, selected input source ID, revision 1 and adoption time. They remain historical events after future real scoring/input changes; they are not current-model proof. No new schema or hash.

## Remaining scope boundaries

- The 27 explicitly missing descriptions remain held; no empty-JD overwrite repair or fabricated input.
- Official selection uses the pre-existing verified URL providers only, not arbitrary external links. Multiple disagreeing official descriptions remain held.
- Existing canonical scores are never replaced by legacy winner selection. Real input change still archives/invalidates normally.
- The reconciliation command is SQLite-specific for Mini deployment; both adapters share source policy and implement normal legacy adoption/backfill.

## Air production-copy rehearsal

- 5,378 holds inspected: 4,613 legacy evaluations adopted, 49 existing canonical
  holds resolved (one genuine input change archived/invalidated), 11 pending
  inputs released, 705 holds retained. Repeat preview proposes no mutations.
- SQL EXCEPT both directions confirms source jobs, source evaluations, source
  workflow/status history and all unrelated tables unchanged. Canonical workflow
  changed only 78 new-to-scored transitions; manual states remain intact.
- Remaining: 84 evaluation conflicts, 269 input conflicts, 27 missing JDs,
  325 requirements conflicts. quick_check=ok.
- Air full quality: 2,614 passed. Reconciliation+input+hygiene focused checks:
  33 passed. No Mini production database changes yet.
