<!--
  EXAMPLE MASTER RÉSUMÉ — this is a template, not a real person.

  jobfeed's `evaluate` scores every job by how well it fits THIS file
  (résumé ↔ job-description match). Out of the box config.example.toml points
  `master_resume_path` here so you can run end-to-end immediately.

  To use your own résumé (your real one is personal data — never commit it):
      cp resume.example.md resume.md         # resume.md is gitignored
      $EDITOR resume.md                       # replace everything below
  then set, in config.toml:  master_resume_path = "resume.md"

  Format notes (how the scorer reads this):
   - Plain Markdown, no fixed schema. Lead with concrete PROJECT / WORK bullets —
     they are the strongest signal; skills-line keywords and coursework count less.
   - State your graduation date / availability so the timing check works.
   - You may keep internal-only sections (e.g. a compensation floor); they inform
     scoring but are never quoted in any output.
-->

# Jordan A. Lee — Software Engineer

Email: jordan.lee@example.com · GitHub: github.com/example-jlee · Location: Austin, TX (open to relocation / remote)

**Availability:** Graduating **December 2026** (BS Computer Science); seeking new-grad / entry-level / internship software engineering roles starting late 2026 or 2027.

## Summary

Early-career backend & distributed-systems engineer with ~9 months of internship
experience shipping production Python and Go services on Postgres + AWS. Comfortable
owning a feature end-to-end: API design, async data pipelines, tests, and CI.

## Skills

- **Languages:** Python (primary), Go, TypeScript, SQL
- **Backend / data:** FastAPI, asyncio, PostgreSQL, Redis, Kafka, REST + gRPC
- **Infra / cloud:** Docker, Kubernetes, AWS (ECS, RDS, S3, Lambda), Terraform, GitHub Actions
- **ML (applied):** PyTorch basics, embeddings / vector search, LLM API integration
- **Practices:** TDD, code review, observability (structured logs, metrics)

## Experience

### Backend Engineering Intern — Northwind Logistics (Summer 2026, 4 mo)
- Built an async ingestion service (Python/FastAPI) that normalized 2M+ shipment
  events/day from 6 carrier APIs into Postgres; cut ingest latency p95 from 9s to 1.2s.
- Designed an idempotent retry + dead-letter pipeline over Kafka, eliminating a
  recurring class of duplicate-order bugs.
- Added contract tests + a GitHub Actions lane; raised service coverage to 88%.

### Software Engineering Intern — CivicData Lab (Summer 2025, 5 mo)
- Shipped a Go microservice exposing a gRPC API for geospatial lookups used by 3
  downstream teams; sustained 4k req/s at p99 < 40ms.
- Containerized the service (Docker) and wrote the Kubernetes deployment + HPA;
  owned the on-call runbook for it.

## Projects

### jobfeed-style résumé matcher (personal, 2026)
- Local-first pipeline that scrapes job boards, dedupes, and scores postings against
  a résumé using a small XGBoost gate + an LLM stage. Python, Postgres, asyncio.

### Distributed key-value store (course capstone, 2025)
- Implemented Raft consensus + a write-ahead log in Go; verified linearizability
  under partition with a Jepsen-style fault-injection harness.

## Education

**B.S. Computer Science**, University of Texas at Austin — *expected Dec 2026*
GPA 3.7 / 4.0. Coursework: Distributed Systems, Operating Systems, Databases, ML.

<!--
## Internal — never echoed in any output (optional)
Compensation floor: 120k base. Why I left CivicData: wanted larger-scale systems.
-->
