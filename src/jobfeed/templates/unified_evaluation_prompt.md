# Unified objective evaluation

Evaluate the resume against the job posting once. Return one JSON object and
nothing else: no markdown fence, commentary, or trailing prose.

The evaluation is limited to:

1. Explicit, objective eligibility constraints in the JD.
2. Evidence in the resume for every required and preferred JD qualification.
3. A match tier synthesizing that evidence.
4. An ATS visibility diagnostic based on how visibly the resume states the
   JD's terminology.

Do not recommend an application action. Do not assess employer prospects,
compensation attractiveness, posting freshness, candidate growth, or the time
or effort needed to apply. Do not output match_score; code computes it from the
requirement evidence. ATS visibility is a separate diagnostic and does not
contribute to qualification.

## Exact output schema

All top-level and nested keys shown below are required. Do not add keys.

```json
{
  "summary": "<concise factual description of the role and its qualifications>",
  "eligibility_status": "pass" | "fail" | "unclear",
  "eligibility_checks": [
    {
      "kind": "<objective constraint type, such as clearance, work_authorization, degree, graduation_window, or location_or_presence>",
      "requirement": "<the explicit JD constraint>",
      "status": "pass" | "fail" | "unclear",
      "candidate_evidence": "<specific resume evidence>" | null,
      "reason": "<why the evidence establishes this status>"
    }
  ],
  "requirements": [
    {
      "requirement": "<one required or preferred qualification from the JD>",
      "priority": "required" | "preferred",
      "category": "skill" | "experience" | "education" | "domain",
      "match": "direct" | "adjacent" | "missing" | "unclear",
      "resume_evidence": "<specific resume evidence>" | null,
      "evidence_type": "professional" | "internship" | "project" | "skills" | "coursework" | "none"
    }
  ],
  "match_tier": "strong_match" | "possible_match" | "weak_match" | "ineligible",
  "one_line": "<one factual sentence citing the decisive evidence or gap>",
  "ats_visibility_score": <integer 0-100>
}
```

## Eligibility rules

- Add a check only for an explicit objective JD constraint. Do not invent one.
- `fail` requires affirmative candidate evidence that conflicts with the
  constraint. Missing resume information is `unclear`, not `fail`.
- Aggregate status is `fail` if any check fails, otherwise `unclear` if any
  check is unclear, otherwise `pass`. An empty check list is `pass`.
- Use `ineligible` only when at least one eligibility check is `fail`.

## Requirement evidence rules

- Include each actual qualification exactly once. Separate `required` from
  `preferred` using the JD's wording; do not promote preferences to requirements.
- `direct` means the resume explicitly demonstrates the qualification.
  `adjacent` means specific evidence transfers but is not the same qualification.
  `missing` means the resume clearly lacks it. `unclear` means the supplied
  documents cannot decide.
- Cite concrete resume evidence. Use null plus evidence_type `none` for missing
  or unclear evidence. A skills-list mention is not professional experience.
- Domain experience counts only when the JD explicitly requires or prefers that
  domain. Distinguish professional, internship, project, and coursework evidence.
- Never infer years of experience from employer age, product age, or unrelated
  numbers. Keep internship tenure separate from non-intern professional tenure.

## Match tier and ATS visibility

- `strong_match`: direct evidence covers nearly all required qualifications.
- `possible_match`: the core is evidenced, with adjacent or limited required gaps.
- `weak_match`: one or more core required qualifications lack credible evidence.
- `ineligible`: reserved for a confirmed eligibility failure, never a score band.
- Preserve a substantive evidence judgment in `match_tier`; downstream code does
  not mechanically replace it based on match_score.
- `ats_visibility_score` estimates literal visibility of JD terminology in the
  resume. Semantic adjacency alone is not literal visibility. This diagnostic
  must not influence `match_tier`.
