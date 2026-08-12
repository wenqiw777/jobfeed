"""Docker isolation and inspect verification for PostgreSQL restore rehearsal."""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from jobfeed.adapters.migration._pg_restore_types import (
    CommandRunner,
    RestoreRehearsalConfig,
    RestoreTarget,
    checked,
)

DATA_PATH = "/var/lib/postgresql/data"
DUMP_PATH = "/restore/source.dump"
_TOKEN_LABEL = "jobfeed.restore.token"


@dataclass(frozen=True, kw_only=True)
class OwnedVolume:
    """Named volume whose random label proves this run created it."""

    name: str
    token: str


@dataclass(frozen=True, kw_only=True)
class OwnedContainer:
    """Container bound to an immutable ID and random ownership label."""

    target: RestoreTarget
    container_id: str
    token: str


def preflight(config: RestoreRehearsalConfig, runner: CommandRunner) -> None:
    """Prove every requested container and volume name is currently absent.

    Args:
        config: Explicit restore targets to inspect.
        runner: Argv-only command executor.

    Raises:
        ValueError: If a requested resource already exists.
        RuntimeError: If Docker cannot prove that a resource is absent.
    """
    for target in (config.source, config.scratch):
        _assert_absent(runner, "container", target.container_name)
        if target.volume_name:
            _assert_absent(runner, "volume", target.volume_name)


def _assert_absent(runner: CommandRunner, kind: str, name: str) -> None:
    result = runner.run(("docker", kind, "inspect", name))
    if result.returncode == 0:
        raise ValueError(f"Docker {kind} {name!r} already exists")
    expected = f"no such {kind}: {name}".casefold()
    if result.returncode != 1 or expected not in result.stderr.casefold():
        raise RuntimeError(f"cannot prove Docker {kind} {name!r} is absent")


def start_target(
    config: RestoreRehearsalConfig,
    target: RestoreTarget,
    runner: CommandRunner,
    created_containers: list[OwnedContainer],
    created_volumes: list[OwnedVolume],
) -> OwnedContainer:
    """Create one isolated target and return its inspected container identity.

    Args:
        config: Shared rehearsal configuration.
        target: One explicit isolated restore target.
        runner: Argv-only command executor.
        created_containers: Cleanup ledger owned by the orchestrator.
        created_volumes: Cleanup ledger owned by the orchestrator.

    Returns:
        Actual container identity read from Docker inspect.

    Raises:
        ValueError: If inspect contradicts the requested isolation.
        RuntimeError: If creation, readiness, or ownership proof fails.
    """
    if target.volume_name:
        volume = _create_owned_volume(runner, target.volume_name)
        created_volumes.append(volume)
    else:
        volume = None
    token = secrets.token_hex(16)
    result = checked(
        runner.run(_docker_run_command(config, target, token)), "docker run"
    )
    container_id = result.stdout.strip()
    if not container_id:
        raise RuntimeError("docker run did not return a container ID")
    owned = OwnedContainer(target=target, container_id=container_id, token=token)
    created_containers.append(owned)
    _wait_until_ready(runner, config, owned)
    _validated_inspect(runner, config, owned, volume)
    return owned


def _create_owned_volume(runner: CommandRunner, volume_name: str) -> OwnedVolume:
    token = secrets.token_hex(16)
    command = (
        "docker",
        "volume",
        "create",
        "--label",
        "jobfeed.restore.rehearsal=true",
        "--label",
        f"{_TOKEN_LABEL}={token}",
        volume_name,
    )
    checked(runner.run(command), "docker volume create")
    result = checked(
        runner.run(("docker", "volume", "inspect", volume_name)),
        "docker volume ownership inspect",
    )
    try:
        document = json.loads(result.stdout)[0]
        actual_token = document["Labels"][_TOKEN_LABEL]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "cannot prove ownership of created rehearsal volume"
        ) from exc
    if document.get("Name") != volume_name or actual_token != token:
        raise RuntimeError("cannot prove ownership of created rehearsal volume")
    return OwnedVolume(name=volume_name, token=token)


