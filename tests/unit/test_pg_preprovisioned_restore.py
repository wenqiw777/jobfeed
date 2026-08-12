"""Socket-free pre-provisioned PostgreSQL restore rehearsal tests."""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from jobfeed.adapters.migration._baseline_workload import artifact_sha256
from jobfeed.adapters.migration.pg_preprovisioned_restore import (
    CaptureReady,
    PreprovisionedRestoreConfig,
    PreprovisionedRestoreResult,
    ProvenanceVerification,
    bootstrap_sha256,
    capture_preprovisioned_restore,
    parse_restore_bootstrap,
    run_preprovisioned_restore,
    verify_host_inspection,
    verify_preprovisioned_provenance,
)
from jobfeed.adapters.migration.pg_restore_attestation import CommandResult

_SOURCE_DSN = "postgresql://jobfeed@restore-source:5432/jobfeed_restore"
_SCRATCH_DSN = "postgresql://jobfeed@restore-scratch:5432/jobfeed_restore"
_DUMP_BYTES = b"pre-provisioned dump"
_PG_RESTORE_CALLS = 3
_PG_RESTORE_CALLS_BEFORE_MUTATION_FAILURE = 2
_SHA256_HEX_LENGTH = 64


class FakePreprovisionedRunner:
    """Serve direct pg_restore/psql/Alembic results without real processes."""

    def __init__(self, config: PreprovisionedRestoreConfig) -> None:
        self.config = config
        self.calls: list[tuple[str, ...]] = []
        self.revisions: dict[str, tuple[str, str]] = {}
        self.same_database_identity = False
        self.mutate_after_source_restore = False
        self._revision_calls: dict[str, int] = {}

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        """Return deterministic direct-command output for one argv request.

        Args:
            argv: Exact direct command argument vector.
            cwd: Optional working directory, recorded only by the caller contract.
            env: Optional complete child environment.

        Returns:
            Successful fake command result unless a test overrides evidence.
        """
        del cwd
        command = tuple(argv)
        self.calls.append(command)
        if command[:2] == ("pg_restore", "--version"):
            return CommandResult(0, "pg_restore (PostgreSQL) 16.4", "")
        if command[0] == "pg_restore":
            dsn = command[command.index("--dbname") + 1]
            if self.mutate_after_source_restore and dsn == _SOURCE_DSN:
                self.config.dump_path.chmod(0o600)
                self.config.dump_path.write_bytes(b"mutated")
            return CommandResult(0, "", "")
        if command[0] == "psql":
            return self._psql(command)
        if command[0] == str(self.config.alembic_executable):
            assert env is not None and env["JOBFEED_DB_URL"] in {
                _SOURCE_DSN,
                _SCRATCH_DSN,
            }
            return CommandResult(0, "", "")
        raise AssertionError(f"unexpected command: {command}")

    def _psql(self, command: tuple[str, ...]) -> CommandResult:
        dsn = command[command.index("--dbname") + 1]
        sql = command[-1]
        if "version_num" in sql:
            count = self._revision_calls.get(dsn, 0)
            self._revision_calls[dsn] = count + 1
            revisions = self.revisions.get(dsn, ("0007", "0008"))
            return CommandResult(0, revisions[min(count, 1)], "")
        service = "restore-source" if dsn == _SOURCE_DSN else "restore-scratch"
        identity_service = "restore-source" if self.same_database_identity else service
        values = (
            "jobfeed_restore",
            "42",
            f"system-{identity_service}",
        )
        return CommandResult(0, "\t".join(values), "")


