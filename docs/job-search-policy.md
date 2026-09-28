# Jobfeed Job Search Policy

Status: confirmed user policy as of 2026-09-02.

This document is the source of truth for Jobfeed discovery, eligibility, triage, and ranking behavior. Items under **Remaining decisions** are not yet confirmed policy.

## Primary objective

Jobfeed's first responsibility is to avoid missing jobs that are worth applying to. Resume fit may affect application order and tailoring advice, but it must never make an otherwise eligible job disappear.

## Target roles

### Primary queue

- United States software-related full-time and new-graduate roles.
- Explicit New Grad, University Graduate, Early Career, Entry Level, Junior, Engineer I, and similar roles.
- Roles requiring 0–3 years of experience.
- Roles with no stated minimum experience when there is no confirmed seniority requirement.
- Software engineering, AI/ML engineering, Forward Deployed Engineering, Product Engineering, Platform Engineering, and Founding Engineering are in scope when their actual work is software-related.
- A title such as Forward Deployed Engineer or Founding Engineer must not be excluded by title alone.

### Secondary queue

- Internships are secondary priority.
- Finish all applicable primary-queue New Grad/full-time jobs before working through internships.
- Internships with Evidence Fit of at least 75 form the first internship priority.
- Internships with Evidence Fit below 75 remain visible and form the final,
  time-permitting priority. They must not be automatically filtered or ignored.

## Candidate facts and constraints

- Work authorization: United States citizen; sponsorship is not required.
- Clearance: no active clearance today, but willing to obtain one.
- Graduation can truthfully be planned for either December 2026 or May 2027. Use the date compatible with the JD and keep the chosen date consistent within that application.
- A future master's degree is possible but is not confirmed and must not currently be used to satisfy enrollment requirements.
- Location: willing to work onsite, hybrid, or remotely anywhere in the United States.
- Relocation: willing to relocate anywhere in the United States.
- There are no excluded United States states or cities.

## Eligibility and hard blockers

An in-scope job defaults to `Apply`. Use `Blocked` only when the official JD provides evidence of a confirmed, non-tailorable conflict.

Confirmed blockers include:

- The job is outside the United States and does not allow Remote US work.
- The work is confirmed to be outside the software-related target scope.
- The JD explicitly requires more than three years of experience.
- The JD explicitly requires a senior/staff/principal level that is incompatible with the 0–3 year search.
- The JD requires an active/current clearance, including an active TS/SCI or Full Scope Poly.
- A mandatory degree, enrollment status, or graduation window does not intersect December 2026 through May 2027.
- When an internship requires the candidate to remain enrolled or return to school after the internship, and future master's enrollment is not confirmed, keep the job visible with `Enrollment eligibility: Uncertain`. Do not assume that a possible future master's degree satisfies the requirement.

Clearance language must be interpreted precisely:

- "Able/eligible to obtain a clearance" -> `Apply`.
- Employer-sponsored clearance after hiring -> `Apply`.
- "Active/current clearance required" -> `Blocked`.

Location must be supported by the structured location or the official JD:

- United States onsite/hybrid -> eligible.
- Remote in the United States -> eligible.
- Missing or ambiguous location -> remain `Pending` while the official JD is checked; do not silently discard it.
- A conflict between a source card and the official JD must be resolved in favor of the official JD.

The following are not blockers:

- Low resume fit.
- Missing keywords in the base resume.
- Missing preferred or nice-to-have skills.
- Lack of prior experience in the employer's exact domain.
- High competition.
- Ambiguous requirements. Ambiguity defaults to `Apply` with a warning after evaluation, unless a hard blocker is confirmed.

## Triage states

`Consider` is not part of the target state model.

- `Pending`: JD enrichment or evaluation is incomplete.
- `Apply`: no confirmed hard blocker; place the job in the application queue.
- `Blocked`: a confirmed hard blocker is present. The UI must show the exact supporting JD evidence.
- `Applied`: the application was submitted.
- `Ignored`: the user explicitly chose not to pursue the job.

