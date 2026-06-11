"""Resume snapshot prefix lookup and global listing tests against PostgreSQL.

Covers get_resume_snapshot_by_prefix (unique / unknown / ambiguous / LIKE
wildcard escaping), list_resume_snapshots (usage counts, source filter,
orphans), and the list_applications resume_hash_prefix filter.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.errors import SnapshotAmbiguousError, SnapshotNotFoundError
from jobfeed.domain.models import ApplicationRecord, QualityBand, ResumeSnapshot
from tests.support.factories import make_job

pytestmark = pytest.mark.postgres

# Named count so assertions avoid PLR2004 magic-value warnings.
_MASTER_USAGE_COUNT = 2


def _sha256(text: str) -> str:
    """Return hex SHA-256 of text.

    Args:
        text: Input string.

    Returns:
        Lowercase hex digest.
    """
    return hashlib.sha256(text.encode()).hexdigest()


def _make_snapshot(
    content: str,
    *,
    source: str = "master",
    resume_hash: str | None = None,
) -> ResumeSnapshot:
    """Build a ResumeSnapshot from content.

    Args:
        content: Resume text.
        source: Snapshot source label.
        resume_hash: Explicit hash override (defaults to sha256 of content).

    Returns:
        ResumeSnapshot with content-addressed hash.
    """
    return ResumeSnapshot(
        resume_hash=resume_hash or _sha256(content),
        captured_at=datetime.now(UTC),
        source=source,
        content=content,
    )


async def _apply_with_snapshots(
    store: PostgresStore,
    canonical_id: str,
    *,
    master: ResumeSnapshot | None = None,
    tailored: ResumeSnapshot | None = None,
) -> str:
    """Save a job and record an application referencing snapshot hashes.

    Args:
        store: Connected store.
        canonical_id: Source-specific natural identity.
        master: Optional master resume snapshot.
        tailored: Optional tailored resume snapshot.

    Returns:
        Store-assigned job id.
    """
    job = make_job(canonical_id, jd_text="JD text", jd_quality=QualityBand.GOOD)
    saved = await store.save_job(job)
    record = ApplicationRecord(
        job_id=saved.job_id,
        applied_at=datetime.now(UTC),
        master_resume_hash=master.resume_hash if master else None,
        tailored_resume_hash=tailored.resume_hash if tailored else None,
    )
    snapshots = [s for s in (master, tailored) if s is not None]
    await store.record_application_with_snapshots(record, snapshots=snapshots or None)
    return saved.job_id


# -- get_resume_snapshot_by_prefix ----------------------------------------


async def test_unique_prefix_resolves(store: PostgresStore) -> None:
    """A prefix matching exactly one snapshot returns the full snapshot."""
    snap = _make_snapshot("master resume v1")
    await store.save_resume_snapshot(snap)

    got = await store.get_resume_snapshot_by_prefix(snap.resume_hash[:12])

    assert got.resume_hash == snap.resume_hash
    assert got.content == "master resume v1"
    assert got.source == "master"


async def test_unknown_prefix_raises_not_found(store: PostgresStore) -> None:
    """A prefix matching nothing raises SnapshotNotFoundError."""
    await store.save_resume_snapshot(_make_snapshot("some resume"))

    with pytest.raises(SnapshotNotFoundError):
        await store.get_resume_snapshot_by_prefix("ffffffffffff0000")


async def test_ambiguous_prefix_raises_ambiguous(store: PostgresStore) -> None:
    """A prefix matching two snapshots raises SnapshotAmbiguousError."""
    snap_a = _make_snapshot("resume a", resume_hash="aaaa1111" + "0" * 56)
    snap_b = _make_snapshot("resume b", resume_hash="aaaa2222" + "0" * 56)
    await store.save_resume_snapshot(snap_a)
    await store.save_resume_snapshot(snap_b)

    with pytest.raises(SnapshotAmbiguousError):
        await store.get_resume_snapshot_by_prefix("aaaa")

    # A longer, unique prefix still resolves.
    got = await store.get_resume_snapshot_by_prefix("aaaa1111")
    assert got.resume_hash == snap_a.resume_hash


async def test_like_wildcards_in_prefix_are_literal(store: PostgresStore) -> None:
    """LIKE metacharacters in the prefix must not act as wildcards."""
    snap = _make_snapshot("wildcard target", resume_hash="abcd" + "0" * 60)
    await store.save_resume_snapshot(snap)

    # '%' would match everything if unescaped.
    with pytest.raises(SnapshotNotFoundError):
        await store.get_resume_snapshot_by_prefix("ab%")

    # '_' would match any single character if unescaped.
    with pytest.raises(SnapshotNotFoundError):
        await store.get_resume_snapshot_by_prefix("a_cd")

    # A literal backslash is escaped, not treated as the LIKE escape char;
    # hex hashes never contain '\\', so no-match is the correct outcome.
    with pytest.raises(SnapshotNotFoundError):
        await store.get_resume_snapshot_by_prefix("ab\\")


# -- list_resume_snapshots -------------------------------------------------


async def test_list_snapshots_usage_counts_and_orphans(
    store: PostgresStore,
) -> None:
    """Usage counts come from applied references; orphans appear with 0."""
    master = _make_snapshot("shared master", source="master")
    tailored = _make_snapshot("tailored for job1", source="tailored")
    orphan = _make_snapshot("never used", source="master")

    await _apply_with_snapshots(store, "snap-list-1", master=master, tailored=tailored)
    await _apply_with_snapshots(store, "snap-list-2", master=master)
    await store.save_resume_snapshot(orphan)

    summaries = {s.resume_hash: s for s in await store.list_resume_snapshots()}

    assert summaries[master.resume_hash].usage_count == _MASTER_USAGE_COUNT
    assert summaries[master.resume_hash].source == "master"
    assert summaries[tailored.resume_hash].usage_count == 1
    assert summaries[tailored.resume_hash].source == "tailored"
    assert summaries[orphan.resume_hash].usage_count == 0


async def test_same_hash_as_master_and_tailored_counts_usage_once(
    store: PostgresStore,
) -> None:
    """One applied row carrying the same hash in both columns counts once.

    Pins the OR join in list_resume_snapshots: a single applied row joins a
    snapshot once even when master_resume_hash AND tailored_resume_hash both
    equal it, so usage_count stays 1 (not 2).
    """
    snap = _make_snapshot("same content in both roles")
    job = make_job("snap-both-1", jd_text="JD text", jd_quality=QualityBand.GOOD)
    saved = await store.save_job(job)
    record = ApplicationRecord(
        job_id=saved.job_id,
        applied_at=datetime.now(UTC),
        master_resume_hash=snap.resume_hash,
        tailored_resume_hash=snap.resume_hash,
    )
    await store.record_application_with_snapshots(record, snapshots=[snap])

    summaries = {s.resume_hash: s for s in await store.list_resume_snapshots()}
    assert summaries[snap.resume_hash].usage_count == 1


async def test_list_snapshots_source_filter(store: PostgresStore) -> None:
    """source= filters on the stored source column."""
    master = _make_snapshot("master only", source="master")
    tailored = _make_snapshot("tailored only", source="tailored")
    await store.save_resume_snapshot(master)
    await store.save_resume_snapshot(tailored)

    only_tailored = await store.list_resume_snapshots(source="tailored")

    assert [s.resume_hash for s in only_tailored] == [tailored.resume_hash]


# -- list_applications resume_hash_prefix ----------------------------------


async def test_list_applications_filters_on_master_or_tailored(
    store: PostgresStore,
) -> None:
    """The prefix filter matches either the master or the tailored hash."""
    master = _make_snapshot("master for job1", source="master")
    tailored = _make_snapshot("tailored for job2", source="tailored")
    job1 = await _apply_with_snapshots(store, "app-filter-1", master=master)
    job2 = await _apply_with_snapshots(store, "app-filter-2", tailored=tailored)

    everything = await store.list_applications(limit=10)
    assert {a.job_id for a in everything} == {job1, job2}

    by_master = await store.list_applications(
        limit=10, resume_hash_prefix=master.resume_hash[:10]
    )
    assert [a.job_id for a in by_master] == [job1]

    by_tailored = await store.list_applications(
        limit=10, resume_hash_prefix=tailored.resume_hash[:10]
    )
    assert [a.job_id for a in by_tailored] == [job2]


async def test_list_applications_prefix_wildcard_is_literal(
    store: PostgresStore,
) -> None:
    """A '%' prefix must not wildcard-match every application."""
    master = _make_snapshot("master for wildcard test", source="master")
    await _apply_with_snapshots(store, "app-wildcard-1", master=master)

    matches = await store.list_applications(limit=10, resume_hash_prefix="%")

    assert matches == []
