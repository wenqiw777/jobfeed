"""Socket-free PostgreSQL restore rehearsal over pre-provisioned Compose DNS."""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar, cast
from urllib.parse import urlsplit

from jobfeed.adapters.migration._baseline_evidence import (
    validate_restore_attestations,
)
from jobfeed.adapters.migration._pg_host_inspection import verify_host_inspection
from jobfeed.adapters.migration._pg_preprovisioned_commands import (
    RestoreInputs,
    assert_dump_fingerprint,
    dump_fingerprint,
    restore_service,
    restore_tool_version,
)
from jobfeed.adapters.migration._pg_preprovisioned_markers import (
    CaptureReady,
    ProvenanceVerification,
    require_sha256,
    write_new_json,
)
from jobfeed.adapters.migration._pg_preprovisioned_types import (
    RESTORE_BOOTSTRAP_PATH,
    RESTORE_CAPTURE_READY_PATH,
    RESTORE_DUMP_PATH,
    RESTORE_POST_INSPECTION_PATH,
    RESTORE_VERIFIED_PATH,
    SCRATCH_RESTORE_DSN,
    SOURCE_RESTORE_DSN,
    PreprovisionedRestoreConfig,
    PreprovisionedRestoreResult,
    RestoreBootstrap,
    bootstrap_sha256,
    parse_restore_bootstrap,
)
from jobfeed.adapters.migration._pg_restore_types import CommandRunner, SubprocessRunner

_CaptureResult = TypeVar("_CaptureResult")

__all__ = [
    "RESTORE_BOOTSTRAP_PATH",
    "RESTORE_CAPTURE_READY_PATH",
    "RESTORE_DUMP_PATH",
    "RESTORE_POST_INSPECTION_PATH",
    "RESTORE_VERIFIED_PATH",
    "SCRATCH_RESTORE_DSN",
    "SOURCE_RESTORE_DSN",
    "PreprovisionedRestoreConfig",
    "PreprovisionedRestoreResult",
    "ProvenanceVerification",
    "RestoreBootstrap",
    "bootstrap_sha256",
    "capture_preprovisioned_restore",
    "load_restore_bootstrap",
    "parse_restore_bootstrap",
    "run_preprovisioned_restore",
    "verify_host_inspection",
    "verify_preprovisioned_provenance",
]


