"""Filesystem identity primitives for SQLite migration staging."""

from __future__ import annotations

import contextlib
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from jobfeed.adapters.store._sqlite_lock import DatabaseFileLock

STAGE_NAME = "artifact.sqlite"
SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
DIRECTORY_FLAGS = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
)
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


@dataclass
class _OwnedIdentity:
    """Record the kernel identity of a run-owned filesystem object."""

    device: int
    inode: int

    @classmethod
    def _from_stat(cls, value: os.stat_result) -> _OwnedIdentity:
        return cls(device=value.st_dev, inode=value.st_ino)

    def _matches(self, value: os.stat_result) -> bool:
        return self.device == value.st_dev and self.inode == value.st_ino


def _canonical_target(target: Path) -> Path:
    if not target.name or target.name in {".", ".."}:
        message = "SQLite migration target must name a file"
        raise ValueError(message)
    parent = target.parent.resolve(strict=True)
    return parent / target.name


def _assert_target_absent(parent_fd: int, target_name: str) -> None:
    names = (target_name, *(f"{target_name}{suffix}" for suffix in SIDECAR_SUFFIXES))
    for name in names:
        if _entry_exists(parent_fd, name):
            message = f"SQLite migration target already exists: {name}"
            raise FileExistsError(message)


def _create_workspace(parent_fd: int, target_name: str) -> str:
    for _attempt in range(32):
        name = f".{target_name}.migration-{secrets.token_hex(12)}"
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue
        return name
    message = "could not allocate a unique SQLite migration workspace"
    raise FileExistsError(message)


def _entry_exists(directory_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _clean_failed_creation(
    *,
    parent_fd: int,
    workspace_name: str | None,
    workspace_fd: int | None,
    stage_fd: int | None,
    database_lock: DatabaseFileLock,
) -> None:
    if stage_fd is not None:
        with contextlib.suppress(OSError):
            os.close(stage_fd)
    if workspace_fd is not None:
        with contextlib.suppress(OSError):
            os.unlink(STAGE_NAME, dir_fd=workspace_fd)
        with contextlib.suppress(OSError):
            os.close(workspace_fd)
    if workspace_name is not None:
        with contextlib.suppress(OSError):
            os.rmdir(workspace_name, dir_fd=parent_fd)
    with contextlib.suppress(OSError):
        database_lock.release()
    with contextlib.suppress(OSError):
        os.close(parent_fd)
