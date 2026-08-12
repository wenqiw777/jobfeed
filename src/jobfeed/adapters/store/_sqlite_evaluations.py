"""SQLite Stage A and Stage B persistence commands."""

from __future__ import annotations

import aiosqlite

from jobfeed.adapters.store._sqlite_values import _canonical_json, _utc_now_text
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.domain.models import StageAResult, StageBResult


async def _save_stage_a(
    lifecycle: SqliteLifecycle,
    job_id: str,
    result: StageAResult,
) -> None:
    """Atomically persist Stage A success and its eligible status advance."""
    numeric_id = int(job_id)
    now = _utc_now_text()
    async with lifecycle.connection() as connection:
        await connection.execute("BEGIN IMMEDIATE")
        try:
            await connection.execute(
                """INSERT INTO evaluations (
                    job_id, stage_a_score, stage_a_one_line,
                    stage_a_timing_eligible, stage_a_status, stage_a_error,
                    stage_a_model, stage_a_cost_usd, stage_a_prompt_hash,
                    stage_a_resume_hash, stage_a_at, updated_at
                ) VALUES (?,?,?,?,'completed',NULL,?,?,?,?,?,?)
                ON CONFLICT(job_id) DO UPDATE SET
                    stage_a_score=excluded.stage_a_score,
                    stage_a_one_line=excluded.stage_a_one_line,
                    stage_a_timing_eligible=excluded.stage_a_timing_eligible,
                    stage_a_status='completed', stage_a_error=NULL,
                    stage_a_model=excluded.stage_a_model,
                    stage_a_cost_usd=excluded.stage_a_cost_usd,
                    stage_a_prompt_hash=excluded.stage_a_prompt_hash,
                    stage_a_resume_hash=excluded.stage_a_resume_hash,
                    stage_a_at=COALESCE(evaluations.stage_a_at, excluded.stage_a_at),
                    stage_b_status=CASE
                        WHEN evaluations.stage_b_status='skipped_below_threshold'
                        THEN NULL ELSE evaluations.stage_b_status END,
                    stage_b_error=CASE
                        WHEN evaluations.stage_b_status='skipped_below_threshold'
                        THEN NULL ELSE evaluations.stage_b_error END,
                    updated_at=excluded.updated_at""",
                (
                    numeric_id,
                    result.score,
                    result.one_line,
                    result.timing_eligible,
                    result.model,
                    result.cost_usd,
                    result.prompt_hash,
                    result.resume_hash,
                    now,
                    now,
                ),
            )
            cursor = await connection.execute(
                "UPDATE job_status SET status='scored', last_status_change_at=? "
                "WHERE job_id=? AND status='new' RETURNING job_id",
                (now, numeric_id),
            )
            changed = await cursor.fetchone()
            await cursor.close()
            if changed is not None:
                await _insert_scored_history(connection, numeric_id, now)
            await connection.commit()
        except BaseException:
            await connection.rollback()
            raise


async def _insert_scored_history(
    connection: aiosqlite.Connection,
    job_id: int,
    changed_at: str,
) -> None:
    await connection.execute(
        "INSERT INTO job_status_history "
        "(job_id,from_status,to_status,changed_at,reason) "
        "VALUES (?,'new','scored',?,'auto_scored')",
        (job_id, changed_at),
    )


async def _save_stage_a_error(
    lifecycle: SqliteLifecycle,
    job_id: str,
    error: str,
) -> None:
    """Record one retryable Stage A failure and increment its counter."""
    await _save_stage_error(lifecycle, int(job_id), "a", error)


