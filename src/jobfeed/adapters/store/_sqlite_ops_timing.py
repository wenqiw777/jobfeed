"""SQLite retained single-step timing persistence."""

from __future__ import annotations

from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.domain.models_perf import StepTiming


async def _record_step_timing(
    lifecycle: SqliteLifecycle,
    timing: StepTiming,
) -> None:
    async with lifecycle.connection() as connection:
        await connection.execute(
            "INSERT INTO step_timings "
            "(run_id,step_type,step_name,elapsed_ms,is_error) VALUES (?,?,?,?,?)",
            (
                timing.run_id,
                timing.step_type,
                timing.step_name,
                timing.elapsed_ms,
                int(timing.is_error),
            ),
        )
