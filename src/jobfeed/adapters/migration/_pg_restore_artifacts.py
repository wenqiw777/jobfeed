"""Exclusive staged-dump and evidence output workspace for restore rehearsal."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

_COPY_CHUNK_SIZE = 1024 * 1024
_STAGED_DUMP = "staged-source.dump"


@dataclass
class EvidenceWorkspace:
    """Run-owned directory descriptor with an immutable staged dump."""

    path: Path
    directory_fd: int
    device: int
    inode: int
    staged_dump_path: Path
    dump_sha256: str
    dump_size_bytes: int
    owned_names: set[str] = field(default_factory=set)

    @classmethod
    def create(cls, output_dir: Path, dump_path: Path) -> EvidenceWorkspace:
        """Exclusively create a workspace and copy one dump through open FDs.

        Args:
            output_dir: New evidence directory that must not already exist.
            dump_path: Existing non-symlink dump file to stage.

        Returns:
            Open run-owned workspace and its exact staged dump digest.

        Raises:
            FileExistsError: If another process owns the output path.
            OSError: If safe directory or file creation/copy fails.
        """
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        os.mkdir(output_dir, mode=0o700)
        directory_fd = os.open(output_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stat = os.fstat(directory_fd)
        workspace = cls(
            path=output_dir,
            directory_fd=directory_fd,
            device=stat.st_dev,
            inode=stat.st_ino,
            staged_dump_path=output_dir / _STAGED_DUMP,
            dump_sha256="",
            dump_size_bytes=0,
        )
        try:
            digest, size = workspace._stage_dump(dump_path)
            workspace.dump_sha256 = digest
            workspace.dump_size_bytes = size
            return workspace
        except BaseException:
            workspace.cleanup()
            raise

    def _stage_dump(self, dump_path: Path) -> tuple[str, int]:
        source_fd = os.open(dump_path, os.O_RDONLY | os.O_NOFOLLOW)
        target_fd = os.open(
            _STAGED_DUMP,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o400,
            dir_fd=self.directory_fd,
        )
        self.owned_names.add(_STAGED_DUMP)
        digest = hashlib.sha256()
        size = 0
        try:
            while chunk := os.read(source_fd, _COPY_CHUNK_SIZE):
                digest.update(chunk)
                size += len(chunk)
                _write_all(target_fd, chunk)
            os.fsync(target_fd)
        finally:
            os.close(source_fd)
            os.close(target_fd)
        return digest.hexdigest(), size

    def assert_dump_unchanged(self) -> None:
        """Require the staged dump bytes to retain their original digest.

        Raises:
            ValueError: If staged bytes changed after the copy.
        """
        if file_sha256(self.staged_dump_path) != self.dump_sha256:
            raise ValueError("staged dump digest changed during restore rehearsal")

    def write_attestations(self, documents: Mapping[str, Mapping[str, object]]) -> None:
        """Atomically link two completed JSON artifacts without replacement.

        Args:
            documents: Exact validated source and scratch documents.

        Raises:
            FileExistsError: If any final artifact was concurrently created.
            OSError: If writing, syncing, or linking fails.
        """
        temporary: list[str] = []
        linked: list[str] = []
        try:
            for name in ("source", "scratch"):
                final = f"{name}-restore-attestation.json"
                temp = f".{final}.{secrets.token_hex(16)}.tmp"
                self._write_exclusive(temp, _json_bytes(documents[name]))
                temporary.append(temp)
            for name, temp in zip(("source", "scratch"), temporary, strict=True):
                final = f"{name}-restore-attestation.json"
                os.link(
                    temp,
                    final,
                    src_dir_fd=self.directory_fd,
                    dst_dir_fd=self.directory_fd,
                    follow_symlinks=False,
                )
                self.owned_names.add(final)
                linked.append(final)
            os.fsync(self.directory_fd)
        except BaseException:
            for name in linked:
                self._unlink_owned(name)
            raise
        finally:
            for name in temporary:
                self._unlink_owned(name)

    def _write_exclusive(self, name: str, content: bytes) -> None:
        file_fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o400,
            dir_fd=self.directory_fd,
        )
        self.owned_names.add(name)
        try:
            _write_all(file_fd, content)
            os.fsync(file_fd)
        finally:
            os.close(file_fd)

    def _unlink_owned(self, name: str) -> None:
        if name not in self.owned_names:
            return
        with suppress(FileNotFoundError):
            os.unlink(name, dir_fd=self.directory_fd)
        self.owned_names.discard(name)

    def close(self) -> None:
        """Close the run-owned directory descriptor after successful capture."""
        if self.directory_fd >= 0:
            os.close(self.directory_fd)
            self.directory_fd = -1

    def cleanup(self) -> None:
        """Remove only owned files and the same directory inode when empty."""
        for name in tuple(self.owned_names):
            self._unlink_owned(name)
        if self.directory_fd >= 0:
            os.close(self.directory_fd)
            self.directory_fd = -1
        try:
            stat = self.path.lstat()
        except FileNotFoundError:
            return
        if (stat.st_dev, stat.st_ino) != (self.device, self.inode):
            return
        with suppress(OSError):
            self.path.rmdir()


def file_sha256(path: Path) -> str:
    """Stream a regular file into a lowercase SHA-256 digest.

    Args:
        path: Exact file path to hash.

    Returns:
        Lowercase hexadecimal SHA-256 digest.
    """
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_COPY_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_all(file_fd: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        offset += os.write(file_fd, content[offset:])


def _json_bytes(document: Mapping[str, object]) -> bytes:
    return (json.dumps(document, sort_keys=True, indent=2) + "\n").encode()
