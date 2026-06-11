"""Job enrichment port: per-posting JD fetch with block/gone classification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from jobfeed.ports.source import EnrichResult


@dataclass(frozen=True, kw_only=True)
class EnrichOutcome:
    """Classified outcome of one enrich attempt.

    Exactly one signal is set per outcome: ``result`` on success,
    ``is_blocked`` when the source rate-limited the caller's IP (back off
    before the next request), ``is_gone`` on a definitive 404/410 (the
    posting is removed; mark it closed), or ``error`` for everything else
    (skip the row and retry on a future pass).
    """

    result: EnrichResult | None = None
    is_blocked: bool = False
    is_gone: bool = False
    error: str | None = None


@runtime_checkable
class JobEnricher(Protocol):
    """Capability to fetch and classify one posting's full JD."""

    async def enrich(self, *, canonical_id: str, url: str) -> EnrichOutcome:
        """Fetch one posting's JD and classify the outcome.

        Implementations may propagate exceptions raised by their underlying
        fetcher; callers that must never crash should wrap calls accordingly.

        Args:
            canonical_id: Platform-specific posting identity.
            url: Public posting URL (context for enrichers that need it).

        Returns:
            Classified enrichment outcome.
        """
        ...


__all__ = ["EnrichOutcome", "JobEnricher"]