Expected flow:

```text
Pending -> Apply -> Applied
        -> Blocked
        -> Ignored
```

`Ignored` is a user action. A low model score must not automatically convert a job to `Ignored`, `Blocked`, or an invisible state.

## Visibility policy

- Every job within the target scope must become visible immediately, including jobs still marked `Pending`.
- Completion of Evidence Fit and the presence of a verdict must not be prerequisites for Results visibility.
- Resume-fit thresholds must not filter jobs out of Results.
- Evaluation updates the recommendation, explanation, warnings, and ranking; it does not control discovery visibility.

## Ranking policy

Ranking answers "which job should be applied to first?" It does not answer "is this job allowed to appear?"

Confirmed ranking inputs are:

- Company and compensation value.
- Posting date and application urgency.
- How well the role's stated career stage matches the candidate's New Grad/early-career target.
- Resume/evidence match as a secondary ordering signal.

The confirmed 35% company-and-compensation component is split as follows:

```text
20% compensation value
15% verified company strength
```

Posting date is a material ranking input, not the sole sort key. The final order must use application priority first and posting date as a tie-breaker. When a source only provides a date rather than a reliable timestamp, use coarse freshness buckets rather than pretending hour-level precision.

Primary-queue jobs always rank ahead of secondary internships. A high-scoring internship must not jump ahead of unfinished eligible New Grad/full-time applications.

Ranking is lexicographic by queue and then by the score within that queue:

```sql
queue_tier ASC,
priority_score DESC,
posted_at DESC
```

Queue tiers are:

```ini
queue_tier = 0  # New Grad / Early Career full-time
queue_tier = 1  # Internship with Evidence Fit >= 75
queue_tier = 2  # Other internships
```

The queue order is strict: complete all eligible Primary jobs first, then work
through internships with Evidence Fit of at least 75, then apply to lower-fit
internships when time remains. A score below 75 never hides an internship.

Primary New Grad / Early Career full-time weights:

```text
35% company and compensation value
30% posting date and application urgency
25% Career Stage Fit
10% Evidence Fit
```

High-match and other internship weights:

```text
40% Evidence Fit
25% company and compensation value
25% posting date and application urgency
10% role-direction match
```

The Evidence Fit threshold of 75 only separates the two internship tiers. It must not affect the visibility or eligibility of New Grad/full-time jobs.

### Compensation value

- Rank in-scope software roles by nominal United States compensation. Do not
  reduce compensation scores for high-cost cities: willingness to relocate to
  high-paying markets is part of the candidate policy.
- Do not create narrow title families for compensation merely because titles
  say backend, full-stack, platform, AI, or Forward Deployed Engineer. For the
  initial benchmark, all in-scope software New Grad/0-3-year full-time roles
  form one cohort; internships use a separate cohort.
- The official JD's explicit base-pay range is the primary per-job evidence.
- Jobfeed's recently collected, explicit JD pay ranges form the preferred
  market benchmark. BLS OEWS software/computer occupation wage percentiles are
  the free fallback when the internal cohort is too small.
- Levels.fyi may be consulted manually as supporting total-compensation context
  or integrated through a licensed API agreement. Do not automatically crawl,
  scrape, or copy Levels.fyi salary data without that permission.
- Do not estimate unspecified equity, bonus, or total compensation. Keep base
  pay and total compensation conceptually separate.
- Missing compensation remains visible and neutral; it never blocks or hides a
  job.

### Verified company strength

Company strength uses only source-backed categories and scale evidence:

- Public company: verify identity and listing through SEC EDGAR, then use dated
  filing evidence for scale where available.
- Startup: verify the company through an authoritative startup/accelerator
  source and preserve active status, team size, batch, and other sourced facts.
- Operating company with unverifiable value: preserve the verified identity but
  do not invent a valuation or prestige score.
