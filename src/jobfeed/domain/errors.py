"""Domain exception types shared across Jobfeed layers."""

from __future__ import annotations


class JobfeedError(Exception):
    """Base exception for expected Jobfeed application failures."""


class ScoringParseError(JobfeedError):
    """Raised when an LLM response cannot be normalized into domain results."""

    def __init__(self, message: str, *, raw_response: str | None = None) -> None:
        """Create a scoring parse error with optional raw response context.

        Args:
            message: Human-readable parse failure.
            raw_response: Raw LLM response that failed parsing, when available.
        """
        super().__init__(message)
        self.raw_response = raw_response


class SourceBusyError(JobfeedError):
    """Raised when a source's exclusive session is already held elsewhere.

    Lives in the domain layer so services can catch source contention without
    importing any adapter (e.g. the LinkedIn cross-process enrich lock). It
    signals a benign skip — the source is busy, not failing — so the scan
    service must not record it as a fetch error.
    """


__all__ = ["JobfeedError", "ScoringParseError", "SourceBusyError"]
