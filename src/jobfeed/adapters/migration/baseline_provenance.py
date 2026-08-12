"""Exact final provenance bundle for a pre-provisioned baseline capture."""

from __future__ import annotations

from typing import cast

from jobfeed.adapters.migration._baseline_workload import artifact_sha256
from jobfeed.adapters.migration._pg_host_inspection import verify_host_inspection
from jobfeed.adapters.migration._pg_preprovisioned_types import parse_restore_bootstrap

_SHA256_HEX_LENGTH = 64

PROVENANCE_DOCUMENT_KEYS = {
    "restore-bootstrap.json",
    "pre-inspection.json",
    "post-inspection.json",
    "capture-ready.json",
    "provenance-verified.json",
    "formal-resource-fingerprints.json",
}
PROVENANCE_INDEX_KEYS = {
    "provenance_version",
    "evidence_index_sha256",
    "restore_bootstrap_sha256",
    "pre_inspection_sha256",
    "post_inspection_sha256",
    "capture_ready_sha256",
    "provenance_verified_sha256",
    "formal_resource_fingerprints_sha256",
}


def build_provenance_index(
    documents: dict[str, object], evidence_index: object
) -> dict[str, object]:
    """Build and validate the final one-way provenance index.

    Args:
        documents: Exact five persisted provenance documents by filename.
        evidence_index: Validated baseline evidence index document.

    Returns:
        Exact versioned provenance index with canonical JSON hashes.

    Raises:
        ValueError: If document coverage or cross-links differ.
    """
    if set(documents) != PROVENANCE_DOCUMENT_KEYS:
        raise ValueError("provenance document coverage mismatch")
    index = {
        "provenance_version": 1,
        "evidence_index_sha256": artifact_sha256(evidence_index),
        "restore_bootstrap_sha256": artifact_sha256(
            documents["restore-bootstrap.json"]
        ),
        "pre_inspection_sha256": artifact_sha256(documents["pre-inspection.json"]),
        "post_inspection_sha256": artifact_sha256(documents["post-inspection.json"]),
        "capture_ready_sha256": artifact_sha256(documents["capture-ready.json"]),
        "provenance_verified_sha256": artifact_sha256(
            documents["provenance-verified.json"]
        ),
        "formal_resource_fingerprints_sha256": artifact_sha256(
            documents["formal-resource-fingerprints.json"]
        ),
    }
    validate_provenance_bundle(documents, evidence_index, index)
    return index


def validate_provenance_bundle(
    documents: object, evidence_index: object, index: object
) -> None:
    """Validate exact files, hashes, host inspection, and marker cross-links.

    Args:
        documents: Exact five persisted provenance documents by filename.
        evidence_index: Baseline evidence index bound by this provenance index.
        index: Final provenance index candidate.

    Raises:
        ValueError: If shape, canonical hashes, or cross-links differ.
    """
    docs = _mapping(documents, "provenance documents")
    if set(docs) != PROVENANCE_DOCUMENT_KEYS:
        raise ValueError("provenance document coverage mismatch")
    value = _mapping(index, "provenance index")
    if set(value) != PROVENANCE_INDEX_KEYS or value["provenance_version"] != 1:
        raise ValueError("provenance index exact schema mismatch")
    expected = _build_provenance_index_unchecked(docs, evidence_index)
    if value != expected:
        raise ValueError("provenance index hash mismatch")
    _validate_formal_fingerprints(docs["formal-resource-fingerprints.json"])
    bootstrap = parse_restore_bootstrap(docs["restore-bootstrap.json"])
    host_sha = verify_host_inspection(
        bootstrap,
        docs["pre-inspection.json"],
        docs["post-inspection.json"],
    )
    ready = _mapping(docs["capture-ready.json"], "capture-ready marker")
    verified = _mapping(docs["provenance-verified.json"], "verified marker")
    if (
        set(ready)
        != {
            "marker_version",
            "bootstrap_sha256",
            "evidence_bundle_sha256",
        }
        or ready["marker_version"] != 1
    ):
        raise ValueError("capture-ready exact schema mismatch")
    if (
        set(verified)
        != {
            "verified_version",
            "bootstrap_sha256",
            "evidence_bundle_sha256",
        }
        or verified["verified_version"] != 1
    ):
        raise ValueError("verified marker exact schema mismatch")
    if ready["bootstrap_sha256"] != host_sha or verified != {
        "verified_version": 1,
        "bootstrap_sha256": host_sha,
        "evidence_bundle_sha256": ready["evidence_bundle_sha256"],
    }:
        raise ValueError("provenance marker cross-link mismatch")


def _build_provenance_index_unchecked(
    documents: dict[str, object], evidence_index: object
) -> dict[str, object]:
    """Return canonical hashes after callers establish exact coverage."""
    return {
        "provenance_version": 1,
        "evidence_index_sha256": artifact_sha256(evidence_index),
        "restore_bootstrap_sha256": artifact_sha256(
            documents["restore-bootstrap.json"]
        ),
        "pre_inspection_sha256": artifact_sha256(documents["pre-inspection.json"]),
        "post_inspection_sha256": artifact_sha256(documents["post-inspection.json"]),
        "capture_ready_sha256": artifact_sha256(documents["capture-ready.json"]),
        "provenance_verified_sha256": artifact_sha256(
            documents["provenance-verified.json"]
        ),
        "formal_resource_fingerprints_sha256": artifact_sha256(
            documents["formal-resource-fingerprints.json"]
        ),
    }


def _validate_formal_fingerprints(value: object) -> None:
    document = _mapping(value, "formal resource fingerprints")
    if set(document) != {"fingerprint_version", "before", "after"} or (
        document["fingerprint_version"] != 1
    ):
        raise ValueError("formal resource fingerprints exact schema mismatch")
    before = _mapping(document["before"], "formal resources before")
    after = _mapping(document["after"], "formal resources after")
    if set(before) != {"container", "volume"} or before != after:
        raise ValueError("formal PostgreSQL resources changed")
    _validate_formal_resource(before["container"], "container")
    _validate_formal_resource(before["volume"], "volume")


def _validate_formal_resource(value: object, kind: str) -> None:
    resource = _mapping(value, f"formal {kind}")
    present = resource.get("present")
    if present is False:
        expected = {"present", "name"}
    elif kind == "container":
        expected = {
            "present",
            "name",
            "container_id",
            "image_id",
            "status",
            "pgdata_mounts",
        }
    else:
        expected = {
            "present",
            "name",
            "driver",
            "created_at",
            "metadata_sha256",
        }
    expected_name = "jobfeed-postgres-1" if kind == "container" else "jobfeed_pgdata"
    if (
        type(present) is not bool
        or set(resource) != expected
        or resource.get("name") != expected_name
    ):
        raise ValueError(f"formal {kind} fingerprint exact schema mismatch")
    if present is False:
        return
    string_fields = (
        ("container_id", "image_id", "status")
        if kind == "container"
        else ("driver", "created_at", "metadata_sha256")
    )
    if any(not isinstance(resource[field], str) for field in string_fields):
        raise ValueError(f"formal {kind} fingerprint field mismatch")
    if kind == "volume":
        digest_value = resource["metadata_sha256"]
        if not isinstance(digest_value, str):
            raise ValueError("formal volume metadata hash mismatch")
        digest = digest_value
        if len(digest) != _SHA256_HEX_LENGTH or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise ValueError("formal volume metadata hash mismatch")
    elif not isinstance(resource["pgdata_mounts"], list):
        raise ValueError("formal container mount fingerprint mismatch")


def _mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    return cast(dict[str, object], value)
