"""Lifecycle contracts for the PostgreSQL store adapter."""

from __future__ import annotations

from typing import Any, cast

import pytest

from jobfeed.adapters.store.postgres import PostgresStore

_JOB_ID = 7


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


class _AcquireDouble:
    """Async context manager returned by an asyncpg pool acquire call."""

    def __init__(self, connection: object) -> None:
        self._connection = connection

    async def __aenter__(self) -> object:
        return self._connection

    async def __aexit__(self, *args: object) -> None:
        del args


class _UnifiedReadConnection:
    """Connection double for one unified evaluation read."""

    def __init__(self, row: dict[str, object] | None) -> None:
        self.row = row
        self.calls: list[tuple[str, int]] = []

    async def fetchrow(self, sql: str, job_id: int) -> dict[str, object] | None:
        self.calls.append((sql, job_id))
        return self.row


class _UnifiedReadPool:
    """Pool double exposing the unified-read connection."""

    def __init__(self, connection: _UnifiedReadConnection) -> None:
        self.connection = connection

    def acquire(self) -> _AcquireDouble:
        return _AcquireDouble(self.connection)


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


async def test_get_current_evaluation_decodes_json_and_stringifies_job_id() -> None:
    """PostgreSQL detail reads return the same canonical shape as SQLite."""
    connection = _UnifiedReadConnection(
        {
            "job_id": _JOB_ID,
            "status": "completed",
            "match_score": 20,
            "match_tier": "weak_match",
            "result_json": '{"summary":"Canonical summary."}',
        }
    )
    store = PostgresStore("postgresql://unused")
    store._pool = cast(Any, _UnifiedReadPool(connection))

    result = await store.get_current_evaluation(str(_JOB_ID))

    assert result is not None
    assert result["job_id"] == str(_JOB_ID)
    assert result["result_json"] == {"summary": "Canonical summary."}
    assert connection.calls[0][1] == _JOB_ID
    assert "FROM evaluation_results" in connection.calls[0][0]


async def test_get_current_evaluation_returns_none_when_missing() -> None:
    connection = _UnifiedReadConnection(None)
    store = PostgresStore("postgresql://unused")
    store._pool = cast(Any, _UnifiedReadPool(connection))

    assert await store.get_current_evaluation("404") is None
