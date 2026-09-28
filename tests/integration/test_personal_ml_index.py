"""Existing databases acquire the metadata-only personal-learning index."""
from pathlib import Path

from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.adapters.store.sqlite_schema import ensure_sqlite_schema


async def test_existing_database_gets_personal_ml_index_on_open(tmp_path: Path):
    lifecycle = SqliteLifecycle(tmp_path / "existing.sqlite", ensure_sqlite_schema)
    await lifecycle.open()
    async with lifecycle.connection() as c:
        await c.execute("INSERT INTO state(key,value) VALUES('retained','yes')")
        await c.execute("DROP INDEX idx_eval_personal_ml")
        await c.execute("DROP INDEX idx_jobs_personal_ml")
    await lifecycle.close()
    await lifecycle.open()
    try:
        async with lifecycle.connection() as c:
            assert (await (await c.execute(
                "SELECT value FROM state WHERE key='retained'"
            )).fetchone())[0] == "yes"
            plan = await (await c.execute(
                "EXPLAIN QUERY PLAN SELECT stage_a_at,job_id,stage_a_score "
                "FROM evaluations WHERE stage_a_status='completed' "
                "ORDER BY stage_a_at,job_id"
            )).fetchall()
            assert any("COVERING INDEX idx_eval_personal_ml" in row[3] for row in plan)
            plan = await (await c.execute(
                "EXPLAIN QUERY PLAN SELECT e.stage_a_score,j.ml_gate_score,"
                "j.ml_gate_fail_reason,j.role_type FROM evaluations e "
                "JOIN jobs j INDEXED BY idx_jobs_personal_ml ON j.id=e.job_id "
                "WHERE e.stage_a_status='completed' ORDER BY e.stage_a_at,e.job_id"
            )).fetchall()
            assert any("COVERING INDEX idx_jobs_personal_ml" in row[3] for row in plan)
    finally:
        await lifecycle.close()
