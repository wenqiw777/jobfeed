"""Cross-process enrich lock for LinkedIn Playwright sessions."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jobfeed.domain.errors import SourceBusyError

STALE_AFTER = timedelta(hours=2)
_LOCK_PERMISSIONS = 0o600
_LOCK_PARTS = 2


class EnrichLocked(SourceBusyError):
    """Raised when another live LinkedIn enrich session is already running.

    Subclasses the domain ``SourceBusyError`` so the scan service can treat
    contention as a benign skip without importing this adapter module.
    """


@dataclass(frozen=True, kw_only=True)
class _LockRecord:
    pid: int
    created_at: datetime


def _pid_alive(pid: int) -> bool:
    """Return whether a process id is currently running.

    Args:
        pid: Process id read from a lock record.

    Returns:
        True if the process exists (including when owned by another user),
        False when no such process is running.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class LinkedInEnrichLock:
    """PID-file lock that serializes LinkedIn browser sessions across processes.

    Ownership is granted ONLY by winning an ``O_EXCL`` create, so two racing
    processes can never both believe they hold the lock. A holder counts as
    blocking only when it is a *different, live* process whose record is younger
    than ``stale_after``; a dead holder (crash) or an over-age record is treated
    as stale and force-replaced.
    """

    def __init__(
        self,
        path: Path,
        *,
        pid: int | None = None,
        now: Callable[[], datetime] | None = None,
        stale_after: timedelta = STALE_AFTER,
        is_pid_alive: Callable[[int], bool] = _pid_alive,
    ) -> None:
        """Create a lock bound to ``path``.

        Args:
            path: Lock file path.
            pid: Process id to write; tests inject this.
            now: Clock function; tests inject this.
            stale_after: Age after which even a live holder is forced out.
            is_pid_alive: Liveness probe for the holder pid; tests inject this.
        """
        self.path = path
        self.pid = pid if pid is not None else os.getpid()
        self._now = now or (lambda: datetime.now(UTC))
        self._stale_after = stale_after
        self._is_pid_alive = is_pid_alive
        self._held = False

    def acquire(self) -> None:
        """Acquire the lock or raise ``EnrichLocked`` if a live holder owns it.

        A stale/dead holder's file is removed first, then recreated exclusively.
        Because ownership is only ever taken by a winning ``O_EXCL`` create, a
        competitor that wins the recreate makes this call raise instead of
        silently co-owning the lock.

        Raises:
            EnrichLocked: If a live, fresh holder owns the lock, or a competitor
                won the exclusive create during a stale takeover.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = self._read_record()
        if record is not None:
            if self._blocks_current_process(record):
                raise EnrichLocked(
                    f"LinkedIn enrich already running under pid {record.pid}"
                )
            # Stale, dead, or our own leftover: drop it before exclusive create.
            self.path.unlink(missing_ok=True)
        self._create_exclusive()

    def _create_exclusive(self) -> None:
        """Create the lock file exclusively; raise if a competitor beat us."""
        payload = f"{self.pid} {self._now().isoformat()}\n"
        try:
            fd = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                _LOCK_PERMISSIONS,
            )
        except FileExistsError as exc:
            raise EnrichLocked(
                "LinkedIn enrich lock was taken by another process"
            ) from exc
        with os.fdopen(fd, "w", encoding="utf-8") as lock_file:
            lock_file.write(payload)
        self._held = True

    def release(self) -> None:
        """Release the lock only if this process still owns the on-disk record."""
        record = self._read_record()
        if record is None:
            return
        if self._held and record.pid == self.pid:
            self.path.unlink(missing_ok=True)
            self._held = False

    def _blocks_current_process(self, record: _LockRecord) -> bool:
        if record.pid == self.pid:
            return False
        if not self._is_pid_alive(record.pid):
            return False
        return self._now() - record.created_at < self._stale_after

    def _read_record(self) -> _LockRecord | None:
        try:
            raw = self.path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        parts = raw.split(maxsplit=1)
        if len(parts) != _LOCK_PARTS:
            return None
        try:
            created_at = datetime.fromisoformat(parts[1])
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            return _LockRecord(pid=int(parts[0]), created_at=created_at)
        except ValueError:
            return None


__all__ = ["EnrichLocked", "LinkedInEnrichLock"]
