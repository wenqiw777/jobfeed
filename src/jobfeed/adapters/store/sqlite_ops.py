"""Public SQLite operational capability composed over one shared lifecycle."""

from __future__ import annotations

from datetime import datetime

from jobfeed.adapters.store import (
    _sqlite_ops_company,
    _sqlite_ops_cost,
    _sqlite_ops_enrichment,
    _sqlite_ops_health,
    _sqlite_ops_resume,
    _sqlite_ops_timing,
)
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.domain.models import (
    AttentionReport,
    CompanyRecord,
    CostEntry,
    LLMUsage,
    ResumeSnapshot,
    ResumeSnapshotSummary,
    UnenrichedJob,
)
from jobfeed.domain.models_perf import StepTiming
from jobfeed.ports.source import StoredEnrichment


class SqliteOps:
    """Persist retained operational behavior through short SQLite connections."""

    def __init__(self, lifecycle: SqliteLifecycle) -> None:
        """Bind operations to an already constructed shared lifecycle."""
        self._lifecycle = lifecycle

    async def upsert_company(self, company: CompanyRecord) -> None:
        """Insert or update one exact-slug company.

        Args: company record to persist.
        """
        await _sqlite_ops_company._upsert_company(self._lifecycle, company)

    async def get_company(self, slug: str) -> CompanyRecord | None:
        """Load one exact-slug company.

        Args: exact company slug.
        Returns: matching record or None.
        """
        return await _sqlite_ops_company._get_company(self._lifecycle, slug)

    async def list_companies(
        self, *, vendor: str | None = None, include_removed: bool = False
    ) -> list[CompanyRecord]:
        """List companies in slug order with optional exact filters.

        Args: optional vendor and removed-row inclusion flag.
        Returns: matching company records ordered by slug.
        """
        return await _sqlite_ops_company._list_companies(
            self._lifecycle, vendor=vendor, include_removed=include_removed
        )

    async def mark_company_removed(self, slug: str) -> bool:
        """Soft-delete a tracked non-removed company.

        Args: exact company slug.
        Returns: whether a row changed.
        """
        return await _sqlite_ops_company._mark_company_removed(self._lifecycle, slug)

    async def bump_discover_failure(self, slug: str) -> int:
        """Atomically increment a company's discover failure count.

        Args: exact company slug.
        Returns: new count, or zero for an unknown slug.
        """
        return await _sqlite_ops_company._bump_discover_failure(self._lifecycle, slug)

    async def reset_discover_failures(self, slug: str) -> None:
        """Reset a company's discover failures without creating a row.

        Args: exact company slug.
        """
        await _sqlite_ops_company._reset_discover_failures(self._lifecycle, slug)

    async def record_enrichment(  # noqa: PLR0913
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
        """Replace enrichment and invalidate stale liveness/gate state.

        Args: job identity, JD fields, enrichment time/source, language and date.
        """
        await _sqlite_ops_enrichment._record_enrichment(
            self._lifecycle,
            job_id=job_id,
            jd_text=jd_text,
            jd_quality=jd_quality,
            enriched_at=enriched_at,
            enrich_source=enrich_source,
            jd_lang=jd_lang,
            posted_at=posted_at,
        )

    async def list_unenriched_jobs(
        self,
        *,
        platform: str,
        limit: int,
        job_ids: list[str] | None = None,
    ) -> list[UnenrichedJob]:
        """List open jobs lacking JD text in stable recency order.

        Args: exact platform and maximum row count.
        Returns: unenriched identity rows.
        """
        return await _sqlite_ops_enrichment._list_unenriched_jobs(
            self._lifecycle,
            platform=platform,
            limit=limit,
            job_ids=job_ids,
        )

    async def mark_job_closed(
        self, *, job_id: str, closed_at: datetime, reason: str | None = None
    ) -> None:
        """Stamp closure while preserving an existing reason when omitted.

        Args: job identity, aware closure time, and optional reason.
        """
        await _sqlite_ops_enrichment._mark_job_closed(
            self._lifecycle, job_id=job_id, closed_at=closed_at, reason=reason
        )

    async def enrich_paste(
        self, *, platform: str, canonical_id: str, jd_text: str
    ) -> str:
        """Apply manual paste enrichment to one exact natural key.

        Args: exact platform, canonical identity, and pasted JD.
        Returns: store-assigned job identity.
        """
        return await _sqlite_ops_enrichment._enrich_paste(
            self._lifecycle,
            platform=platform,
            canonical_id=canonical_id,
            jd_text=jd_text,
        )

    async def get_enrichment(
        self, *, platform: str, canonical_id: str
    ) -> StoredEnrichment | None:
        """Load the source-facing enrichment snapshot for an exact key.

        Args: exact platform and canonical identity.
        Returns: stored enrichment or None.
        """
        return await _sqlite_ops_enrichment._get_enrichment(
            self._lifecycle, platform=platform, canonical_id=canonical_id
        )

    async def get_closed_canonical_ids(self, *, platform: str) -> set[str]:
        """Return definitive closed identities excluding stale backfill guesses.

        Args: exact source platform.
        Returns: unordered canonical identity set.
        """
        return await _sqlite_ops_enrichment._get_closed_canonical_ids(
            self._lifecycle, platform=platform
        )

    async def get_resume_snapshot(self, resume_hash: str) -> ResumeSnapshot | None:
        """Load one snapshot by exact case-sensitive hash.

        Args: exact resume hash.
        Returns: full snapshot or None.
        """
        return await _sqlite_ops_resume._get_resume_snapshot(
            self._lifecycle, resume_hash
        )

    async def get_resume_snapshot_by_prefix(self, prefix: str) -> ResumeSnapshot:
        """Resolve one literal case-sensitive resume hash prefix.

        Args: literal resume hash prefix.
        Returns: the unique matching snapshot.
        Raises: SnapshotNotFoundError or SnapshotAmbiguousError by match count.
        """
        return await _sqlite_ops_resume._get_resume_snapshot_by_prefix(
            self._lifecycle, prefix
        )

    async def list_resume_snapshots(
        self, source: str | None = None
    ) -> list[ResumeSnapshotSummary]:
        """List snapshot summaries with distinct applied-row usage counts.

        Args: optional exact stored source.
        Returns: summaries ordered by capture time descending and hash ascending.
        """
        return await _sqlite_ops_resume._list_resume_snapshots(self._lifecycle, source)

    async def register_resume_variant(
        self, *, name: str, description: str | None = None
    ) -> bool:
        """Register a first-write-wins named resume variant.

        Args: exact variant name and optional description.
        Returns: true only when inserted.
        """
        return await _sqlite_ops_resume._register_resume_variant(
            self._lifecycle, name=name, description=description
        )

    async def get_state(self, key: str) -> str | None:
        """Read an exact operational state key.

        Args: exact state key.
        Returns: stored value or None.
        """
        return await _sqlite_ops_cost._get_state(self._lifecycle, key)

    async def set_state(self, key: str, value: str) -> None:
        """Upsert an exact operational state key.

        Args: exact key and replacement value.
        """
        await _sqlite_ops_cost._set_state(self._lifecycle, key, value)

    async def record_cost(self, *, day: str, spent_usd: float, calls: int = 1) -> None:
        """Atomically add daily spend and attempted-call count.

        Args: exact day key, spend delta, and call delta.
        """
        await _sqlite_ops_cost._record_cost_public(
            self._lifecycle, day=day, spent_usd=spent_usd, calls=calls
        )

    async def get_cost(self, day: str) -> CostEntry | None:
        """Load one exact daily cost ledger row.

        Args: exact day key.
        Returns: cost entry or None.
        """
        return await _sqlite_ops_cost._get_cost(self._lifecycle, day)

    async def record_llm_usage_with_cost(
        self, *, day: str, spent_usd: float, usage: LLMUsage
    ) -> None:
        """Atomically append paid-call usage and its ledger spend.

        Args: exact ledger day, spend delta, and usage record.
        """
        await _sqlite_ops_cost._record_llm_usage_with_cost(
            self._lifecycle, day=day, spent_usd=spent_usd, usage=usage
        )

    async def needs_attention(
        self, *, days: int = 7, max_per_category: int = 10
    ) -> AttentionReport:
        """Build independently capped pipeline-health categories.

        Args: recent lookback days and independent category cap.
        Returns: unordered attention categories.
        """
        return await _sqlite_ops_health._needs_attention(
            self._lifecycle, days=days, max_per_category=max_per_category
        )

    async def mark_stale_jobs_closed(
        self, *, older_than_days: int, dry_run: bool
    ) -> int:
        """Count or close open unusable-JD rows older than a strict cutoff.

        Args: minimum age in days and dry-run selector.
        Returns: matched or updated row count.
        """
        return await _sqlite_ops_health._mark_stale_jobs_closed(
            self._lifecycle, older_than_days=older_than_days, dry_run=dry_run
        )

    async def record_step_timing(self, timing: StepTiming) -> None:
        """Append one timing row using the database-generated creation time.

        Args: step timing to persist.
        """
        await _sqlite_ops_timing._record_step_timing(self._lifecycle, timing)


__all__ = ["SqliteOps"]
