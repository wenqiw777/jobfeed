# Conservative eligibility blocker comparison

## Fixed objective

The blocker exists only to hide jobs that contain explicit, evidence-backed hard
conflicts with the candidate profile. Priority remains a soft ordering signal and
cannot Block a job. Missing, ambiguous, preferred, or alternative qualifications
default to not blocked.

Candidate profile used by this experiment:

- graduation years: 2026 or 2027;
- completed/expected credential: Bachelor;
- target experience ceiling: 3 years;
- no active security clearance;
- United States software roles within the existing seniority policy.

## Compared approaches

1. Existing deterministic gate: fastest and cheapest, but its regex/context rules
   can propose false Blocks.
2. Full-JD Luna gate: semantic reading can understand alternatives and negation,
   but it costs one model pass and can vary.
3. Fine-tuned local semantic candidate model: learns explicit hard-conflict clauses
   and hard negatives locally. It proposes evidence but cannot issue the final Block.
4. Non-symmetric cascade: native facts, strict rules, and the local model run in
   parallel for recall; Luna independently reads the full JD only for a proposed
   conflict; a fixed policy executor Blocks only when the verifier confirms the
   same decisive fact and relationship. Disagreement means not blocked. This is the
   recommended production architecture if its validation passes.

## Experiment contract

- Use the same frozen 1,000 SDE JDs for development comparison: 200 official pages
  without Greenhouse/Lever/Ashby native API results and 800 LinkedIn-only-in-current-
  corpus jobs. All 1,000 company strings and normalized full texts are distinct.
  LinkedIn is stratified as 600 guest, 100 direct LinkedIn, and 100 JobSpy.
- Every model Block must name exactly one blocker category and return a contiguous
  quote from the JD or title.
- A deterministic validator rejects ungrounded evidence and incomplete output.
- Model reviews are exploratory labels, not gold. They can train candidates and
  locate disagreements, but cannot establish production accuracy.
- Report Block count, adjudicated true/false Blocks, precision, disagreements,
  runtime, and model-call volume. If adjudication coverage is incomplete, do not
  call the result accuracy.

## Acceptance and non-goals

After development, build a locked approximately 700-JD challenge set: for each of
the seven Block categories, 60 likely hard conflicts and 40 trap negatives covering
preferred, alternative, negated, company-background, and ability-to-obtain wording.
All predicted Blocks receive two independent human blind labels; disagreements get
a third human adjudication. A stronger model only reviews human uncertainty and
cross-method conflicts and is never gold. Audit 200 high-risk non-Blocks separately.

The preferred production candidate must have zero observed false Blocks, 100%
verbatim evidence, at least 299 reviewed predicted Blocks overall, and at least 59
reviewed Blocks for each automatically enabled category. With zero errors these
sizes bound the one-sided 95% false-Block rate below about 1% overall and 5% per
category. Categories below the threshold remain shadow-only.

Priority accuracy, salary parsing, UI integration, migration, and production
deployment are out of scope until the user chooses an approach.

The first verifiable result is prediction files over the identical 1,000 IDs.
The plan becomes invalid if the candidate profile above is wrong or if a Block is
allowed without explicit source evidence.

## Development results (2026-09-05)

The frozen cohort contains 1,000 complete SDE JDs: 200 official pages without a
Greenhouse/Lever/Ashby native result and 800 LinkedIn-derived records. It contains
1,000 distinct company strings and 1,000 distinct normalized full texts. This is
a development cohort, not human gold and not a source-prevalence estimate.

The existing deterministic gate proposed 415 Blocks; Luna proposed 342. They
agreed on Block for 272 jobs, while the deterministic gate alone blocked 143 and
Luna alone blocked 70. Requiring both systems to identify the same category and
overlapping decisive evidence produced 87 conservative Blocks. No accuracy is
reported because none of these 1,000 decisions has the required human adjudication.

Manual error discovery already makes the current deterministic implementation
unsafe as a final gate. Examples include treating `8 months/40hrs per week` as
more than three years, and blocking `Junior Frontend Engineer` for excessive
experience. Its 415 proposals remain useful for finding candidates, not for
issuing final Blocks.

Two local candidate implementations were run on the RTX 3080 with the same
600/200/200 document split and Luna's exploratory decisions as teacher labels:

| Local candidate | Test recall vs teacher | JDs escalated to verifier |
| --- | ---: | ---: |
| Frozen ModernBERT embeddings + XGBoost | 100% | 97% |
| End-to-end ModernBERT, two epochs | 100% | 97% |
| End-to-end ModernBERT at the dev 95% point | 98.7% | 91.5% |

The end-to-end run processed 1,032,698 source tokens across 1,495 chunks with zero
truncation and took 188.5 seconds for training plus scoring on the RTX 3080. The
local layer currently fails its economic purpose: preserving all teacher Blocks
saves only 3% of strong-model calls. Lower thresholds save more calls only by
discarding candidate conflicts. This result rejects both tested local models for
the current cascade; it does not prove that local NLP is impossible. The labels
are teacher labels rather than human truth, and the fixed development split is
not a production benchmark.

## Decision after the demo

1. **Strict rules only.** Lowest cost and deterministic, but misses semantic
   conflicts. It must be restricted to a small evidence whitelist; the current
   broad deterministic gate cannot be used unchanged.
2. **Luna on every JD.** Best semantic coverage among the tested approaches and
   simplest behavior, but incurs one model call per job. It still requires a fixed
   evidence validator and cannot serve as its own gold label.
3. **Strict candidate rules, then Luna verification.** Recommended. Only explicit
   candidate signals invoke Luna; Luna must confirm the same source fact, and the
   fixed policy executor makes the final decision. Missing signals and every
   disagreement remain not blocked. This accepts lower Block recall to minimize
   false Blocks and model cost, matching the product policy that a missed Block is
   less harmful than hiding a viable job.

Do not add the tested ModernBERT candidate layer to option 3: it raises complexity
without materially reducing calls. Native ATS facts may enter the same verifier
contract when present, but hostname or ATS identity alone never makes a fact true.

Status: development comparison complete; option 3 recommended; production change
and the locked human-adjudicated acceptance run remain unauthorized.