def _bootstrap(dump_path: Path) -> dict[str, object]:
    digest = hashlib.sha256(_DUMP_BYTES).hexdigest()
    base = {
        "image_digest": f"sha256:{'c' * 64}",
        "project_label": "jobfeed-migration-run-123",
        "network_name": "jobfeed-migration-run-123_default",
    }
    return {
        "bootstrap_version": 1,
        "project_label": "jobfeed-migration-run-123",
        "network": {
            "name": "jobfeed-migration-run-123_default",
            "internal": True,
        },
        "dump_mount": {
            "runner_path": str(dump_path),
            "read_only": True,
            "sha256": digest,
        },
        "source": {
            **base,
            "service": "restore-source",
            "container_id": "a" * 64,
        },
        "scratch": {
            **base,
            "service": "restore-scratch",
            "container_id": "b" * 64,
        },
        "runner": {
            **base,
            "service": "migration-runner",
            "container_id": "d" * 64,
        },
    }


def _host_docs(dump_path: Path) -> dict[str, object]:
    network = "jobfeed-migration-run-123_default"
    project = "jobfeed-migration-run-123"

    def container(service: str, container_id: str) -> dict[str, object]:
        suffix = service.removeprefix("restore-")
        mounts: list[dict[str, object]] = [
            {
                "Type": "volume",
                "Name": f"{project}_restore_{suffix}_data",
                "Destination": "/var/lib/postgresql/data",
                "RW": True,
            }
        ]
        if service == "migration-runner":
            mounts = [
                {
                    "Type": "bind",
                    "Source": "/host/frozen.dump",
                    "Destination": str(dump_path),
                    "RW": False,
                },
                {
                    "Type": "bind",
                    "Source": "/host/artifacts",
                    "Destination": "/migration/artifacts",
                    "RW": True,
                },
                {
                    "Type": "bind",
                    "Source": "/host/run",
                    "Destination": "/run/jobfeed-migration",
                    "RW": True,
                },
            ]
        return {
            "Id": container_id,
            "Image": f"sha256:{'c' * 64}",
            "Config": {
                "Labels": {
                    "com.docker.compose.project": project,
                    "com.docker.compose.service": service,
                }
            },
            "NetworkSettings": {"Networks": {network: {}}},
            "State": {"Running": True},
            "HostConfig": {"Privileged": False, "PortBindings": {}},
            "Mounts": mounts,
        }

    return {
        "network": {
            "Id": "network-id",
            "Name": network,
            "Internal": True,
            "Labels": {"com.docker.compose.project": project},
        },
        "source": container("restore-source", "a" * 64),
        "scratch": container("restore-scratch", "b" * 64),
        "runner": container("migration-runner", "d" * 64),
    }


def _config(tmp_path: Path) -> PreprovisionedRestoreConfig:
    dump = tmp_path / "source.dump"
    dump.write_bytes(_DUMP_BYTES)
    dump.chmod(0o400)
    project = tmp_path / "project"
    (project / "migrations").mkdir(parents=True)
    (project / "migrations" / "alembic.ini").write_text("[alembic]\n", "utf-8")
    executable = project / ".venv" / "bin" / "alembic"
    executable.parent.mkdir(parents=True)
    executable.write_text("", "utf-8")
    return PreprovisionedRestoreConfig(
        dump_path=dump,
        project_root=project,
        alembic_executable=executable,
        source_dsn=_SOURCE_DSN,
        scratch_dsn=_SCRATCH_DSN,
        expected_project_label="jobfeed-migration-run-123",
        bootstrap=parse_restore_bootstrap(_bootstrap(dump)),
    )


def _capture(result: PreprovisionedRestoreResult) -> dict[str, object]:
    assert result.source_dsn == _SOURCE_DSN
    assert result.scratch_dsn == _SCRATCH_DSN
    assert result.dump_size_bytes == len(_DUMP_BYTES)
    assert len(result.bootstrap_sha256) == _SHA256_HEX_LENGTH
    return result.attestations


