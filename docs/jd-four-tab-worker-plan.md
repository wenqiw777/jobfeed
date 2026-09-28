# JD four-tab workers

Scope: GitHub JD enrichment only. Four owned background tabs share the permitted
target queue. Each loads/extracts one target, streams its result, then waits 1000 ms
before reuse. Failed extraction also receives cooldown. No changes to other
source collectors, persistence, or the bridge's single-task contract.

Acceptance and evidence (2026-09-17):
- [x] Test-first: old worker failed the concurrency check with `1 !== 4`.
- [x] Four tabs, one result per target, immediate streaming, post-extraction delay.
- [x] Held-timer test proves no reuse before cooldown release; one released
  worker progresses without waiting for the other three.
- [x] Missing permission opens no tab; extraction failures retain target errors.
- [x] Worker cancellation closes owned tabs; bridge cancel/disconnect now also
  removes the complete owned-tab set. Creation races are cleaned in finally.
- [x] `node --test tests/extension/*.test.cjs`: 21 tests passed.
- [x] `node --check` on worker and service worker passed.
- [ ] Reload unpacked extension and validate in real Chrome. User action needed:
  extension-management access is unavailable to this agent. No live throughput
  or production completeness claim; no scan launched as part of this change.

Review/dispositions:
- The scheduler intentionally uses rendered pages instead of HTTP batches, so
  the prior Workable API shortcut and HTTP status host-stop are no longer used
  in this path. Existing direct-fetch helper remains for other callers.
- Page completion is followed by up to 30 extraction attempts, 500 ms apart;
  successful extraction returns immediately. Existing Greenhouse iframe fallback
  retained. Sites rendering later than this bounded window can still fail.
- Four full pages may consume more resources and be slower than direct HTTP.
  Actual speed and site-specific extraction compatibility need the live check.
- Existing unrelated checkout changes preserved; no commit, push, or merge.
