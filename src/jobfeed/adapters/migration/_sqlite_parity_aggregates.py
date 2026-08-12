"""Reproduce PostgreSQL baseline aggregates from one SQLite snapshot."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from statistics import mean

import aiosqlite

from jobfeed.adapters.migration._pg_baseline_manifest import aggregate_manifest


async def capture_sqlite_aggregate_manifest(
    connection: aiosqlite.Connection, *, as_of_utc: str
) -> dict[str, object]:
    """Capture the frozen aggregate manifest at the source cutoff.

    Args:
        connection: Open target connection inside a read transaction.
        as_of_utc: Source manifest cutoff shared by every rolling query.

    Returns:
        Counts plus canonical aggregate hashes in source-manifest shape.

    Raises:
        ValueError: If the cutoff is not an aware UTC-compatible timestamp.
    """
    as_of = _parse_as_of(as_of_utc)
    cutoff = _utc_text(as_of - timedelta(days=30))
    date_cutoff = (as_of.date() - timedelta(days=30)).isoformat()
    pending_stage_a = await _scalar(
        connection,
        "SELECT COUNT(*) FROM jobs LEFT JOIN evaluations e ON e.job_id=jobs.id "
        "WHERE jobs.closed_at IS NULL "
        "AND (e.job_id IS NULL OR e.stage_a_status IS NULL "
        "OR (e.stage_a_status='error' AND e.stage_a_error_count < 3))",
    )
    pending_stage_b = await _scalar(
        connection,
        "SELECT COUNT(*) FROM jobs JOIN evaluations e ON e.job_id=jobs.id "
        "WHERE e.stage_a_status='completed' "
        "AND (e.stage_b_status IS NULL OR "
        "(e.stage_b_status='error' AND e.stage_b_error_count < 3))",
    )
    raw = {
        "as_of_utc": as_of_utc,
        "window_days": 30,
        "pending_stage_a": _integer(pending_stage_a),
        "pending_stage_b": _integer(pending_stage_b),
        "needs_attention": await _attention(connection, cutoff),
        "funnel": await _funnel(connection, cutoff),
        "daily_cost": await _dict_rows(
            connection,
            "SELECT day, spent_usd, calls, last_updated FROM cost_ledger "
            "WHERE day >= ? ORDER BY day DESC",
            (date_cutoff,),
        ),
        "llm_percentiles": await _llm_percentiles(connection, cutoff),
    }
    return aggregate_manifest(raw)


async def _attention(
    connection: aiosqlite.Connection, cutoff: str
) -> dict[str, object]:
    return {
        "enrich_errors": await _dict_rows(
            connection,
            "SELECT id AS job_id, title, company, enrich_error AS detail FROM jobs "
            "WHERE enrich_error IS NOT NULL AND discovered_at >= ? ORDER BY id",
            (cutoff,),
        ),
        "low_quality_scored": await _dict_rows(
            connection,
            "SELECT j.id AS job_id, j.title, j.company, j.jd_quality AS detail "
            "FROM jobs j JOIN evaluations e ON e.job_id=j.id "
            "WHERE j.jd_quality IN ('stub','partial') "
            "AND e.stage_a_status='completed' AND j.discovered_at >= ? ORDER BY j.id",
            (cutoff,),
        ),
        "stuck_scoring": await _dict_rows(
            connection,
            "SELECT j.id AS job_id, j.title, j.company, "
            "e.stage_a_error_count, e.stage_b_error_count "
            "FROM jobs j JOIN evaluations e ON e.job_id=j.id "
            "WHERE e.stage_a_error_count >= 3 OR e.stage_b_error_count >= 3 "
            "ORDER BY j.id",
        ),
    }


async def _funnel(
    connection: aiosqlite.Connection, cutoff: str
) -> list[dict[str, object]]:
    greatest_stage = (
        "CASE WHEN jobs_gate_passed IS NULL AND stage_a_scored IS NULL "
        "AND stage_b_scored IS NULL THEN NULL ELSE "
        "max(coalesce(jobs_gate_passed,-1),coalesce(stage_a_scored,-1),"
        "coalesce(stage_b_scored,-1)) END"
    )
    greatest_scored = (
        "CASE WHEN stage_a_scored IS NULL AND stage_b_scored IS NULL THEN NULL "
        "ELSE max(coalesce(stage_a_scored,-1),coalesce(stage_b_scored,-1)) END"
    )
    return await _dict_rows(
        connection,
        f"SELECT run_id, jobs_filtered+jobs_ml_gated+({greatest_stage}) "
        f"total_candidates, jobs_ml_gated+({greatest_stage}) after_filter, "
        f"({greatest_stage}) after_gate, ({greatest_scored}) scored "
        "FROM pipeline_runs WHERE source='evaluate' AND started_at >= ? "
        "ORDER BY started_at DESC, run_id DESC",
        (cutoff,),
    )


async def _llm_percentiles(
    connection: aiosqlite.Connection, cutoff: str
) -> list[dict[str, object]]:
    rows = await _dict_rows(
        connection,
        "SELECT substr(timestamp,1,10) day, latency_ms, input_tokens, output_tokens "
        "FROM llm_usage WHERE timestamp >= ? ORDER BY day, latency_ms, id",
        (cutoff,),
    )
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row["day"]), []).append(row)
    return [
        {
            "day": day,
            "p50": _percentile(records, 0.5),
            "p95": _percentile(records, 0.95),
            "avg_in": mean(_number(row["input_tokens"]) for row in records),
            "avg_out": mean(_number(row["output_tokens"]) for row in records),
        }
        for day, records in grouped.items()
    ]


def _percentile(rows: list[dict[str, object]], percentile: float) -> float:
    values = sorted(_number(row["latency_ms"]) for row in rows)
    position = (len(values) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] + (values[upper] - values[lower]) * fraction


async def _dict_rows(
    connection: aiosqlite.Connection,
    sql: str,
    params: tuple[object, ...] = (),
) -> list[dict[str, object]]:
    cursor = await connection.execute(sql, params)
    try:
        rows = await cursor.fetchall()
        names = tuple(item[0] for item in cursor.description or ())
        return [dict(zip(names, row, strict=True)) for row in rows]
    finally:
        await cursor.close()


async def _scalar(connection: aiosqlite.Connection, sql: str) -> object:
    cursor = await connection.execute(sql)
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    if row is None:
        raise ValueError("SQLite aggregate scalar returned no row")
    return row[0]


def _integer(value: object) -> int:
    if type(value) is not int:
        raise ValueError(f"SQLite aggregate count is not an integer: {value!r}")
    return value


def _number(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"SQLite aggregate value is not numeric: {value!r}")
    return float(value)


def _parse_as_of(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("manifest aggregate cutoff is not a timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("manifest aggregate cutoff must be timezone-aware")
    return parsed.astimezone(UTC)


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