def test_socket_free_restore_binds_bootstrap_and_live_database_identity(
    tmp_path: Path,
) -> None:
    """The runner uses direct argv and passes only derived evidence to capture."""
    config = _config(tmp_path)
    runner = FakePreprovisionedRunner(config)
    seen_bootstrap_sha: list[str] = []

    def capture(result: PreprovisionedRestoreResult) -> dict[str, object]:
        seen_bootstrap_sha.append(result.bootstrap_sha256)
        return _capture(result)

    attestations = run_preprovisioned_restore(config, capture, runner=runner)

    assert attestations["source"]["container_id"] == "a" * 64
    assert attestations["scratch"]["container_id"] == "b" * 64
    assert attestations["source"]["pre_upgrade_revision"] == "0007"
    assert attestations["source"]["post_upgrade_revision"] == "0008"
    assert not any(command[0] == "docker" for command in runner.calls)
    restores = [command for command in runner.calls if command[0] == "pg_restore"]
    assert len(restores) == _PG_RESTORE_CALLS
    assert all("docker" not in command for command in restores)


def test_host_mode_0600_is_accepted_when_mount_provenance_is_read_only(
    tmp_path: Path,
) -> None:
    """Docker :ro does not rewrite host mode bits; host inspection proves RO."""
    config = _config(tmp_path)
    config.dump_path.chmod(0o600)
    runner = FakePreprovisionedRunner(config)
    assert run_preprovisioned_restore(config, _capture, runner=runner)
    psql_sql = [command[-1] for command in runner.calls if command[0] == "psql"]
    assert all("current_setting" not in sql for sql in psql_sql)


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://jobfeed@127.0.0.1:5432/jobfeed_restore",
        "postgresql://jobfeed@localhost:5432/jobfeed_restore",
        "postgresql://jobfeed@[::1]:5432/jobfeed_restore",
        "postgresql://jobfeed:secret@restore-source:5432/jobfeed_restore",
    ],
)
def test_loopback_or_password_dsn_is_rejected_before_commands(
    tmp_path: Path, dsn: str
) -> None:
    """Only password-free Compose service DNS targets are accepted."""
    config = _config(tmp_path)
    config = PreprovisionedRestoreConfig(**{**config.__dict__, "source_dsn": dsn})
    runner = FakePreprovisionedRunner(config)
    with pytest.raises(ValueError, match="DSN"):
        run_preprovisioned_restore(config, _capture, runner=runner)
    assert runner.calls == []


def test_user_attestation_field_is_rejected_by_exact_bootstrap_schema(
    tmp_path: Path,
) -> None:
    """Bootstrap cannot smuggle user-authored restore attestations into capture."""
    config = _config(tmp_path)
    document = _bootstrap(config.dump_path)
    document["restore_attestations"] = {"source": {}, "scratch": {}}
    with pytest.raises(ValueError, match="exact keys"):
        parse_restore_bootstrap(document)


def test_live_database_identities_must_be_distinct(tmp_path: Path) -> None:
    """Separate service DSNs must resolve to distinct live database identities."""
    config = _config(tmp_path)
    runner = FakePreprovisionedRunner(config)
    runner.same_database_identity = True
    with pytest.raises(ValueError, match="distinct"):
        run_preprovisioned_restore(config, _capture, runner=runner)


def test_host_reinspection_must_match_runner_bound_bootstrap(tmp_path: Path) -> None:
    """The host verifier detects resource identity changes after runner exit."""
    config = _config(tmp_path)
    bootstrap = _bootstrap(config.dump_path)
    before = _host_docs(config.dump_path)
    after = copy.deepcopy(before)
    source = after["source"]
    assert isinstance(source, dict)
    source["Image"] = f"sha256:{'d' * 64}"

    with pytest.raises(ValueError, match="inspection mismatch"):
        verify_host_inspection(config.bootstrap, before, after)

    assert verify_host_inspection(config.bootstrap, before, before) == bootstrap_sha256(
        config.bootstrap
    )
    assert bootstrap_sha256(parse_restore_bootstrap(bootstrap)) == bootstrap_sha256(
        config.bootstrap
    )


def test_host_verifier_requires_read_only_runner_dump_mount(tmp_path: Path) -> None:
    """Host acceptance fails if runner dump mount is absent or writable."""
    config = _config(tmp_path)
    documents = _host_docs(config.dump_path)
    runner = documents["runner"]
    assert isinstance(runner, dict)
    mounts = runner["Mounts"]
    assert isinstance(mounts, list)
    mount = mounts[0]
    assert isinstance(mount, dict)
    mount["RW"] = True
    with pytest.raises(ValueError, match="not read-only"):
        verify_host_inspection(config.bootstrap, documents, documents)