def load_restore_bootstrap() -> RestoreBootstrap:
    """Load the fixed runner bootstrap without a user-selectable path.

    Returns:
        Exact parsed bootstrap mounted by the host wrapper.

    Raises:
        OSError: If the fixed bootstrap file cannot be read.
        ValueError: If JSON, schema, or the fixed dump mount path differs.
    """
    try:
        document = json.loads(RESTORE_BOOTSTRAP_PATH.read_text("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("restore bootstrap is not valid JSON") from exc
    bootstrap = parse_restore_bootstrap(document)
    if bootstrap.dump_mount.runner_path != RESTORE_DUMP_PATH:
        raise ValueError("restore bootstrap dump mount is not the canonical path")
    return bootstrap


def capture_preprovisioned_restore(
    config: PreprovisionedRestoreConfig,
    capture: Callable[[PreprovisionedRestoreResult], object],
    *,
    evidence_bundle_sha256: Callable[[object], str],
    runner: CommandRunner | None = None,
    capture_ready_path: Path = RESTORE_CAPTURE_READY_PATH,
) -> CaptureReady:
    """Run phase-one restore/capture and atomically publish its ready marker.

    Args:
        config: Fixed bootstrap, Compose DNS DSNs, and runner paths.
        capture: Baseline capture callback returning the completed evidence bundle.
        evidence_bundle_sha256: Canonical hash function for that bundle.
        runner: Injectable direct argv-only executor.
        capture_ready_path: Fixed runner marker path; injectable only for tests.

    Returns:
        Marker binding bootstrap and completed evidence bundle hashes.

    Raises:
        ValueError: If restore/capture or bundle hash validation fails.
        FileExistsError: If a marker already exists.
    """
    captured: list[object] = []

    def bind(result: PreprovisionedRestoreResult) -> object:
        bundle = capture(result)
        captured.append(bundle)
        return bundle

    run_preprovisioned_restore(config, bind, runner=runner)
    bundle_sha = evidence_bundle_sha256(captured[0])
    require_sha256(bundle_sha, "evidence bundle SHA-256")
    marker = CaptureReady(
        marker_version=1,
        bootstrap_sha256=bootstrap_sha256(config.bootstrap),
        evidence_bundle_sha256=bundle_sha,
    )
    write_new_json(capture_ready_path, marker.__dict__)
    return marker


def verify_preprovisioned_provenance(
    verification: ProvenanceVerification,
    *,
    verified_path: Path = RESTORE_VERIFIED_PATH,
) -> dict[str, object]:
    """Run phase-two host provenance and bundle verification inside the runner.

    Args:
        verification: Bootstrap, host inspections, ready marker, and bundle hash.
        verified_path: Fixed final marker path; injectable only for tests.

    Returns:
        Exact verified marker document.

    Raises:
        ValueError: If resource or bundle provenance differs.
        FileExistsError: If a verified marker already exists.
    """
    host_sha = verify_host_inspection(
        cast(RestoreBootstrap, verification.bootstrap),
        verification.pre_docs,
        verification.post_docs,
    )
    actual_bundle_sha = verification.actual_evidence_bundle_sha256
    require_sha256(actual_bundle_sha, "evidence bundle SHA-256")
    if verification.capture_ready.marker_version != 1:
        raise ValueError("unknown capture-ready marker version")
    if verification.capture_ready.bootstrap_sha256 != host_sha:
        raise ValueError("capture-ready bootstrap SHA mismatch")
    if verification.capture_ready.evidence_bundle_sha256 != actual_bundle_sha:
        raise ValueError("capture-ready evidence bundle SHA mismatch")
    verified = {
        "verified_version": 1,
        "bootstrap_sha256": host_sha,
        "evidence_bundle_sha256": actual_bundle_sha,
    }
    write_new_json(verified_path, verified)
    return verified


def run_preprovisioned_restore(
    config: PreprovisionedRestoreConfig,
    capture: Callable[[PreprovisionedRestoreResult], _CaptureResult],
    *,
    runner: CommandRunner | None = None,
) -> _CaptureResult:
    """Restore both pre-provisioned services and capture in one trusted session.

    Args:
        config: Fixed bootstrap, Compose DNS DSNs, and runner paths.
        capture: Baseline callback invoked only with derived attestations.
        runner: Injectable direct argv-only executor for deterministic tests.

    Returns:
        The capture callback result.

    Raises:
        ValueError: If bootstrap, DSN, dump, live identity, or revision differs.
        RuntimeError: If pg_restore, psql, or Alembic fails.

    Side effects:
        Restores and upgrades two already-provisioned isolated PostgreSQL services.
        Docker lifecycle is intentionally outside this socket-free runner API.

    Note:
        Container/image/project/network provenance is host-inspected evidence, not
        runner-independent proof. The host wrapper must re-inspect immutable IDs
        after this command and compare ``bootstrap_sha256`` before accepting output.
    """
    _validate_config(config)
    command_runner = runner or SubprocessRunner()
    dump = dump_fingerprint(config.dump_path)
    if dump.sha256 != config.bootstrap.dump_mount.sha256:
        raise ValueError("restore dump SHA differs from bootstrap provenance")
    version = restore_tool_version(command_runner)
    inputs = RestoreInputs(dump=dump, restore_version=version)
    source = restore_service(
        config,
        config.bootstrap.source,
        config.source_dsn,
        command_runner,
        inputs=inputs,
    )
    assert_dump_fingerprint(config.dump_path, dump)
    scratch = restore_service(
        config,
        config.bootstrap.scratch,
        config.scratch_dsn,
        command_runner,
        inputs=inputs,
    )
    assert_dump_fingerprint(config.dump_path, dump)
    validated = validate_restore_attestations(source, scratch, dump_sha256=dump.sha256)
    result = PreprovisionedRestoreResult(
        attestations=cast(dict[str, dict[str, object]], validated),
        source_dsn=config.source_dsn,
        scratch_dsn=config.scratch_dsn,
        dump_path=config.dump_path,
        dump_sha256=dump.sha256,
        dump_size_bytes=dump.size_bytes,
        bootstrap_sha256=bootstrap_sha256(config.bootstrap),
    )
    return capture(result)


def _validate_config(config: PreprovisionedRestoreConfig) -> None:
    if config.expected_project_label != config.bootstrap.project_label:
        raise ValueError("restore bootstrap project label mismatch")
    if config.dump_path != config.bootstrap.dump_mount.runner_path:
        raise ValueError("restore dump path differs from bootstrap mount")
    if not config.dump_path.is_file():
        raise ValueError("restore dump path must name a file")
    if not (config.project_root / "migrations" / "alembic.ini").is_file():
        raise ValueError("project root must contain migrations/alembic.ini")
    if not config.alembic_executable.is_file():
        raise ValueError("Alembic executable must be an explicit file")
    _validate_dsn(config.source_dsn, config.bootstrap.source.service)
    _validate_dsn(config.scratch_dsn, config.bootstrap.scratch.service)
    if config.source_dsn == config.scratch_dsn:
        raise ValueError("restore service DSNs must differ")


def _validate_dsn(dsn: str, expected_service: str) -> None:
    try:
        value = urlsplit(dsn)
        hostname = value.hostname
        port = value.port
    except ValueError as exc:
        raise ValueError("restore DSN is malformed") from exc
    if value.scheme != "postgresql" or hostname != expected_service:
        raise ValueError("restore DSN must use its exact Compose service DNS")
    if value.password is not None or value.username is None:
        raise ValueError("restore DSN must be password-free with an explicit user")
    if port not in (None, 5432) or not value.path.removeprefix("/"):
        raise ValueError("restore DSN must use the internal PostgreSQL port/database")
    if value.query or value.fragment or _is_loopback(hostname):
        raise ValueError("restore DSN cannot use loopback, query, or fragment values")


def _is_loopback(hostname: str) -> bool:
    if hostname.casefold() in {"localhost", "host.docker.internal"}:
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False
