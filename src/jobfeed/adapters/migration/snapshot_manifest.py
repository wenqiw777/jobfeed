"""Public validation boundary for PostgreSQL baseline snapshot manifests."""

from __future__ import annotations

from jobfeed.adapters.migration import _baseline_evidence_shape as shape
from jobfeed.adapters.migration._baseline_evidence import _validate_manifest


def validate_snapshot_manifest(manifest: object) -> dict[str, object]:
    """Validate one independent PostgreSQL baseline snapshot manifest.

    Args:
        manifest: Parsed manifest artifact produced by baseline capture.

    Returns:
        The exact validated string-keyed manifest object.

    Raises:
        ValueError: If provenance, schema, quiescence, or table metrics differ.
    """
    manifest_doc = shape.mapping(manifest, "manifest")
    _validate_manifest(manifest_doc)
    return manifest_doc


__all__ = ["validate_snapshot_manifest"]
