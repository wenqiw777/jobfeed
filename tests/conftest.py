"""Shared pytest fixtures for Jobfeed tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
import structlog

from jobfeed.adapters.store.sqlite import SQLiteStore


@pytest.fixture(autouse=True)
def reset_structlog_state() -> Iterator[None]:
    """Reset global structlog configuration and context around each test."""
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()
    yield
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()


@pytest_asyncio.fixture(params=["sqlite"])
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
    else:
        pytest.skip(
            f"backend {request.param!r} not yet implemented"
        )
