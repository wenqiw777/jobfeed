"""Safety and provenance tests for PostgreSQL restore attestations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from jobfeed.adapters.migration._baseline_workload import artifact_sha256
from jobfeed.adapters.migration.pg_restore_attestation import (
    CommandResult,
    RestoreRehearsalConfig,
    RestoreTarget,
    create_restore_attestations,
)

_RESTORE_COUNT = 2


class FakeRunner:
    """Return deterministic Docker/PostgreSQL evidence without starting processes."""

    def __init__(self, config: RestoreRehearsalConfig) -> None:
        self.config = config
        self.calls: list[
            tuple[tuple[str, ...], Path | None, Mapping[str, str] | None]
        ] = []
        self.existing: set[tuple[str, str]] = set()
        self.hijack_created_volume = False
        self.fail_restore_container: str | None = None
        self._volume_labels: dict[str, dict[str, str]] = {}
        self._revision_calls: dict[str, int] = {}

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        """Record argv and synthesize outputs from the requested target."""
        command = tuple(argv)
        self.calls.append((command, cwd, env))
        if command[0] == "docker":
            return self._docker(command)
        if command[0] == str(self.config.alembic_executable):
            return CommandResult(0, "", "")
        raise AssertionError(f"unexpected command: {command}")

    def _docker(self, command: tuple[str, ...]) -> CommandResult:
        if command[:3] == ("docker", "container", "inspect"):
            exists = ("container", command[3]) in self.existing
            return CommandResult(0 if exists else 1, "", "")
        if command[:3] == ("docker", "volume", "inspect"):
            if command[3] in self._volume_labels:
                labels = self._volume_labels[command[3]]
                return CommandResult(
                    0,
                    json.dumps([{"Name": command[3], "Labels": labels}]),
                    "",
                )
            exists = ("volume", command[3]) in self.existing
            return CommandResult(0 if exists else 1, "", "")
        if command[:3] == ("docker", "volume", "create"):
            volume_name = command[-1]
            token = command[-2].split("=", 1)[1]
            self._volume_labels[volume_name] = {
                "jobfeed.restore.token": "hijacked"
                if self.hijack_created_volume
                else token
            }
            return CommandResult(0, volume_name, "")
        if command[:2] == ("docker", "run"):
            return CommandResult(0, "created-container", "")
        if command[:2] == ("docker", "inspect"):
            return CommandResult(0, self._inspect(command[2]), "")
        if command[:2] == ("docker", "exec"):
            return self._exec(command)
        if command[:3] in {
            ("docker", "rm", "--force"),
            ("docker", "volume", "rm"),
        }:
            return CommandResult(0, "", "")
        raise AssertionError(f"unexpected Docker command: {command}")

    def _exec(self, command: tuple[str, ...]) -> CommandResult:
        if "pg_isready" in command:
            return CommandResult(0, "ready", "")
        if "pg_restore" in command and "--version" in command:
            return CommandResult(0, "pg_restore (PostgreSQL) 16.4", "")
        if "pg_restore" in command:
            if command[2] == self.fail_restore_container:
                return CommandResult(1, "", "restore failed")
            return CommandResult(0, "", "")
        if "psql" in command:
            return self._psql(command)
        raise AssertionError(f"unexpected Docker exec command: {command}")

    def _target(self, container_name: str) -> RestoreTarget:
        return next(
            target
            for target in (self.config.source, self.config.scratch)
            if target.container_name == container_name
        )

    def _inspect(self, container_name: str) -> str:
        target = self._target(container_name)
        storage = {
            "Type": "volume" if target.volume_name else "tmpfs",
            "Destination": "/var/lib/postgresql/data",
        }
        if target.volume_name:
            storage["Name"] = target.volume_name
        document = [
            {
                "Id": f"sha256:{container_name}",
                "Name": f"/{container_name}",
                "Config": {"Image": self.config.postgres_image},
                "Mounts": [
                    {
                        "Type": "bind",
                        "Source": str(self.config.dump_path.resolve()),
                        "Destination": "/restore/source.dump",
                        "RW": False,
                    },
                    storage,
                ],
                "NetworkSettings": {
                    "Ports": {
                        "5432/tcp": [
                            {"HostIp": "127.0.0.1", "HostPort": str(target.host_port)}
                        ]
                    }
                },
            }
        ]
        return json.dumps(document)

    def _psql(self, command: tuple[str, ...]) -> CommandResult:
        container_name = command[2]
        sql = command[-1]
        if "version_num" in sql:
            count = self._revision_calls.get(container_name, 0)
            self._revision_calls[container_name] = count + 1
            return CommandResult(0, "0007" if count == 0 else "0008", "")
        assert "pg_control_system" in sql
        return CommandResult(0, f"jobfeed_restore\t42\tsystem-{container_name}", "")


def _config(tmp_path: Path) -> RestoreRehearsalConfig:
    dump = tmp_path / "source.dump"
    dump.write_bytes(b"immutable pg dump")
    project = tmp_path / "project"
    (project / "migrations").mkdir(parents=True)
    (project / "migrations" / "alembic.ini").write_text("[alembic]\n", "utf-8")
    executable = project / ".venv" / "bin" / "alembic"
    executable.parent.mkdir(parents=True)
    executable.write_text("", "utf-8")
    return RestoreRehearsalConfig(
        dump_path=dump,
        project_root=project,
        output_dir=tmp_path / "evidence",
        alembic_executable=executable,
        source=RestoreTarget(container_name="jf-rehearsal-source", host_port=55431),
        scratch=RestoreTarget(
            container_name="jf-rehearsal-scratch",
            host_port=55432,
            volume_name="jf_rehearsal_scratch_data",
        ),
    )


def test_attestations_derive_dump_container_database_and_command_identity(
    tmp_path: Path,
) -> None:
    """Attestation identity comes only from artifacts and live command output."""
    config = _config(tmp_path)
    runner = FakeRunner(config)

    attestations = create_restore_attestations(config, runner=runner)

    digest = hashlib.sha256(config.dump_path.read_bytes()).hexdigest()
    assert attestations["source"]["dump_sha256"] == digest
    assert attestations["source"]["container_id"] == "sha256:jf-rehearsal-source"
    assert attestations["source"]["restore_tool_version"] == "16.4"
    assert attestations["source"]["pre_upgrade_revision"] == "0007"
    assert attestations["source"]["post_upgrade_revision"] == "0008"
    assert (
        attestations["source"]["database_identity"]
        != attestations["scratch"]["database_identity"]
    )
    source_file = config.output_dir / "source-restore-attestation.json"
    scratch_file = config.output_dir / "scratch-restore-attestation.json"
    assert json.loads(source_file.read_text("utf-8")) == attestations["source"]
    assert json.loads(scratch_file.read_text("utf-8")) == attestations["scratch"]

    run_commands = [
        call[0] for call in runner.calls if call[0][:2] == ("docker", "run")
    ]
    assert len(run_commands) == _RESTORE_COUNT
    assert all("readonly" in " ".join(command) for command in run_commands)
    assert all("127.0.0.1:" in " ".join(command) for command in run_commands)
    restore_commands = [
        call[0]
        for call in runner.calls
        if "pg_restore" in call[0] and "--version" not in call[0]
    ]
    assert attestations["source"]["restore_command_sha256"] == artifact_sha256(
        list(restore_commands[0])
    )


@pytest.mark.parametrize(
    ("container_name", "volume_name"),
    [
        ("postgres", None),
        ("jobfeed-postgres-1", None),
        ("safe-rehearsal", "pgdata"),
        ("safe-rehearsal", "jobfeed_pgdata"),
    ],
)
def test_formal_and_compose_targets_are_rejected_before_docker_calls(
    tmp_path: Path, container_name: str, volume_name: str | None
) -> None:
    """Known production and Compose identities cannot be rehearsal targets."""
    config = _config(tmp_path)
    config = RestoreRehearsalConfig(
        **{
            **config.__dict__,
            "source": RestoreTarget(
                container_name=container_name,
                host_port=55431,
                volume_name=volume_name,
            ),
        }
    )
    runner = FakeRunner(config)

    with pytest.raises(ValueError, match="reserved"):
        create_restore_attestations(config, runner=runner)

    assert runner.calls == []


@pytest.mark.parametrize("kind", ["container", "volume"])
def test_preflight_rejects_existing_resources(tmp_path: Path, kind: str) -> None:
    """A name collision fails before any restore resource is created."""
    config = _config(tmp_path)
    runner = FakeRunner(config)
    name = (
        config.source.container_name
        if kind == "container"
        else config.scratch.volume_name
    )
    assert name is not None
    runner.existing.add((kind, name))

    with pytest.raises(ValueError, match="already exists"):
        create_restore_attestations(config, runner=runner)

    assert not any(call[0][:2] == ("docker", "run") for call in runner.calls)


def test_restore_failure_cleans_only_created_resources_and_writes_nothing(
    tmp_path: Path,
) -> None:
    """A partial rehearsal removes its new containers/volumes and no evidence."""
    config = _config(tmp_path)
    runner = FakeRunner(config)
    runner.fail_restore_container = config.scratch.container_name

    with pytest.raises(RuntimeError, match="pg_restore"):
        create_restore_attestations(config, runner=runner)

    commands = [call[0] for call in runner.calls]
    assert ("docker", "rm", "--force", config.source.container_name) in commands
    assert ("docker", "rm", "--force", config.scratch.container_name) in commands
    assert ("docker", "volume", "rm", config.scratch.volume_name) in commands
    assert not config.output_dir.exists()


def test_volume_creation_race_never_deletes_unproven_volume(tmp_path: Path) -> None:
    """A raced named volume is rejected and never treated as cleanup-owned."""
    config = _config(tmp_path)
    runner = FakeRunner(config)
    runner.hijack_created_volume = True

    with pytest.raises(RuntimeError, match="ownership"):
        create_restore_attestations(config, runner=runner)

    commands = [call[0] for call in runner.calls]
    assert ("docker", "volume", "rm", config.scratch.volume_name) not in commands
