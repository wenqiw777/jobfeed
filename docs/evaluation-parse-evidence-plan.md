# Persist failed evaluation responses

## Result and boundary

Persist each malformed paid response before retrying or setting the final stage
error, for both canonical and legacy Stage A/B paths. Keep the complete response,
parser message/type, run ID, source and canonical IDs, input revision, one-based
attempt, model, timestamp, tokens, cost, cache and latency metadata. Retries and
successful later attempts do not overwrite earlier evidence.

Use the existing durable state store in the same SQLite/PostgreSQL database.
Keys begin `evaluation-parse-error:` followed by run, stage, source ID and a random
UUID. No schema migration, new hash logic, prompt persistence, model call, score
reset or retry-policy change. Logs contain the evidence key, not the raw response.
A failed evidence write propagates instead of silently retrying another paid call.
Previously discarded responses cannot be recovered by this change.

## Verification

Eight service integration cases failed before implementation, then passed:
canonical/legacy x Stage A/B x terminal failure/retry success. Tests reopen the
real SQLite database and compare original long Unicode responses exactly, with
per-attempt identity and model/cost metadata. Complete failure keeps both attempts;
recovery keeps the first failure and the ordinary successful score.

Full Air quality and remote CI are required before Mini reload. Mini verification
uses a separate temporary SQLite database, not synthetic production scores or paid
model calls. Production errors will be captured after activation.

## Retrieval and offline diagnosis

Find records by run in the application's database:

```sql
SELECT key, value FROM state
WHERE key LIKE 'evaluation-parse-error:RUN_ID:%';
```

Each value is JSON; `raw_response` is the untouched text to pass to an offline
parser after correcting the parser or extracting the JSON. A reparse need not call
the model. This change stores evidence; it does not automatically rewrite scores
from recovered responses or add a new review UI.

Air verification completed: `make quality` in an isolated tracked-tree export
passed Ruff, formatting, mypy, and 2,635 tests (18 skipped, 477 deselected,
8 expected failures). Two legacy test doubles were updated to implement the
state-store operation now used by the evaluator.
