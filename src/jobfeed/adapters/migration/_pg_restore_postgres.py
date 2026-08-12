"""PostgreSQL restore, migration, and live identity attestation primitives."""

from __future__ import annotations

import hashlib
import os
import re

from jobfeed.adapters.migration._baseline_workload import artifact_sha256
from jobfeed.adapters.migration._pg_restore_docker import DUMP_PATH, OwnedContainer
from jobfeed.adapters.migration._pg_restore_types import (
    CommandRunner,
    RestoreRehearsalConfig,
    RestoreTarget,
    checked,
)

_DATABASE_IDENTITY_PARTS = 3
_DUMP_HASH_PARTS = 2


def restore_and_attest(
    config: RestoreRehearsalConfig,
    owned: OwnedContainer,
    runner: CommandRunner,
    *,
    dump_sha256: str,
) -> dict[str, object]:
    """Restore and upgrade one target using independently derived evidence.

    Args:
        config: Shared rehearsal configuration.
        owned: One verified running isolated restore target.
        runner: Argv-only command executor.
        dump_sha256: Digest streamed from the selected dump file.

    Returns:
        One exact restore-attestation document.

    Raises:
        ValueError: If tool output, revision transition, or identity is invalid.
        RuntimeError: If restore, migration, or PostgreSQL commands fail.
    """
    target = owned.target
    _assert_container_dump(runner, owned, dump_sha256)
    version = _restore_version(runner, owned)
    restore_command = _restore_command(config, owned)
    checked(runner.run(restore_command), "pg_restore")
    _assert_container_dump(runner, owned, dump_sha256)
    pre_revision = _revision(runner, config, owned)
    if pre_revision != "0007":
        raise ValueError(f"restored dump must be at Alembic 0007, got {pre_revision}")
    _upgrade(runner, config, target)
    post_revision = _revision(runner, config, owned)
    if post_revision != "0008":
        raise ValueError(f"Alembic upgrade must finish at 0008, got {post_revision}")
    return {
        "attestation_version": 1,
        "dump_sha256": dump_sha256,
        "container_id": owned.container_id,
        "database_identity": _database_identity(runner, config, owned),
        "restore_tool": "pg_restore",
        "restore_tool_version": version,
        "restore_command_sha256": artifact_sha256(list(restore_command)),
        "pre_upgrade_revision": pre_revision,
        "post_upgrade_revision": post_revision,
    }


def _assert_container_dump(
    runner: CommandRunner, owned: OwnedContainer, expected_sha256: str
) -> None:
    command = (
        "docker",
        "exec",
        owned.container_id,
        "sha256sum",
        DUMP_PATH,
    )
    output = checked(runner.run(command), "hash staged dump in container").stdout
    parts = output.strip().split()
    if (
        len(parts) != _DUMP_HASH_PARTS
        or parts[0] != expected_sha256
        or parts[1] != DUMP_PATH
    ):
        raise ValueError("container staged dump digest mismatch")


def _restore_version(runner: CommandRunner, owned: OwnedContainer) -> str:
    result = checked(
        runner.run(("docker", "exec", owned.container_id, "pg_restore", "--version")),
        "pg_restore --version",
    )
    match = re.search(r"(\d+(?:\.\d+)*)\s*$", result.stdout.strip())
    if match is None:
        raise ValueError("pg_restore version output is unrecognized")
    return match.group(1)


def _restore_command(
    config: RestoreRehearsalConfig, owned: OwnedContainer
) -> tuple[str, ...]:
    return (
        "docker",
        "exec",
        owned.container_id,
        "pg_restore",
        "--exit-on-error",
        "--no-owner",
        "--no-privileges",
        "--username",
        config.database_user,
        "--dbname",
        config.database_name,
        DUMP_PATH,
    )


def _psql_command(
    config: RestoreRehearsalConfig, owned: OwnedContainer, sql: str
) -> tuple[str, ...]:
    return (
        "docker",
        "exec",
        owned.container_id,
        "psql",
        "--no-psqlrc",
        "--quiet",
        "--tuples-only",
        "--no-align",
        "--field-separator=\t",
        "--set",
        "ON_ERROR_STOP=1",
        "--username",
        config.database_user,
        "--dbname",
        config.database_name,
        "--command",
        sql,
    )


def _revision(
    runner: CommandRunner,
    config: RestoreRehearsalConfig,
    owned: OwnedContainer,
) -> str:
    command = _psql_command(config, owned, "SELECT version_num FROM alembic_version")
    return checked(runner.run(command), "read Alembic revision").stdout.strip()


def _upgrade(
    runner: CommandRunner,
    config: RestoreRehearsalConfig,
    target: RestoreTarget,
) -> None:
    dsn = (
        f"postgresql://{config.database_user}@127.0.0.1:"
        f"{target.host_port}/{config.database_name}"
    )
    command = (
        str(config.alembic_executable),
        "-c",
        str(config.project_root / "migrations" / "alembic.ini"),
        "upgrade",
        "0008",
    )
    env = {"JOBFEED_DB_URL": dsn}
    if "PATH" in os.environ:
        env["PATH"] = os.environ["PATH"]
    checked(runner.run(command, cwd=config.project_root, env=env), "Alembic upgrade")


def _database_identity(
    runner: CommandRunner,
    config: RestoreRehearsalConfig,
    owned: OwnedContainer,
) -> str:
    sql = (
        "SELECT current_database(), "
        "(SELECT oid::text FROM pg_database WHERE datname=current_database()), "
        "(SELECT system_identifier::text FROM pg_control_system())"
    )
    output = checked(
        runner.run(_psql_command(config, owned, sql)), "read database identity"
    ).stdout.strip()
    parts = output.split("\t")
    if len(parts) != _DATABASE_IDENTITY_PARTS or any(not part for part in parts):
        raise ValueError("live database identity output is incomplete")
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()
