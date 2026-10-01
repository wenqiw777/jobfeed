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