def _docker_run_command(
    config: RestoreRehearsalConfig, target: RestoreTarget, token: str
) -> tuple[str, ...]:
    command = [
        "docker",
        "run",
        "--detach",
        "--name",
        target.container_name,
        "--publish",
        f"127.0.0.1:{target.host_port}:5432",
        "--mount",
        f"type=bind,src={config.dump_path.resolve()},dst={DUMP_PATH},readonly",
        "--label",
        "jobfeed.restore.rehearsal=true",
        "--label",
        f"{_TOKEN_LABEL}={token}",
    ]
    if target.volume_name:
        command.extend(
            ("--mount", f"type=volume,src={target.volume_name},dst={DATA_PATH}")
        )
    else:
        command.extend(("--tmpfs", f"{DATA_PATH}:rw,noexec,nosuid,size=1g"))
    command.extend(
        (
            "--env",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            "--env",
            f"POSTGRES_USER={config.database_user}",
            "--env",
            f"POSTGRES_DB={config.database_name}",
            config.postgres_image,
        )
    )
    return tuple(command)


def _wait_until_ready(
    runner: CommandRunner,
    config: RestoreRehearsalConfig,
    owned: OwnedContainer,
) -> None:
    command = (
        "docker",
        "exec",
        owned.container_id,
        "pg_isready",
        "--username",
        config.database_user,
        "--dbname",
        config.database_name,
    )
    for _ in range(60):
        if runner.run(command).returncode == 0:
            return
        time.sleep(0.25)
    raise RuntimeError("isolated PostgreSQL container did not become ready")


def _validated_inspect(
    runner: CommandRunner,
    config: RestoreRehearsalConfig,
    owned: OwnedContainer,
    volume: OwnedVolume | None,
) -> None:
    result = checked(runner.run(("docker", "inspect", owned.container_id)), "inspect")
    document, dump, data, ports = _parse_container_inspect(result.stdout)
    target = owned.target
    if document.get("Id") != owned.container_id:
        raise ValueError("Docker inspect container ID mismatch")
    if document.get("Name") != f"/{target.container_name}":
        raise ValueError("Docker inspect container name mismatch")
    image = document.get("Config")
    if not isinstance(image, dict) or image.get("Image") != config.postgres_image:
        raise ValueError("Docker inspect image mismatch")
    labels = image.get("Labels")
    if not isinstance(labels, dict) or labels.get(_TOKEN_LABEL) != owned.token:
        raise ValueError("Docker inspect container ownership mismatch")
    _validate_mounts_and_port(config, target, dump, data, ports)
    if volume is not None:
        _assert_owned_volume(runner, volume)


def _parse_container_inspect(
    stdout: str,
) -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    list[dict[str, str]],
]:
    try:
        values = json.loads(stdout)
        document = values[0]
        mounts = document["Mounts"]
        ports = document["NetworkSettings"]["Ports"]["5432/tcp"]
        dump = next(mount for mount in mounts if mount["Destination"] == DUMP_PATH)
        data = next(mount for mount in mounts if mount["Destination"] == DATA_PATH)
    except (
        IndexError,
        KeyError,
        StopIteration,
        TypeError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("Docker inspect evidence is incomplete") from exc
    return document, dump, data, ports


def _validate_mounts_and_port(
    config: RestoreRehearsalConfig,
    target: RestoreTarget,
    dump: dict[str, object],
    data: dict[str, object],
    ports: list[dict[str, str]],
) -> None:
    if dump.get("Type") != "bind" or dump.get("RW") is not False:
        raise ValueError("pg_dump must be mounted read-only")
    if Path(str(dump.get("Source"))).resolve() != config.dump_path.resolve():
        raise ValueError("Docker inspect dump source mismatch")
    expected_storage = "volume" if target.volume_name else "tmpfs"
    if data.get("Type") != expected_storage or (
        target.volume_name and data.get("Name") != target.volume_name
    ):
        raise ValueError("Docker inspect isolated storage mismatch")
    if ports != [{"HostIp": "127.0.0.1", "HostPort": str(target.host_port)}]:
        raise ValueError("Docker inspect loopback port mismatch")


def _assert_owned_volume(runner: CommandRunner, volume: OwnedVolume) -> None:
    result = checked(
        runner.run(("docker", "volume", "inspect", volume.name)),
        "Docker volume ownership inspect",
    )
    try:
        document = json.loads(result.stdout)[0]
        token = document["Labels"][_TOKEN_LABEL]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("cannot prove rehearsal volume ownership") from exc
    if document.get("Name") != volume.name or token != volume.token:
        raise RuntimeError("cannot prove rehearsal volume ownership")
