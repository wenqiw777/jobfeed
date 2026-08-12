"""Compose SQLite evaluation claims and fenced pipeline-run leases."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from jobfeed.adapters.store._sqlite_evaluation_claims import (
    _SqliteEvaluationClaims,
)
from jobfeed.adapters.store._sqlite_run_leases import _SqliteRunLeases
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle


class SqliteClaimsRuns(_SqliteEvaluationClaims, _SqliteRunLeases):
    """Provide atomic claim and run-lease capabilities for a SQLite store."""

    def __init__(
        self,
        lifecycle: SqliteLifecycle,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Bind capabilities to an already constructed SQLite lifecycle.

        Args:
            lifecycle: Shared connection lifecycle opened by the composing facade.
        """
        self._lifecycle = lifecycle
        self._clock = clock

    def _claim_time(self, value: datetime | None) -> datetime:
        resolved = value
        if resolved is None:
            if self._clock is None:
                raise ValueError("claim time requires an injected application clock")
            resolved = self._clock()
        if resolved.tzinfo is None or resolved.utcoffset() is None:
            raise ValueError("claim time must be an aware datetime")
        return resolved.astimezone(UTC)


__all__ = ["SqliteClaimsRuns"]
