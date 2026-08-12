"""Thin hidden CLI wiring for the socket-free migration runner."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from jobfeed.adapters.migration._pg_preprovisioned_markers import CaptureReady

migrate_module = importlib.import_module("jobfeed.cli.migrate")


def test_final_publish_never_replaces_concurrent_destination(tmp_path: Path) -> None:
    """A competing artifact directory remains byte-for-byte untouched."""
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "evidence-index.json").write_text("new", encoding="utf-8")
    destination = tmp_path / "bundle"
    destination.mkdir()
    sentinel = destination / "owned-by-other-run"
    sentinel.write_text("keep", encoding="utf-8")

    try:
        migrate_module._publish_directory_no_replace(staging, destination)
    except FileExistsError:
        pass
    else:
        raise AssertionError("concurrent destination was replaced")

    assert sentinel.read_text("utf-8") == "keep"
    assert (staging / "evidence-index.json").read_text("utf-8") == "new"


def test_hidden_runner_captures_then_verifies_host_inspection(
    tmp_path: Path, monkeypatch: object
) -> None:
    """The internal runner binds capture output to pre/post host documents."""
    pre = tmp_path / "pre.json"
    post = tmp_path / "post.json"
    bootstrap_path = tmp_path / "bootstrap.json"
    ready_path = tmp_path / "capture-ready.json"
    verified_path = tmp_path / "verified.json"
    pre.write_text('{"phase":"pre"}', encoding="utf-8")
    post.write_text('{"phase":"post"}', encoding="utf-8")
    bootstrap_path.write_text('{"bootstrap_version":1}', encoding="utf-8")
    ready_path.write_text(
        json.dumps(
            {
                "marker_version": 1,
                "bootstrap_sha256": "b" * 64,
                "evidence_bundle_sha256": "c" * 64,
            }
        ),
        encoding="utf-8",
    )
    workload = tmp_path / "workload.json"
    workload.write_text('{"workload_version":1}', encoding="utf-8")
    artifact = tmp_path / "bundle"
    verified: list[object] = []
    bootstrap = SimpleNamespace(project_label="jobfeed-migration-test")
    result = SimpleNamespace(
        source_dsn="postgresql://jobfeed@restore-source:5432/jobfeed_restore",
        scratch_dsn="postgresql://jobfeed@restore-scratch:5432/jobfeed_restore",
        dump_sha256="d" * 64,
        dump_size_bytes=10,
        attestations={"source": {}, "scratch": {}},
    )

    monkeypatch.setattr(migrate_module, "RESTORE_POST_INSPECTION_PATH", post)
    monkeypatch.setattr(migrate_module, "RESTORE_BOOTSTRAP_PATH", bootstrap_path)
    monkeypatch.setattr(migrate_module, "RESTORE_CAPTURE_READY_PATH", ready_path)
    monkeypatch.setattr(migrate_module, "RESTORE_VERIFIED_PATH", verified_path)
    monkeypatch.setattr(migrate_module, "_RESTORE_PRE_INSPECTION_PATH", pre)
    monkeypatch.setattr(migrate_module, "load_restore_bootstrap", lambda: bootstrap)
    monkeypatch.setattr(
        migrate_module,
        "capture_pg_baseline",
        lambda *_args, **_kwargs: ({"manifest": 1}, {"benchmark": 1}),
    )
    monkeypatch.setattr(migrate_module, "validate_evidence_bundle", lambda *_: None)
    alembic = tmp_path / "alembic"
    alembic.write_text("", encoding="utf-8")
    monkeypatch.setattr(migrate_module.shutil, "which", lambda _name: str(alembic))

    def capture_restore(_config: object, capture: object, **_kwargs: object) -> object:
        bundle = capture(result)
        return CaptureReady(
            marker_version=1,
            bootstrap_sha256="b" * 64,
            evidence_bundle_sha256=migrate_module.artifact_sha256(bundle),
        )

    monkeypatch.setattr(
        migrate_module, "capture_preprovisioned_restore", capture_restore
    )

    def verify(value: object) -> None:
        verified.append(value)
        verified_path.write_text(
            json.dumps(
                {
                    "verified_version": 1,
                    "bootstrap_sha256": "b" * 64,
                    "evidence_bundle_sha256": "c" * 64,
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(migrate_module, "verify_preprovisioned_provenance", verify)
    monkeypatch.setattr(
        migrate_module,
        "build_provenance_index",
        lambda _documents, _index: {"provenance_version": 1},
    )
    monkeypatch.setattr(migrate_module, "validate_provenance_bundle", lambda *_: None)

    command = migrate_module.migrate.commands["_capture-preprovisioned-baseline"]
    invocation = CliRunner().invoke(
        command,
        [
            "--machine-token-env",
            "JOBFEED_BENCH_MACHINE_TOKEN",
            "--workload",
            str(workload),
            "--artifact-dir",
            str(artifact),
        ],
        env={
            "JOBFEED_BENCH_MACHINE_TOKEN": "same-host-token",
            "JOBFEED_MIGRATION_GIT_COMMIT": "a" * 40,
        },
    )

    assert invocation.exit_code == 0, invocation.output
    assert json.loads((artifact / "snapshot-manifest.json").read_text("utf-8")) == {
        "manifest": 1
    }
    assert len(verified) == 1
    verification = verified[0]
    assert verification.pre_docs == {"phase": "pre"}
    assert verification.post_docs == {"phase": "post"}
    assert (artifact / "restore-bootstrap.json").is_file()
    assert (artifact / "pre-inspection.json").is_file()
    assert (artifact / "post-inspection.json").is_file()
    assert (artifact / "capture-ready.json").is_file()
    assert (artifact / "provenance-verified.json").is_file()
    assert (artifact / "provenance-index.json").is_file()


def test_hidden_runner_rejects_missing_or_invalid_injected_git_commit() -> None:
    """The migration image never depends on a copied .git directory."""
    command = migrate_module.migrate.commands["_capture-preprovisioned-baseline"]
    result = CliRunner().invoke(
        command,
        [
            "--machine-token-env",
            "JOBFEED_BENCH_MACHINE_TOKEN",
            "--workload",
            __file__,
            "--artifact-dir",
            "/tmp/unused-baseline-artifact",
        ],
        env={
            "JOBFEED_BENCH_MACHINE_TOKEN": "same-host-token",
            "JOBFEED_MIGRATION_GIT_COMMIT": "not-a-commit",
        },
    )
    assert result.exit_code != 0
    assert "40 lowercase hexadecimal" in result.output
