"""Digest service that renders Markdown from evaluated jobs."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from jobfeed.domain.digest import render_attention_footer, render_digest
from jobfeed.domain.models import JobEvaluation
from jobfeed.observability import JobfeedLogger
from jobfeed.ports.store import JobStore
from jobfeed.ports.store_ops import StoreOpsMixin
from jobfeed.ports.store_status import StoreStatusMixin

LAST_RENDERED_KEY = "digest.last_rendered_at"


@runtime_checkable
class DigestStore(JobStore, StoreOpsMixin, StoreStatusMixin, Protocol):
    """Combined store capability required by DigestService."""


class DigestService:
    """Application service for rendering the current job digest."""

    def __init__(self, store: DigestStore, logger: JobfeedLogger) -> None:
        """Create a digest service with injected ports.

        Args:
            store: Persistence port used to load evaluated jobs, KV state,
                and attention reports.
            logger: Structured logger for digest events.
        """
        self.store = store
        self.logger = logger

    async def run(
        self,
        cutoff_at: datetime | None = None,
        *,
        top: int | None = None,
        output_dir: Path | None = None,
    ) -> str:
        """Render a Markdown digest from stored evaluations.

        Args:
            cutoff_at: Optional boundary for new vs previously seen apply jobs.
                When omitted, the stored ``digest.last_rendered_at`` KV value
                is used; after a successful render the key is set to now.
            top: Optional per-group row cap forwarded to domain rendering.
            output_dir: Optional directory; when set, ``today.md`` and a
                UTC-dated ``YYYY-MM-DD.md`` are written (overwritten).

        Returns:
            Markdown digest.
        """
        effective_cutoff = cutoff_at if cutoff_at is not None else await self._cutoff()
        evaluations = await self.store.list_evaluated_jobs()
        stats = _stats_for(evaluations)
        digest = render_digest(evaluations, stats, cutoff_at=effective_cutoff, top=top)
        digest = await self._append_footer(digest)
        if output_dir is not None:
            _write_digest_files(output_dir, digest)
        await self.store.set_state(LAST_RENDERED_KEY, datetime.now(UTC).isoformat())
        self.logger.info(
            "digest_rendered",
            total_jobs=stats["total_jobs"],
            stage_b_evaluated=stats["stage_b_evaluated"],
        )
        return digest

    async def _cutoff(self) -> datetime | None:
        """Read the stored KV cutoff; malformed or missing values mean None."""
        raw = await self.store.get_state(LAST_RENDERED_KEY)
        if raw is None:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            self.logger.warning("digest_cutoff_state_invalid", value=raw)
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            self.logger.warning("digest_cutoff_state_naive", value=raw)
            return None
        return parsed

    async def _append_footer(self, digest: str) -> str:
        """Append the attention footer when any bucket has items."""
        attention = await self.store.workflow_attention()
        report = await self.store.needs_attention()
        footer = render_attention_footer(attention, report)
        if not footer:
            return digest
        return digest + "\n" + footer


def _write_digest_files(output_dir: Path, digest: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "today.md").write_text(digest, encoding="utf-8")
    dated_name = f"{datetime.now(UTC).date().isoformat()}.md"
    (output_dir / dated_name).write_text(digest, encoding="utf-8")


def _stats_for(evaluations: list[JobEvaluation]) -> dict[str, object]:
    stage_a_count = sum(1 for item in evaluations if item.stage_a is not None)
    stage_b_count = sum(1 for item in evaluations if item.stage_b is not None)
    # TODO(phase1): Replace placeholder scan/filter counters with store-backed
    # run statistics once historical pipeline run queries exist.
    return {
        "total_jobs": len(evaluations),
        "scraped_today": len(evaluations),
        "llm_calls_today": stage_a_count + stage_b_count,
        "stage_b_evaluated": stage_b_count,
        "filtered_count": 0,
    }


__all__ = ["LAST_RENDERED_KEY", "DigestService", "DigestStore"]
