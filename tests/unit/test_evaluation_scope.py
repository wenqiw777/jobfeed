"""Daily and latest-scan evaluation scope persistence."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from jobfeed.domain.models import PipelineRun
from jobfeed.services.evaluation_scope import (
    load_evaluation_scope_ids,
    load_real_job_scope_ids,
    persist_scan_insertions,
)


class _StateStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get_state(self, key: str) -> str | None:
        return self.values.get(key)

    async def set_state(self, key: str, value: str) -> None:
        self.values[key] = value

    async def resolve_real_job_ids(self, source_ids: list[str]) -> list[str]:
        parents = {"10": "5", "11": "5", "12": "8"}
        return list(dict.fromkeys(parents[source_id] for source_id in source_ids))


def _run(run_id: str, ids: list[str], day: int = 3) -> PipelineRun:
    run = PipelineRun(
        run_id=run_id,
        started_at=datetime(2026, 9, day, 12, tzinfo=UTC),
        source="all",
    )
    run.scan_inserted_job_ids = ids
    return run


@pytest.mark.asyncio
async def test_daily_scope_accumulates_multiple_scans_and_latest_stays_exact() -> None:
    store = _StateStore()

    await persist_scan_insertions(store, _run("scan-1", ["10", "11"]))
    await persist_scan_insertions(store, _run("scan-2", ["11", "12"]))

    assert await load_evaluation_scope_ids(store, "today", today=date(2026, 9, 3)) == [
        "10",
        "11",
        "12",
    ]
    assert await load_evaluation_scope_ids(
        store, "latest_scan", today=date(2026, 9, 3)
    ) == ["11", "12"]
    assert await load_real_job_scope_ids(store, "today", today=date(2026, 9, 3)) == [
        "5",
        "8",
    ]


@pytest.mark.asyncio
async def test_daily_scope_expires_without_reusing_yesterdays_ids() -> None:
    store = _StateStore()
    store.values["today_scan_inserted_job_ids"] = json.dumps(
        {"date": "2026-09-02", "job_ids": ["old"]}
    )

    assert await load_evaluation_scope_ids(store, "today", today=date(2026, 9, 3)) == []
