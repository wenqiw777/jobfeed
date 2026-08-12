"""Safety and provenance tests for PostgreSQL restore attestations."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from jobfeed.adapters.migration._baseline_workload import artifact_sha256
from jobfeed.adapters.migration.pg_restore_attestation import (
    RestoreRehearsalConfig,
    RestoreRehearsalResult,
    RestoreTarget,
    run_restore_rehearsal,
)
from tests.support.fake_restore_runner import FakeRestoreRunner

_RESTORE_COUNT = 2


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


def _capture(result: RestoreRehearsalResult) -> dict[str, dict[str, object]]:
    assert result.source_dsn.endswith(":55431/jobfeed_restore")
    assert result.scratch_dsn.endswith(":55432/jobfeed_restore")
    assert result.staged_dump_path.read_bytes() == b"immutable pg dump"
    return result.attestations


def test_rehearsal_derives_identity_and_invokes_capture_before_cleanup(
    tmp_path: Path,
) -> None:
    """One canonical API creates evidence, runs capture, and then cleans resources."""
    config = _config(tmp_path)
    runner = FakeRestoreRunner(config)

    attestations = run_restore_rehearsal(config, _capture, runner=runner)

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
    for name in ("source", "scratch"):
        output = config.output_dir / f"{name}-restore-attestation.json"
        assert json.loads(output.read_text("utf-8")) == attestations[name]
    run_commands = [
        call[0] for call in runner.calls if call[0][:2] == ("docker", "run")
    ]
    assert len(run_commands) == _RESTORE_COUNT
    assert all("readonly" in " ".join(command) for command in run_commands)
    restore_commands = [
        call[0]
        for call in runner.calls
        if "pg_restore" in call[0] and "--version" not in call[0]
    ]
    assert attestations["source"]["restore_command_sha256"] == artifact_sha256(
        list(restore_commands[0])
    )
    assert all(command[2].startswith("sha256:") for command in restore_commands)


@pytest.mark.parametrize(
    ("container_name", "volume_name"),
    [
        ("postgres", None),
        ("jobfeed-postgres-1", None),
        ("safe", "pgdata"),
        ("safe", "jobfeed_pgdata"),
    ],
)
def test_formal_targets_are_rejected_before_docker(
    tmp_path: Path, container_name: str, volume_name: str | None
) -> None:
    """Known formal and Compose identities cannot be rehearsal targets."""
    config = _config(tmp_path)
    config = replace(
        config,
        source=RestoreTarget(
            container_name=container_name, host_port=55431, volume_name=volume_name
        ),
    )
    runner = FakeRestoreRunner(config)
    with pytest.raises(ValueError, match="reserved"):
        run_restore_rehearsal(config, _capture, runner=runner)
    assert runner.calls == []


def test_preflight_requires_explicit_not_found_diagnostic(tmp_path: Path) -> None:
    """An unclassified Docker failure never passes the absence gate."""
    config = _config(tmp_path)
    runner = FakeRestoreRunner(config)
    original = runner.run

    def empty_error(*args: object, **kwargs: object):
        result = original(*args, **kwargs)
        if result.returncode == 1:
            return type(result)(1, "", "")
        return result

    runner.run = empty_error  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="cannot prove"):
        run_restore_rehearsal(config, _capture, runner=runner)


@pytest.mark.parametrize("kind", ["container", "volume"])
def test_preflight_rejects_existing_resource(tmp_path: Path, kind: str) -> None:
    """Any existing target name blocks all restore creation."""
    config = _config(tmp_path)
    runner = FakeRestoreRunner(config)
    name = (
        config.source.container_name
        if kind == "container"
        else config.scratch.volume_name
    )
    assert name is not None
    runner.existing.add((kind, name))
    with pytest.raises(ValueError, match="already exists"):
        run_restore_rehearsal(config, _capture, runner=runner)
    assert not any(call[0][:2] == ("docker", "run") for call in runner.calls)


def test_unproven_created_volume_is_never_mounted_or_deleted(tmp_path: Path) -> None:
    """A create-name race cannot enter the owned volume ledger."""
    config = _config(tmp_path)
    runner = FakeRestoreRunner(config)
    runner.hijack_created_volume = True
    with pytest.raises(RuntimeError, match="ownership"):
        run_restore_rehearsal(config, _capture, runner=runner)
    commands = [call[0] for call in runner.calls]
    assert ("docker", "volume", "rm", config.scratch.volume_name) not in commands


def test_staged_dump_mutation_fails_before_second_restore(tmp_path: Path) -> None:
    """Both restores must observe the same immutable staged dump digest."""
    config = _config(tmp_path)
    runner = FakeRestoreRunner(config)
    runner.mutate_staged_after_first_hash = True
    with pytest.raises(ValueError, match="dump digest"):
        run_restore_rehearsal(config, _capture, runner=runner)
    restores = [
        call
        for call in runner.calls
        if "pg_restore" in call[0] and "--version" not in call[0]
    ]
    assert len(restores) == 1
    assert not config.output_dir.exists()


@pytest.mark.parametrize(
    ("container_id", "revisions", "message"),
    [
        ("sha256:jf-rehearsal-source", ("0006", "0008"), "0007"),
        ("sha256:jf-rehearsal-source", ("0007", "0009"), "0008"),
    ],
)
def test_revision_transition_is_exact(
    tmp_path: Path, container_id: str, revisions: tuple[str, str], message: str
) -> None:
    """Any pre/post revision other than 0007 to 0008 fails closed."""
    config = _config(tmp_path)
    runner = FakeRestoreRunner(config)
    runner.revisions[container_id] = revisions
    with pytest.raises(ValueError, match=message):
        run_restore_rehearsal(config, _capture, runner=runner)


def test_name_swap_never_executes_or_deletes_replacement_container(
    tmp_path: Path,
) -> None:
    """Commands and cleanup bind immutable IDs, not reusable container names."""
    config = _config(tmp_path)
    runner = FakeRestoreRunner(config)
    failed_id = "sha256:jf-rehearsal-scratch"
    runner.fail_restore_id = failed_id
    runner.swap_failed_container = True
    with pytest.raises(RuntimeError, match="pg_restore"):
        run_restore_rehearsal(config, _capture, runner=runner)
    removes = [
        call[0] for call in runner.calls if call[0][:3] == ("docker", "rm", "--force")
    ]
    assert ("docker", "rm", "--force", failed_id) not in removes
    assert all(command[3] != config.scratch.container_name for command in removes)


def test_output_directory_race_preserves_attacker_file(tmp_path: Path) -> None:
    """Exclusive output ownership rejects a raced directory without overwriting it."""
    config = _config(tmp_path)
    runner = FakeRestoreRunner(config)
    runner.race_output_directory = True
    with pytest.raises(FileExistsError):
        run_restore_rehearsal(config, _capture, runner=runner)
    assert (config.output_dir / "attacker").read_text("utf-8") == "keep"


def test_attestation_link_race_never_overwrites_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Atomic no-replace links preserve a concurrently created final artifact."""
    config = _config(tmp_path)
    runner = FakeRestoreRunner(config)
    real_link = os.link
    raced = False

    def race_link(src: str, dst: str, **kwargs: object) -> None:
        nonlocal raced
        if not raced:
            raced = True
            fd = kwargs["dst_dir_fd"]
            attack = os.open(
                dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400, dir_fd=fd
            )
            os.write(attack, b"attacker")
            os.close(attack)
        real_link(src, dst, **kwargs)

    monkeypatch.setattr(
        "jobfeed.adapters.migration._pg_restore_artifacts.os.link", race_link
    )
    with pytest.raises(FileExistsError):
        run_restore_rehearsal(config, _capture, runner=runner)
    assert (
        config.output_dir / "source-restore-attestation.json"
    ).read_bytes() == b"attacker"
