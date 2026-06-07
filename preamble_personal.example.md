<!--
  EXAMPLE personal calibration preamble — OPTIONAL, synthetic, not a real person.

  This appendix is appended to the scoring prompt (after templates/_shared_preamble.md)
  to calibrate scores to ONE candidate's real application history. The generic rubric
  references "the personal preamble" throughout — hiring window, ghost anchors, GPA
  notes — and THIS file is where those candidate-specific values live. Without it the
  scorer falls back to generic calibration (still works, just less tuned to you).

  To use it (personal data — never commit your real one):
      cp preamble_personal.example.md preamble_personal.md   # preamble_personal.md is gitignored
      $EDITOR preamble_personal.md
  then in config.toml:  preamble_personal_path = "preamble_personal.md"

  It pairs with resume.example.md (same example candidate: Jordan A. Lee).
-->

## Candidate hiring window

- Graduating **December 2026** (B.S. CS). Active window: roles starting **summer-2026
  internship through 2027 new-grad / entry-level / junior**.
- A JD that requires graduation in Spring/Fall 2025, "Class of 2024 only", or a PhD
  graduating 2028+, is `timing_eligible: "mismatch"`. Anything not stating timing is
  `"eligible"`.

## GPA presentation

- Cumulative GPA 3.7/4.0 — above typical bars. Per the shared rubric, GPA is **not** a
  paper-screen filter unless the JD explicitly states a minimum; don't penalize for it.

## Compensation floor (internal — never echo in any output)

- Base floor ~$120k. Never quote or reason about salary in scoring.

## Calibrated anchors (this candidate's real applications → real outcomes)

Match a JD to the closest anchor and calibrate the band accordingly.

- **OA issued — Acme Cloud, "New Grad SWE, Platform" (2026)** → score **≥ 88**.
  This résumé class cleared every paper screen for mid-size cloud-backend roles.
- **Recruiter call — Beacon Systems, "Backend Engineer I" (2026)** → score **≥ 93**.
  Human follow-up on a distributed-systems JD; strongest positive anchor.
- **Structural ghost — Nimbus IoT, "SWE Intern", on-site Boston, non-local (2025)**
  → score **74–80**. Mid-size non-FAANG + non-local auto-filter; résumé never read.
- **Volume ghost — FAANG-tier multi-team new-grad pool (2025)** → score **82–87**.
  Résumé matched the JD; lost the selectivity lottery, not a content failure.
