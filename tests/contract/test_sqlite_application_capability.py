"""Behavior contracts for SQLite application and snapshot aggregates."""

from __future__ import annotations

from pathlib import Path

import pytest

from jobfeed.adapters.store.sqlite_status_applications import (
    SqliteStatusApplications,
)
from jobfeed.domain.errors import SnapshotAmbiguousError, SnapshotNotFoundError
from jobfeed.domain.models import (
    ApplicationRecord,
    ResumeSnapshot,
    TransitionRequest,
)
from tests.support.sqlite_status_application_fixtures import (
    NOW,
    _open_lifecycle,
    _seed_job,
)


class _AtTime(SqliteStatusApplications):
    def _application_time(self, value=None):  # type: ignore[no-untyped-def]
        return NOW if value is None else super()._application_time(value)


def _record(job_id: str, *, master: str | None = None) -> ApplicationRecord:
    return ApplicationRecord(
        job_id=job_id,
        applied_at=NOW,
        master_resume_hash=master,
        verdict_snapshot='{"raw":  1}',
        fit_snapshot=None,
        hooks_snapshot="not-json",
        notes="audit",
    )


def _snapshot(resume_hash: str, *, content: str = "resume") -> ResumeSnapshot:
    return ResumeSnapshot(
        resume_hash=resume_hash,
        captured_at=NOW,
        source="master",
        content=content,
    )


async def test_apply_aggregate_is_atomic_and_duplicate_commits_new_artifacts(
    tmp_path: Path,
) -> None:
    lifecycle = await _open_lifecycle(tmp_path)
    applications = _AtTime(lifecycle)
    job_id = await _seed_job(lifecycle, "apply")
    await applications.transition_status(
        TransitionRequest(job_id=job_id, new_status="ignored", force=True)
    )

    with pytest.raises(ValueError, match="terminal"):
        await applications.record_application_with_snapshots(
            _record(job_id, master="aa"),
            snapshots=[_snapshot("aa")],
            resume_variant="terminal-variant",
        )
    assert await applications.get_resume_snapshot("aa") is None
    assert await applications.get_application(job_id) is None

    second = await _seed_job(lifecycle, "new-apply")
    assert await applications.record_application_with_snapshots(
        _record(second, master="bb"),
        snapshots=[_snapshot("bb", content="first")],
        resume_variant="base",
    )
    history_before = await applications.get_status_history(second)
    assert not await applications.record_application_with_snapshots(
        _record(second, master="cc"),
        snapshots=[_snapshot("cc")],
        resume_variant="duplicate",
    )
    assert (await applications.get_resume_snapshot("cc")).content == "resume"  # type: ignore[union-attr]
    assert await applications.get_status_history(second) == history_before
    persisted = await applications.get_application(second)
    assert persisted is not None
    assert persisted.master_resume_hash == "bb"
    assert persisted.verdict_snapshot == '{"raw":  1}'
    assert persisted.hooks_snapshot == "not-json"
    await lifecycle.close()


async def test_active_apply_does_not_regress_status_and_rolls_back_injected_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = await _open_lifecycle(tmp_path)
    applications = _AtTime(lifecycle)
    job_id = await _seed_job(lifecycle, "active")
    await applications.transition_status(
        TransitionRequest(job_id=job_id, new_status="interviewing", force=True)
    )
    before = await applications.get_status(job_id)
    assert await applications.record_application_with_snapshots(_record(job_id))
    after = await applications.get_status(job_id)
    assert after is not None and before is not None
    assert after.status == "interviewing"
    assert after.last_status_change_at == before.last_status_change_at
    assert (await applications.get_status_history(job_id))[0] == "interviewing"

    failed = await _seed_job(lifecycle, "injected")

    async def _fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("guard failure")

    monkeypatch.setattr(applications, "_before_application_guard", _fail)
    with pytest.raises(RuntimeError, match="guard failure"):
        await applications.record_application_with_snapshots(
            _record(failed, master="dd"),
            snapshots=[_snapshot("dd")],
            resume_variant="failed",
        )
    assert await applications.get_resume_snapshot("dd") is None
    assert await applications.get_application(failed) is None
    await lifecycle.close()


async def test_snapshot_prefix_listing_usage_and_first_write_wins(
    tmp_path: Path,
) -> None:
    lifecycle = await _open_lifecycle(tmp_path)
    applications = _AtTime(lifecycle)
    job_id = await _seed_job(lifecycle, "snapshots")
    assert await applications.record_application_with_snapshots(
        _record(job_id, master="aa11"),
        snapshots=[_snapshot("aa11", content="original")],
    )
    assert not await applications.record_application_with_snapshots(
        _record(job_id),
        snapshots=[_snapshot("aa11", content="replacement"), _snapshot("aa22")],
    )
    assert (await applications.get_resume_snapshot("aa11")).content == "original"  # type: ignore[union-attr]
    resolved = await applications.get_resume_snapshot_by_prefix("aa11")
    assert resolved.resume_hash == "aa11"
    with pytest.raises(SnapshotAmbiguousError):
        await applications.get_resume_snapshot_by_prefix("aa")
    with pytest.raises(SnapshotNotFoundError):
        await applications.get_resume_snapshot_by_prefix("ff")
    summaries = await applications.list_resume_snapshots()
    assert {row.resume_hash: row.usage_count for row in summaries} == {
        "aa11": 1,
        "aa22": 0,
    }
    assert [row.job_id for row in await applications.list_applications(
        resume_hash_prefix="aa1"
    )] == [job_id]
    await lifecycle.close()


async def test_application_stats_use_append_order_and_first_causal_response(
    tmp_path: Path,
) -> None:
    lifecycle = await _open_lifecycle(tmp_path)
    applications = _AtTime(lifecycle)
    first = await _seed_job(lifecycle, "stats-a")
    second = await _seed_job(lifecycle, "stats-b")
    assert await applications.record_application_with_snapshots(
        _record(first), resume_variant="alpha"
    )
    assert await applications.record_application_with_snapshots(_record(second))
    async with lifecycle.connection() as connection:
        await connection.execute(
            "INSERT INTO job_status_history "
            "(job_id,from_status,to_status,changed_at) VALUES (?,?,?,?)",
            (int(first), "applied", "interviewing", "2026-08-14T12:00:00.000000Z"),
        )
        await connection.execute(
            "INSERT INTO job_status_history "
            "(job_id,from_status,to_status,changed_at) VALUES (?,?,?,?)",
            (int(second), "applied", "rejected", "2026-08-11T12:00:00.000000Z"),
        )
    stats = await applications.application_stats(since_days_ago=30, by_resume=True)
    assert (
        stats.applied_count,
        stats.response_count,
        stats.interview_count,
        stats.rejection_count,
        stats.median_days_to_response,
    ) == (2, 2, 1, 1, 1.0)
    assert stats.by_resume is not None
    assert stats.by_resume["alpha"].interviews == 1
    assert stats.by_resume["unknown"].rejections == 1
    await lifecycle.close()
