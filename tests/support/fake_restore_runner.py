"""Deterministic Docker/PostgreSQL command fake for restore rehearsal tests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from jobfeed.adapters.migration.pg_restore_attestation import (
    CommandResult,
    RestoreRehearsalConfig,
    RestoreTarget,
)


class FakeRestoreRunner:
    """Synthesize isolated-resource evidence without starting any process."""

    def __init__(self, config: RestoreRehearsalConfig) -> None:
        self.config = config
        self.calls: list[
            tuple[tuple[str, ...], Path | None, Mapping[str, str] | None]
        ] = []
        self.existing: set[tuple[str, str]] = set()
        self.fail_restore_id: str | None = None
        self.hijack_created_volume = False
        self.mutate_staged_after_first_hash = False
        self.race_output_directory = False
        self.revisions: dict[str, tuple[str, str]] = {}
        self.swap_failed_container = False
        self._containers: dict[str, dict[str, object]] = {}
        self._volume_labels: dict[str, dict[str, str]] = {}
        self._revision_calls: dict[str, int] = {}
        self._hash_calls = 0

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        """Record argv and synthesize outputs from the requested target.

        Args:
            argv: Exact command argument vector.
            cwd: Optional requested working directory.
            env: Optional requested child environment.

        Returns:
            Deterministic captured command result.

        Raises:
            AssertionError: If production invokes an unexpected command.
        """
        command = tuple(argv)
        self.calls.append((command, cwd, env))
        if command[0] == "docker":
            return self._docker(command)
        if command[0] == str(self.config.alembic_executable):
            return CommandResult(0, "", "")
        raise AssertionError(f"unexpected command: {command}")

    def _docker(self, command: tuple[str, ...]) -> CommandResult:
        if command[:3] == ("docker", "container", "inspect"):
            return self._preflight_inspect("container", command[3])
        if command[:3] == ("docker", "volume", "inspect"):
            return self._volume_inspect(command[3])
        if command[:3] == ("docker", "volume", "create"):
            return self._create_volume(command)
        if command[:2] == ("docker", "run"):
            return self._create_container(command)
        if command[:2] == ("docker", "inspect"):
            return self._container_inspect(command[2])
        if command[:2] == ("docker", "exec"):
            return self._exec(command)
        if command[:3] in {
            ("docker", "rm", "--force"),
            ("docker", "volume", "rm"),
        }:
            return CommandResult(0, "", "")
        raise AssertionError(f"unexpected Docker command: {command}")

    def _preflight_inspect(self, kind: str, name: str) -> CommandResult:
        if self.race_output_directory and name == self.config.scratch.container_name:
            self.config.output_dir.mkdir()
            (self.config.output_dir / "attacker").write_text("keep", "utf-8")
        if (kind, name) in self.existing:
            return CommandResult(0, "[]", "")
        return CommandResult(1, "", f"Error: No such {kind}: {name}")

    def _volume_inspect(self, name: str) -> CommandResult:
        if name in self._volume_labels:
            return CommandResult(
                0,
                json.dumps([{"Name": name, "Labels": self._volume_labels[name]}]),
                "",
            )
        return self._preflight_inspect("volume", name)

    def _create_volume(self, command: tuple[str, ...]) -> CommandResult:
        name = command[-1]
        token = command[-2].split("=", 1)[1]
        self._volume_labels[name] = {
            "jobfeed.restore.token": "hijacked" if self.hijack_created_volume else token
        }
        return CommandResult(0, name, "")

    def _create_container(self, command: tuple[str, ...]) -> CommandResult:
        name = command[command.index("--name") + 1]
        token_arg = next(
            value for value in command if "jobfeed.restore.token=" in value
        )
        token = token_arg.split("=", 1)[1]
        container_id = f"sha256:{name}"
        self._containers[container_id] = {
            "name": name,
            "token": token,
            "dump": Path(
                next(value for value in command if "dst=/restore" in value)
                .split(",")[1]
                .split("=", 1)[1]
            ),
        }
        return CommandResult(0, container_id, "")

    def _container_inspect(self, container_id: str) -> CommandResult:
        document = self._containers.get(container_id)
        if document is None:
            return CommandResult(1, "", f"Error: No such object: {container_id}")
        target = self._target(str(document["name"]))
        storage: dict[str, object] = {
            "Type": "volume" if target.volume_name else "tmpfs",
            "Destination": "/var/lib/postgresql/data",
        }
        if target.volume_name:
            storage["Name"] = target.volume_name
        value = [
            {
                "Id": container_id,
                "Name": f"/{target.container_name}",
                "Config": {
                    "Image": self.config.postgres_image,
                    "Labels": {"jobfeed.restore.token": document["token"]},
                },
                "Mounts": [
                    {
                        "Type": "bind",
                        "Source": str(document["dump"]),
                        "Destination": "/restore/source.dump",
                        "RW": False,
                    },
                    storage,
                ],
                "NetworkSettings": {
                    "Ports": {
                        "5432/tcp": [
                            {
                                "HostIp": "127.0.0.1",
                                "HostPort": str(target.host_port),
                            }
                        ]
                    }
                },
            }
        ]
        return CommandResult(0, json.dumps(value), "")

    def _exec(self, command: tuple[str, ...]) -> CommandResult:
        container_id = command[2]
        if "pg_isready" in command:
            return CommandResult(0, "ready", "")
        if "sha256sum" in command:
            return self._dump_hash(container_id)
        if "pg_restore" in command and "--version" in command:
            return CommandResult(0, "pg_restore (PostgreSQL) 16.4", "")
        if "pg_restore" in command:
            if result := self._restore_failure(container_id):
                return result
            return CommandResult(0, "", "")
        if "psql" in command:
            return self._psql(container_id, command[-1])
        raise AssertionError(f"unexpected Docker exec command: {command}")

    def _restore_failure(self, container_id: str) -> CommandResult | None:
        if container_id != self.fail_restore_id:
            return None
        if self.swap_failed_container:
            self._containers.pop(container_id, None)
        return CommandResult(1, "", "restore failed")

    def _dump_hash(self, container_id: str) -> CommandResult:
        path = Path(str(self._containers[container_id]["dump"]))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self._hash_calls += 1
        if self.mutate_staged_after_first_hash and self._hash_calls == 1:
            path.chmod(0o600)
            path.write_bytes(b"mutated staged dump")
        return CommandResult(0, f"{digest}  /restore/source.dump", "")

    def _psql(self, container_id: str, sql: str) -> CommandResult:
        if "version_num" in sql:
            count = self._revision_calls.get(container_id, 0)
            self._revision_calls[container_id] = count + 1
            values = self.revisions.get(container_id, ("0007", "0008"))
            return CommandResult(0, values[min(count, 1)], "")
        assert "pg_control_system" in sql
        return CommandResult(0, f"jobfeed_restore\t42\tsystem-{container_id}", "")

    def _target(self, name: str) -> RestoreTarget:
        return next(
            target
            for target in (self.config.source, self.config.scratch)
            if target.container_name == name
        )
