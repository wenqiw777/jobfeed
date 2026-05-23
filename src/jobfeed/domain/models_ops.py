"""Operational domain models for companies, costs, and pipeline health."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(kw_only=True)
class CompanyRecord:
    """ATS company source tracking record."""

    slug: str
    ats_vendor: str | None = None
    ats_override: bool = False
    last_verified_at: datetime | None = None
    last_probe_attempt_at: datetime | None = None
    job_count_last_scan: int = 0
    consecutive_discover_failures: int = 0
    notes: str | None = None


@dataclass(kw_only=True)
class CostEntry:
    """Daily LLM cost ledger row."""

    day: str
    spent_usd: float
    calls: int
    last_updated: datetime


@dataclass(kw_only=True)
class DigestStats:
    """Aggregate counts for digest footer rendering."""

    total_jobs: int
    scored_today: int
    stage_b_evaluated: int
    filtered_count: int
    llm_calls_today: int
    total_cost_today_usd: float


@dataclass(kw_only=True)
class AttentionItem:
    """Single pipeline health concern."""

    job_id: str
    title: str
    company: str
    category: str
    detail: str


@dataclass(kw_only=True)
class AttentionReport:
    """Pipeline health attention report."""

    enrich_errors: list[AttentionItem] = field(default_factory=list)
    low_quality_scored: list[AttentionItem] = field(default_factory=list)
    # Jobs stuck in 'error' past the Stage A/B retry cap: no longer retried, so
    # they need manual triage (plan Task 1).
    stuck_scoring: list[AttentionItem] = field(default_factory=list)


__all__ = [
    "AttentionItem",
    "AttentionReport",
    "CompanyRecord",
    "CostEntry",
    "DigestStats",
]
