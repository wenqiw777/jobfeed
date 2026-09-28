"""Durable scan persistence capabilities beyond the core job store."""

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from jobfeed.domain.models import JobPosting, SaveJobResult


class PipelineStore(Protocol):
    """Atomic batch receipts fenced by the active scan lease."""

    async def get_state(self, key: str) -> str | None:
        """Read a persisted pipeline state value.

        Args:
            key: Stable state or request key.

        Returns:
            Saved value, or None if absent.
        """
        ...

    async def set_state(self, key: str, value: str) -> None:
        """Persist a pipeline state value.

        Args:
            key: Stable state or request key.
            value: Source value to normalize.
        """
        ...

    async def save_job_batch(
        self,
        jobs: list[JobPosting],
        *,
        receipt_key: str,
        run_id: str,
        owner_id: str,
        generation: int,
    ) -> list[SaveJobResult]:
        """Save a job batch under its active scan lease.

        Args:
            jobs: Postings to group or persist.
            receipt_key: Idempotent receipt identity for the batch.
            run_id: Identifier of the current scan run.
            owner_id: Owner of the active scan lease.
            generation: Fencing generation of the acquired lease.

        Returns:
            One persistence outcome per input posting.
        """
        ...


class PipelineStep(Protocol):
    """Run-scoped journal operation consumed by source work."""

    async def step(
        self,
        name: str,
        payload: Any,
        work: Callable[[Any], Awaitable[Any]],
        *,
        retry_errors: bool = False,
    ) -> Any:
        """Run or reuse one journaled operation.

        Args:
            name: Stable operation name within the run.
            payload: JSON-compatible input persisted before execution.
            work: Async operation invoked with the saved input.
            retry_errors: Whether failed results may be executed again.

        Returns:
            The persisted or newly computed operation output.
        """
        ...

    async def load_partial(self, name: str) -> list[dict[str, Any]]:
        """Read saved browser batches.

        Args:
            name: Stable scan operation name.

        Returns:
            Rows already accepted for this operation.
        """
        ...

    async def save_partial(self, name: str, rows: list[dict[str, Any]]) -> None:
        """Persist a browser batch before acknowledgement.

        Args:
            name: Stable scan operation name.
            rows: Source rows received in the batch.
        """
        ...
