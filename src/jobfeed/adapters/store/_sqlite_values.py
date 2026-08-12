"""Canonical SQLite value encoding and row hydration for store adapters."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from jobfeed.domain.models import JobPosting, QualityBand

_UTC_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _utc_text(value: datetime) -> str:
    """Encode an aware datetime as canonical six-microsecond UTC text."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("SQLite timestamps require an aware datetime")
    return value.astimezone(UTC).strftime(_UTC_FORMAT)


def _utc_now_text() -> str:
    """Return the application clock in canonical UTC text."""
    return _utc_text(datetime.now(UTC))


def _datetime_from_text(value: object | None) -> datetime | None:
    """Decode nullable canonical timestamp text as an aware datetime."""
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored SQLite timestamp is not timezone-aware")
    return parsed.astimezone(UTC)


def _canonical_json(value: object) -> str:
    """Encode JSON with stable Unicode bytes and reject non-finite numbers."""
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except ValueError as error:
        raise ValueError("canonical JSON rejects non-finite numbers") from error


def _job_from_row(row: aiosqlite.Row) -> JobPosting:
    """Hydrate a job row into the public domain representation."""
    quality = row["jd_quality"]
    discovered_at = _datetime_from_text(row["discovered_at"])
    if discovered_at is None:
        raise ValueError("stored job discovered_at is NULL")
    return JobPosting(
        id=str(row["id"]),
        platform=str(row["platform"]),
        canonical_id=str(row["canonical_id"]),
        url=str(row["url"]),
        title=str(row["title"]),
        company=str(row["company"]),
        location=str(row["location"]),
        discovered_at=discovered_at,
        jd_text=row["jd_text"],
        jd_quality=QualityBand(quality) if quality else None,
        posted_at=_datetime_from_text(row["posted_at"]),
        enriched_at=_datetime_from_text(row["enriched_at"]),
        enrich_source=row["enrich_source"],
        closed_at=_datetime_from_text(row["closed_at"]),
        enrich_error=row["enrich_error"],
    )


def _optional_json(value: object | None) -> Any:
    """Decode nullable SQLite JSON text without hiding malformed storage."""
    return None if value is None else json.loads(str(value))
