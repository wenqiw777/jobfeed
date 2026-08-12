"""Compose SQLite status, application, and interview capabilities."""

from __future__ import annotations

from datetime import UTC, datetime

from jobfeed.adapters.store._sqlite_application import _SqliteApplication
from jobfeed.adapters.store._sqlite_interviews import _SqliteInterviews
from jobfeed.adapters.store._sqlite_status import _SqliteStatus
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle


class SqliteStatusApplications(_SqliteStatus, _SqliteApplication, _SqliteInterviews):
    """Expose status/application/interview aggregates over one lifecycle."""

    def __init__(self, lifecycle: SqliteLifecycle) -> None:
        """Bind the capability to a lifecycle owned by the final store facade."""
        self._lifecycle = lifecycle

    def _application_time(self, value: datetime | None = None) -> datetime:
        candidate = datetime.now(UTC) if value is None else value
        if candidate.tzinfo is None or candidate.utcoffset() is None:
            raise ValueError("SQLite application time must be aware")
        return candidate.astimezone(UTC)


__all__ = ["SqliteStatusApplications"]
