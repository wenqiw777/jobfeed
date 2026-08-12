"""Own SQLite connections plus safe online backup and atomic restore."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol

import aiosqlite

from jobfeed.adapters.store._sqlite_backup import (
    _create_online_backup,
    _restore_database,
)
from jobfeed.adapters.store._sqlite_connection import (
    _open_configured_connection,
    _scalar,
)
from jobfeed.adapters.store._sqlite_errors import (
    SqliteDatabaseValidationError,
    SqliteLifecycleBusyError,
    SqliteLifecycleError,
    SqliteLifecycleStateError,
    UnsupportedSqliteVersionError,
)
from jobfeed.adapters.store._sqlite_lock import DatabaseFileLock


class SchemaInitializer(Protocol):
    """Initialize or migrate schema on an already configured connection."""

    async def __call__(self, connection: aiosqlite.Connection, /) -> None:
        """Initialize schema and finish any transaction before returning.

        Args:
            connection: Configured adapter connection owned by the lifecycle.

        Raises:
            Exception: Propagates initialization or migration failure.
        """
        ...


class SqliteLifecycle:
    """Manage configured SQLite connections and database file lifecycle."""

    def __init__(self, path: Path, initializer: SchemaInitializer) -> None:
        """Create a closed lifecycle.

        Args:
            path: Persistent SQLite database file. Its parent must exist.
            initializer: Async schema initializer or migrator callback. It receives
                a fully configured connection and must finish its transaction.
        """
        self._path = path.resolve(strict=False)
        self._initializer = initializer
        self._anchor: aiosqlite.Connection | None = None
        self._database_lock: DatabaseFileLock | None = None
        self._state_lock = asyncio.Lock()
        self._active_uses = 0
        self._is_restoring = False

    @property
    def is_open(self) -> bool:
        """Return whether initialization completed and connections may be leased.

        Returns:
            True after successful open and before close, otherwise False.
        """
        return self._anchor is not None

    async def open(self) -> None:
        """Open, configure, version-check, and initialize this lifecycle.

        Repeated calls are idempotent. The lifecycle is published only after the
        initializer returns without an open transaction.

        Raises:
            SqliteLifecycleBusyError: Another lifecycle is replacing the file.
            UnsupportedSqliteVersionError: SQLite is older than version 3.35.
            SqliteLifecycleStateError: Restore is already active or the initializer
                leaves a transaction open.
            Exception: Propagates connection and initializer failures.
        """
        async with self._state_lock:
            if self._anchor is not None:
                return
            if self._is_restoring:
                msg = "SQLite lifecycle is restoring and cannot be opened"
                raise SqliteLifecycleStateError(msg)
            database_lock = DatabaseFileLock(self._path)
            database_lock.acquire_shared()
            connection: aiosqlite.Connection | None = None
            try:
                connection = await _open_configured_connection(self._path)
                await self._run_initializer(connection)
            except BaseException:
                if connection is not None:
                    await connection.close()
                database_lock.release()
                raise
            self._anchor = connection
            self._database_lock = database_lock

    async def close(self) -> None:
        """Close the anchor and release its shared file lock idempotently.

        Raises:
            SqliteLifecycleStateError: A connection or backup is still active.
            Exception: Propagates an anchor close failure after detaching it.
        """
        async with self._state_lock:
            if self._anchor is None:
                return
            if self._active_uses:
                msg = "cannot close SQLite lifecycle with an active connection"
                raise SqliteLifecycleStateError(msg)
            connection = self._anchor
            database_lock = self._database_lock
            self._anchor = None
            self._database_lock = None
            try:
                await connection.close()
            finally:
                if database_lock is not None:
                    database_lock.release()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[aiosqlite.Connection]:
        """Yield a configured connection while keeping restore excluded.

        Yields:
            A new autocommit connection with WAL, foreign keys, the five-second
            busy timeout, and deterministic ``unicode_casefold`` registered.

        Returns:
            An async context manager for the configured connection lease.

        Raises:
            SqliteLifecycleStateError: The lifecycle is not open.
            Exception: Propagates connection configuration and close failures.
        """
        await self._start_use()
        connection: aiosqlite.Connection | None = None
        try:
            connection = await _open_configured_connection(self._path)
            yield connection
        finally:
            if connection is not None:
                await connection.close()
            await self._finish_use()

    async def backup_to(self, destination: Path) -> Path:
        """Publish an online, validated backup without overwriting a path.

        Args:
            destination: New backup file; its parent must exist.

        Returns:
            The destination path supplied by the caller.

        Raises:
            SqliteLifecycleStateError: The lifecycle is closed or paths match.
            FileExistsError: A file, directory, or symlink already exists there.
            SqliteDatabaseValidationError: The staged snapshot fails validation.
            Exception: Propagates backup, sync, and publication failures.
        """
        if _same_path(self._path, destination):
            msg = "backup destination must differ from the live database"
            raise SqliteLifecycleStateError(msg)
        anchor = await self._start_use()
        try:
            return await _create_online_backup(anchor, destination)
        finally:
            await self._finish_use()

    async def restore_from(self, source: Path) -> None:
        """Validate and atomically replace this closed database from ``source``.

        Args:
            source: Read-only SQLite backup used to build the replacement.

        Raises:
            SqliteLifecycleStateError: This lifecycle is open or paths match.
            SqliteLifecycleBusyError: Another lifecycle holds the target open.
            SqliteDatabaseValidationError: Source, stage, or sidecars are unsafe.
            Exception: Propagates backup, sync, and atomic replacement failures.
        """
        if _same_path(self._path, source):
            msg = "restore source must differ from the target database"
            raise SqliteLifecycleStateError(msg)
        await self._start_restore()
        database_lock = DatabaseFileLock(self._path)
        try:
            database_lock.acquire_exclusive()
            await _restore_database(source, self._path, self._validate_restore_stage)
        finally:
            database_lock.release()
            await self._finish_restore()

    async def _start_use(self) -> aiosqlite.Connection:
        async with self._state_lock:
            if self._anchor is None:
                msg = "SQLite lifecycle is not open"
                raise SqliteLifecycleStateError(msg)
            self._active_uses += 1
            return self._anchor

    async def _finish_use(self) -> None:
        async with self._state_lock:
            self._active_uses -= 1

    async def _start_restore(self) -> None:
        async with self._state_lock:
            if self._anchor is not None:
                msg = "SQLite lifecycle must be closed before restore"
                raise SqliteLifecycleStateError(msg)
            if self._is_restoring:
                msg = "SQLite lifecycle is already restoring"
                raise SqliteLifecycleStateError(msg)
            self._is_restoring = True

    async def _finish_restore(self) -> None:
        async with self._state_lock:
            self._is_restoring = False

    async def _validate_restore_stage(self, stage: Path) -> None:
        connection = await _open_configured_connection(stage)
        try:
            await self._run_initializer(connection)
            # Atomic publication moves one file, so fold any validator writes out
            # of WAL before the stage can become the live database.
            journal_mode = await _scalar(connection, "PRAGMA journal_mode=DELETE")
            if str(journal_mode).lower() != "delete":
                msg = "SQLite restore stage could not leave WAL mode safely"
                raise SqliteDatabaseValidationError(msg)
        finally:
            await connection.close()

    async def _run_initializer(self, connection: aiosqlite.Connection) -> None:
        await self._initializer(connection)
        if connection.in_transaction:
            await connection.rollback()
            msg = "schema initializer returned with an active transaction"
            raise SqliteLifecycleStateError(msg)


def _same_path(left: Path, right: Path) -> bool:
    return left.resolve(strict=False) == right.resolve(strict=False)


__all__ = [
    "SchemaInitializer",
    "SqliteDatabaseValidationError",
    "SqliteLifecycle",
    "SqliteLifecycleBusyError",
    "SqliteLifecycleError",
    "SqliteLifecycleStateError",
    "UnsupportedSqliteVersionError",
]
