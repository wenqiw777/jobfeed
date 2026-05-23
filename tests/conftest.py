"""Shared pytest fixtures for Jobfeed tests."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
import structlog

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.adapters.store.sqlite import SQLiteStore


async def _migrated_pg_store(url: str) -> PostgresStore:
    """Connect a PostgresStore at url and apply the Alembic schema.

    Args:
        url: PostgreSQL DSN.

    Returns:
        Connected, migrated PostgresStore.
    """
    import subprocess

    store = PostgresStore(url)
    await store.connect()
    subprocess.run(
        ["alembic", "-c", "migrations/alembic.ini", "upgrade", "head"],
        env={**os.environ, "JOBFEED_DB_URL": url},
        check=True,
        capture_output=True,
    )
    return store


async def _reset_pg_schema(url: str) -> None:
    """Drop and recreate the public schema for per-test isolation.

    Used when reusing a long-lived Postgres (e.g. the CI service) so each test
    starts from an empty schema without launching a new container.

    Args:
        url: PostgreSQL DSN.
    """
    import asyncpg

    conn = await asyncpg.connect(url)
    try:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await conn.close()


@pytest.fixture(autouse=True)
def reset_structlog_state() -> Iterator[None]:
    """Reset global structlog configuration and context around each test."""
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()
    yield
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()


@pytest_asyncio.fixture(
    params=[
        "sqlite",
        pytest.param("postgres", marks=pytest.mark.postgres),
    ],
)
async def contract_store(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> AsyncIterator:
    """Yield a connected store instance for contract tests.

    Args:
        request: Pytest fixture request with parametrization.
        tmp_path: Temporary directory for database files.

    Yields:
        Connected store implementing the JobStore protocol.
    """
    if request.param == "sqlite":
        s = SQLiteStore(tmp_path / "contract.db")
        await s.connect()
        try:
            yield s
        finally:
            await s.close()
    elif request.param == "postgres":
        _require_pg = os.environ.get("JOBFEED_REQUIRE_POSTGRES") == "1"
        dsn = os.environ.get("JOBFEED_PG_DSN")
        if dsn:
            # Reuse a provisioned Postgres (e.g. the CI service) instead of
            # launching a container per test; reset the schema for isolation.
            await _reset_pg_schema(dsn)
            store = await _migrated_pg_store(dsn)
            try:
                yield store
            finally:
                await store.close()
            return
        try:
            from testcontainers.postgres import PostgresContainer
        except ImportError:
            if _require_pg:
                pytest.fail("testcontainers required but not installed")
            pytest.skip("testcontainers not available")

        with PostgresContainer("postgres:16") as pg:
            url = pg.get_connection_url().replace(
                "postgresql+psycopg2://",
                "postgresql://",
            )
            store = await _migrated_pg_store(url)
            try:
                yield store
            finally:
                await store.close()
    else:
        pytest.skip(f"backend {request.param!r} not implemented")