- Insufficient information: assign a neutral unknown value, not a low value.

An unverified unicorn label, estimated valuation, prestige claim, ownership,
mentorship, or supposed learning opportunity must not affect the score. A
startup is not automatically weaker than a public company; explicit high pay
and application urgency can place it ahead.

### Career Stage Fit

Career Stage Fit answers how well the role's stated career stage matches the
candidate's New Grad/0-3-year search target. It measures compatibility, not how
clearly the JD is written. A clearly stated incompatible cohort must never
receive a high score merely because the requirement is explicit.

The semantic JD parser supplies structured facts rather than a direct score:

- role level: New Grad, entry level, junior, mid, senior, or unknown;
- required experience range, including whether it is required or preferred;
- graduation or hiring window;
- current-enrollment or return-to-school requirements;
- exact source evidence and its document offsets; and
- field state: `present`, `not_stated`, `ambiguous`, or `unavailable`.

Eligibility is resolved before Career Stage Fit. A mandatory graduation window
that does not intersect December 2026 through May 2027, required experience
above three years, or an incompatible senior/staff/principal level is
`Blocked`. Blocked jobs have no Career Stage Fit or priority score; both are
`null`/not applicable rather than misleading numeric values.

For jobs that pass eligibility, use these initial scoring anchors:

```text
100  A mandatory graduation/hiring window explicitly matches 2026/2027
 95  The title or JD explicitly targets New Grad or University Graduate candidates
 85  Early Career, Entry Level, Engineer I, Junior, or an equivalent signal
 80  Explicitly requires 0-1 years of experience
 70  Explicitly requires 0-2 years of experience
 60  Explicitly requires 0-3 years of experience
null No career-stage information, conflicting evidence, or unavailable evidence
```

Required conditions take precedence over preferred conditions. Specific
numeric constraints take precedence over vague title language, and a mandatory
graduation window takes precedence over a generic New Grad label. Conflicting
signals produce `ambiguous` with no observed score; the system must not select
the most favorable signal.

When Career Stage Fit is `not_stated`, `ambiguous`, or `unavailable`, the UI
shows `--` plus the field state and reason. Priority calculation may use a
separately stored neutral effective value of 50 so missing data neither rewards
nor penalizes the job, but it must never expose that neutral prior as an
observed Career Stage Fit score.

### Evidence Fit

Evidence Fit estimates how strongly the candidate's existing, truthful
experience would persuade a recruiter that the candidate can perform the job.
It is based on evidence, not keyword coverage. Use the following initial
internal weights:

```text
40% evidence of similar work in projects or internships
25% demonstrated use of the role's core technical skills
20% similarity of work type, such as backend, infrastructure, ML, or full-stack
15% additional relevant evidence, such as industry, open-source, research, or scale
```

A skill appearing only in the resume's Skills section is weak evidence. A
project that actually used the skill is medium evidence. An internship or other
substantive experience performing similar work is strong evidence. Resume
tailoring may improve keyword coverage, but it must not invent stronger
experience evidence.

For New Grad/full-time jobs, Evidence Fit is only a 10% within-queue ordering
signal. Low Evidence Fit never hides or blocks an otherwise eligible job. For
internships, the confirmed threshold of 75 determines whether the job enters
the high-match or other-internship tier.

These concepts must remain separate:

- New Grad clarity: whether the role targets the candidate's career stage.
- Evidence Fit: whether truthful existing experience supports recruiter-level
  credibility for the role.
- Hard blocker: a confirmed, non-tailorable eligibility conflict.
- Keyword coverage: a resume-tailoring concern, not a ranking or eligibility
  gate.

## Resume tailoring and fit

