"""A busy writer is retried without replaying an entered transaction body."""

import asyncio
import sqlite3

import aiosqlite
import pytest

from jobfeed.adapters.store import _sqlite_capability_support as support
from jobfeed.adapters.store._sqlite_capability_support import _immediate_transaction
from jobfeed.adapters.store._sqlite_real_job_identity import _aliases


async def test_busy_begin_recovers_after_original_timeout(tmp_path):
    path = tmp_path / "busy.sqlite"
    async with aiosqlite.connect(path, isolation_level=None) as owner:
        await owner.execute("CREATE TABLE evidence(value TEXT)")
        await owner.execute("BEGIN IMMEDIATE")
        async with aiosqlite.connect(path, isolation_level=None) as waiter:
            await waiter.execute("PRAGMA busy_timeout=10")

            async def release():
                await asyncio.sleep(0.1)
                await owner.rollback()

            task = asyncio.create_task(release())
            try:
                async with _immediate_transaction(waiter):
                    await waiter.execute("INSERT INTO evidence VALUES('once')")
            finally:
                await task
            assert await (
                await waiter.execute("SELECT * FROM evidence")
            ).fetchall() == [("once",)]


async def test_non_busy_body_failure_rolls_back_without_retry(tmp_path):
    async with aiosqlite.connect(
        tmp_path / "failure.sqlite", isolation_level=None
    ) as db:
        await db.execute("CREATE TABLE evidence(value TEXT)")
        with pytest.raises(sqlite3.OperationalError, match="missing"):
            async with _immediate_transaction(db):
                await db.execute("INSERT INTO evidence VALUES('rollback')")
                await db.execute("INSERT INTO missing VALUES(1)")
        assert not db.in_transaction
        assert await (await db.execute("SELECT * FROM evidence")).fetchall() == []


async def test_busy_budget_exhaustion_never_enters_body(tmp_path, monkeypatch):

    monkeypatch.setattr(support, "_BUSY_ATTEMPTS", 2)
    monkeypatch.setattr(support, "_BUSY_RETRY_DELAY", 0)
    path = tmp_path / "exhausted.sqlite"
    async with (
        aiosqlite.connect(path, isolation_level=None) as owner,
        aiosqlite.connect(path, isolation_level=None) as waiter,
    ):
        await owner.execute("BEGIN IMMEDIATE")
        await waiter.execute("PRAGMA busy_timeout=1")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            async with _immediate_transaction(waiter):
                pytest.fail("must not enter a transaction without ownership")
        assert not waiter.in_transaction
        await owner.rollback()


async def test_cancelling_busy_wait_does_not_retry_or_keep_lock(tmp_path):
    path = tmp_path / "cancel.sqlite"
    async with (
        aiosqlite.connect(path, isolation_level=None) as owner,
        aiosqlite.connect(path, isolation_level=None) as waiter,
    ):
        await owner.execute("BEGIN IMMEDIATE")
        await waiter.execute("PRAGMA busy_timeout=1")

        async def wait():
            async with _immediate_transaction(waiter):
                pytest.fail("owner still holds writer lock")

        task = asyncio.create_task(wait())
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await owner.rollback()
        assert not waiter.in_transaction
        async with _immediate_transaction(waiter):
            pass


async def test_alias_lookup_uses_index_and_exact_namespace(tmp_path):

    async with aiosqlite.connect(tmp_path / "aliases.sqlite") as db:
        await db.execute("CREATE TABLE state(key TEXT PRIMARY KEY, value TEXT)")
        await db.executemany(
            "INSERT INTO state VALUES(?,?)",
            [
                ("workday-alias:salesforce:JR355250-1", "JR355250"),
                ("WORKDAY-ALIAS:other:R1-1", "R1"),
                ("other:receipt", "unrelated"),
            ],
        )
        statements = []
        await db.set_trace_callback(statements.append)
        assert await _aliases(db) == {("salesforce", "JR355250-1"): "JR355250"}
        query = next(sql for sql in statements if sql.startswith("SELECT"))
        plan = await (await db.execute("EXPLAIN QUERY PLAN " + query)).fetchall()
        assert all("SCAN state" not in row[3] for row in plan)


async def test_cancelled_queued_begin_rolls_back_late_acquisition(tmp_path):
    path = tmp_path / "queued-cancel.sqlite"
    async with (
        aiosqlite.connect(path, isolation_level=None) as owner,
        aiosqlite.connect(path, isolation_level=None) as waiter,
    ):
        await owner.execute("BEGIN IMMEDIATE")
        await waiter.execute("PRAGMA busy_timeout=500")
        task = asyncio.create_task(support._begin_immediate(waiter))
        await asyncio.sleep(0.02)
        task.cancel()
        await owner.rollback()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not waiter.in_transaction
        await owner.execute("BEGIN IMMEDIATE")
        await owner.rollback()
