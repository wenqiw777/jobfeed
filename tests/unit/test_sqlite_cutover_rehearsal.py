"""Exact evidence contracts for the PostgreSQL-to-SQLite rehearsal."""

from __future__ import annotations

import copy

import pytest

from jobfeed.adapters.migration._baseline_workload import artifact_sha256
from jobfeed.adapters.migration.sqlite_cutover_rehearsal import (
    validate_cutover_evidence,
)


def _documents() -> tuple[dict[str, object], ...]:
    manifest = {
        "format_version": 1,
        "git_commit": "c" * 40,
        "source": {"source_dump_sha256": "b" * 64},
    }
    imported = {"sqlite_file_sha256": "a" * 64}
    parity = {
        "is_match": True,
        "manifest_sha256": artifact_sha256(manifest),
    }
    index = {
        "cutover_evidence_version": 1,
        "source_dump_sha256": "b" * 64,
        "git_commit": "c" * 40,
        "manifest_sha256": artifact_sha256(manifest),
        "import_result_sha256": artifact_sha256(imported),
        "parity_result_sha256": artifact_sha256(parity),
        "sqlite_file_sha256": "a" * 64,
    }
    return manifest, imported, parity, index


def test_exact_cutover_evidence_cross_links_all_outputs() -> None:
    """The one-way index binds source, import, parity, and SQLite identity."""
    validate_cutover_evidence(*_documents())


@pytest.mark.parametrize(
    "mutation",
    ["manifest", "import", "parity", "sqlite", "extra"],
)
def test_cutover_evidence_rejects_tampering(mutation: str) -> None:
    """Any changed document, file identity, or unknown field fails closed."""
    manifest, imported, parity, index = copy.deepcopy(_documents())
    if mutation == "manifest":
        manifest["changed"] = True
    elif mutation == "import":
        imported["changed"] = True
    elif mutation == "parity":
        parity["is_match"] = False
    elif mutation == "sqlite":
        imported["sqlite_file_sha256"] = "d" * 64
    else:
        index["extra"] = True

    with pytest.raises(ValueError, match="cutover"):
        validate_cutover_evidence(manifest, imported, parity, index)
