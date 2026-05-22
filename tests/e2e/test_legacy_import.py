"""E2E test: legacy v16 import + parity assertion.

Generates the legacy fixture if it doesn't exist, imports into a
fresh SQLiteStore, and runs the parity assertion harness.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from jobfeed.adapters.store.legacy_import import import_legacy_sqlite
from jobfeed.adapters.store.parity import verify_import_parity
from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.models import JobPosting

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures"
LEGACY_DB = FIXTURE_DIR / "legacy_v16.db"
MANIFEST_JSON = FIXTURE_DIR / "legacy_v16_manifest.json"
GENERATOR = FIXTURE_DIR / "generate_legacy_fixture.py"


@pytest.fixture(autouse=True, scope="module")
def ensure_fixture_exists() -> None:
    """Generate the legacy fixture DB if it doesn't exist."""
    if not LEGACY_DB.exists() or not MANIFEST_JSON.exists():
        subprocess.run(
            [sys.executable, str(GENERATOR)],
            check=True,
            capture_output=True,
        )
    assert LEGACY_DB.exists(), f"Fixture DB not found: {LEGACY_DB}"
    assert MANIFEST_JSON.exists(), f"Manifest not found: {MANIFEST_JSON}"


@pytest.fixture
def manifest() -> dict:
    """Load the manifest JSON."""
    return json.loads(MANIFEST_JSON.read_text("utf-8"))


@pytest.fixture
async def target_store(tmp_path: Path) -> SQLiteStore:
    """Create a fresh SQLiteStore for import testing."""
    store = SQLiteStore(tmp_path / "imported.db")
    await store.connect()
    try:
        yield store  # type: ignore[misc]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_legacy_import_and_parity(
    target_store: SQLiteStore,
    manifest: dict,
) -> None:
    """Import legacy fixture into fresh store and verify parity."""
    # Import
    report = await import_legacy_sqlite(LEGACY_DB, target_store)

    # Verify import report
    assert not report.errors, f"Import errors: {report.errors}"
    assert report.duration_s > 0

    # Verify per-table counts match manifest
    for table, info in manifest["tables"].items():
        expected = info["row_count"]
        actual = report.tables_imported.get(table, 0)
        assert actual == expected, (
            f"Table {table}: expected {expected} rows, got {actual}"
        )

    # Run parity assertion
    parity = await verify_import_parity(LEGACY_DB, target_store, manifest)

    # Report details on failure
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


@pytest.mark.asyncio
async def test_import_preserves_job_ids(
    target_store: SQLiteStore,
) -> None:
    """Verify legacy job IDs are preserved after import."""
    await import_legacy_sqlite(LEGACY_DB, target_store)

    # Read imported jobs
    rows = await target_store.read_all_rows("jobs")
    imported_ids = {row["id"] for row in rows}

    # Legacy fixture has jobs 1-20
    for i in range(1, 21):
        assert i in imported_ids, f"Job ID {i} not preserved"


@pytest.mark.asyncio
async def test_import_report_counts(
    target_store: SQLiteStore,
    manifest: dict,
) -> None:
    """Verify ImportReport table counts match manifest."""
    report = await import_legacy_sqlite(LEGACY_DB, target_store)

    assert "jobs" in report.tables_imported
    assert report.tables_imported["jobs"] == manifest["tables"]["jobs"]["row_count"]
    expected_eval = manifest["tables"]["evaluations"]["row_count"]
    assert report.tables_imported["evaluations"] == expected_eval


@pytest.mark.asyncio
async def test_import_state_key_mapping(
    target_store: SQLiteStore,
) -> None:
    """Verify schema_version is mapped to legacy_schema_version."""
    await import_legacy_sqlite(LEGACY_DB, target_store)

    state_rows = await target_store.read_all_rows("state")
    keys = {row["key"] for row in state_rows}

    # schema_version should be remapped to legacy_schema_version
    assert "legacy_schema_version" in keys
    # The original schema_version key should NOT be in the imported data
    # (it gets remapped)
    legacy_version = next(
        row["value"] for row in state_rows if row["key"] == "legacy_schema_version"
    )
    assert legacy_version == "16"


@pytest.mark.asyncio
async def test_import_column_mapping(
    target_store: SQLiteStore,
) -> None:
    """Verify legacy column names are mapped to new schema names."""
    await import_legacy_sqlite(LEGACY_DB, target_store)

    # Check evaluations have new column names (not legacy block_* names)
    evals = await target_store.read_all_rows("evaluations")
    if evals:
        first_eval = evals[0]
        # New schema columns should be present
        assert "stage_a_timing_eligible" in first_eval
        assert "stage_a_resume_hash" in first_eval
        assert "stage_b_verdict_json" in first_eval
        assert "stage_b_summary_json" in first_eval
        assert "stage_b_fit_json" in first_eval
        assert "stage_b_hooks_json" in first_eval

    # Check applied has new column names
    applied = await target_store.read_all_rows("applied")
    if applied:
        first_applied = applied[0]
        assert "verdict_snapshot" in first_applied
        assert "fit_snapshot" in first_applied
        assert "hooks_snapshot" in first_applied


@pytest.mark.asyncio
async def test_import_null_location_coalesced(
    target_store: SQLiteStore,
) -> None:
    """Verify NULL locations are coalesced to empty string."""
    await import_legacy_sqlite(LEGACY_DB, target_store)

    jobs = await target_store.read_all_rows("jobs")
    for job in jobs:
        assert job["location"] is not None, (
            f"Job {job['id']} has NULL location after import"
        )


@pytest.mark.asyncio
async def test_triggers_reenabled_after_import(
    target_store: SQLiteStore,
) -> None:
    """Verify triggers are re-enabled after import completes."""
    await import_legacy_sqlite(LEGACY_DB, target_store)

    # Insert a new job after import -- trigger should auto-seed status
    job = JobPosting(
        platform="test",
        canonical_id="post-import-001",
        url="https://example.com/post-import",
        title="Post Import Job",
        company="Test Corp",
        location="Remote",
        discovered_at=datetime.now(tz=UTC),
    )
    result = await target_store.save_job(job)

    # Trigger should have created a status row
    status = await target_store.get_status(result.job_id)
    assert status is not None
    assert status.status == "new"


@pytest.mark.asyncio
async def test_triggers_reenabled_on_failure(tmp_path: Path) -> None:
    """Verify triggers are re-enabled even when import fails."""
    store = SQLiteStore(tmp_path / "fail_test.db")
    await store.connect()

    try:
        # Patch bulk_insert_evaluations to raise mid-import
        async def failing_insert(_rows: list) -> int:
            raise RuntimeError("Simulated import failure")

        with (
            patch.object(
                store,
                "bulk_insert_evaluations",
                side_effect=failing_insert,
            ),
            pytest.raises(RuntimeError, match="Simulated import failure"),
        ):
            await import_legacy_sqlite(LEGACY_DB, store)

        # Triggers should still be re-enabled
        # Verify by inserting a job and checking status auto-seed
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
    finally:
        await store.close()
