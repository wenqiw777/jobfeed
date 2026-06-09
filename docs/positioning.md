# Positioning & messaging

Archived 2026-06-09. Working notes for how jobfeed describes itself. Not a spec.

## Who it's for

Primary user: **me.** A local-first job search tool I actually use, built as a
restructure of a messy legacy repo. Open-source and clean enough to double as a
portfolio / resume project, but "someone else adopts it" is secondary.

Implication: comparisons to hosted products (Jobright et al.) are not the
scoreboard. Those are SaaS copilots for a mass audience; jobfeed is a programmable,
local, single-user pipeline. Different quadrant, not a worse copy.

## Anti-positioning (what it is NOT)

- Not a job board — doesn't compete on job count.
- Not an auto-apply / autofill bot.
- Not a resume builder.
- Not a hosted "AI job search copilot."

Scope discipline is deliberate. Each of those would dilute the "clean local
pipeline" identity and pull toward a worse version of an existing product.

## README first-screen — current draft

```
Local-first job search filtering for technical candidates.

Jobfeed scans configured job sources, deduplicates postings, applies rule-based
and ML prefilters, evaluates fit with your resume using configurable LLM backends,
and produces an explainable daily digest — without sending your whole job search
workflow to a hosted SaaS product.
```

## Tightened variant (parked for consideration)

```
Local-first job search engine for technical candidates.

Jobfeed pulls from the job sources you configure, dedupes, and prefilters with
rules + a local ML gate. Then it scores each posting against your résumé with the
LLM backend you choose, and hands you one explainable daily digest. Your résumé
and your search never leave your machine.
```

## Open editing notes

- "filtering" undersells the eval + ML gate, which is the hard part. Considered
  "engine" / "radar" for the headline.
- Second sentence of the current draft is a 5-verb run-on; breaking it gives the
  evaluation step (the main course) its own beat.
- "your whole job search workflow" is vague; the concrete privacy claim is that
  your résumé and search preferences never leave your machine.

## Real differentiators (for self-use)

- Local: résumé, preferences, application history stay on your machine.
- Transparent scoring: you can see why a posting passes or is blocked, and tune it.
- Programmable: sources, hard filters, and scoring profile are yours to configure.
- Free, no subscription, runs offline.
