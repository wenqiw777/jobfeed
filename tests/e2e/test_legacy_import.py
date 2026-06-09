"""E2E test: legacy v16 import + parity assertion (PostgreSQL target).

Generates the legacy fixture if it doesn't exist, imports into a freshly
migrated PostgresStore (the shared ``store`` fixture), and runs the parity
assertion harness.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from jobfeed.adapters.store.legacy_import import import_legacy_sqlite
from jobfeed.adapters.store.parity import verify_import_parity
from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import JobPosting

pytestmark = pytest.mark.postgres

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures"
LEGACY_DB = FIXTURE_DIR / "legacy_v16.db"
MANIFEST_JSON = FIXTURE_DIR / "legacy_v16_manifest.json"


@pytest.fixture(autouse=True, scope="module")
def ensure_fixture_exists() -> None:
    """Require the checked-in legacy fixture DB and manifest."""
    assert LEGACY_DB.exists(), f"Fixture DB not found: {LEGACY_DB}"
    assert MANIFEST_JSON.exists(), f"Manifest not found: {MANIFEST_JSON}"


@pytest.fixture
def manifest() -> dict:
    """Load the manifest JSON."""
    return json.loads(MANIFEST_JSON.read_text("utf-8"))


async def test_legacy_import_and_parity(
    store: PostgresStore,
    manifest: dict,
) -> None:
    """Import legacy fixture into a fresh store and verify parity."""
    report = await import_legacy_sqlite(LEGACY_DB, store)

    assert not report.errors, f"Import errors: {report.errors}"
    assert report.duration_s > 0

    for table, info in manifest["tables"].items():
        expected = info["row_count"]
        actual = report.tables_imported.get(table, 0)
        assert actual == expected, (
            f"Table {table}: expected {expected} rows, got {actual}"
        )

    parity = await verify_import_parity(LEGACY_DB, store, manifest)

    if not parity.passed:
        failures = [f"  {c.name}: {c.details}" for c in parity.checks if not c.passed]
        pytest.fail("Parity checks failed:\n" + "\n".join(failures))

    assert parity.passed
    expected_checks = 9
    assert len(parity.checks) == expected_checks, (
        f"Expected {expected_checks} parity checks, got {len(parity.checks)}"
    )
    for check in parity.checks:
        assert check.passed, f"Check {check.name} failed: {check.details}"


async def test_import_preserves_job_ids(store: PostgresStore) -> None:
    """Verify legacy job IDs are preserved after import."""
    await import_legacy_sqlite(LEGACY_DB, store)

    rows = await store.read_all_rows("jobs")
    imported_ids = {int(row["id"]) for row in rows}

    # Legacy fixture has jobs 1-20
    for i in range(1, 21):
        assert i in imported_ids, f"Job ID {i} not preserved"


async def test_import_report_counts(
    store: PostgresStore,
    manifest: dict,
) -> None:
    """Verify ImportReport table counts match manifest."""
    report = await import_legacy_sqlite(LEGACY_DB, store)

    assert "jobs" in report.tables_imported
    assert report.tables_imported["jobs"] == manifest["tables"]["jobs"]["row_count"]
    expected_eval = manifest["tables"]["evaluations"]["row_count"]
    assert report.tables_imported["evaluations"] == expected_eval


async def test_import_state_key_mapping(store: PostgresStore) -> None:
    """Verify schema_version is mapped to legacy_schema_version."""
    await import_legacy_sqlite(LEGACY_DB, store)

    state_rows = await store.read_all_rows("state")
    keys = {row["key"] for row in state_rows}

    # schema_version should be remapped to legacy_schema_version.
    assert "legacy_schema_version" in keys
    legacy_version = next(
        row["value"] for row in state_rows if row["key"] == "legacy_schema_version"
    )
    assert legacy_version == "16"


async def test_import_column_mapping(store: PostgresStore) -> None:
    """Verify legacy column names are mapped to new schema names."""
    await import_legacy_sqlite(LEGACY_DB, store)

    # Check evaluations have new column names (not legacy block_* names).
    evals = await store.read_all_rows("evaluations")
    if evals:
        first_eval = evals[0]
        assert "stage_a_timing_eligible" in first_eval
        assert "stage_a_resume_hash" in first_eval
        assert "stage_b_verdict_json" in first_eval
        assert "stage_b_summary_json" in first_eval
        assert "stage_b_fit_json" in first_eval
        assert "stage_b_hooks_json" in first_eval

    # Check applied has new column names.
    applied = await store.read_all_rows("applied")
    if applied:
        first_applied = applied[0]
        assert "verdict_snapshot" in first_applied
        assert "fit_snapshot" in first_applied
        assert "hooks_snapshot" in first_applied


async def test_import_null_location_coalesced(store: PostgresStore) -> None:
    """Verify NULL locations are coalesced to empty string."""
    await import_legacy_sqlite(LEGACY_DB, store)

    jobs = await store.read_all_rows("jobs")
    for job in jobs:
        assert job["location"] is not None, (
            f"Job {job['id']} has NULL location after import"
        )


async def test_triggers_reenabled_after_import(store: PostgresStore) -> None:
    """Verify triggers are re-enabled after import completes."""
    await import_legacy_sqlite(LEGACY_DB, store)

    # Insert a new job after import -- trigger should auto-seed status.
    job = JobPosting(
        platform="test",
        canonical_id="post-import-001",
        url="https://example.com/post-import",
        title="Post Import Job",
        company="Test Corp",
        location="Remote",
        discovered_at=datetime.now(tz=UTC),
    )
    result = await store.save_job(job)

    status = await store.get_status(result.job_id)
    assert status is not None
    assert status.status == "new"


async def test_triggers_reenabled_on_failure(store: PostgresStore) -> None:
    """Verify triggers are re-enabled even when import fails."""

    async def failing_insert(_rows: list) -> int:
        raise RuntimeError("Simulated import failure")

    with (
        patch.object(store, "bulk_insert_evaluations", side_effect=failing_insert),
        pytest.raises(RuntimeError, match="Simulated import failure"),
    ):
        await import_legacy_sqlite(LEGACY_DB, store)

    # Triggers should still be re-enabled: insert a job and check status seed.
    job = JobPosting(
        platform="test",
        canonical_id="after-fail-001",
        url="https://example.com/after-fail",
        title="After Fail Job",
        company="Test Corp",
        location="Remote",
        discovered_at=datetime.now(tz=UTC),
    )
    result = await store.save_job(job)
    status = await store.get_status(result.job_id)
    assert status is not None, "Trigger not re-enabled after failed import"
    assert status.status == "new"
