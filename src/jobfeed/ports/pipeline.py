"""Durable scan persistence capabilities beyond the core job store."""

from typing import Protocol

from jobfeed.domain.models import JobPosting, SaveJobResult


class PipelineStore(Protocol):
    """Atomic batch receipts fenced by the active scan lease."""

    async def get_state(self, key: str) -> str | None: ...

    async def set_state(self, key: str, value: str) -> None: ...

    async def save_job_batch(
        self,
        jobs: list[JobPosting],
        *,
        receipt_key: str,
        run_id: str,
        owner_id: str,
        generation: int,
    ) -> list[SaveJobResult]: ...
