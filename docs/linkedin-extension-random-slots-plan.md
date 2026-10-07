# LinkedIn Extension and three random daily slots

Authorized 2026-10-06: deploy to Mac Mini. The daily schedule applies to the existing all-source scan followed by unrated evaluation.

Acceptance: Extension enabled, Guest disabled, existing Extension links preserved; choose three distinct slots from twelve Detroit two-hour windows and one minute in each; persist choices and attempts; retain no catch-up/no retry behavior; Mini bridge and API healthy; read back both launch agents and saved plan.

Reuse existing random planner and cycle. Preserve today's already selected plan and attempts during deployment; full twelve-slot selection begins with the next day's plan. Active server work retains its original configuration and is not interrupted.

Risks: manual scans are outside the daily timer limit; busy/disconnected or failed runs can leave fewer than three successful scans. Random timing does not guarantee avoidance of LinkedIn detection. Host timezone must match America/Detroit for launchd calendar triggers.

Progress:
- [x] Test-first evidence: 7 failing checks and 1 passing compatibility check before implementation.
- [x] Scheduler and cycle checks: 31 passed; Ruff and diff whitespace checks passed.
- [x] Mini script deployed. API and disk readback verify Extension=true, Guest=false with all other editable configuration unchanged. Both launch agents loaded and plutil validation passed. Mini direct runtime smoke passed; pytest is absent on Mini, so the full 31-test suite ran locally. Health DB/Redis/server OK and bridge connected with linkedin-search-results support. Existing run was not cancelled or server restarted.

Deployment evidence: today's persisted plan remains [139, 648, 1085]. Tomorrow 2026-10-07 saved minutes [209, 563, 1213] = 03:29, 09:23, 20:13 Detroit. Daily planner moved from 00:05 to 00:00 and checks an exact current-minute trigger after installation, preventing a selected 00:00 minute from being lost. Manual installation does not start a scan. New complete Extension scan has not been run as part of deployment; only configuration, bridge and scheduler were verified. Backups are under Mini data/scheduler/deploy-backup-20261006. No release blockers remain.
