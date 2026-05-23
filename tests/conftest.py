"""Shared pytest fixtures for Jobfeed tests.

PostgreSQL is the only supported store backend. PG-backed fixtures resolve a
DSN once per session — preferring ``PGTEST_DSN`` (a CI service or local
Postgres), otherwise starting a single testcontainers Postgres for the run.
When neither is available the PG fixtures skip (so the pure-unit suite still
runs without Docker), unless ``PGTEST_REQUIRE=1`` forces a failure.
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
    """Drop and recreate the public schema for per-test isolation.

    Args:
        url: PostgreSQL DSN.
    """
    import asyncpg

    conn = await asyncpg.connect(url)
    try:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await conn.close()


def _reset_and_migrate_sync(url: str) -> None:
    """Reset the schema and reapply migrations from synchronous code.

    Used by sync fixtures (e.g. CLI runner tests) where no event loop is
    running, so ``asyncio.run`` is safe.

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

    require_pg = os.environ.get("PGTEST_REQUIRE") == "1"
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


async def _fresh_connected_store(url: str) -> PostgresStore:
    """Reset the schema, reapply migrations, and return a connected store.

    Args:
        url: PostgreSQL DSN.

    Returns:
        Connected PostgresStore against an empty, migrated database.
    """
    await _reset_pg_schema(url)
    _alembic_upgrade(url)
    store = PostgresStore(url)
    await store.connect()
    return store


@pytest_asyncio.fixture
async def store(pg_url: str) -> AsyncIterator[PostgresStore]:
    """Yield a connected PostgresStore against a freshly migrated schema.

    Args:
        pg_url: Session PostgreSQL DSN.

    Yields:
        Connected PostgresStore.
    """
    s = await _fresh_connected_store(pg_url)
    try:
        yield s
    finally:
        await s.close()


@pytest_asyncio.fixture
async def contract_store(pg_url: str) -> AsyncIterator[PostgresStore]:
    """Yield a connected store for the shared store-contract suite.

    Args:
        pg_url: Session PostgreSQL DSN.

    Yields:
        Connected store implementing the JobStore protocol.
    """
    s = await _fresh_connected_store(pg_url)
    try:
        yield s
    finally:
        await s.close()


@pytest.fixture
def fresh_pg_dsn(pg_url: str) -> str:
    """Reset + migrate the session database and return its DSN (sync).

    For CLI runner tests that build their own store from a config file.

    Args:
        pg_url: Session PostgreSQL DSN.

    Returns:
        DSN of a freshly migrated, empty database.
    """
    _reset_and_migrate_sync(pg_url)
    return pg_url
