"""Advisory file locking for coordinated SQLite replacement."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

from jobfeed.adapters.store._sqlite_errors import SqliteLifecycleBusyError


class DatabaseFileLock:
    """Hold a shared-open or exclusive-restore lock for one database path."""

    def __init__(self, database_path: Path) -> None:
        self._path = database_path.with_name(f".{database_path.name}.lifecycle.lock")
        self._file_descriptor: int | None = None

    def acquire_shared(self) -> None:
        """Acquire a non-blocking shared lock for an open lifecycle."""
        self._acquire(fcntl.LOCK_SH, "shared")

    def acquire_exclusive(self) -> None:
        """Acquire a non-blocking exclusive lock for atomic replacement."""
        self._acquire(fcntl.LOCK_EX, "exclusive")

    def release(self) -> None:
        """Release this instance's lock and file descriptor idempotently."""
        file_descriptor = self._file_descriptor
        self._file_descriptor = None
        if file_descriptor is None:
            return
        try:
            fcntl.flock(file_descriptor, fcntl.LOCK_UN)
        finally:
            os.close(file_descriptor)

    def _acquire(self, mode: int, label: str) -> None:
        if self._file_descriptor is not None:
            msg = "database file lock is already held"
            raise RuntimeError(msg)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        file_descriptor = os.open(self._path, flags, 0o600)
        try:
            fcntl.flock(file_descriptor, mode | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(file_descriptor)
            msg = f"could not acquire {label} SQLite lifecycle lock"
            raise SqliteLifecycleBusyError(msg) from error
        self._file_descriptor = file_descriptor
