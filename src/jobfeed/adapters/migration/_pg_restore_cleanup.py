"""Ownership-checked cleanup for PostgreSQL restore rehearsal resources."""

from __future__ import annotations

import json
from collections.abc import Sequence

from jobfeed.adapters.migration._pg_restore_docker import (
    OwnedContainer,
    OwnedVolume,
)
from jobfeed.adapters.migration._pg_restore_types import CommandRunner

_TOKEN_LABEL = "jobfeed.restore.token"


def cleanup(
    runner: CommandRunner,
    containers: Sequence[OwnedContainer],
    volumes: Sequence[OwnedVolume],
) -> None:
    """Best-effort remove only resources still carrying this run's label.

    Args:
        runner: Argv-only command executor.
        containers: Immutable IDs and ownership tokens recorded after creation.
        volumes: Names and ownership tokens recorded after creation.
    """
    for owned in reversed(containers):
        if _container_is_owned(runner, owned):
            runner.run(("docker", "rm", "--force", owned.container_id))
    for volume in reversed(volumes):
        if _volume_is_owned(runner, volume):
            runner.run(("docker", "volume", "rm", volume.name))


def _container_is_owned(runner: CommandRunner, owned: OwnedContainer) -> bool:
    result = runner.run(("docker", "inspect", owned.container_id))
    if result.returncode != 0:
        return False
    try:
        document = json.loads(result.stdout)[0]
        token = document["Config"]["Labels"][_TOKEN_LABEL]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        return False
    return bool(document.get("Id") == owned.container_id and token == owned.token)


def _volume_is_owned(runner: CommandRunner, volume: OwnedVolume) -> bool:
    result = runner.run(("docker", "volume", "inspect", volume.name))
    if result.returncode != 0:
        return False
    try:
        document = json.loads(result.stdout)[0]
        token = document["Labels"][_TOKEN_LABEL]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        return False
    return bool(document.get("Name") == volume.name and token == volume.token)
