"""Independent SQLite persistence for versioned unified evaluation results."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timedelta

from jobfeed.adapters.store._sqlite_capability_support import (
    _fetch_row,
    _fetch_rows,
    _hydrate_job,
    _immediate_transaction,
    _require_utc_timestamp,
    _validate_limit,
)
from jobfeed.adapters.store._sqlite_evaluations import _insert_scored_history
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.domain.models import JobPosting

_RESULT_COLUMNS = (
    "job_id, status, eligibility_status, match_tier, match_score, "
    "ats_visibility_score, result_json, evaluator_version, model, prompt_hash, "
    "resume_hash, cost_usd, evaluated_at, updated_at, error, error_count"
)
_VALID_CORPORA = frozenset({"unrated", "all", "failed"})
_STALE_CLAIM_AFTER = timedelta(hours=1)


async def _claim_pending_evaluations(  # noqa: PLR0913 - mirrors public store API
    lifecycle: SqliteLifecycle,
    *,
    evaluator_version: str,
    corpus: str,
    limit: int,
    max_days: int | None,
    now: datetime,
) -> list[JobPosting]:
    sql, params = _pending_evaluation_query(
        evaluator_version=evaluator_version,
        corpus=corpus,
        limit=limit,
        max_days=max_days,
        now=now,
    )
    timestamp = _require_utc_timestamp(now)
    async with (
        lifecycle.connection() as connection,
        _immediate_transaction(connection),
    ):
        rows = await _fetch_rows(connection, sql, params)
        for row in rows:
            await connection.execute(
                """INSERT INTO evaluation_results(
                       job_id,status,evaluator_version,updated_at
                   ) VALUES(?,'in_progress',?,?)
                   ON CONFLICT(job_id) DO UPDATE SET
                       status='in_progress', eligibility_status=NULL,
                       match_tier=NULL, match_score=NULL,
                       ats_visibility_score=NULL, result_json=NULL,
                       evaluator_version=excluded.evaluator_version,
                       model=NULL, prompt_hash=NULL, resume_hash=NULL,
                       cost_usd=NULL, evaluated_at=NULL,
                       updated_at=excluded.updated_at,
                       error=CASE WHEN evaluation_results.status='error'
                                  THEN evaluation_results.error ELSE NULL END,
                       error_count=CASE
                           WHEN evaluation_results.evaluator_version=
                                excluded.evaluator_version
                           THEN evaluation_results.error_count ELSE 0 END""",
                (int(row["id"]), evaluator_version, timestamp),
            )
    return [_hydrate_job(row) for row in rows]


async def _preview_pending_evaluations(  # noqa: PLR0913 - mirrors public store API
    lifecycle: SqliteLifecycle,
    *,
    evaluator_version: str,
    corpus: str,
    limit: int,
    max_days: int | None,
    now: datetime,
) -> list[JobPosting]:
    sql, params = _pending_evaluation_query(
        evaluator_version=evaluator_version,
        corpus=corpus,
        limit=limit,
        max_days=max_days,
        now=now,
    )
    async with lifecycle.connection() as connection:
        rows = await _fetch_rows(connection, sql, params)
    return [_hydrate_job(row) for row in rows]


def _pending_evaluation_query(
    *,
    evaluator_version: str,
    corpus: str,
    limit: int,
    max_days: int | None,
    now: datetime,
) -> tuple[str, list[object]]:
    _validate_request(evaluator_version, corpus, limit, max_days)
    conditions = ["jobs.closed_at IS NULL", "jobs.jd_text IS NOT NULL"]
    params: list[object] = []
    stale_before = _require_utc_timestamp(now - _STALE_CLAIM_AFTER)
    if corpus == "failed":
        conditions.append("evaluation_results.status='error'")
    elif corpus == "unrated":
        conditions.append(
            "(evaluation_results.job_id IS NULL "
            "OR evaluation_results.evaluator_version<>? "
            "OR evaluation_results.status='error' "
            "OR (evaluation_results.status='in_progress' "
            "AND evaluation_results.updated_at<?))"
        )
        params.extend((evaluator_version, stale_before))
    else:
        conditions.append(
            "(evaluation_results.job_id IS NULL "
            "OR evaluation_results.evaluator_version<>? "
            "OR evaluation_results.status<>'in_progress' "
            "OR evaluation_results.updated_at<?)"
        )
        params.extend((evaluator_version, stale_before))
    if max_days is not None:
        conditions.append("jobs.discovered_at>=?")
        params.append(_require_utc_timestamp(now - timedelta(days=max_days)))
    params.append(limit)
    sql = (
        "SELECT jobs.* FROM jobs LEFT JOIN evaluation_results "
        "ON evaluation_results.job_id=jobs.id WHERE "
        + " AND ".join(conditions)
        + " ORDER BY jobs.discovered_at DESC, jobs.id DESC LIMIT ?"
    )
    return sql, params


async def _save_evaluation(
    lifecycle: SqliteLifecycle,
    job_id: str,
    result: object,
    *,
    now: datetime,
) -> None:
    numeric_id = int(job_id)
    evaluator_version = _required_text(result, "evaluator_version")
    timestamp = _require_utc_timestamp(now)
    result_json = _encode_json(_required_value(result, "result_json"))
    values = (
        numeric_id,
        _required_text(result, "eligibility_status"),
        _required_text(result, "match_tier"),
        _required_value(result, "match_score"),
        _required_value(result, "ats_visibility_score"),
        result_json,
        evaluator_version,
        _optional_text(result, "model"),
        _optional_text(result, "prompt_hash"),
        _optional_text(result, "resume_hash"),
        getattr(result, "cost_usd", None),
        timestamp,
        timestamp,
    )
    async with (
        lifecycle.connection() as connection,
        _immediate_transaction(connection),
    ):
        await connection.execute(
            """INSERT INTO evaluation_results(
                   job_id,status,eligibility_status,match_tier,match_score,
                   ats_visibility_score,result_json,evaluator_version,model,
                   prompt_hash,resume_hash,cost_usd,evaluated_at,updated_at,
                   error,error_count
               ) VALUES(?, 'completed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0)
               ON CONFLICT(job_id) DO UPDATE SET
                   status='completed',
                   eligibility_status=excluded.eligibility_status,
                   match_tier=excluded.match_tier,
                   match_score=excluded.match_score,
                   ats_visibility_score=excluded.ats_visibility_score,
                   result_json=excluded.result_json,
                   evaluator_version=excluded.evaluator_version,
                   model=excluded.model, prompt_hash=excluded.prompt_hash,
                   resume_hash=excluded.resume_hash, cost_usd=excluded.cost_usd,
                   evaluated_at=excluded.evaluated_at,
                   updated_at=excluded.updated_at, error=NULL""",
            values,
        )
        cursor = await connection.execute(
            "UPDATE job_status SET status='scored', last_status_change_at=? "
            "WHERE job_id=? AND status='new' RETURNING job_id",
            (timestamp, numeric_id),
        )
        changed = await cursor.fetchone()
        await cursor.close()
        if changed is not None:
            await _insert_scored_history(connection, numeric_id, timestamp)


async def _save_evaluation_error(
    lifecycle: SqliteLifecycle,
    job_id: str,
    error: str,
    evaluator_version: str,
    *,
    now: datetime,
) -> None:
    if not evaluator_version:
        raise ValueError("evaluator_version must not be empty")
    timestamp = _require_utc_timestamp(now)
    async with (
        lifecycle.connection() as connection,
        _immediate_transaction(connection),
    ):
        await connection.execute(
            """INSERT INTO evaluation_results(
                   job_id,status,evaluator_version,updated_at,error,error_count
               ) VALUES(?,'error',?,?,?,1)
               ON CONFLICT(job_id) DO UPDATE SET
                   status='error', eligibility_status=NULL, match_tier=NULL,
                   match_score=NULL, ats_visibility_score=NULL, result_json=NULL,
                   evaluator_version=excluded.evaluator_version,
                   model=NULL, prompt_hash=NULL, resume_hash=NULL, cost_usd=NULL,
                   evaluated_at=NULL, updated_at=excluded.updated_at,
                   error=excluded.error,
                   error_count=CASE
                       WHEN evaluation_results.evaluator_version=
                            excluded.evaluator_version
                       THEN evaluation_results.error_count+1 ELSE 1 END""",
            (int(job_id), evaluator_version, timestamp, error),
        )


async def _release_evaluation_claim(
    lifecycle: SqliteLifecycle,
    job_id: str,
    evaluator_version: str,
    *,
    now: datetime,
) -> None:
    timestamp = _require_utc_timestamp(now)
    params = (timestamp, int(job_id), evaluator_version)
    async with (
        lifecycle.connection() as connection,
        _immediate_transaction(connection),
    ):
        await connection.execute(
            """UPDATE evaluation_results SET status='error', updated_at=?
               WHERE job_id=? AND evaluator_version=? AND status='in_progress'
                 AND error IS NOT NULL""",
            params,
        )
        await connection.execute(
            """DELETE FROM evaluation_results
               WHERE job_id=? AND evaluator_version=? AND status='in_progress'
                 AND error IS NULL""",
            (int(job_id), evaluator_version),
        )


async def _get_current_evaluation(
    lifecycle: SqliteLifecycle,
    job_id: str,
) -> dict[str, object] | None:
    async with lifecycle.connection() as connection:
        row = await _fetch_row(
            connection,
            f"SELECT {_RESULT_COLUMNS} FROM evaluation_results WHERE job_id=?",
            (int(job_id),),
        )
    if row is None:
        return None
    result = {
        # aiosqlite.Row iteration yields values, so explicit keys are required.
        key: row[key]
        for key in row.keys()  # noqa: SIM118
    }
    result["job_id"] = str(result["job_id"])
    raw_json = result["result_json"]
    result["result_json"] = None if raw_json is None else json.loads(str(raw_json))
    return result


def _validate_request(
    evaluator_version: str,
    corpus: str,
    limit: int,
    max_days: int | None,
) -> None:
    if not evaluator_version:
        raise ValueError("evaluator_version must not be empty")
    if corpus not in _VALID_CORPORA:
        raise ValueError("corpus must be 'unrated', 'all', or 'failed'")
    _validate_limit(limit)
    if max_days is not None and (isinstance(max_days, bool) or max_days < 0):
        raise ValueError("max_days must be a non-negative integer")


def _required_text(result: object, name: str) -> str:
    value = getattr(result, name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must not be empty")
    return value


def _required_value(result: object, name: str) -> object:
    try:
        return getattr(result, name)
    except AttributeError as exc:
        raise ValueError(f"{name} is required") from exc


def _optional_text(result: object, name: str) -> str | None:
    value = getattr(result, name, None)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{name} must be text or None")
    return value


def _encode_json(value: object) -> str:
    if isinstance(value, str):
        value = json.loads(value)
    if isinstance(value, Mapping):
        value = dict(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


__all__ = []
