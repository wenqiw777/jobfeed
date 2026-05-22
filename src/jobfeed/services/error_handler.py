"""Service-layer error handler for recoverable pipeline failures."""

from __future__ import annotations

from dataclasses import dataclass

from jobfeed.domain.errors import ScoringParseError
from jobfeed.domain.models import PipelineRun
from jobfeed.domain.types import StageName
from jobfeed.observability import JobfeedLogger
from jobfeed.ports.store import JobStore


@dataclass(frozen=True, kw_only=True)
class ServiceErrorHandler:
    """Centralized service handler for recoverable errors and run counters."""

    store: JobStore
    logger: JobfeedLogger

    async def handle_stage_parse_error(
        self,
        run: PipelineRun,
        stage: StageName,
        job_id: str,
        error: ScoringParseError,
    ) -> None:
        """Persist a stage parse failure and increment the run error count.

        Args:
            run: Pipeline run whose error counter should be incremented.
            stage: Evaluation stage that failed parsing.
            job_id: Store-assigned job identity.
            error: Parse error raised by domain scoring.
        """
        await self._handle_stage_error(
            run=run,
            stage=stage,
            job_id=job_id,
            error=_format_error(error),
            event=self._stage_event(stage, "parse_failed"),
        )

    async def handle_stage_runtime_error(
        self,
        run: PipelineRun,
        stage: StageName,
        job_id: str,
        error: Exception,
    ) -> None:
        """Persist a stage runtime failure and increment the run error count.

        Args:
            run: Pipeline run whose error counter should be incremented.
            stage: Evaluation stage whose LLM request failed.
            job_id: Store-assigned job identity.
            error: Runtime exception raised while scoring the job.
        """
        await self._handle_stage_error(
            run=run,
            stage=stage,
            job_id=job_id,
            error=_format_error(error),
            event=self._stage_event(stage, "runtime_failed"),
        )

    async def _handle_stage_error(
        self,
        run: PipelineRun,
        stage: StageName,
        job_id: str,
        error: str,
        event: str,
    ) -> None:
        await self._save_stage_error(stage, job_id, error)
        run.errors += 1
        self.logger.error(event, job_id=job_id, error=error)

    def handle_source_fetch_error(
        self,
        run: PipelineRun,
        source_name: str,
        error: Exception,
    ) -> None:
        """Record a recoverable source fetch failure.

        Args:
            run: Pipeline run whose error counter should be incremented.
            source_name: Source label used for logs and run diagnostics.
            error: Exception raised by the source adapter.
        """
        run.errors += 1
        self.logger.error("scan_source_failed", source=source_name, error=str(error))

    async def _save_stage_error(
        self,
        stage: StageName,
        job_id: str,
        error: str,
    ) -> None:
        if stage == "stage_a":
            await self.store.save_stage_a_error(job_id, error)
            return
        await self.store.save_stage_b_error(job_id, error)

    def _stage_event(self, stage: StageName, suffix: str) -> str:
        return f"{stage}_{suffix}"


def _format_error(error: Exception) -> str:
    return str(error) or error.__class__.__name__


__all__ = ["ServiceErrorHandler"]
