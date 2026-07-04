"""Pipeline run and dry-run preview domain dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(kw_only=True)
class DryRunPreviewItem:
    """One job that an evaluation dry-run would process."""

    stage: str
    job_id: str | None
    title: str
    company: str


@dataclass(kw_only=True)
class PipelineRun:
    """Aggregate counters and timing for a scan or evaluation run."""

    run_id: str
    started_at: datetime
    source: str
    status: str = "running"
    jobs_discovered: int = 0
    jobs_inserted: int = 0
    jobs_updated: int = 0
    jobs_filtered: int = 0
    jobs_ml_gated: int = 0
    jobs_gate_passed: int = 0
    stage_a_scored: int = 0
    stage_b_scored: int = 0
    jobs_scored: int = 0
    total_llm_cost_usd: float = 0.0
    errors: int = 0
    finished_at: datetime | None = None
    dry_run_preview: list[DryRunPreviewItem] = field(default_factory=list)
