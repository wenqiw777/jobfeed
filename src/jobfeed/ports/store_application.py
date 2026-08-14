"""Application-audit store port: applied rows, resume snapshots, variants.

Split out of ``store_ext.py`` to keep both modules under the 300-line gate.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from jobfeed.domain.models import (
    ApplicationRecord,
    ApplicationStats,
    ResumeSnapshot,
    ResumeSnapshotSummary,
)


@runtime_checkable
class StoreApplicationMixin(Protocol):
    """Application audit trail and resume snapshot methods."""

    async def record_application(self, record: ApplicationRecord) -> bool:
        """Record application with atomic status transition.

        Args:
            record: Application audit record.

        Returns:
            True if new, False if already applied.
        """
        ...

    async def record_application_with_snapshots(
        self,
        record: ApplicationRecord,
        *,
        snapshots: list[ResumeSnapshot] | None = None,
        resume_variant: str | None = None,
    ) -> bool:
        """Record application with resume snapshots in one atomic transaction.

        Upserts snapshots, inserts the applied row, checks idempotency,
        guards terminal status, transitions to applied, and optionally
        auto-registers the resume variant -- all inside a single transaction.

        Args:
            record: Application audit record.
            snapshots: Optional resume snapshots to persist atomically.
            resume_variant: Optional variant name to set on the job status;
                auto-registered if not already known.

        Returns:
            True if new, False if already applied.

        Raises:
            ValueError: If the job is in a terminal status.
        """
        ...

    async def get_application(self, job_id: str) -> ApplicationRecord | None:
        """Load a single application record by job_id.

        Args:
            job_id: Store-assigned job identity.

        Returns:
            Application record if found, else None.
        """
        ...

    async def list_applications(
        self,
        *,
        limit: int = 100,
        resume_hash_prefix: str | None = None,
    ) -> list[ApplicationRecord]:
        """List application records by recency.

        Args:
            limit: Max records.
            resume_hash_prefix: Optional literal hash prefix; keeps only
                records whose master OR tailored resume hash starts with it.

        Returns:
            Application records.
        """
        ...

    async def application_stats(
        self,
        *,
        since_days_ago: int | None = 30,
        by_resume: bool = False,
    ) -> ApplicationStats:
        """Aggregate application statistics.

        Args:
            since_days_ago: Time window, or None for all time.
            by_resume: Include per-variant breakdown.

        Returns:
            Application statistics.
        """
        ...

    async def save_resume_snapshot(self, snapshot: ResumeSnapshot) -> None:
        """Content-addressed resume insert (no-op if exists).

        Args:
            snapshot: Resume snapshot to persist.
        """
        ...

    async def get_resume_snapshot(
        self,
        resume_hash: str,
    ) -> ResumeSnapshot | None:
        """Load resume snapshot by hash.

        Args:
            resume_hash: Content-addressed hash.

        Returns:
            Snapshot if found, else None.
        """
        ...

    async def get_resume_snapshot_by_prefix(self, prefix: str) -> ResumeSnapshot:
        """Resolve a resume snapshot by a unique hash prefix.

        The prefix is treated as a literal string: SQL LIKE wildcards
        (``%``, ``_``) in it must not wildcard-match.

        Args:
            prefix: Hash prefix to resolve.

        Returns:
            The single matching snapshot.

        Raises:
            SnapshotNotFoundError: If no snapshot matches the prefix.
            SnapshotAmbiguousError: If two or more snapshots match.
        """
        ...

    async def list_resume_snapshots(
        self,
        source: str | None = None,
    ) -> list[ResumeSnapshotSummary]:
        """List every resume snapshot with its applied-row usage count.

        Usage count is the number of ``applied`` rows referencing the hash
        as master OR tailored resume; orphans appear with usage 0.

        Args:
            source: Optional filter on the stored source column.

        Returns:
            Snapshot summaries (without content), newest first.
        """
        ...

    async def register_resume_variant(
        self,
        *,
        name: str,
        description: str | None = None,
    ) -> bool:
        """Register a named resume variant.

        Args:
            name: Variant name.
            description: Optional description.

        Returns:
            True if new, False if existed.
        """
        ...