def test_host_verifier_rejects_formal_or_extra_storage_mount(tmp_path: Path) -> None:
    """Restore databases cannot reuse formal pgdata or acquire extra mounts."""
    config = _config(tmp_path)
    documents = _host_docs(config.dump_path)
    source = documents["source"]
    assert isinstance(source, dict)
    mounts = source["Mounts"]
    assert isinstance(mounts, list)
    mount = mounts[0]
    assert isinstance(mount, dict)
    mount["Name"] = "jobfeed_pgdata"
    with pytest.raises(ValueError, match="storage mismatch"):
        verify_host_inspection(config.bootstrap, documents, documents)


def test_two_phase_markers_bind_resources_and_evidence_bundle(tmp_path: Path) -> None:
    """Capture-ready and verified markers bind one immutable provenance chain."""
    config = _config(tmp_path)
    runner = FakePreprovisionedRunner(config)
    bundle = {"manifest": "m", "benchmark": "b", "index": "i"}
    ready = capture_preprovisioned_restore(
        config,
        lambda _result: bundle,
        evidence_bundle_sha256=artifact_sha256,
        runner=runner,
        capture_ready_path=tmp_path / "capture-ready.json",
    )
    documents = _host_docs(config.dump_path)
    verified = verify_preprovisioned_provenance(
        ProvenanceVerification(
            bootstrap=config.bootstrap,
            pre_docs=documents,
            post_docs=documents,
            capture_ready=ready,
            actual_evidence_bundle_sha256=artifact_sha256(bundle),
        ),
        verified_path=tmp_path / "verified.json",
    )
    assert verified["bootstrap_sha256"] == bootstrap_sha256(config.bootstrap)
    assert verified["evidence_bundle_sha256"] == artifact_sha256(bundle)


def test_phase_two_rejects_bundle_hash_substitution(tmp_path: Path) -> None:
    """Post-inspect verification cannot approve a different evidence bundle."""
    config = _config(tmp_path)
    documents = _host_docs(config.dump_path)
    ready = CaptureReady(
        marker_version=1,
        bootstrap_sha256=bootstrap_sha256(config.bootstrap),
        evidence_bundle_sha256="e" * 64,
    )
    with pytest.raises(ValueError, match="bundle SHA mismatch"):
        verify_preprovisioned_provenance(
            ProvenanceVerification(
                bootstrap=config.bootstrap,
                pre_docs=documents,
                post_docs=documents,
                capture_ready=ready,
                actual_evidence_bundle_sha256="f" * 64,
            ),
            verified_path=tmp_path / "verified.json",
        )


def test_dump_mutation_between_restores_fails_closed(tmp_path: Path) -> None:
    """Both restores and capture remain bound to one stable dump digest."""
    config = _config(tmp_path)
    runner = FakePreprovisionedRunner(config)
    runner.mutate_after_source_restore = True
    with pytest.raises(ValueError, match="dump"):
        run_preprovisioned_restore(config, _capture, runner=runner)
    assert (
        len([call for call in runner.calls if call[0] == "pg_restore"])
        == _PG_RESTORE_CALLS_BEFORE_MUTATION_FAILURE
    )


@pytest.mark.parametrize(
    ("revisions", "message"),
    [(("0006", "0008"), "0007"), (("0007", "0009"), "0008")],
)
def test_revision_transition_must_be_exact(
    tmp_path: Path, revisions: tuple[str, str], message: str
) -> None:
    """Only the frozen 0007 to 0008 transition can produce an attestation."""
    config = _config(tmp_path)
    runner = FakePreprovisionedRunner(config)
    runner.revisions[_SOURCE_DSN] = revisions
    with pytest.raises(ValueError, match=message):
        run_preprovisioned_restore(config, _capture, runner=runner)
