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


__all__ = ["JobfeedError", "ScoringParseError"]
