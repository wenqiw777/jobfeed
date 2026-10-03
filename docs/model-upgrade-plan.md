# Quick and Detailed model upgrade

Requested: Quick uses `codex-cli/gpt-6-luna`; Detailed uses
`codex-cli/gpt-6.1-sol` on the existing Mac mini runtime and Codex account.
Preserve scoring thresholds, concurrency, source settings and completed scores.
No bulk evaluation, account switch, API-key setup or main merge.

The two model IDs appear in the Mini model catalog. Official OpenAI model pages
confirm the IDs and Standard pricing used by Jobfeed's existing cost estimator:
https://developers.openai.com/api/docs/models/gpt-6-luna
https://developers.openai.com/api/docs/models/gpt-6.1-sol

Initial build check rejected both models because their vendored prices were
absent. Commit 26be2a44 adds only these two records. Thirty relevant checks and
CI run 37091936122 passed. Mini settings changed only the two model strings.

Real calls exposed a second issue: the isolated Codex command ignores the host
provider config. Luna succeeded, while Sol returned HTTP400 in that mode.
Sol succeeded with the existing host provider config. Preserve only the selected
OpenAI-auth provider's nonsecret connection settings as explicit CLI overrides;
keep user rules, MCP, hooks, notifications, unrelated models and headers isolated.

- [x] Regression RED: selected host provider absent from isolated command.
- [x] GREEN: selected provider preserved; unrelated/private settings excluded;
  configured target model retained; no-config behavior retained. 49 focused
  checks pass. Full quality: Ruff, format, mypy and 3100 tests pass.
- [ ] Deploy adapter fix while no pipeline run is active; preserve Mini edits.
- [ ] Both real Jobfeed adapters return schema-valid results with the target IDs.
- [ ] Effective HTTP settings and health verified; exact final commit CI passes.

Evidence directory: `artifacts/model-upgrade-20261002` on Mini. Config backup
is private; public proof files contain model IDs, usage and connection results.
