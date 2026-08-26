"""Lifecycle contracts for the PostgreSQL store adapter."""

from __future__ import annotations

from typing import Any, cast

import pytest

from jobfeed.adapters.store.postgres import PostgresStore


class _PoolDouble:
    """Minimal asyncpg pool double that records close calls."""

    def __init__(self, *, close_error: Exception | None = None) -> None:
        self.close_calls = 0
        self._close_error = close_error

    async def close(self) -> None:
        """Record closure and optionally raise the configured failure."""
        self.close_calls += 1
        if self._close_error is not None:
            raise self._close_error


async def test_connect_and_close_are_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated lifecycle calls create and close exactly one pool."""
    pool = _PoolDouble()
    create_calls = 0

    async def create_pool(*args: object, **kwargs: object) -> Any:
        del args, kwargs
        nonlocal create_calls
        create_calls += 1
        return pool

    monkeypatch.setattr(
        "jobfeed.adapters.store.postgres.asyncpg.create_pool",
        create_pool,
    )
    store = PostgresStore("postgresql://unused")

    await store.connect()
    await store.connect()
    await store.close()
    await store.close()

    assert create_calls == 1
    assert pool.close_calls == 1


async def test_operation_before_connect_and_close_before_connect() -> None:
    """Unopened operations fail clearly while closing unopened state is safe."""
    store = PostgresStore("postgresql://unused")

    await store.close()
    await store.close()
    with pytest.raises(RuntimeError, match="not connected"):
        await store.get_job("1")


async def test_failed_connect_leaves_store_disconnected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pool creation failure propagates without publishing partial state."""

    async def create_pool(*args: object, **kwargs: object) -> Any:
        del args, kwargs
        raise OSError("database unavailable")

    monkeypatch.setattr(
        "jobfeed.adapters.store.postgres.asyncpg.create_pool",
        create_pool,
    )
    store = PostgresStore("postgresql://unused")

    with pytest.raises(OSError, match="database unavailable"):
        await store.connect()
    with pytest.raises(RuntimeError, match="not connected"):
        store._get_pool()


async def test_failed_close_propagates_after_store_is_detached() -> None:
    """A close failure is reported and does not expose the failed pool again."""
    pool = _PoolDouble(close_error=OSError("close failed"))
    store = PostgresStore("postgresql://unused")
    store._pool = cast(Any, pool)

    with pytest.raises(OSError, match="close failed"):
        await store.close()

    with pytest.raises(RuntimeError, match="not connected"):
        store._get_pool()
    await store.close()
    assert pool.close_calls == 1
