"""Atomic application + resume-snapshot transaction tests against PostgreSQL.

Covers record_application_with_snapshots: atomicity, idempotency,
terminal-status rollback, content-addressed dedup, and variant auto-register.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import ApplicationRecord, QualityBand, ResumeSnapshot
from tests.support.factories import make_job

pytestmark = pytest.mark.postgres


def _make_job(canonical_id: str = "apply-tx-1") -> object:
    """Shortcut for a minimal job fixture.

    Args:
        canonical_id: Source-specific natural identity.

    Returns:
        Job posting fixture.
    """
    return make_job(canonical_id, jd_text="JD text", jd_quality=QualityBand.GOOD)


def _sha256(text: str) -> str:
    """Return hex SHA-256 of text.

    Args:
        text: Input string.

    Returns:
        Lowercase hex digest.
    """
    return hashlib.sha256(text.encode()).hexdigest()


def _make_snapshot(content: str, *, source: str = "master") -> ResumeSnapshot:
    """Build a ResumeSnapshot from content.

    Args:
        content: Resume text.
        source: Snapshot source label.

    Returns:
        ResumeSnapshot with content-addressed hash.
    """
    return ResumeSnapshot(
        resume_hash=_sha256(content),
        captured_at=datetime.now(UTC),
        source=source,
        content=content,
    )


def _make_record(job_id: str) -> ApplicationRecord:
    """Build a minimal ApplicationRecord.

    Args:
        job_id: Store-assigned job identity.

    Returns:
        ApplicationRecord ready for insertion.
    """
    return ApplicationRecord(
        job_id=job_id,
        applied_at=datetime.now(UTC),
        master_resume_hash=_sha256("master resume v1"),
        notes="test apply",
    )


# -- happy path ----------------------------------------------------------


async def test_apply_with_snapshots_writes_atomically(
    store: PostgresStore,
) -> None:
    """Successful apply writes snapshots + applied row + history atomically."""
    saved = await store.save_job(_make_job("atomic-1"))
    master = _make_snapshot("master resume v1", source="master")
    tailored = _make_snapshot("tailored resume v1", source="tailored")
    record = ApplicationRecord(
        job_id=saved.job_id,
        applied_at=datetime.now(UTC),
        master_resume_hash=master.resume_hash,
        tailored_resume_hash=tailored.resume_hash,
        notes="atomic test",
    )

    is_new = await store.record_application_with_snapshots(
        record, snapshots=[master, tailored]
    )

    assert is_new is True

    # Snapshots persisted
    got_master = await store.get_resume_snapshot(master.resume_hash)
    assert got_master is not None
    assert got_master.content == "master resume v1"

    got_tailored = await store.get_resume_snapshot(tailored.resume_hash)
    assert got_tailored is not None
    assert got_tailored.content == "tailored resume v1"

    # Applied row exists
    apps = await store.list_applications(limit=10)
    assert any(a.job_id == saved.job_id for a in apps)

    # Status transitioned
    pool = store._get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM job_status WHERE job_id = $1",
            int(saved.job_id),
        )
    assert row is not None
    assert row["status"] == "applied"

    # History entry
    async with pool.acquire() as conn:
        hist = await conn.fetchrow(
            """SELECT to_status, reason
               FROM job_status_history
               WHERE job_id = $1 AND to_status = 'applied'""",
            int(saved.job_id),
        )
    assert hist is not None
    assert hist["reason"] == "record_application"


# -- terminal-status rollback -------------------------------------------


async def test_terminal_status_rolls_back_everything(
    store: PostgresStore,
) -> None:
    """Forced failure from terminal-status guard rolls back snapshots too."""
    saved = await store.save_job(_make_job("rollback-1"))

    # Force the job into a terminal status (rejected).
    pool = store._get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE job_status SET status = 'rejected' WHERE job_id = $1",
            int(saved.job_id),
        )

    snap = _make_snapshot("should not persist")
    record = ApplicationRecord(
        job_id=saved.job_id,
        applied_at=datetime.now(UTC),
        master_resume_hash=snap.resume_hash,
        notes="terminal test",
    )

    with pytest.raises(ValueError, match="terminal status"):
        await store.record_application_with_snapshots(record, snapshots=[snap])

    # Snapshot must NOT exist (rolled back).
    got = await store.get_resume_snapshot(snap.resume_hash)
    assert got is None

    # Applied row must NOT exist (rolled back).
    apps = await store.list_applications(limit=100)
    assert not any(a.job_id == saved.job_id for a in apps)


# -- idempotency --------------------------------------------------------


async def test_reapply_returns_false_original_unchanged(
    store: PostgresStore,
) -> None:
    """Re-apply returns False; original data unchanged; exactly one applied row."""
    saved = await store.save_job(_make_job("idempotent-1"))
    snap1 = _make_snapshot("master resume v1")
    record1 = ApplicationRecord(
        job_id=saved.job_id,
        applied_at=datetime.now(UTC),
        master_resume_hash=snap1.resume_hash,
        notes="first apply",
    )

    first = await store.record_application_with_snapshots(record1, snapshots=[snap1])
    assert first is True

    # Second attempt with different notes.
    snap2 = _make_snapshot("master resume v2")
    record2 = ApplicationRecord(
        job_id=saved.job_id,
        applied_at=datetime.now(UTC),
        master_resume_hash=snap2.resume_hash,
        notes="second apply",
    )

    second = await store.record_application_with_snapshots(record2, snapshots=[snap2])
    assert second is False

    # Original notes preserved.
    apps = await store.list_applications(limit=100)
    matching = [a for a in apps if a.job_id == saved.job_id]
    assert len(matching) == 1
    assert matching[0].notes == "first apply"

    # Second snapshot persisted (content-addressed, not rolled back
    # by the idempotency early-return).
    got = await store.get_resume_snapshot(snap2.resume_hash)
    assert got is not None


# -- content-addressed dedup --------------------------------------------


async def test_same_resume_across_two_jobs_one_snapshot(
    store: PostgresStore,
) -> None:
    """Same resume content across two jobs yields one resume_snapshots row."""
    saved1 = await store.save_job(_make_job("dedup-1"))
    saved2 = await store.save_job(_make_job("dedup-2"))

    shared_content = "identical master resume"
    snap = _make_snapshot(shared_content)

    record1 = ApplicationRecord(
        job_id=saved1.job_id,
        applied_at=datetime.now(UTC),
        master_resume_hash=snap.resume_hash,
    )
    record2 = ApplicationRecord(
        job_id=saved2.job_id,
        applied_at=datetime.now(UTC),
        master_resume_hash=snap.resume_hash,
    )

    await store.record_application_with_snapshots(record1, snapshots=[snap])
    await store.record_application_with_snapshots(record2, snapshots=[snap])

    # Only one row in resume_snapshots for this hash.
    pool = store._get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM resume_snapshots WHERE resume_hash = $1",
            snap.resume_hash,
        )
    assert count == 1


# -- variant auto-register ---------------------------------------------


async def test_resume_variant_auto_registered(
    store: PostgresStore,
) -> None:
    """Setting resume_variant for unregistered variant auto-registers it."""
    saved = await store.save_job(_make_job("variant-1"))
    record = _make_record(saved.job_id)
    variant_name = "senior-swe-v2"

    is_new = await store.record_application_with_snapshots(
        record, resume_variant=variant_name
    )
    assert is_new is True

    # Variant was auto-registered.
    pool = store._get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT name FROM resume_variants WHERE name = $1",
            variant_name,
        )
    assert row is not None
    assert row["name"] == variant_name

    # Status has the variant set.
    async with pool.acquire() as conn:
        status = await conn.fetchrow(
            "SELECT resume_variant FROM job_status WHERE job_id = $1",
            int(saved.job_id),
        )
    assert status is not None
    assert status["resume_variant"] == variant_name

    # History records the variant.
    async with pool.acquire() as conn:
        hist = await conn.fetchrow(
            """SELECT resume_variant_at_change
               FROM job_status_history
               WHERE job_id = $1 AND to_status = 'applied'""",
            int(saved.job_id),
        )
    assert hist is not None
    assert hist["resume_variant_at_change"] == variant_name


async def test_no_snapshots_still_works(
    store: PostgresStore,
) -> None:
    """Calling with snapshots=None works like a plain record_application."""
    saved = await store.save_job(_make_job("no-snap-1"))
    record = _make_record(saved.job_id)

    is_new = await store.record_application_with_snapshots(record)
    assert is_new is True

    apps = await store.list_applications(limit=10)
    assert any(a.job_id == saved.job_id for a in apps)
