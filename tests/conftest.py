"""Shared pytest fixtures for Jobfeed tests.

PostgreSQL is the only supported store backend. PG-backed fixtures resolve a
DSN once per session — preferring ``PGTEST_DSN`` (a CI service or local
Postgres), otherwise starting a single testcontainers Postgres for the run.
When neither is available the PG fixtures skip (so the pure-unit suite still
runs without Docker), unless ``JOBFEED_REQUIRE_POSTGRES=1`` forces a failure.

Schema is migrated **once per session** (``migrated_pg_url``); each PG-backed
test then resets only the *data* via a fast ``TRUNCATE ... RESTART IDENTITY
CASCADE`` (``_truncate_all_data``) rather than re-running the full Alembic
chain. Tests that exercise the migration/DDL process itself opt into the
slower full-reset fixtures (``blank_migrated_dsn`` / ``empty_schema_dsn``).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
import structlog

from jobfeed.adapters.store.postgres import PostgresStore
from tests.support.store_contract_control import PostgresStoreContractControl


def _alembic_upgrade(url: str) -> None:
    """Apply the Alembic schema to ``url`` (synchronous subprocess).

    Args:
        url: PostgreSQL DSN.
    """
    subprocess.run(
        ["alembic", "-c", "migrations/alembic.ini", "upgrade", "head"],
        env={**os.environ, "JOBFEED_DB_URL": url},
        check=True,
        capture_output=True,
    )


async def _reset_pg_schema(url: str) -> None:
    """Drop and recreate the public schema (full DDL reset).

    Args:
        url: PostgreSQL DSN.
    """
    import asyncpg

    conn = await asyncpg.connect(url)
    try:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await conn.close()


async def _truncate_all_data(url: str) -> None:
    """Fast per-test reset: truncate every user table, preserving schema.

    Discovers the table list dynamically (so new migrations are picked up
    without editing this helper) and truncates them all in a single statement.
    ``RESTART IDENTITY`` resets serial sequences so id-from-1 assertions hold;
    ``CASCADE`` satisfies foreign keys regardless of truncation order. The
    Alembic bookkeeping table is excluded so the migrated schema persists.

    Args:
        url: PostgreSQL DSN.
    """
    import asyncpg

    conn = await asyncpg.connect(url)
    try:
        rows = await conn.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_type = 'BASE TABLE'
              AND table_name <> 'alembic_version'
            """
        )
        tables = [row["table_name"] for row in rows]
        if not tables:
            return
        quoted = ", ".join(f'"{name}"' for name in tables)
        await conn.execute(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE")
    finally:
        await conn.close()


def _reset_and_migrate_sync(url: str) -> None:
    """Reset the schema and reapply migrations from synchronous code.

    Used by full-reset sync fixtures where no event loop is running, so
    ``asyncio.run`` is safe.

    Args:
        url: PostgreSQL DSN.
    """
    asyncio.run(_reset_pg_schema(url))
    _alembic_upgrade(url)


@pytest.fixture(autouse=True)
def reset_structlog_state() -> Iterator[None]:
    """Reset global structlog configuration and context around each test."""
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()
    yield
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    """Resolve a PostgreSQL DSN for the whole test session.

    Yields:
        A reachable PostgreSQL DSN.
    """
    dsn = os.environ.get("PGTEST_DSN")
    if dsn:
        yield dsn
        return

    require_pg = (
        os.environ.get("JOBFEED_REQUIRE_POSTGRES") == "1"
        or os.environ.get("PGTEST_REQUIRE") == "1"
    )
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        if require_pg:
            pytest.fail("testcontainers required but not installed")
        pytest.skip("PostgreSQL unavailable (set PGTEST_DSN or install testcontainers)")

    try:
        container = PostgresContainer("postgres:16")
        container.start()
    except Exception as exc:  # Docker missing/unhealthy → skip unless forced.
        if require_pg:
            pytest.fail(f"could not start Postgres container: {exc}")
        pytest.skip(f"PostgreSQL container unavailable: {exc}")

    try:
        yield container.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql://"
        )
    finally:
        container.stop()


