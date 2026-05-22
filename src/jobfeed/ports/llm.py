"""LLM completion port protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from jobfeed.domain.models import LLMRequest, LLMResponse


@runtime_checkable
class LLMClient(Protocol):
    """Adapter-neutral completion capability used by evaluation services."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Complete one LLM request.

        Args:
            request: Adapter-neutral request with messages and model settings.

        Returns:
            Adapter-neutral completion response with usage metadata.
        """
        ...
