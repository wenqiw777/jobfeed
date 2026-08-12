"""SQLite retained resume snapshot and variant operational contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jobfeed.domain.errors import SnapshotAmbiguousError, SnapshotNotFoundError
from tests.support.sqlite_jobs_evaluations import make_job
from tests.support.sqlite_ops import open_sqlite_ops

_NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
_CANONICAL_TIMESTAMP_LENGTH = 27


async def _insert_snapshot(  # noqa: PLR0913
    lifecycle,
    resume_hash: str,
    *,
    captured_at: datetime = _NOW,
    source: str = "master",
    content: str = "raw\ncontent",
    notes: str | None = None,
) -> None:
    async with lifecycle.connection() as connection:
        await connection.execute(
            "INSERT INTO resume_snapshots "
            "(resume_hash,captured_at,source,content,notes) VALUES (?,?,?,?,?)",
            (
                resume_hash,
                captured_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                source,
                content,
                notes,
            ),
        )


async def test_resume_exact_and_literal_unique_prefix_lookup(tmp_path: Path) -> None:
    """Snapshot reads preserve raw fields and literal case-sensitive prefixes."""
    lifecycle, ops, _jobs = await open_sqlite_ops(tmp_path / "snapshot.db")
    try:
        await _insert_snapshot(
            lifecycle,
            "aa11cc22",
            content="字节\n不重排",
            notes="note",
        )
        snapshot = await ops.get_resume_snapshot("aa11cc22")
        assert snapshot is not None
        assert snapshot.content == "字节\n不重排"
        assert snapshot.notes == "note"
        assert snapshot.captured_at == _NOW
        assert await ops.get_resume_snapshot("AA11cc22") is None
        assert (await ops.get_resume_snapshot_by_prefix("aa11")).resume_hash == (
            "aa11cc22"
        )
        for prefix in ("missing", "aa%", "a_", "aa\\"):
            with pytest.raises(SnapshotNotFoundError):
                await ops.get_resume_snapshot_by_prefix(prefix)

        await _insert_snapshot(lifecycle, "aa11dd33")
        with pytest.raises(SnapshotAmbiguousError):
            await ops.get_resume_snapshot_by_prefix("aa11")
    finally:
        await lifecycle.close()


async def test_resume_listing_usage_source_and_stable_order(tmp_path: Path) -> None:
    """Snapshot summaries include orphans and count each applied row once."""
    lifecycle, ops, jobs = await open_sqlite_ops(tmp_path / "snapshot-list.db")
    try:
        older = "aa11"
        newer = "bb22"
        same_time = "cc33"
        await _insert_snapshot(lifecycle, older, captured_at=_NOW - timedelta(days=1))
        await _insert_snapshot(lifecycle, newer, source="tailored")
        await _insert_snapshot(lifecycle, same_time, source="tailored")
        saved = await jobs.save_job(_job("applied"))
        async with lifecycle.connection() as connection:
            await connection.execute(
                "INSERT INTO applied "
                "(job_id,applied_at,master_resume_hash,tailored_resume_hash) "
                "VALUES (?,?,?,?)",
                (
                    int(saved.job_id),
                    _NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    newer,
                    newer,
                ),
            )
        summaries = await ops.list_resume_snapshots()
        assert [(row.resume_hash, row.usage_count) for row in summaries] == [
            (newer, 1),
            (same_time, 0),
            (older, 0),
        ]
        assert [
            row.resume_hash
            for row in await ops.list_resume_snapshots(source="tailored")
        ] == [newer, same_time]
    finally:
        await lifecycle.close()


async def test_register_resume_variant_is_first_write_wins(tmp_path: Path) -> None:
    """Variant registration is idempotent and duplicate descriptions do not win."""
    lifecycle, ops, _jobs = await open_sqlite_ops(tmp_path / "variant.db")
    try:
        assert await ops.register_resume_variant(name="backend", description="first")
        assert not await ops.register_resume_variant(
            name="backend", description="second"
        )
        async with lifecycle.connection() as connection:
            cursor = await connection.execute(
                "SELECT description,created_at FROM resume_variants WHERE name=?",
                ("backend",),
            )
            row = await cursor.fetchone()
            await cursor.close()
        assert row is not None and row[0] == "first"
        assert len(row[1]) == _CANONICAL_TIMESTAMP_LENGTH and row[1].endswith("Z")
    finally:
        await lifecycle.close()


def _job(canonical_id: str):
    return make_job(canonical_id)
