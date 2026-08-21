"""SQLite reads for personal relevance-learning observations."""

from __future__ import annotations

import aiosqlite

from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.personal_ml_learning import PersonalMLObservation


async def _list_personal_ml_observations(
    lifecycle: SqliteLifecycle,
    *,
    quick_pass_threshold: int,
) -> list[PersonalMLObservation]:
    """Return successful Quick labels in their original completion order."""
    async with lifecycle.connection() as connection:
        connection.row_factory = aiosqlite.Row
        cursor = await connection.execute(
            """SELECT e.stage_a_score, j.ml_gate_score,
                      j.ml_gate_fail_reason, j.role_type
               FROM evaluations e
               JOIN jobs j ON j.id=e.job_id
               WHERE e.stage_a_status='completed'
               ORDER BY e.stage_a_at ASC, e.job_id ASC"""
        )
        rows = await cursor.fetchall()
        await cursor.close()
    return [
        PersonalMLObservation(
            quick_pass=int(row["stage_a_score"]) >= quick_pass_threshold,
            model_score=(
                float(row["ml_gate_score"])
                if row["ml_gate_score"] is not None
                else None
            ),
            baseline_pass=row["ml_gate_fail_reason"] is None,
            family=str(row["role_type"] or "other"),
        )
        for row in rows
    ]


__all__ = ["_list_personal_ml_observations"]
