# Random daily guest scans

Approved: LinkedIn guest instead of the logged-in extension; three daily scans
at randomly selected local times, not fixed hours/minutes. Use 01:00–03:00,
09:00–11:00, 17:00–19:00 as visible default windows. Keep existing all-source
scan then evaluate cycle. No backfill launch, account login, or main merge.

Acceptance: draw three minute slots once per day; preserve them on restart;
reject stale-day, late, duplicate and malformed timer deliveries; claim before
execution so failures cannot automatically repeat. A daily local planning agent
updates the scan timer without visiting any site. Initial schedule is tomorrow.

Guest: carry over the two active extension search keywords and US location,
remove account-linked URL parameters, keep its existing 1000-job cap, enable
anonymous guest, disable logged-in extension and Playwright sources. Preserve
other settings. Validate source wiring and one anonymous request, no DB scan.

Evidence:
- Old timer had eight fixed slots/day; disabled and unloaded for containment.
- Regression RED: new random scheduling tests fail at missing module.
- [x] Full quality gate: 3098 passed, 19 skipped, 8 expected failures; Ruff,
  formatting, strict production mypy pass. Random/idempotency regressions pass.
- [x] Mini effective HTTP configuration confirms guest=true, extension=false,
  browser=false. Guest adapter built from saved settings; exactly one anonymous
  list request returned HTTP200 / 10 cards, no login cookie/authorization and
  no database scan/evaluation triggered. Two existing searches / US / 1000 cap.
- [ ] Commit and CI pass; Mini settings and loaded random calendar verified.
- [ ] Anonymous guest smoke and no automatic run during installation verified.
