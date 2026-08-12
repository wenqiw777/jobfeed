"""Create non-self-reported evidence for isolated PostgreSQL dump restores."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from jobfeed.adapters.migration._baseline_evidence import (
    validate_restore_attestations,
)
from jobfeed.adapters.migration._pg_restore_docker import (
    cleanup,
    preflight,
    start_target,
)
from jobfeed.adapters.migration._pg_restore_postgres import restore_and_attest
from jobfeed.adapters.migration._pg_restore_types import (
    CommandResult,
    CommandRunner,
    RestoreRehearsalConfig,
    RestoreTarget,
    SubprocessRunner,
)

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_SQL_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_MIN_HOST_PORT = 1024
_MAX_HOST_PORT = 65535
_RESERVED_CONTAINERS = {
    "postgres",
    "jobfeed-postgres-1",
    "jobfeed_postgres_1",
    "jobfeed-cli",
    "jaeger",
}
_RESERVED_VOLUMES = {
    "pgdata",
    "jobfeed_pgdata",
    "mlcache",
    "jobfeed_mlcache",
    "jaeger_data",
    "jobfeed_jaeger_data",
}

__all__ = [
    "CommandResult",
    "CommandRunner",
    "RestoreRehearsalConfig",
    "RestoreTarget",
    "create_restore_attestations",
]


def create_restore_attestations(
    config: RestoreRehearsalConfig, *, runner: CommandRunner | None = None
) -> dict[str, dict[str, object]]:
    """Restore one dump twice, upgrade 0007 to 0008, and write attestations.

    Args:
        config: Explicit new resources and local dump/project paths.
        runner: Injectable argv-only command runner for deterministic tests.

    Returns:
        Validated ``source`` and ``scratch`` attestation documents.

    Raises:
        ValueError: If paths, names, ports, revisions, or evidence are unsafe.
        RuntimeError: If Docker, PostgreSQL, restore, or Alembic commands fail.

    Side effects:
        Creates two isolated containers and optional volumes. They remain running
        after success for baseline capture and are removed on any failure.
    """
    _validate_config(config)
    command_runner = runner or SubprocessRunner()
    preflight(config, command_runner)
    created_containers: list[str] = []
    created_volumes: list[str] = []
    dump_sha256 = _file_sha256(config.dump_path)
    try:
        documents: dict[str, dict[str, object]] = {}
        for label, target in (
            ("source", config.source),
            ("scratch", config.scratch),
        ):
            container_id = start_target(
                config,
                target,
                command_runner,
                created_containers,
                created_volumes,
            )
            documents[label] = restore_and_attest(
                config,
                target,
                command_runner,
                container_id=container_id,
                dump_sha256=dump_sha256,
            )
        validated = validate_restore_attestations(
            documents["source"], documents["scratch"], dump_sha256=dump_sha256
        )
        typed = cast(dict[str, dict[str, object]], validated)
        _write_attestations(config.output_dir, typed)
        return typed
    except BaseException:
        cleanup(command_runner, created_containers, created_volumes)
        raise


def _validate_config(config: RestoreRehearsalConfig) -> None:
    _validate_paths(config)
    _validate_database_values(config)
    _validate_targets(config)
    for name in ("source", "scratch"):
        if (config.output_dir / f"{name}-restore-attestation.json").exists():
            raise ValueError("restore attestation output already exists")


def _validate_paths(config: RestoreRehearsalConfig) -> None:
    if not config.dump_path.is_file():
        raise ValueError("pg_dump path must name an existing regular file")
    if not (config.project_root / "migrations" / "alembic.ini").is_file():
        raise ValueError("project root must contain migrations/alembic.ini")
    if not config.alembic_executable.is_file():
        raise ValueError("Alembic executable must be an explicit existing file")


def _validate_database_values(config: RestoreRehearsalConfig) -> None:
    if not config.postgres_image or any(
        char.isspace() for char in config.postgres_image
    ):
        raise ValueError("PostgreSQL image must be one non-empty argv value")
    if not _SQL_NAME.fullmatch(config.database_name) or not _SQL_NAME.fullmatch(
        config.database_user
    ):
        raise ValueError("database and user names must be simple SQL identifiers")


def _validate_targets(config: RestoreRehearsalConfig) -> None:
    targets = (config.source, config.scratch)
    for target in targets:
        _validate_target(target)
    if config.source.container_name == config.scratch.container_name:
        raise ValueError("restore container names must be distinct")
    if config.source.host_port == config.scratch.host_port:
        raise ValueError("restore host ports must be distinct")
    volumes = [target.volume_name for target in targets if target.volume_name]
    if len(set(volumes)) != len(volumes):
        raise ValueError("restore volume names must be distinct")


def _validate_target(target: RestoreTarget) -> None:
    if not _NAME.fullmatch(target.container_name):
        raise ValueError("container name has unsupported characters")
    if target.container_name.casefold() in _RESERVED_CONTAINERS:
        raise ValueError("container name is reserved for formal or Compose services")
    if not _MIN_HOST_PORT <= target.host_port <= _MAX_HOST_PORT:
        raise ValueError("restore host port must be between 1024 and 65535")
    if target.volume_name is None:
        return
    if not _NAME.fullmatch(target.volume_name):
        raise ValueError("volume name has unsupported characters")
    if target.volume_name.casefold() in _RESERVED_VOLUMES:
        raise ValueError("volume name is reserved for formal or Compose data")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_attestations(
    output_dir: Path, documents: Mapping[str, Mapping[str, object]]
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary: list[Path] = []
    outputs: list[Path] = []
    try:
        for name in ("source", "scratch"):
            output = output_dir / f"{name}-restore-attestation.json"
            temp = output.with_suffix(".json.tmp")
            temp.write_text(
                json.dumps(documents[name], sort_keys=True, indent=2) + "\n", "utf-8"
            )
            temporary.append(temp)
            outputs.append(output)
        for temp, output in zip(temporary, outputs, strict=True):
            temp.replace(output)
    except Exception:
        for path in (*temporary, *outputs):
            path.unlink(missing_ok=True)
        raise
