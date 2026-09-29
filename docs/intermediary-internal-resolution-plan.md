# Internal-first intermediary resolution with official-search fallback

## Result

Dice, Jobs via Dice, Haystack, Jobverse and Jobverse.io publishers are recognized
across scan sources; Dice and Jobverse.io outbound domains are also recognized.
Every normal scan now resolves these postings after all source writes finish:

1. An indexed, bounded title lookup searches the internal corpus first. Scoped
   official IDs take precedence; otherwise require employer evidence, compatible
   title/location, and substantial matching JD content. Group matches by real job.
2. A unique internal ATS match uses that existing official parent. Its completed
   evaluation is retained; no policy reset or paid reevaluation is introduced.
3. If no match exists, an ephemeral Codex CLI web-search call finds official ATS
   URLs or an official requisition ID. Known employer Workday routes are reused
   from internal records, avoiding dependence on a fully indexed application URL.
4. Independently fetch the official HTTPS ATS page and parse its JobPosting data.
   Search/model claims alone cannot create an accepted record. Store the full
   official JD, including requirements omitted by intermediaries.
5. Link through the existing scoped identity resolver. Preserve the original source
   URL and JD. Confirm the two source rows actually share a parent before recording
   a successful match. User-decision/identity conflicts remain unresolved.

Unresolved sources are excluded from canonical and legacy paid scoring and Results.
Original source records, scores, Applied/Ignored history and diagnostic dispositions
are retained. No UI components were changed. SQLite Results row/count/selection
queries use the same exact publisher/hostname predicate as evaluation.

## Dependencies, controls and limits

- Production runtime is SQLite; `idx_jobs_title_lookup(title_norm,id)` is additive
  and installed through the existing schema initializer. No data backfill at startup.
- Reuse installed Codex CLI authentication; no new account or API key. Search runs
  in an ephemeral read-only directory with shell and multi-agent tools disabled.
  Only public job facts are supplied, never the resume/profile.
- `[intermediary]` config: `enabled` controls resolution; publisher exclusion is
  unconditional. `external_search=false` retains internal-only resolution.
  Defaults: three searches per scan, 60 seconds total per search+verification,
  `gpt-5.6-luna`, no automatic call retries. Excess work is deferred to later scans.
- Token/cost usage is recorded under the scan ID, with stage unset. Raw model output
  is persisted before parsing. Dispositions live under
  `intermediary-resolution:PLATFORM:NATIVE_ID`; search evidence under
  `official-search:RUN_ID:PLATFORM:NATIVE_ID`.
- A search failure waits 24 hours; no match/ambiguity waits seven days before another
  external search. Internal matching always runs first, even during this cooldown.
- More than 200 title candidates or multiple matching parents is unresolved, never
  an arbitrary first match. Title-only or employer-substring matches are rejected.
- Content matching requires at least 80 words and 60 distinct words, at most 5,000
  words, plus either 90% ordered similarity or 98% source coverage with at least
  70% official coverage. The asymmetric case accommodates complete official JDs
  containing additional requirements/benefits. These conservative rules are not a
  universal guarantee of requisition identity.
- Official verification currently supports concrete URLs on recognized Workday,
  Greenhouse, Lever, Ashby, Eightfold and Southwest ATS hosts and readable structured
  JobPosting data. Unreadable/unsupported sites, login walls and redirects remain
  unresolved. It does not claim to recover every intermediary posting.
- No hash field/calculation/dependency, forced score reset, application submission,
  production bulk rewrite, browser extension update or UI redesign.

## Verification and findings

- Initial tests failed because resolver/matcher/search modules were absent. The
  alias test additionally demonstrated two requests for one Workday requisition;
  the fix verifies that requisition once and returns one candidate.
- Unit/integration coverage includes publisher and domain boundaries, trusted hosts,
  exact identity, long JD additions, wrong employer/location/title, closed postings,
  ambiguity, source-order independence, preserving completed canonical scores,
  no new Stage A claim, Results exclusion, internal-before-search ordering, search
  budget, cooldown, failed search, cancellation fencing and decision-conflict reporting.
- Real SQLite direct checks cover index query-plan use, additive index installation,
  canonical IDs, exact score-row preservation and `PRAGMA quick_check`.
- Live Air search used actual Mini Jobright source 484554. It recovered Leidos's
  R-00192938 using its official careers page plus observed internal employer ATS
  routes. Search took 20.5 seconds and recorded $0.00561936 estimated model cost.
  The independent HTTP verifier accepted the full Workday JD. Earlier research
  attempts returned an unsupported careers URL and a wrong older vacancy, both
  rejected rather than silently accepted.
- A temporary SQLite roundtrip replayed that real search response (no extra paid
  call), fetched the official JD live, and linked the original Dice source to the
  official parent. The next pass reported `matched_internal`, with integrity `ok`.
  Evidence: `artifacts/direct-link-demo/automatic-search.json` and `roundtrip.json`.
- No Mini production data or services have been changed by this feature task.

## Release status

Local implementation and review only. Final Air quality evidence is recorded below.
No main push or Mini deployment is included without explicit release approval.

Final Air quality: `make quality` in a clean tracked-tree export passed Ruff,
format checking, mypy (359 source files), and 2,663 tests; 18 skipped,
477 deselected, eight expected failures. Existing warning dispositions are unchanged.
The final review fixed hostname/path false positives, preserved official parent IDs
when the intermediary was older, fenced post-search writes after lease loss, and
verified actual merge success before reporting a match.
