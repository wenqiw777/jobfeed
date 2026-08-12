"""Exact, acyclic baseline evidence and restore-attestation contracts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import cast

from jobfeed.adapters.migration._baseline_workload import artifact_sha256

_HEX_LENGTH = 64
_ATTESTATION_KEYS = {
    "attestation_version",
    "dump_sha256",
    "container_id",
    "database_identity",
    "restore_tool",
    "restore_tool_version",
    "restore_command_sha256",
    "pre_upgrade_revision",
    "post_upgrade_revision",
}
_MANIFEST_KEYS = {
    "format_version",
    "created_at_utc",
    "git_commit",
    "schema_registry",
    "source",
    "restore_attestations",
    "writer_quiescence",
    "tables",
    "activity_maxima",
    "aggregates",
    "target",
}
_BENCHMARK_KEYS = {
    "report_version",
    "created_at_utc",
    "git_commit",
    "snapshot_manifest_sha256",
    "workload_sha256",
    "machine_fingerprint",
    "host_identifier_sha256",
    "cpu_identifier_sha256",
    "warmup_count",
    "sample_count",
    "read_consistency",
    "queries",
    "contention",
    "open_workloads",
}
_INDEX_KEYS = {
    "evidence_version",
    "source_dump_sha256",
    "manifest_sha256",
    "benchmark_sha256",
    "workload_sha256",
    "git_commit",
}


@dataclass(frozen=True, kw_only=True)
class RestoreAttestation:
    """Orchestrator evidence for one dump restore and 0008 upgrade."""

    attestation_version: int
    dump_sha256: str
    container_id: str
    database_identity: str
    restore_tool: str
    restore_tool_version: str
    restore_command_sha256: str
    pre_upgrade_revision: str
    post_upgrade_revision: str


def _mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    return cast(dict[str, object], value)


def _exact_keys(value: Mapping[str, object], expected: set[str], name: str) -> None:
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
    if len(text) != _HEX_LENGTH or any(char not in "0123456789abcdef" for char in text):
        raise ValueError(f"{name} must be lowercase SHA-256")
    return text


def _restore_attestation(value: object, name: str) -> RestoreAttestation:
    document = _mapping(value, name)
    _exact_keys(document, _ATTESTATION_KEYS, name)
    if document["attestation_version"] != 1:
        raise ValueError(f"{name} unknown attestation version")
    if document["pre_upgrade_revision"] != "0007":
        raise ValueError(f"{name} must attest pre-upgrade revision 0007")
    if document["post_upgrade_revision"] != "0008":
        raise ValueError(f"{name} must attest post-upgrade revision 0008")
    return RestoreAttestation(
        attestation_version=1,
        dump_sha256=_sha(document["dump_sha256"], f"{name}.dump_sha256"),
        container_id=_text(document["container_id"], f"{name}.container_id"),
        database_identity=_sha(
            document["database_identity"], f"{name}.database_identity"
        ),
        restore_tool=_text(document["restore_tool"], f"{name}.restore_tool"),
        restore_tool_version=_text(
            document["restore_tool_version"], f"{name}.restore_tool_version"
        ),
        restore_command_sha256=_sha(
            document["restore_command_sha256"], f"{name}.restore_command_sha256"
        ),
        pre_upgrade_revision="0007",
        post_upgrade_revision="0008",
    )


def validate_restore_attestations(
    source: object, scratch: object, *, dump_sha256: str
) -> dict[str, object]:
    """Validate two distinct restores of the exact named dump.

    Args:
        source: Immutable read/manifest database restore attestation.
        scratch: Disposable contention database restore attestation.
        dump_sha256: Independently hashed dump artifact.

    Returns:
        Canonical source/scratch attestation document.

    Raises:
        ValueError: If provenance is incomplete, inconsistent, or not distinct.
    """
    expected_dump = _sha(dump_sha256, "dump_sha256")
    source_value = _restore_attestation(source, "source attestation")
    scratch_value = _restore_attestation(scratch, "scratch attestation")
    if (
        source_value.dump_sha256 != expected_dump
        or scratch_value.dump_sha256 != expected_dump
    ):
        raise ValueError("restore attestations do not bind the selected dump")
    if source_value.container_id == scratch_value.container_id:
        raise ValueError("restore attestations require distinct containers")
    if source_value.database_identity == scratch_value.database_identity:
        raise ValueError("restore attestations require distinct database identities")
    return {"source": asdict(source_value), "scratch": asdict(scratch_value)}


def validate_evidence_bundle(
    manifest: object,
    benchmark: object,
    index: object,
    *,
    verify_hashes: bool = True,
) -> None:
    """Validate exact artifact schemas and one-way manifest-to-benchmark hashes.

    Args:
        manifest: Independent dump/schema/data artifact.
        benchmark: Artifact that binds the manifest and workload hashes.
        index: Final artifact that binds both prior artifacts.
        verify_hashes: Whether to recompute manifest and benchmark SHA values.

    Raises:
        ValueError: If schemas, versions, or acyclic hash links differ.
    """
    manifest_doc = _mapping(manifest, "manifest")
    benchmark_doc = _mapping(benchmark, "benchmark")
    index_doc = _mapping(index, "evidence index")
    _exact_keys(manifest_doc, _MANIFEST_KEYS, "manifest")
    _exact_keys(benchmark_doc, _BENCHMARK_KEYS, "benchmark")
    _exact_keys(index_doc, _INDEX_KEYS, "evidence index")
    if manifest_doc["format_version"] != 1 or benchmark_doc["report_version"] != 1:
        raise ValueError("unknown manifest or benchmark version")
    if index_doc["evidence_version"] != 1:
        raise ValueError("unknown evidence index version")
    manifest_source = _mapping(manifest_doc["source"], "manifest.source")
    dump_sha = _sha(manifest_source.get("source_dump_sha256"), "source dump")
    if index_doc["source_dump_sha256"] != dump_sha:
        raise ValueError("evidence index dump SHA mismatch")
    if benchmark_doc["workload_sha256"] != index_doc["workload_sha256"]:
        raise ValueError("benchmark workload SHA mismatch")
    if verify_hashes:
        manifest_sha = artifact_sha256(manifest_doc)
        benchmark_sha = artifact_sha256(benchmark_doc)
        if benchmark_doc["snapshot_manifest_sha256"] != manifest_sha:
            raise ValueError("benchmark manifest SHA mismatch")
        if index_doc["manifest_sha256"] != manifest_sha:
            raise ValueError("evidence index manifest SHA mismatch")
        if index_doc["benchmark_sha256"] != benchmark_sha:
            raise ValueError("evidence index benchmark SHA mismatch")


def machine_fingerprint(host_identifier: str, cpu_identifier: str) -> str:
    """Hash stable host and CPU identifiers without exposing their plaintext.

    Args:
        host_identifier: Stable local hardware or host identifier.
        cpu_identifier: Stable CPU model identifier.

    Returns:
        Combined lowercase SHA-256 digest.
    """
    return hashlib.sha256(f"{host_identifier}\0{cpu_identifier}".encode()).hexdigest()


def component_fingerprints(
    host_identifier: str, cpu_identifier: str
) -> tuple[str, str, str]:
    """Return combined, host-only, and CPU-only hashed fingerprints.

    Args:
        host_identifier: Stable local hardware or host identifier.
        cpu_identifier: Stable CPU model identifier.

    Returns:
        Combined, host, and CPU SHA-256 values.
    """
    return (
        machine_fingerprint(host_identifier, cpu_identifier),
        hashlib.sha256(host_identifier.encode()).hexdigest(),
        hashlib.sha256(cpu_identifier.encode()).hexdigest(),
    )
