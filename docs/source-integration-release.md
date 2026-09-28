# Source integration release verification

Scope: PR #25, complete source selection/settings and repair its committed
runtime dependencies. Indeed and LinkedIn Guest remain available. GitHub feeds
are labeled GitHub job lists. Canonical job workflow, ML model changes and Redis
scan activation remain outside this release.

## Resolved blockers

- Persist source-native/external identity and enrichment retry state in SQLite,
  including additive upgrade of existing databases. No hash fields added.
- Save partial source results and wait for every source before marking the run
  failed; retain the correct failing source and complete insertion counters.
- Depend on the pipeline port from services; allow only the newly used pure
  standard-library modules in the domain import contract.
- Narrow optional types after existing guards, document public contracts and
  flatten excessive nesting without changing source semantics.

## Evidence

Verification used an exported committed/staged snapshot, independently of the
uncommitted work in the development checkout.

- Source setup defers model construction until extraction is needed. The full
  backend suite also passes with codex removed from PATH, reproducing CI.
- Source/API tests: 21 passed. Frontend: 169 passed; production build passed.
- Final backend: 2250 passed, 418 deselected, 34 file-length warnings.
- Ruff lint and format check passed; mypy passed for 328 source files.
- Partial scan regression: initially only one of two sources was counted at
  finalization; after repair both postings and final counters are preserved.
- SQLite upgrade and reconnect tests directly inspect a temporary database;
  existing rows survive column installation and cooldown timestamps survive
  reconnect. The live database was not migrated or modified.
- Independent review found the partial-scan finalization race; the revised
  wait-before-finalize behavior was re-reviewed with no remaining blocker.

The default backend lane excludes PostgreSQL/live/browser/ML-model tests.
The Git-index assertion in the exported snapshot used the actual repository's
index; runtime Python modules came exclusively from the exported source tree.
Existing non-failing file-length warnings do not require unrelated splitting.
Remote quality and browser checks must pass before the authorized rebase merge.