- A tailored resume will be produced for each application.
- Missing skill keywords in the base resume have no filtering value because JD keywords can be incorporated during tailoring.
- Keyword coverage is a tailoring task, not a discovery or eligibility gate.
- Resume fit may estimate how strongly the underlying experience supports the JD and how much interview preparation is needed.
- Resume fit may change order within a queue, but low fit still defaults to `Apply` when no hard blocker exists.
- Facts that cannot be changed through tailoring include employment history, project history, degree/enrollment status, graduation timing, citizenship, clearance, and years of experience.

## Confirmed current-system gaps

The following are time-specific findings from the live system on 2026-09-02:

- `stage_a_threshold` is 70 and currently prevents relevant jobs from receiving a verdict.
- In the hard-filtered and deduplicated candidate window, 2,736 complete, software-related, 0–3-year-or-unknown, non-senior JDs had no verdict and were absent from Results.
- Of those, 1,651 were `skipped_below_threshold`, 253 were `in_progress`, and 832 were unevaluated.
- The Triage UI defaults to `posted_desc`, not a composite application-priority ranking.

Examples of hidden target roles included Palantir Forward Deployed Software Engineer New Grad, NewsBreak Software Engineer Junior New Grad, Cadence Application Software Developer New Grad, Ciena Software Developer New Grad, General Motors Software Engineer Early Career, and several other explicit New Grad roles.

## Remaining work and decisions

The company-and-compensation component and its 20%/15% split are confirmed.
The remaining calibration work is to derive percentile cutoffs from the
collected JD compensation distribution and compare a shadow ranking before
activation. Do not define static company tiers or hand-maintained membership
lists before that evidence.

The following are implementation and verification work, not additional user
policy decisions:

- Source-by-source recall verification for Jobright, LinkedIn, and company ATS
  sources.
- Replacing the Stage A visibility threshold and removing `Consider` from the
  target Triage state model.
- Rendering company evidence in Triage and comparing the current order with a
  shadow company-aware ranking before activating it.

## Company intelligence integration

Accepted scope on 2026-09-02:

- Sync the YC public company API, the multi-accelerator Startup Portfolios
  dataset, and the AI Startups Hiring watchlist into one local company
  intelligence cache.
- Preserve source provenance and source-specific confidence. An upstream
  `isUnicorn` value remains an unverified claim, and the AI watchlist is a
  discovery signal only.
- Match jobs by official website domain when available and otherwise by an
  exact normalized company name. Never use substring matching.
- A failed refresh must retain the last successful data for that source.
- Expose matched evidence to the jobs read path before it affects ranking.

Deliberately deferred until evidence from the integrated data can be reviewed:

- Exact company-value scores and tier cutoffs.
- Whether hiring activity or accelerator membership should change urgency.
- External verification rules for valuation, funding, and public-company
  status.

### Confirmed large-company sources

Confirmed on 2026-09-02:

1. Use SEC EDGAR as the authoritative United States public-company layer.
   Sync the company/ticker/exchange mapping and use submissions plus XBRL
   company facts for former names and dated financial scale evidence.
2. Join SEC companies to Wikidata by CIK to add official websites, aliases,
   parent relationships, industries, and dated employee counts. Wikidata is
   supporting evidence, not an authority for eligibility or an undated size
   claim.
3. Use GLEIF only as a third-layer legal-entity and parent-company resolver
   for identities that remain unresolved. GLEIF does not supply a company
   quality score.
4. Keep all three sources in the local source-provenance cache. A source
   refresh failure retains its last successful snapshot.
5. Do not use OpenCorporates, Alpha Vantage, Crunchbase, Dealroom, DOL visa
   filings, or copied Fortune/S&P/Unicorn CSV files as primary company-value
   sources under the current free/local design.

Engineering defaults that do not require another product decision:

- Refresh the small SEC company index daily.
- Refresh SEC bulk financial/submission data and Wikidata enrichment weekly.
- Query GLEIF only for unresolved identities and cache successful responses
  for 30 days.
- Preserve the observation date for every time-varying value.
- Unknown or stale company evidence remains neutral and never hides or blocks
  a job.
