"""Run retry lookup includes current canonical scoring failures."""

from datetime import UTC, datetime, timedelta

import aiosqlite
import pytest

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.models import JobPosting, QualityBand


@pytest.mark.parametrize("stage", ["a", "b"])
@pytest.mark.parametrize(
    "case",
    [
        ("error", 1, True, True),
        ("error", 3, True, False),
        ("completed", 1, True, False),
        ("error", 1, False, False),
    ],
)
async def test_canonical_run_retry_is_current_bounded_and_owned(tmp_path, stage, case):
    status, count, in_window, expected = case
    path = tmp_path / "retry.sqlite"
    store = SQLiteStore(path)
    await store.connect()
    try:
        now = datetime.now(UTC)
        saved = await store.save_job(
            JobPosting(
                platform="test",
                canonical_id="retry",
                url="https://example.test/job",
                title="Engineer",
                company="Example",
                location="Remote",
                discovered_at=now,
                jd_text="Build software services. " * 30,
                jd_quality=QualityBand.FULL,
            )
        )
        real_id = await store.resolve_real_job_id(saved.job_id)
        await store.claim_real_job_stage_a_by_ids([real_id])
        async with aiosqlite.connect(path) as db:
            await db.execute(
                """INSERT INTO pipeline_runs
                (run_id,started_at,finished_at,source,status) VALUES (?,?,?,?,?)""",
                (
                    "target",
                    (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                    (now + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                    "evaluate",
                    "succeeded",
                ),
            )
            updated = now if in_window else now + timedelta(hours=1)
            await db.execute(
                f"""UPDATE real_job_evaluations SET stage_a_status='completed',
                stage_{stage}_status=?,stage_{stage}_error_count=?,updated_at=?
                WHERE real_job_id=?""",
                (status, count, updated.isoformat().replace("+00:00", "Z"), real_id),
            )
            await db.commit()
        assert await store.list_retryable_run_error_job_ids("target") == (
            [saved.job_id] if expected else []
        )
    finally:
        await store.close()
