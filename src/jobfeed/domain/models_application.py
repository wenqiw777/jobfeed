"""Application audit trail domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(kw_only=True)
class ApplicationRecord:
    """Frozen audit snapshot of a job application."""

    job_id: str
    applied_at: datetime
    master_resume_hash: str | None = None
    tailored_resume_hash: str | None = None
    cover_letter: str | None = None
    application_method: str | None = None
    verdict_snapshot: str | None = None
    fit_snapshot: str | None = None
    hooks_snapshot: str | None = None
    notes: str | None = None


@dataclass(kw_only=True)
class ResumeSnapshot:
    """Content-addressed resume stored by sha256 hash."""

    resume_hash: str
    captured_at: datetime
    source: str
    content: str
    notes: str | None = None

    def __post_init__(self) -> None:
        if not self.resume_hash or not all(
            c in "0123456789abcdef" for c in self.resume_hash.lower()
        ):
            raise ValueError("resume_hash must be a non-empty hex string")


@dataclass(frozen=True, kw_only=True)
class ResumeSnapshotSummary:
    """Snapshot listing row: identity, provenance, usage count — no content."""

    resume_hash: str
    captured_at: datetime
    source: str
    usage_count: int


@dataclass(kw_only=True)
class ResumeVariant:
    """Named resume variant for A/B tracking."""

    name: str
    description: str | None = None
    created_at: datetime


@dataclass(kw_only=True)
class ResumeVariantStats:
    """Per-variant application outcome breakdown."""

    sent: int
    responses: int
    interviews: int
    offers: int
    rejections: int


@dataclass(kw_only=True)
class ApplicationStats:
    """Aggregate application statistics over a time window."""

    applied_count: int
    response_count: int
    interview_count: int
    offer_count: int
    rejection_count: int
    median_days_to_response: float | None = None
    by_resume: dict[str, ResumeVariantStats] | None = None


__all__ = [
    "ApplicationRecord",
    "ApplicationStats",
    "ResumeSnapshot",
    "ResumeSnapshotSummary",
    "ResumeVariant",
    "ResumeVariantStats",
]
