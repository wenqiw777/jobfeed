# GitHub-list job destination permissions

Diagnosis: the unpacked extension used a static destination allowlist from an older job inventory. The 2026-09-20 02:30 UTC retry recorded 56 missing-permission results, not network timeouts. Forty missing URL origins cover 54 of those jobs, including HTTP inputs and company career-site redirects. They are now added as exact-origin host patterns in manifest.json; no all-sites or wildcard-subdomain access was added.

Excluded: two IDEXX links redirected to community.workday.com. This has not been established as a job destination and was not authorized in the manifest merely to suppress the error.

Validation: configuration-only change (test-first exception). Direct comparison with the persisted failed origins confirms all 54 scoped jobs are covered and no duplicate host patterns were introduced. Seven existing extraction/worker tests passed. The old github-jd-hosts.json is a historical input inventory, not the runtime permissions source; it was not rewritten as a new full inventory.

Activation: Chrome must reload the unpacked Jobfeed Jobright Source extension to apply the changed manifest. Automated navigation to chrome://extensions/ was blocked by the browser tool URL policy. No workaround was attempted. User reload is required before live permission readback and another targeted scan can verify recovery. Complete JD extraction is not guaranteed by permission coverage; parsing failures remain separate.

Evidence: artifacts/redis-memory-repair/permission-repair.json lists every added origin and the excluded redirect. No scan was started in this step.

## 2026-09-28 scan follow-up

Run `9e1d1fc7-99b9-4f57-9347-89aa675ebfcd` reported 22/46 complete GitHub-list descriptions and 24 failed targets. SQLite `jobs.enrich_error_code` for this run gives 17 `missing_permission`, 4 `no_complete_jd`, 2 `transient`, and 1 `parse_failed`. The 17 permission failures cover 14 origins, all already present in the tracked `manifest.json` (commit `6c6f866`, 2026-09-25). The installed Chrome extension has not been verified as reloaded with those permissions. Browser automation was blocked from `chrome://extensions/` by its URL policy; user reload is pending. Do not claim those 17 recovered until a targeted live retry confirms it.

The four unavailable pages had observed snapshots: Precisely showed Cloudflare verification; two Varsity Brands links redirected to the careers home page; Akuna displayed "Role not found." The two IDEXX links redirected to Workday maintenance. The Icon Ashby posting is live in the official posting API, but its `descriptionPlain` contains only two careers URLs. These seven lack a verified complete employer JD; retain their failure/retry state rather than treating a wrapper, challenge, or link as one.

Verification so far: all 14 required origins occur in the manifest; 23 GitHub JD extension tests pass. Remaining acceptance: reload the unpacked extension, retry the 17 permission targets, inspect per-target descriptions and error codes, and report residual failures separately.
