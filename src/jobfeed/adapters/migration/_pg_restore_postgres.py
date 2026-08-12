"""PostgreSQL restore, migration, and live identity attestation primitives."""

from __future__ import annotations

import hashlib
import os
import re

from jobfeed.adapters.migration._baseline_workload import artifact_sha256
from jobfeed.adapters.migration._pg_restore_docker import DUMP_PATH
from jobfeed.adapters.migration._pg_restore_types import (
    CommandRunner,
    RestoreRehearsalConfig,
    RestoreTarget,
    checked,
)

_DATABASE_IDENTITY_PARTS = 3


def restore_and_attest(
    config: RestoreRehearsalConfig,
    target: RestoreTarget,
    runner: CommandRunner,
    *,
    container_id: str,
    dump_sha256: str,
) -> dict[str, object]:
    """Restore and upgrade one target using independently derived evidence.

    Args:
        config: Shared rehearsal configuration.
        target: One running isolated restore target.
        runner: Argv-only command executor.
        container_id: Identity already verified through Docker inspect.
        dump_sha256: Digest streamed from the selected dump file.

    Returns:
        One exact restore-attestation document.

    Raises:
        ValueError: If tool output, revision transition, or identity is invalid.
        RuntimeError: If restore, migration, or PostgreSQL commands fail.
    """
    version = _restore_version(runner, target)
    restore_command = _restore_command(config, target)
    checked(runner.run(restore_command), "pg_restore")
    pre_revision = _revision(runner, config, target)
    if pre_revision != "0007":
        raise ValueError(f"restored dump must be at Alembic 0007, got {pre_revision}")
    _upgrade(runner, config, target)
    post_revision = _revision(runner, config, target)
    if post_revision != "0008":
        raise ValueError(f"Alembic upgrade must finish at 0008, got {post_revision}")
    return {
        "attestation_version": 1,
        "dump_sha256": dump_sha256,
        "container_id": container_id,
        "database_identity": _database_identity(runner, config, target),
        "restore_tool": "pg_restore",
        "restore_tool_version": version,
        "restore_command_sha256": artifact_sha256(list(restore_command)),
        "pre_upgrade_revision": pre_revision,
        "post_upgrade_revision": post_revision,
    }


def _restore_version(runner: CommandRunner, target: RestoreTarget) -> str:
    result = checked(
        runner.run(
            ("docker", "exec", target.container_name, "pg_restore", "--version")
        ),
        "pg_restore --version",
    )
    match = re.search(r"(\d+(?:\.\d+)*)\s*$", result.stdout.strip())
    if match is None:
        raise ValueError("pg_restore version output is unrecognized")
    return match.group(1)


def _restore_command(
    config: RestoreRehearsalConfig, target: RestoreTarget
) -> tuple[str, ...]:
    return (
        "docker",
        "exec",
        target.container_name,
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
    config: RestoreRehearsalConfig, target: RestoreTarget, sql: str
) -> tuple[str, ...]:
    return (
        "docker",
        "exec",
        target.container_name,
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
    target: RestoreTarget,
) -> str:
    command = _psql_command(config, target, "SELECT version_num FROM alembic_version")
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
    target: RestoreTarget,
) -> str:
    sql = (
        "SELECT current_database(), "
        "(SELECT oid::text FROM pg_database WHERE datname=current_database()), "
        "(SELECT system_identifier::text FROM pg_control_system())"
    )
    output = checked(
        runner.run(_psql_command(config, target, sql)), "read database identity"
    ).stdout.strip()
    parts = output.split("\t")
    if len(parts) != _DATABASE_IDENTITY_PARTS or any(not part for part in parts):
        raise ValueError("live database identity output is incomplete")
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()
