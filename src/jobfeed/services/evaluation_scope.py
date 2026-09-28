"""Persist and load exact job-id scopes for evaluation runs."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date, datetime
from typing import Protocol, TypedDict

from jobfeed.domain.models import PipelineRun

LATEST_SCAN_KEY = "latest_scan_inserted_job_ids"
TODAY_SCAN_KEY = "today_scan_inserted_job_ids"


class EvaluationScopeStore(Protocol):
    """Small state-store surface needed by evaluation scopes."""

    async def get_state(self, key: str) -> str | None:
        """Read a persisted evaluation-scope state value.

        Args:
            key: Persistent state key.

        Returns:
            Stored state value, or None when the key is absent.
        """
        ...

    async def set_state(self, key: str, value: str) -> None:
        """Write a persisted evaluation-scope state value.

        Args:
            key: Persistent state key.
            value: State value to write.
        """
        ...

    async def resolve_real_job_ids(self, source_ids: list[str]) -> list[str]:
        """Resolve source posting IDs to their distinct canonical parents.

        Args:
            source_ids: Source posting IDs whose canonical parents are required.

        Returns:
            Distinct canonical IDs in first-source order.

        Raises:
            ValueError: If an ID is invalid or a requested source lacks a canonical
                parent.
        """
        ...


class EvaluationScopeCounts(TypedDict):
    """Serializable counts returned to the web UI."""

    date: str
    today: int
    latest_scan: int


async def persist_scan_insertions(
    store: EvaluationScopeStore,
    run: PipelineRun,
) -> None:
    """Record both the latest scan and today's accumulated insertions.

    Args:
        store: Store providing the state or posting reads required by this operation.
        run: Scan run containing newly inserted source IDs.
    """
    job_ids = _unique_strings(run.scan_inserted_job_ids)
    await store.set_state(
        LATEST_SCAN_KEY,
        _encode({"run_id": run.run_id, "job_ids": job_ids}),
    )

    scan_date = run.started_at.astimezone().date().isoformat()
    existing = _decode(await store.get_state(TODAY_SCAN_KEY))
    accumulated: list[str] = []
    if existing.get("date") == scan_date:
        accumulated = _job_ids(existing)
    await store.set_state(
        TODAY_SCAN_KEY,
        _encode(
            {"date": scan_date, "job_ids": _unique_strings([*accumulated, *job_ids])}
        ),
    )


async def load_evaluation_scope_ids(
    store: EvaluationScopeStore,
    scope: str,
    *,
    today: date | None = None,
) -> list[str]:
    """Load exact IDs for a bounded evaluation scope.

    Args:
        store: Store providing the state or posting reads required by this operation.
        scope: Bounded scope name: today or latest_scan.
        today: Local calendar date override; defaults to the current local date.

    Returns:
        Distinct inserted source IDs belonging to the selected scope.

    Raises:
        ValueError: If scope is neither today nor latest_scan.
    """
    if scope == "latest_scan":
        return _job_ids(_decode(await store.get_state(LATEST_SCAN_KEY)))
    if scope == "today":
        payload = _decode(await store.get_state(TODAY_SCAN_KEY))
        local_today = today or datetime.now().astimezone().date()
        return (
            _job_ids(payload) if payload.get("date") == local_today.isoformat() else []
        )
    raise ValueError(f"unknown evaluation scope: {scope!r}")


async def load_real_job_scope_ids(
    store: EvaluationScopeStore,
    scope: str,
    *,
    today: date | None = None,
) -> list[str]:
    """Resolve source insertions to distinct real jobs before paid claims.

    Args:
        store: Store providing the state or posting reads required by this operation.
        scope: Bounded scope name: today or latest_scan.
        today: Local calendar date override; defaults to the current local date.

    Returns:
        Distinct canonical IDs corresponding to the selected source scope.
    """
    source_ids = await load_evaluation_scope_ids(store, scope, today=today)
    return await store.resolve_real_job_ids(source_ids)


async def evaluation_scope_counts(
    store: EvaluationScopeStore,
    *,
    today: date | None = None,
) -> EvaluationScopeCounts:
    """Return exact bounded-scope counts for the evaluation dialog.

    Args:
        store: Store providing the state or posting reads required by this operation.
        today: Local calendar date override; defaults to the current local date.

    Returns:
        Local date and source insertion counts for today and the latest scan.
    """
    local_today = today or datetime.now().astimezone().date()
    today_ids = await load_evaluation_scope_ids(store, "today", today=local_today)
    latest_ids = await load_evaluation_scope_ids(
        store, "latest_scan", today=local_today
    )
    return {
        "date": local_today.isoformat(),
        "today": len(today_ids),
        "latest_scan": len(latest_ids),
    }


def _decode(raw: str | None) -> dict[str, object]:
    if raw is None:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _job_ids(payload: dict[str, object]) -> list[str]:
    values = payload.get("job_ids")
    return _unique_strings(values) if isinstance(values, list) else []


def _unique_strings(values: Iterable[object]) -> list[str]:
    return list(dict.fromkeys(value for value in values if isinstance(value, str)))


def _encode(payload: dict[str, object]) -> str:
    return json.dumps(payload, separators=(",", ":"))
