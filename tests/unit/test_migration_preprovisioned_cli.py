"""Thin hidden CLI wiring for the socket-free migration runner."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from jobfeed.adapters.migration._pg_preprovisioned_markers import CaptureReady

migrate_module = importlib.import_module("jobfeed.cli.migrate")


def test_hidden_runner_captures_then_verifies_host_inspection(
    tmp_path: Path, monkeypatch: object
) -> None:
    """The internal runner binds capture output to pre/post host documents."""
    pre = tmp_path / "pre.json"
    post = tmp_path / "post.json"
    pre.write_text('{"phase":"pre"}', encoding="utf-8")
    post.write_text('{"phase":"post"}', encoding="utf-8")
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
    monkeypatch.setattr(migrate_module, "_RESTORE_PRE_INSPECTION_PATH", pre)
    monkeypatch.setattr(migrate_module, "load_restore_bootstrap", lambda: bootstrap)
    monkeypatch.setattr(
        migrate_module,
        "capture_pg_baseline",
        lambda *_args, **_kwargs: ({"manifest": 1}, {"benchmark": 1}),
    )
    monkeypatch.setattr(migrate_module, "validate_evidence_bundle", lambda *_: None)
    monkeypatch.setattr(
        migrate_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="a" * 40 + "\n"),
    )

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

    monkeypatch.setattr(migrate_module, "verify_preprovisioned_provenance", verify)

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
        env={"JOBFEED_BENCH_MACHINE_TOKEN": "same-host-token"},
    )

    assert invocation.exit_code == 0, invocation.output
    assert json.loads((artifact / "snapshot-manifest.json").read_text("utf-8")) == {
        "manifest": 1
    }
    assert len(verified) == 1
    verification = verified[0]
    assert verification.pre_docs == {"phase": "pre"}
    assert verification.post_docs == {"phase": "post"}
