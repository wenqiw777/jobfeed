"""Create non-self-reported evidence for isolated PostgreSQL dump restores."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from typing import TypeVar, cast

from jobfeed.adapters.migration._baseline_evidence import (
    validate_restore_attestations,
)
from jobfeed.adapters.migration._pg_restore_artifacts import EvidenceWorkspace
from jobfeed.adapters.migration._pg_restore_cleanup import cleanup
from jobfeed.adapters.migration._pg_restore_docker import (
    OwnedContainer,
    OwnedVolume,
    preflight,
    start_target,
)
from jobfeed.adapters.migration._pg_restore_postgres import restore_and_attest
from jobfeed.adapters.migration._pg_restore_types import (
    CommandResult,
    CommandRunner,
    RestoreRehearsalConfig,
    RestoreRehearsalResult,
    RestoreTarget,
    SubprocessRunner,
)

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_SQL_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_MIN_HOST_PORT = 1024
_MAX_HOST_PORT = 65535
_CaptureResult = TypeVar("_CaptureResult")
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
    "RestoreRehearsalResult",
    "RestoreTarget",
    "run_restore_rehearsal",
]


def run_restore_rehearsal(
    config: RestoreRehearsalConfig,
    capture: Callable[[RestoreRehearsalResult], _CaptureResult],
    *,
    runner: CommandRunner | None = None,
) -> _CaptureResult:
    """Restore one dump twice and run capture inside the verified session.

    Args:
        config: Explicit new resources and local dump/project paths.
        capture: Canonical baseline callback; receives derived evidence and DSNs.
        runner: Injectable argv-only command runner for deterministic tests.

    Returns:
        The capture callback's result after resources are safely cleaned.

    Raises:
        ValueError: If paths, names, ports, revisions, or evidence are unsafe.
        RuntimeError: If Docker, PostgreSQL, restore, or Alembic commands fail.

    Side effects:
        Creates two isolated containers, a staged dump, and optional volumes.
        Capture runs before the containers are removed; JSON evidence remains.
    """
    _validate_config(config)
    command_runner = runner or SubprocessRunner()
    preflight(config, command_runner)
    workspace = EvidenceWorkspace.create(config.output_dir, config.dump_path)
    staged_config = replace(config, dump_path=workspace.staged_dump_path)
    created_containers: list[OwnedContainer] = []
    created_volumes: list[OwnedVolume] = []
    is_evidence_written = False
    try:
        documents: dict[str, dict[str, object]] = {}
        for label, target in (
            ("source", staged_config.source),
            ("scratch", staged_config.scratch),
        ):
            owned = start_target(
                staged_config,
                target,
                command_runner,
                created_containers,
                created_volumes,
            )
            documents[label] = restore_and_attest(
                staged_config,
                owned,
                command_runner,
                dump_sha256=workspace.dump_sha256,
            )
            workspace.assert_dump_unchanged()
        validated = validate_restore_attestations(
            documents["source"],
            documents["scratch"],
            dump_sha256=workspace.dump_sha256,
        )
        typed = cast(dict[str, dict[str, object]], validated)
        workspace.write_attestations(typed)
        is_evidence_written = True
        result = RestoreRehearsalResult(
            attestations=typed,
            source_dsn=_dsn(staged_config, staged_config.source),
            scratch_dsn=_dsn(staged_config, staged_config.scratch),
            staged_dump_path=workspace.staged_dump_path,
            dump_sha256=workspace.dump_sha256,
            dump_size_bytes=workspace.dump_size_bytes,
        )
        return capture(result)
    finally:
        cleanup(command_runner, created_containers, created_volumes)
        if is_evidence_written:
            workspace.close()
        else:
            workspace.cleanup()


def _validate_config(config: RestoreRehearsalConfig) -> None:
    _validate_paths(config)
    _validate_database_values(config)
    _validate_targets(config)
    if config.output_dir.exists() or config.output_dir.is_symlink():
        raise ValueError("restore evidence output directory already exists")


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


def _dsn(config: RestoreRehearsalConfig, target: RestoreTarget) -> str:
    return (
        f"postgresql://{config.database_user}@127.0.0.1:"
        f"{target.host_port}/{config.database_name}"
    )
