# 21:00 scan and evaluation repair

Result: prevent cookie-only ADP snapshots from ending readiness polling early;
require the canonical Stage B response schema through Codex CLI --output-schema;
report malformed JSON without guessing token truncation from character count.

Boundaries: no production data writes, paid reruns, deployment, or unrelated cleanup.
Workday maintenance remains transient. Existing evaluations and parse evidence stay intact.

Acceptance:
- [x] Failing then passing checks for long malformed JSON and braces inside strings.
- [x] Failing then passing checks for Codex schema subprocess contract and canonical request.
- [x] Failing then passing checks for cookie-only hydration and bounded timeout.
- [x] Focused Python and extension tests plus lint; inspect final diff.

Live evidence: scan b2ca57f9-0824-406e-bce1-1521df02504d returned 59/62 browser JDs.
Two ADP snapshots contain only the cookie privacy text. Evaluation
7cb60bfc-453a-427b-947b-ccb646617a93 failed Perchwell 487176 after missing fields,
then malformed quotation delimiters. Five other parse failures recovered on retry.

Risks: strict schemas depend on installed Codex support; requests without a schema stay unchanged.
Real ADP hydration and a paid evaluation on Mini require post-deployment verification.

Verification evidence:
- Before changes: two parser regression cases, Codex output-schema subprocess check,
  canonical request check, two cookie readiness cases, and backend cookie handling failed.
- After changes: 129 focused Python checks passed; all 93 extension checks passed.
- Ruff passed; mypy passed for all five changed source modules; git diff --check passed.
- Mini /opt/homebrew/bin/codex exec --help confirms --output-schema support.
- Schema matches the parser fixture and rejects each of the three missing JD fields.

Disposition: local implementation complete. No deployment or paid retry performed.
Mini browser verification remains a release check, not a claim of live recovery.
Historical warning/error rows remain truthful; prior results are not reset.

## Authorized release and live verification

User authorized main publication, Mini deployment, and targeted paid verification.
Initial main publication: 681a24e; CI found a missing Returns docstring,
fixed in d9dd32e; rerun 36808207391 passed.

Perchwell 487176: canonical Stage B run c52c10b3-9eec-4b9a-96e6-3822d8150dfc
completed one Stage B, zero Stage A, zero errors, cost $0.05742. Direct DB check
confirms stage_b_status=completed and stage_b_verdict=apply; original Stage A
completion time is unchanged. Historical error text remains audit data.

Real browser check discovered an additional ADP cause: SFC-SHELL has computed
visibility:hidden, while its job descendants are visible. The old snapshot
pruned the whole subtree. A failing regression now passes after visibility is
checked per text node, retaining hidden-subtree and form-control exclusion.
Mini extension recaptured Society Insurance 487140 (3204 characters) and
Forrest Logistics 487111 (3924 characters). Both are full; errors and retry
cooldowns cleared through normal store persistence. Workday IDEXX still
returns its official maintenance page; this is transient, not a complete JD.
Browser evidence is on Mini in artifacts/21h-repair-verification/.

Retry lookup also missed canonical errors because it read only evaluations.
Add real_job_evaluations with the existing run time boundary, retry cap,
resolved-status exclusion and deduplication; SQLite and PostgreSQL queries
stay equivalent. Two failing canonical cases became passing; 61 retry, route,
views and hygiene checks passed. All 95 extension checks passed; mypy and
Ruff passed on changed query modules.

Final source release: c09550e, including ADP snapshot fix e249b6c and canonical
Retry fix. Mini fast-forwarded to this release and restarted with zero active
runs. CI 36808948948 passed the complete quality gate, extension tests, frontend
lint/tests/build. Mini health reports DB/Redis ok and its extension connected;
the normal com.wenqi.jobfeed.server LaunchAgent is running.

Final direct DB checks confirm both ADP rows are full with errors and cooldowns
cleared; Perchwell Stage B is completed/apply and the original Stage A timestamp
is unchanged. The corrected retry lookup returns no remaining errors for the
original 21:00 evaluation, as expected after recovery. Temporary browser probes
were removed from tracked extension files; evidence scripts remain untracked.
Release and targeted verification complete. Workday maintenance remains an
external transient condition handled by the existing retry policy.
