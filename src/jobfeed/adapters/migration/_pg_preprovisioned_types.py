"""Exact typed bootstrap schema for socket-free PostgreSQL restore rehearsal."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from jobfeed.adapters.migration._baseline_workload import artifact_sha256

_HEX = frozenset("0123456789abcdef")
_SHA256_HEX_LENGTH = 64
_SERVICE_COUNT = 3
_PROJECT = re.compile(r"jobfeed-migration-[a-z0-9][a-z0-9_.-]*\Z")
_BOOTSTRAP_KEYS = {
    "bootstrap_version",
    "project_label",
    "network",
    "dump_mount",
    "source",
    "scratch",
    "runner",
}
_NETWORK_KEYS = {"name", "internal"}
_DUMP_KEYS = {"runner_path", "read_only", "sha256"}
_SERVICE_KEYS = {
    "service",
    "container_id",
    "image_digest",
    "project_label",
    "network_name",
}
RESTORE_BOOTSTRAP_PATH = Path("/run/jobfeed-migration/bootstrap.json")
RESTORE_DUMP_PATH = Path("/run/jobfeed-migration/source.dump")
RESTORE_POST_INSPECTION_PATH = Path("/run/jobfeed-migration/post-inspection.json")
RESTORE_CAPTURE_READY_PATH = Path("/run/jobfeed-migration/capture-ready.json")
RESTORE_VERIFIED_PATH = Path("/run/jobfeed-migration/provenance-verified.json")
SOURCE_RESTORE_DSN = "postgresql://jobfeed@restore-source:5432/jobfeed_restore"
SCRATCH_RESTORE_DSN = "postgresql://jobfeed@restore-scratch:5432/jobfeed_restore"


@dataclass(frozen=True, kw_only=True)
class NetworkBootstrap:
    """One run-scoped internal Compose network."""

    name: str
    is_internal: bool


@dataclass(frozen=True, kw_only=True)
class DumpMountBootstrap:
    """Read-only dump mount visible inside the migration runner."""

    runner_path: Path
    is_read_only: bool
    sha256: str


@dataclass(frozen=True, kw_only=True)
class RestoreServiceBootstrap:
    """Wrapper-inspected identity for one pre-provisioned PostgreSQL service."""

    service: str
    container_id: str
    image_digest: str
    project_label: str
    network_name: str


@dataclass(frozen=True, kw_only=True)
class RestoreBootstrap:
    """Exact wrapper-to-runner provenance for two isolated restore services."""

    bootstrap_version: int
    project_label: str
    network: NetworkBootstrap
    dump_mount: DumpMountBootstrap
    source: RestoreServiceBootstrap
    scratch: RestoreServiceBootstrap
    runner: RestoreServiceBootstrap


@dataclass(frozen=True, kw_only=True)
class PreprovisionedRestoreConfig:
    """Trusted runner inputs for one socket-free restore and capture session."""

    dump_path: Path
    project_root: Path
    alembic_executable: Path
    source_dsn: str
    scratch_dsn: str
    expected_project_label: str
    bootstrap: RestoreBootstrap


@dataclass(frozen=True, kw_only=True)
class PreprovisionedRestoreResult:
    """Derived evidence supplied to capture while both services remain live."""

    attestations: dict[str, dict[str, object]]
    source_dsn: str
    scratch_dsn: str
    dump_path: Path
    dump_sha256: str
    dump_size_bytes: int
    bootstrap_sha256: str


def parse_restore_bootstrap(document: object) -> RestoreBootstrap:
    """Parse an exact v1 wrapper provenance document.

    Args:
        document: JSON-decoded bootstrap candidate from the fixed runner mount.

    Returns:
        Immutable exact bootstrap values.

    Raises:
        ValueError: If any key, identity, mount, or cross-field binding differs.
    """
    value = _mapping(document, "restore bootstrap")
    _exact_keys(value, _BOOTSTRAP_KEYS, "restore bootstrap")
    if value["bootstrap_version"] != 1:
        raise ValueError("unknown restore bootstrap version")
    project = _project(value["project_label"], "restore project label")
    network = _parse_network(value["network"])
    dump = _parse_dump(value["dump_mount"])
    source = _parse_service(value["source"], "source")
    scratch = _parse_service(value["scratch"], "scratch")
    runner = _parse_service(value["runner"], "runner")
    if (
        source.service != "restore-source"
        or scratch.service != "restore-scratch"
        or runner.service != "migration-runner"
    ):
        raise ValueError("restore bootstrap service names mismatch")
    if (
        len({source.container_id, scratch.container_id, runner.container_id})
        != _SERVICE_COUNT
    ):
        raise ValueError("restore bootstrap container IDs must differ")
    for service in (source, scratch, runner):
        if service.project_label != project or service.network_name != network.name:
            raise ValueError("restore bootstrap service binding mismatch")
    return RestoreBootstrap(
        bootstrap_version=1,
        project_label=project,
        network=network,
        dump_mount=dump,
        source=source,
        scratch=scratch,
        runner=runner,
    )


def bootstrap_sha256(bootstrap: RestoreBootstrap) -> str:
    """Hash the canonical host-inspected bootstrap document.

    Args:
        bootstrap: Exact parsed pre-run or post-run host inspection.

    Returns:
        Canonical JSON artifact SHA-256 for bundle cross-checking.
    """
    return artifact_sha256(bootstrap_document(bootstrap))


def bootstrap_document(bootstrap: RestoreBootstrap) -> dict[str, object]:
    """Return the exact canonical JSON shape for one parsed bootstrap.

    Args:
        bootstrap: Exact parsed host inspection.

    Returns:
        JSON-serializable bootstrap document with stable field names.
    """
    return {
        "bootstrap_version": bootstrap.bootstrap_version,
        "project_label": bootstrap.project_label,
        "network": {
            "name": bootstrap.network.name,
            "internal": bootstrap.network.is_internal,
        },
        "dump_mount": {
            "runner_path": str(bootstrap.dump_mount.runner_path),
            "read_only": bootstrap.dump_mount.is_read_only,
            "sha256": bootstrap.dump_mount.sha256,
        },
        "source": _service_document(bootstrap.source),
        "scratch": _service_document(bootstrap.scratch),
        "runner": _service_document(bootstrap.runner),
    }


def _service_document(service: RestoreServiceBootstrap) -> dict[str, object]:
    return {
        "service": service.service,
        "container_id": service.container_id,
        "image_digest": service.image_digest,
        "project_label": service.project_label,
        "network_name": service.network_name,
    }


def _parse_network(value: object) -> NetworkBootstrap:
    document = _mapping(value, "restore bootstrap network")
    _exact_keys(document, _NETWORK_KEYS, "restore bootstrap network")
    name = _text(document["name"], "restore bootstrap network name")
    if document["internal"] is not True:
        raise ValueError("restore bootstrap network must be internal")
    return NetworkBootstrap(name=name, is_internal=True)


def _parse_dump(value: object) -> DumpMountBootstrap:
    document = _mapping(value, "restore bootstrap dump mount")
    _exact_keys(document, _DUMP_KEYS, "restore bootstrap dump mount")
    path = Path(_text(document["runner_path"], "restore dump runner path"))
    if not path.is_absolute() or document["read_only"] is not True:
        raise ValueError("restore dump must be an absolute read-only runner mount")
    return DumpMountBootstrap(
        runner_path=path,
        is_read_only=True,
        sha256=_sha(document["sha256"], "restore dump SHA-256"),
    )


def _parse_service(value: object, name: str) -> RestoreServiceBootstrap:
    document = _mapping(value, f"{name} restore service")
    _exact_keys(document, _SERVICE_KEYS, f"{name} restore service")
    digest = _text(document["image_digest"], f"{name} image digest")
    if not digest.startswith("sha256:"):
        raise ValueError(f"{name} image digest must be SHA-256")
    _sha(digest.removeprefix("sha256:"), f"{name} image digest")
    return RestoreServiceBootstrap(
        service=_text(document["service"], f"{name} service"),
        container_id=_sha(document["container_id"], f"{name} container ID"),
        image_digest=digest,
        project_label=_project(document["project_label"], f"{name} project label"),
        network_name=_text(document["network_name"], f"{name} network name"),
    )


def _mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    return cast(dict[str, object], value)


def _exact_keys(value: dict[str, object], expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise ValueError(
            f"{name} exact keys mismatch: missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be non-empty text")
    return value


def _sha(value: object, name: str) -> str:
    text = _text(value, name)
    if len(text) != _SHA256_HEX_LENGTH or any(
        character not in _HEX for character in text
    ):
        raise ValueError(f"{name} must be lowercase SHA-256")
    return text


def _project(value: object, name: str) -> str:
    text = _text(value, name)
    if not _PROJECT.fullmatch(text):
        raise ValueError(f"{name} must be a run-scoped migration project label")
    return text