async def _save_stage_b(
    lifecycle: SqliteLifecycle,
    job_id: str,
    result: StageBResult,
) -> None:
    """Persist a complete Stage B result using canonical structured JSON."""
    numeric_id = int(job_id)
    blocks = _stage_b_blocks(result)
    now = _utc_now_text()
    async with lifecycle.connection() as connection:
        await connection.execute(
            """INSERT INTO evaluations (
                job_id, stage_b_verdict, stage_b_jd_summary,
                stage_b_verdict_json, stage_b_summary_json, stage_b_fit_json,
                stage_b_hooks_json, stage_b_status, stage_b_error, stage_b_model,
                stage_b_cost_usd, stage_b_prompt_hash, stage_b_resume_hash,
                stage_b_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,'completed',NULL,?,?,?,?,?,?)
            ON CONFLICT(job_id) DO UPDATE SET
                stage_b_verdict=excluded.stage_b_verdict,
                stage_b_jd_summary=excluded.stage_b_jd_summary,
                stage_b_verdict_json=excluded.stage_b_verdict_json,
                stage_b_summary_json=excluded.stage_b_summary_json,
                stage_b_fit_json=excluded.stage_b_fit_json,
                stage_b_hooks_json=excluded.stage_b_hooks_json,
                stage_b_status='completed', stage_b_error=NULL,
                stage_b_model=excluded.stage_b_model,
                stage_b_cost_usd=excluded.stage_b_cost_usd,
                stage_b_prompt_hash=excluded.stage_b_prompt_hash,
                stage_b_resume_hash=excluded.stage_b_resume_hash,
                stage_b_at=COALESCE(evaluations.stage_b_at, excluded.stage_b_at),
                updated_at=excluded.updated_at""",
            (
                numeric_id,
                result.verdict.value,
                result.jd_summary,
                *(_canonical_json(blocks[key]) for key in _BLOCK_KEYS),
                result.model,
                result.cost_usd,
                result.prompt_hash,
                result.resume_hash,
                now,
                now,
            ),
        )


async def _save_stage_b_error(
    lifecycle: SqliteLifecycle,
    job_id: str,
    error: str,
) -> None:
    """Record one retryable Stage B failure and increment its counter."""
    await _save_stage_error(lifecycle, int(job_id), "b", error)


async def _save_stage_error(
    lifecycle: SqliteLifecycle,
    job_id: int,
    stage: str,
    error: str,
) -> None:
    now = _utc_now_text()
    async with lifecycle.connection() as connection:
        await connection.execute(
            f"""INSERT INTO evaluations (
                job_id, stage_{stage}_status, stage_{stage}_error,
                stage_{stage}_error_count, updated_at
            ) VALUES (?,'error',?,1,?)
            ON CONFLICT(job_id) DO UPDATE SET
                stage_{stage}_status='error',
                stage_{stage}_error=excluded.stage_{stage}_error,
                stage_{stage}_error_count=evaluations.stage_{stage}_error_count+1,
                updated_at=excluded.updated_at""",
            (job_id, error, now),
        )


async def _mark_stage_b_skipped(
    lifecycle: SqliteLifecycle,
    job_id: str,
) -> None:
    """Idempotently skip Stage B unless a completed result already exists."""
    numeric_id = int(job_id)
    async with lifecycle.connection() as connection:
        await connection.execute(
            "UPDATE evaluations SET stage_b_status='skipped_below_threshold', "
            "updated_at=? WHERE job_id=? "
            "AND (stage_b_status IS NULL OR stage_b_status<>'completed')",
            (_utc_now_text(), numeric_id),
        )


_BLOCK_KEYS = ("verdict", "jd_summary", "fit_analysis", "resume_hooks")


def _stage_b_blocks(result: StageBResult) -> dict[str, object]:
    raw = result.raw_blocks or {}
    derived = {
        "verdict": {"recommendation": result.verdict.value},
        "jd_summary": {
            "role_in_3_lines": result.jd_summary,
            "must_haves": [],
            "nice_to_haves": [],
            "red_flags_in_jd": [],
        },
        "fit_analysis": {
            "score_0_100": result.fit_analysis.score,
            "strong_match": [
                {
                    "requirement": item.requirement,
                    "evidence_from_resume": item.evidence,
                }
                for item in result.fit_analysis.strengths
            ],
            "gaps": [
                {
                    "requirement": item.requirement,
                    "severity": item.severity,
                    "mitigation": item.mitigation,
                }
                for item in result.fit_analysis.gaps
            ],
        },
        "resume_hooks": {
            "lead_with": result.resume_hooks[0] if result.resume_hooks else "",
            "supporting": result.resume_hooks[1:],
            "avoid_mentioning": [],
        },
    }
    return {key: raw.get(key, derived[key]) for key in _BLOCK_KEYS}
