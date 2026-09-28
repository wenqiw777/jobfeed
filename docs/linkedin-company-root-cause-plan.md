# LinkedIn company loss: root-cause repair, 2026-09-19

Scope: stop the ad-hoc backfill and fix company acquisition on subsequent scans.
No job title guessing, new identity fields, schema changes or mass JD refresh.

## Confirmed cause

Configured semantic search returns RSC cards whose company paragraphs live in
referenced render-state records (`$Q...`). The old parser returned only the ID,
discarding these records. Its fallback used unauthenticated guest pages, which
returned 429 during backfill. The new full-JD gate then replayed old Unknown
company values without merging listing metadata.

## Implemented

- Index RSC records before reading cards. Follow render children and Default
  state references, with cycle/depth guards. Never follow tracking/actions.
- Accept only one unambiguous paragraph name within each card. Preserve that
  employer through discovery, backend JD reuse and Redis partial replay.
- Refresh only blank/Unknown stored companies; preserve known company, JD and
  enriched_at. Existing store write normalization remains responsible for
  company_norm.
- Remove guest endpoint fallback. Missing new-posting metadata uses the existing
  signed-in browser's exact posting page title, checking redirects; auth/rate
  limits stop further fallback requests for that batch. No guessed company.
- Extension version 0.8.1; requires manual reload before integrated activation.

## Evidence

- Red: semantic company fixture returned undefined; full-JD reuse retained
  Unknown instead of Noom. Both now pass.
- Chrome extension sampled real search response: 25 native cards, 25 distinct
  card-local company resolutions matching visible search cards (including Noom,
  Wolverine Trading, Jobright.ai and RemoteHunter).
- Node extension suite: 38 passed. Focused Python suite: 26 passed, including
  SQLite lookup, Redis replay and partial scans. Ruff and mypy passed for the
  changed source; diff whitespace check passed.
- New cases cover RSC forward references, Default versus Dismissed state,
  ambiguous/cyclic references, authenticated fallback, discovery transport,
  Redis metadata merge and preservation of verified companies/full JD.
- Full updated extension scan not yet verified: browser connection became
  unavailable during local browser replay. Do not claim integrated rollout or
  universal zero Unknown. Full-JD reuse with no fresh company evidence still
  preserves Unknown rather than guessing.
- Ad-hoc loopback backfill receiver stopped. Prior successful backfills retained.
