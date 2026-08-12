"""SQLite integrity validation for migration publication candidates."""

from __future__ import annotations

from pathlib import Path

import aiosqlite


class MigrationArtifactValidationError(RuntimeError):
    """Raised when a staged SQLite migration artifact is not self-consistent."""


async def _validate_sqlite_artifact(path: Path) -> None:
    uri = f"{path.resolve(strict=True).as_uri()}?mode=ro&immutable=1"
    try:
        connection = await aiosqlite.connect(uri, uri=True, isolation_level=None)
        try:
            integrity_cursor = await connection.execute("PRAGMA integrity_check")
            try:
                integrity = await integrity_cursor.fetchone()
            finally:
                await integrity_cursor.close()
            foreign_key_cursor = await connection.execute("PRAGMA foreign_key_check")
            try:
                foreign_key_violation = await foreign_key_cursor.fetchone()
            finally:
                await foreign_key_cursor.close()
        finally:
            await connection.close()
    except (aiosqlite.DatabaseError, OSError) as error:
        message = f"SQLite migration artifact validation failed for {path}"
        raise MigrationArtifactValidationError(message) from error
    if integrity != ("ok",) or foreign_key_violation is not None:
        message = f"SQLite migration artifact validation failed for {path}"
        raise MigrationArtifactValidationError(message)