@pytest.fixture(scope="session")
def migrated_pg_url(pg_url: str) -> str:
    """Migrate the session database to head exactly once and return its DSN.

    The full Alembic chain (and the ``DROP SCHEMA`` that precedes it) runs a
    single time per test session; per-test isolation is then provided by the
    fast ``_truncate_all_data`` data reset rather than re-migrating. All schema
    objects (tables, the ``trg_jobs_seed_status`` trigger, indexes) are built
    once here and persist for the whole run.

    Args:
        pg_url: Session PostgreSQL DSN.

    Returns:
        DSN of the migrated session database.
    """
    _reset_and_migrate_sync(pg_url)
    return pg_url


async def _fresh_connected_store(url: str) -> PostgresStore:
    """Truncate all data and return a connected store against empty tables.

    The schema is assumed already migrated (``migrated_pg_url``); only data is
    reset, which is dramatically faster than a per-test re-migration.

    Args:
        url: PostgreSQL DSN of the migrated session database.

    Returns:
        Connected PostgresStore against an empty (but migrated) database.
    """
    await _truncate_all_data(url)
    store = PostgresStore(url)
    await store.connect()
    return store


@pytest_asyncio.fixture
async def store(migrated_pg_url: str) -> AsyncIterator[PostgresStore]:
    """Yield a connected PostgresStore against freshly truncated tables.

    Args:
        migrated_pg_url: Session PostgreSQL DSN (migrated once per session).

    Yields:
        Connected PostgresStore.
    """
    s = await _fresh_connected_store(migrated_pg_url)
    try:
        yield s
    finally:
        await s.close()


@pytest_asyncio.fixture
async def contract_store(migrated_pg_url: str) -> AsyncIterator[PostgresStore]:
    """Yield a connected store for the shared store-contract suite.

    Args:
        migrated_pg_url: Session PostgreSQL DSN (migrated once per session).

    Yields:
        Connected store implementing the JobStore protocol.
    """
    s = await _fresh_connected_store(migrated_pg_url)
    try:
        yield s
    finally:
        await s.close()


@pytest.fixture
def contract_control(migrated_pg_url: str) -> PostgresStoreContractControl:
    """Yield backend setup controls for the shared store contract."""
    return PostgresStoreContractControl(migrated_pg_url)


@pytest.fixture
def fresh_pg_dsn(migrated_pg_url: str) -> str:
    """Truncate the session database and return its DSN (sync).

    For CLI runner tests that build their own store from a config file. The
    schema persists from the session-scoped migration; only data is reset, via
    ``asyncio.run`` since no event loop is running in these sync tests.

    Args:
        migrated_pg_url: Session PostgreSQL DSN (migrated once per session).

    Returns:
        DSN of a migrated, data-empty database.
    """
    asyncio.run(_truncate_all_data(migrated_pg_url))
    return migrated_pg_url


@pytest.fixture
def blank_migrated_dsn(pg_url: str) -> str:
    """Drop the schema and reapply the full Alembic chain, then return its DSN.

    For tests that exercise the migration/DDL process itself and therefore need
    a genuinely freshly-migrated schema (not a pre-migrated-then-truncated one).
    Slower than ``fresh_pg_dsn`` — use only when the schema build is under test.

    Args:
        pg_url: Session PostgreSQL DSN.

    Returns:
        DSN of a freshly dropped-and-migrated database.
    """
    _reset_and_migrate_sync(pg_url)
    return pg_url


@pytest.fixture
def empty_schema_dsn(pg_url: str) -> str:
    """Drop and recreate an empty public schema (no migrations), return its DSN.

    For tests whose command/code under test is responsible for creating the
    schema. Leaves ``public`` empty with no tables and no ``alembic_version``.

    Args:
        pg_url: Session PostgreSQL DSN.

    Returns:
        DSN of a database with an empty public schema.
    """
    asyncio.run(_reset_pg_schema(pg_url))
    return pg_url
