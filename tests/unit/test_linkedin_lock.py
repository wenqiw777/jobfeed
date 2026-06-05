"""Unit tests for the LinkedIn Playwright enrich lock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jobfeed.adapters.sources._linkedin_lock import EnrichLocked, LinkedInEnrichLock
from jobfeed.domain.errors import SourceBusyError

PID_ONE = 111
PID_TWO = 222
FRESH_OFFSET = timedelta(minutes=5)
STALE_OFFSET = timedelta(hours=3)
LOCKED_AT = datetime(2026, 5, 31, 10, 0, tzinfo=UTC)


def _alive(_pid: int) -> bool:
    return True


def _dead(_pid: int) -> bool:
    return False


def test_enrich_locked_is_domain_source_busy_error() -> None:
    """Contention is catchable via the domain error without importing adapters."""
    assert issubclass(EnrichLocked, SourceBusyError)


def test_linkedin_enrich_lock_acquire_and_release(tmp_path) -> None:
    """A lock acquire writes the PID/timestamp and release removes it."""
    lock_path = tmp_path / "enrich.lock"
    lock = LinkedInEnrichLock(lock_path, pid=PID_ONE, now=lambda: LOCKED_AT)

    lock.acquire()

    assert lock_path.read_text(encoding="utf-8").startswith(f"{PID_ONE} ")
    lock.release()
    assert not lock_path.exists()


def test_linkedin_enrich_lock_rejects_fresh_live_other_pid(tmp_path) -> None:
    """A fresh lock from another live PID raises EnrichLocked."""
    lock_path = tmp_path / "enrich.lock"
    LinkedInEnrichLock(lock_path, pid=PID_ONE, now=lambda: LOCKED_AT).acquire()
    other = LinkedInEnrichLock(
        lock_path,
        pid=PID_TWO,
        now=lambda: LOCKED_AT + FRESH_OFFSET,
        is_pid_alive=_alive,
    )

    with pytest.raises(EnrichLocked, match="already running"):
        other.acquire()


def test_linkedin_enrich_lock_takes_over_stale_pid(tmp_path) -> None:
    """A live holder older than two hours is replaced by the new PID."""
    lock_path = tmp_path / "enrich.lock"
    LinkedInEnrichLock(lock_path, pid=PID_ONE, now=lambda: LOCKED_AT).acquire()
    other = LinkedInEnrichLock(
        lock_path,
        pid=PID_TWO,
        now=lambda: LOCKED_AT + STALE_OFFSET,
        is_pid_alive=_alive,
    )

    other.acquire()

    assert lock_path.read_text(encoding="utf-8").startswith(f"{PID_TWO} ")


def test_linkedin_enrich_lock_takes_over_dead_holder_even_when_fresh(tmp_path) -> None:
    """A crashed (dead-PID) holder is stale regardless of age."""
    lock_path = tmp_path / "enrich.lock"
    LinkedInEnrichLock(lock_path, pid=PID_ONE, now=lambda: LOCKED_AT).acquire()
    other = LinkedInEnrichLock(
        lock_path,
        pid=PID_TWO,
        now=lambda: LOCKED_AT + FRESH_OFFSET,
        is_pid_alive=_dead,
    )

    other.acquire()

    assert lock_path.read_text(encoding="utf-8").startswith(f"{PID_TWO} ")


def test_create_exclusive_loses_race_raises(tmp_path) -> None:
    """Ownership requires winning O_EXCL; an existing file forces EnrichLocked."""
    lock_path = tmp_path / "enrich.lock"
    lock_path.write_text("999 placeholder\n", encoding="utf-8")
    lock = LinkedInEnrichLock(lock_path, pid=PID_ONE, now=lambda: LOCKED_AT)

    with pytest.raises(EnrichLocked, match="taken by another process"):
        lock._create_exclusive()


def test_release_noops_for_non_owner(tmp_path) -> None:
    """release() never deletes a lock this process does not own."""
    lock_path = tmp_path / "enrich.lock"
    LinkedInEnrichLock(lock_path, pid=PID_ONE, now=lambda: LOCKED_AT).acquire()
    non_owner = LinkedInEnrichLock(lock_path, pid=PID_TWO, now=lambda: LOCKED_AT)

    non_owner.release()

    assert lock_path.read_text(encoding="utf-8").startswith(f"{PID_ONE} ")
