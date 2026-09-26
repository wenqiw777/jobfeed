"""Operational domain models for companies, costs, and pipeline health."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


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


@dataclass(frozen=True, kw_only=True)
class CompanyIntelligenceMatch:
    """Merged source evidence returned for an exact company match."""

    name: str
    domain: str | None
    sources: tuple[str, ...]
    accelerators: tuple[str, ...]
    accelerator_batches: tuple[str, ...]
    yc_top_company: bool
    is_hiring: bool | None
    team_size: int | None
    claimed_unicorn: bool
    exited: bool | None
    categories: tuple[str, ...]
    company_statuses: tuple[str, ...]
    public_company: bool
    confidence: Literal["medium", "low"]
    watchlist_only: bool


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


@dataclass(frozen=True, kw_only=True)
class UnenrichedJob:
    """Identity snapshot of a job row still awaiting JD enrichment."""

    job_id: str
    canonical_id: str
    url: str


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
    "CompanyIntelligenceMatch",
    "CompanyRecord",
    "CostEntry",
    "DigestStats",
    "UnenrichedJob",
]
