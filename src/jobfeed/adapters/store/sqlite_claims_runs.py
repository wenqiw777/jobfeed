"""Compose SQLite evaluation claims and fenced pipeline-run leases."""

from __future__ import annotations

from jobfeed.adapters.store._sqlite_evaluation_claims import (
    _SqliteEvaluationClaims,
)
from jobfeed.adapters.store._sqlite_run_leases import _SqliteRunLeases
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle


class SqliteClaimsRuns(_SqliteEvaluationClaims, _SqliteRunLeases):
    """Provide atomic claim and run-lease capabilities for a SQLite store."""

    def __init__(self, lifecycle: SqliteLifecycle) -> None:
        """Bind capabilities to an already constructed SQLite lifecycle.

        Args:
            lifecycle: Shared connection lifecycle opened by the composing facade.
        """
        self._lifecycle = lifecycle


__all__ = ["SqliteClaimsRuns"]
