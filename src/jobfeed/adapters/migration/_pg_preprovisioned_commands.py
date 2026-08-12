"""Direct pg_restore, psql, and Alembic commands for pre-provisioned services."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from jobfeed.adapters.migration._baseline_workload import artifact_sha256
from jobfeed.adapters.migration._pg_preprovisioned_types import (
    PreprovisionedRestoreConfig,
    RestoreServiceBootstrap,
)
from jobfeed.adapters.migration._pg_restore_types import CommandRunner, checked

_DATABASE_IDENTITY_PARTS = 3


@dataclass(frozen=True, kw_only=True)
class DumpFingerprint:
    """Stable digest and size of the runner's read-only dump mount."""

    sha256: str
    size_bytes: int


@dataclass(frozen=True, kw_only=True)
class RestoreInputs:
    """Immutable dump and tool evidence shared by both restore services."""

    dump: DumpFingerprint
    restore_version: str


def restore_service(
    config: PreprovisionedRestoreConfig,
    service: RestoreServiceBootstrap,
    dsn: str,
    runner: CommandRunner,
    *,
    inputs: RestoreInputs,
) -> dict[str, object]:
    """Restore and attest one pre-provisioned PostgreSQL service.

    Args:
        config: Validated runner paths and service DSNs.
        service: Wrapper-inspected identity expected from live PostgreSQL.
        dsn: Password-free Compose DNS connection string.
        runner: Direct argv-only command executor.
        inputs: Shared dump fingerprint and locally executed tool version.

    Returns:
        One exact restore attestation derived from commands and live identity.

    Raises:
        ValueError: If provenance, dump stability, or revision differs.
        RuntimeError: If any direct command fails.
    """
    identity_before = _live_database_identity(runner, dsn)
    restore_command = _restore_command(dsn, config.dump_path)
    checked(runner.run(restore_command), "pg_restore")
    assert_dump_fingerprint(config.dump_path, inputs.dump)
    pre_revision = _revision(runner, dsn)
    if pre_revision != "0007":
        raise ValueError(f"restored dump must be at Alembic 0007, got {pre_revision}")
    _upgrade(config, dsn, runner)
    post_revision = _revision(runner, dsn)
    if post_revision != "0008":
        raise ValueError(f"Alembic upgrade must finish at 0008, got {post_revision}")
    identity_after = _live_database_identity(runner, dsn)
    if identity_after != identity_before:
        raise ValueError("live database identity changed during upgrade")
    return {
        "attestation_version": 1,
        "dump_sha256": inputs.dump.sha256,
        "container_id": service.container_id,
        "database_identity": identity_after,
        "restore_tool": "pg_restore",
        "restore_tool_version": inputs.restore_version,
        "restore_command_sha256": artifact_sha256(list(restore_command)),
        "pre_upgrade_revision": pre_revision,
        "post_upgrade_revision": post_revision,
    }


def dump_fingerprint(path: Path) -> DumpFingerprint:
    """Hash a non-symlink regular dump through one open file descriptor.

    Args:
        path: Fixed runner dump mount path.

    Returns:
        Dump SHA-256 and byte size.

    Raises:
        ValueError: If the path is not a regular file.
        OSError: If the path cannot be opened or read.
    """
    file_fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    digest = hashlib.sha256()
    size = 0
    try:
        metadata = os.fstat(file_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("restore dump mount must be a regular file")
        while chunk := os.read(file_fd, 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    finally:
        os.close(file_fd)
    return DumpFingerprint(sha256=digest.hexdigest(), size_bytes=size)


def assert_dump_fingerprint(path: Path, expected: DumpFingerprint) -> None:
    """Require current dump bytes and size to equal the initial fingerprint.

    Args:
        path: Fixed runner dump mount path.
        expected: Initial fingerprint to preserve.

    Raises:
        ValueError: If the dump bytes, size, type, or permissions changed.
    """
    if dump_fingerprint(path) != expected:
        raise ValueError("restore dump changed during rehearsal")


def restore_tool_version(runner: CommandRunner) -> str:
    """Execute local pg_restore and parse its version.

    Args:
        runner: Direct argv-only command executor.

    Returns:
        PostgreSQL tool version text.

    Raises:
        ValueError: If version output is unrecognized.
        RuntimeError: If pg_restore cannot run.
    """
    result = checked(runner.run(("pg_restore", "--version")), "pg_restore --version")
    match = re.search(r"PostgreSQL\)\s+(\d+(?:\.\d+)*)\b", result.stdout.strip())
    if match is None:
        raise ValueError("pg_restore version output is unrecognized")
    return match.group(1)


def _restore_command(dsn: str, dump_path: Path) -> tuple[str, ...]:
    return (
        "pg_restore",
        "--exit-on-error",
        "--no-owner",
        "--no-privileges",
        "--dbname",
        dsn,
        str(dump_path),
    )


def _psql_command(dsn: str, sql: str) -> tuple[str, ...]:
    return (
        "psql",
        "--no-psqlrc",
        "--quiet",
        "--tuples-only",
        "--no-align",
        "--field-separator=\t",
        "--set",
        "ON_ERROR_STOP=1",
        "--dbname",
        dsn,
        "--command",
        sql,
    )


def _revision(runner: CommandRunner, dsn: str) -> str:
    command = _psql_command(dsn, "SELECT version_num FROM alembic_version")
    return checked(runner.run(command), "read Alembic revision").stdout.strip()


def _upgrade(
    config: PreprovisionedRestoreConfig, dsn: str, runner: CommandRunner
) -> None:
    command = (
        str(config.alembic_executable),
        "-c",
        str(config.project_root / "migrations" / "alembic.ini"),
        "upgrade",
        "0008",
    )
    environment = {"JOBFEED_DB_URL": dsn}
    if "PATH" in os.environ:
        environment["PATH"] = os.environ["PATH"]
    checked(
        runner.run(command, cwd=config.project_root, env=environment),
        "Alembic upgrade",
    )


def _live_database_identity(runner: CommandRunner, dsn: str) -> str:
    sql = (
        "SELECT current_database(), "
        "(SELECT oid::text FROM pg_database WHERE datname=current_database()), "
        "(SELECT system_identifier::text FROM pg_control_system())"
    )
    output = checked(
        runner.run(_psql_command(dsn, sql)), "read live database identity"
    ).stdout.strip()
    values = tuple(output.split("\t"))
    if len(values) != _DATABASE_IDENTITY_PARTS or any(not value for value in values):
        raise ValueError("live database identity is incomplete")
    return hashlib.sha256("\0".join(values).encode()).hexdigest()
