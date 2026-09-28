"""Legacy run breakdown is shown only when current rows still match that run."""

from pathlib import Path

from jobfeed.adapters.store.sqlite import SQLiteStore

_START = "2026-09-24T01:37:21.000000Z"
_FINISH = "2026-09-24T01:53:44.000000Z"
_REVIEWED = "2026-09-24T01:45:00.000000Z"
_LATER = "2026-09-24T02:00:00.000000Z"


async def test_legacy_breakdown_requires_complete_unchanged_results(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "jobfeed.sqlite")
    await store.connect()
    try:
        async with store._lifecycle.connection() as connection:
            await connection.execute(
                """INSERT INTO pipeline_runs
                   (run_id, started_at, finished_at, source, status, stage_b_scored)
                   VALUES (?,?,?,?,?,?)""",
                ("old-eval", _START, _FINISH, "evaluate", "succeeded", 3),
            )
            await connection.executemany(
                """INSERT INTO jobs
                   (id, platform, canonical_id, url, title, company, location,
                    discovered_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                [
                    (i, "test", str(i), f"https://example.test/{i}",
                     "Engineer", "Example", "Remote", _START)
                    for i in range(1, 4)
                ],
            )
            await connection.executemany(
                """INSERT INTO evaluations
                   (job_id, stage_b_verdict, stage_b_status, stage_b_at, updated_at)
                   VALUES (?,?,'completed',?,?)""",
                [(1, "apply", _REVIEWED, _REVIEWED),
                 (2, "consider", _REVIEWED, _REVIEWED),
                 (3, "skip", _REVIEWED, _REVIEWED)],
            )

        assert await store.get_historical_run_verdict_counts("old-eval") == {
            "apply": 1, "consider": 1, "skip": 1,
        }

        async with store._lifecycle.connection() as connection:
            await connection.execute(
                "UPDATE evaluations SET updated_at=? WHERE job_id=1", (_LATER,)
            )
        assert await store.get_historical_run_verdict_counts("old-eval") is None
    finally:
        await store.close()
