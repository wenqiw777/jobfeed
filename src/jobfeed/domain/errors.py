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


class SnapshotNotFoundError(LookupError):
    """Raised when no resume snapshot matches a hash prefix."""


class SnapshotAmbiguousError(LookupError):
    """Raised when a resume hash prefix matches two or more snapshots."""


class SourceBusyError(JobfeedError):
    """Raised when a source's exclusive session is already held elsewhere.

    Lives in the domain layer so services can catch source contention without
    importing any adapter (e.g. the LinkedIn cross-process enrich lock). It
    signals a benign skip — the source is busy, not failing — so the scan
    service must not record it as a fetch error.
    """


class SourceConfigError(JobfeedError):
    """Raised when a requested scan source is disabled or misconfigured.

    Lives in the domain layer so the web layer can reject a trigger request
    without importing CLI/config machinery; the composition layer translates
    configuration failures (e.g. a disabled source) into this type.
    """


class ResumeNotConfiguredError(JobfeedError):
    """Raised when the master resume file is missing on a scoring run.

    A dedicated type (not FileNotFoundError) so the web trigger route can
    map exactly this user misconfiguration to a 400 without mislabeling
    other missing files (ML model, price table) that are server faults.
    """


class RunConflictError(Exception):
    """A pipeline run of the requested type is already active."""


__all__ = [
    "JobfeedError",
    "ResumeNotConfiguredError",
    "RunConflictError",
    "ScoringParseError",
    "SnapshotAmbiguousError",
    "SnapshotNotFoundError",
    "SourceBusyError",
    "SourceConfigError",
]
