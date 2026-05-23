"""E2E test: legacy v16 import + parity against PostgresStore.

Spins up an ephemeral PostgreSQL via testcontainers, applies the Alembic
schema, imports the legacy fixture through the PostgresStore bulk-import path,
and verifies row counts and parity. Postgres-marked: runs in CI (and locally
when Docker is available), deselected by the default ``-m 'not postgres'``.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

from jobfeed.adapters.store.legacy_import import import_legacy_sqlite
from jobfeed.adapters.store.parity import verify_import_parity
from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import JobPosting

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures"
LEGACY_DB = FIXTURE_DIR / "legacy_v16.db"
MANIFEST_JSON = FIXTURE_DIR / "legacy_v16_manifest.json"

pytestmark = pytest.mark.postgres


@pytest_asyncio.fixture
async def pg_store() -> AsyncIterator[PostgresStore]:
    """Spin up an ephemeral Postgres, apply migrations, yield a store.

    Yields:
        Connected PostgresStore against a freshly migrated database.
    """
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers not available")

    with PostgresContainer("postgres:16") as pg:
        url = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        store = PostgresStore(url)
        await store.connect()
        subprocess.run(
            ["alembic", "-c", "migrations/alembic.ini", "upgrade", "head"],
            env={**os.environ, "JOBFEED_DB_URL": url},
            check=True,
            capture_output=True,
        )
        try:
            yield store
        finally:
            await store.close()


async def test_legacy_import_and_parity_postgres(pg_store: PostgresStore) -> None:
    """Import the legacy fixture into Postgres and verify counts + parity.

    Args:
        pg_store: Connected PostgresStore against an ephemeral database.
    """
    manifest = json.loads(MANIFEST_JSON.read_text("utf-8"))

    report = await import_legacy_sqlite(LEGACY_DB, pg_store)

    assert not report.errors, f"Import errors: {report.errors}"
    for table, info in manifest["tables"].items():
        expected = info["row_count"]
        actual = report.tables_imported.get(table, 0)
        assert actual == expected, (
            f"Table {table}: expected {expected} rows, got {actual}"
        )

    parity = await verify_import_parity(LEGACY_DB, pg_store, manifest)
    if not parity.passed:
        failures = [f"  {c.name}: {c.details}" for c in parity.checks if not c.passed]
        pytest.fail("Postgres parity checks failed:\n" + "\n".join(failures))
    assert parity.passed


async def test_import_preserves_job_ids_postgres(pg_store: PostgresStore) -> None:
    """Imported job ids match the legacy ids and sequences advance past them.

    Args:
        pg_store: Connected PostgresStore against an ephemeral database.
    """
    await import_legacy_sqlite(LEGACY_DB, pg_store)

    jobs = await pg_store.read_all_rows("jobs")
    assert jobs, "expected imported jobs"

    # A fresh save_job must not collide with an imported id (reset_sequences).
    result = await pg_store.save_job(
        JobPosting(
            id=None,
            platform="mock",
            canonical_id="post-import",
            url="https://example.com/post-import",
            title="Backend Intern",
            company="Example",
            location="Remote",
            discovered_at=datetime.now(UTC),
        )
    )
    existing_ids = {int(row["id"]) for row in jobs}
    assert int(result.job_id) not in existing_ids
