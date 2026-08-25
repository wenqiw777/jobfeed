"""Pure snapshot-building helpers for the application service."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from jobfeed.domain.models import ResumeSnapshot


def content_hash(text: str) -> str:
    """SHA-256 hex digest of text content.

    Args:
        text: Content to hash.

    Returns:
        Hex digest string.
    """
    return hashlib.sha256(text.encode()).hexdigest()


def build_snapshots(
    *,
    master_resume: str,
    tailored_resume: str | None,
    now: datetime,
    master_hash: str,
    tailored_hash: str | None,
) -> list[ResumeSnapshot]:
    """Build the resume snapshot list for an application.

    Args:
        master_resume: Master resume content.
        tailored_resume: Optional tailored resume content.
        now: Capture timestamp.
        master_hash: Content hash of the master resume.
        tailored_hash: Content hash of the tailored resume, when present.

    Returns:
        Master snapshot, plus the tailored snapshot when provided.
    """
    snapshots = [
        ResumeSnapshot(
            resume_hash=master_hash,
            captured_at=now,
            source="master",
            content=master_resume,
        ),
    ]
    if tailored_resume is not None and tailored_hash is not None:
        snapshots.append(
            ResumeSnapshot(
                resume_hash=tailored_hash,
                captured_at=now,
                source="tailored",
                content=tailored_resume,
            ),
        )
    return snapshots


def unified_evaluation_dumps(
    result: dict[str, object],
) -> tuple[str | None, str | None, str | None]:
    """Serialize one canonical evaluation into the legacy snapshot columns.

    Args:
        result: Canonical unified evaluation payload.

    Returns:
        Full canonical JSON in the primary snapshot slot; deprecated slots empty.
    """
    return (json.dumps(result, sort_keys=True), None, None)


__all__ = ["build_snapshots", "content_hash", "unified_evaluation_dumps"]
