"""Exclusive two-phase marker files for pre-provisioned restore provenance."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

_SHA256_HEX_LENGTH = 64


@dataclass(frozen=True, kw_only=True)
class CaptureReady:
    """Phase-one marker written before the detached runner waits for host inspect."""

    marker_version: int
    bootstrap_sha256: str
    evidence_bundle_sha256: str


@dataclass(frozen=True, kw_only=True)
class ProvenanceVerification:
    """Phase-two resources and hashes needed for final runner verification."""

    bootstrap: object
    pre_docs: object
    post_docs: object
    capture_ready: CaptureReady
    actual_evidence_bundle_sha256: str


def require_sha256(value: str, name: str) -> None:
    """Require one lowercase SHA-256 digest.

    Args:
        value: Candidate digest.
        name: Field name for diagnostics.

    Raises:
        ValueError: If the candidate is not a lowercase SHA-256.
    """
    if len(value) != _SHA256_HEX_LENGTH or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"{name} must be lowercase SHA-256")


def write_new_json(path: Path, document: object) -> None:
    """Write one marker through a no-follow, no-replace file descriptor.

    Args:
        path: New marker path.
        document: JSON-serializable marker document.

    Raises:
        FileExistsError: If a path already exists.
        OSError: If safe creation, writing, or syncing fails.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o400,
    )
    try:
        content = (
            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        offset = 0
        while offset < len(content):
            offset += os.write(file_descriptor, content[offset:])
        os.fsync(file_descriptor)
    finally:
        os.close(file_descriptor)
