"""Operational store port: company, enrichment, cost, state, health."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from jobfeed.domain.models import (
    AttentionReport,
    CompanyRecord,
    CostEntry,
    DigestStats,
    UnenrichedJob,
)
from jobfeed.domain.models_llm import LLMUsage


@runtime_checkable
class StoreOpsMixin(Protocol):
    """Company, enrichment, cost, state, and pipeline health."""

    async def upsert_company(self, company: CompanyRecord) -> None:
        """Insert or update a company record.

        Args:
            company: Company record.
        """
        ...

    async def get_company(self, slug: str) -> CompanyRecord | None:
        """Load a company by slug.

        Args:
            slug: Company slug.

        Returns:
            Company record if found, else None.
        """
        ...

    async def list_companies(
        self,
        *,
        vendor: str | None = None,
        include_removed: bool = False,
    ) -> list[CompanyRecord]:
        """List companies with optional filters.

        Args:
            vendor: Filter by ATS vendor.
            include_removed: Include soft-deleted.

        Returns:
            Matching company records.
        """
        ...

    async def mark_company_removed(self, slug: str) -> bool:
        """Soft-delete via ats_vendor='removed'.

        Args:
            slug: Company slug.

        Returns:
            True if a tracked, not-already-removed company was matched.
        """
        ...

    async def bump_discover_failure(self, slug: str) -> int:
        """Increment consecutive discover-failure counter.

        Args:
            slug: Company slug.

        Returns:
            New failure count.
        """
        ...

    async def reset_discover_failures(self, slug: str) -> None:
        """Zero the discover-failure counter.

        Args:
            slug: Company slug.
        """
        ...

    async def record_enrichment(
        self,
        *,
        job_id: str,
        jd_text: str,
        jd_quality: str,
        enriched_at: datetime,
        enrich_source: str,
        jd_lang: str | None = None,
        posted_at: datetime | None = None,
    ) -> None:
        """Stamp a job as enriched with JD body and quality.

        Args:
            job_id: Store-assigned job identity.
            jd_text: JD body text.
            jd_quality: Quality band string.
            enriched_at: Enrichment timestamp.
            enrich_source: Source label.
            jd_lang: Optional detected language.
            posted_at: Optional JD-derived posting date. Fills the column
                only when it is NULL — an exact card-derived date already
                stored is never overwritten by this approximate value.
        """
        ...

    async def list_unenriched_jobs(
        self,
        *,
        platform: str,
        limit: int,
        job_ids: list[str] | None = None,
    ) -> list[UnenrichedJob]:
        """List open jobs on a platform that still have no JD text.

        Args:
            platform: Source platform to scope the listing to.
            limit: Maximum rows to return.
            job_ids: Optional store identities restricting the queue.

        Returns:
            Rows with jd_text IS NULL and closed_at IS NULL, newest
            discovered_at first (id breaks ties). Empty when none match.
        """
        ...

    async def mark_job_closed(
        self,
        *,
        job_id: str,
        closed_at: datetime,
        reason: str | None = None,
    ) -> None:
        """Stamp a single job as closed (posting confirmed gone).

        Args:
            job_id: Store-assigned job identity.
            closed_at: Closure timestamp to set.
            reason: Optional marker recording WHY the row was closed, stamped
                into enrich_error for ops triage (house convention, e.g.
                'gone:{status}:{vendor}', 'backfill:stale-no-jd'). None
                leaves enrich_error untouched.
        """
        ...

    async def enrich_paste(
        self,
        *,
        platform: str,
        canonical_id: str,
        jd_text: str,
    ) -> str:
        """Manual JD paste fallback.

        Args:
            platform: Source platform.
            canonical_id: Platform-specific identity.
            jd_text: Pasted JD text.

        Returns:
            Store-assigned job identity.
        """
        ...

    async def get_state(self, key: str) -> str | None:
        """Read a key-value state entry.

        Args:
            key: State key.

        Returns:
            Value if found, else None.
        """
        ...

    async def set_state(self, key: str, value: str) -> None:
        """Write a key-value state entry.

        Args:
            key: State key.
            value: State value.
        """
        ...

    async def record_cost(self, *, day: str, spent_usd: float, calls: int = 1) -> None:
        """Upsert daily cost ledger spend and attempted call count.

        Args:
            day: YYYY-MM-DD date string.
            spent_usd: Cost to accumulate.
            calls: Attempted LLM calls to accumulate.
        """
        ...

    async def get_cost(self, day: str) -> CostEntry | None:
        """Read a single day's cost entry.

        Args:
            day: YYYY-MM-DD date.

        Returns:
            Cost entry if found, else None.
        """
        ...

    async def record_llm_usage(self, usage: LLMUsage) -> None:
        """Record a single LLM call's usage metrics.

        Args:
            usage: LLM usage metrics for one call.
        """
        ...

    async def record_llm_usage_with_cost(
        self,
        *,
        day: str,
        spent_usd: float,
        usage: LLMUsage,
    ) -> None:
        """Atomically record LLM usage and same-call ledger spend.

        Args:
            day: YYYY-MM-DD cost ledger day.
            spent_usd: Cost to accumulate.
            usage: LLM usage metrics for one call.
        """
        ...

    async def get_cost_range(self, *, since_days: int = 30) -> list[CostEntry]:
        """Read cost entries within a date range.

        Args:
            since_days: Days to look back.

        Returns:
            Cost entries ordered by day descending.
        """
        ...

    async def digest_stats(self, *, threshold: int = 60) -> DigestStats:
        """Aggregate counts for digest footer.

        Args:
            threshold: Score threshold for filtered_count.

        Returns:
            Digest statistics.
        """
        ...

    async def needs_attention(
        self,
        *,
        days: int = 7,
        max_per_category: int = 10,
    ) -> AttentionReport:
        """Surface pipeline health concerns.

        Args:
            days: Look-back window.
            max_per_category: Max items per category.

        Returns:
            Attention report.
        """
        ...

    async def mark_stale_jobs_closed(
        self,
        *,
        older_than_days: int,
        dry_run: bool,
    ) -> int:
        """Close stale jobs that have no usable JD and have not been closed yet.

        Targets rows where:
        - jd_quality IS NULL or jd_quality IN ('missing', 'abandoned')
        - discovered_at < now() - make_interval(days => older_than_days)
        - closed_at IS NULL

        Args:
            older_than_days: Discovery-age threshold (exclusive).
            dry_run: When True, count matching rows without writing.

        Returns:
            Row count: matched rows (dry_run=True) or updated rows (dry_run=False).
        """
        ...
