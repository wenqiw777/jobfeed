"""Bounded SQLite real-job resolver; caller owns the source-write transaction."""

from __future__ import annotations

import aiosqlite

from jobfeed.adapters.store._sqlite_values import _job_from_row, _utc_text
from jobfeed.domain.models import JobPosting
from jobfeed.domain.normalize import normalize, normalize_company
from jobfeed.domain.real_job_evaluation import (
    canonical_sort_dates,
    official_closed_at,
    representative_source_id,
)
from jobfeed.domain.real_job_identity import (
    ATS_REQUISITION_PROVIDERS,
    compatible_role_facts,
    source_identifiers,
    strict_content_equivalent,
)

_CANDIDATE_LIMIT = 50
_NEUTRAL = ("new", "scored")


async def resolve_sqlite_real_job(
    connection: aiosqlite.Connection,
    source_id: int,
    job: JobPosting,
    *,
    include_content: bool = True,
) -> bool:
    """Join one source only when a scoped ID or exact substantive body proves it.

    Args:
        connection: Open source-write transaction on an identity-enabled DB.
        source_id: Stored source row to resolve.
        job: Incoming posting with any newly observed Apply or identity URL.
        include_content: Also search the bounded same-company/title JD candidates.

    Returns:
        Whether at least one parent was merged during this resolution.

    Raises:
        RuntimeError: If the source disappears or the merge bound is exceeded.
    """
    source = await _source_row(connection, source_id)
    if source is None:
        raise RuntimeError("source disappeared during identity resolution")
    source_parent = int(source["real_job_id"])
    source_job = _job_from_row(source)
    source_job.apply_url = job.apply_url or source_job.apply_url
    source_job.identity_evidence_url = job.identity_evidence_url
    merged = False
    for _ in range(_CANDIDATE_LIMIT):
        observed = await _observed_candidates(
            connection, source_parent, source_id, source_job
        )
        candidates = dict(observed)
        if include_content:
            candidates.update(
                await _content_candidates(connection, source_parent, source_job)
            )
        if len(candidates) > 1:
            if len(observed) == 1:
                candidates = observed
            else:
                other_parent, other_source = min(candidates.items())
                await _review(
                    connection,
                    (source_parent, other_parent),
                    (source_id, other_source),
                    "multiple_identity_candidates",
                )
                return merged
        if not candidates:
            return merged
        other_parent, other_source = next(iter(candidates.items()))
        if await _conflicting_requisitions(connection, source_parent, other_parent):
            await _review(
                connection,
                (source_parent, other_parent),
                (source_id, other_source),
                "conflicting_requisition_ids",
            )
            return merged
        statuses = await _explicit_statuses(connection, source_parent, other_parent)
        if len(statuses) > 1:
            await _review(
                connection,
                (source_parent, other_parent),
                (source_id, other_source),
                "explicit_status_conflict",
            )
            return merged
        await _merge(connection, source_parent, other_parent)
        merged = True
        source_parent = min(source_parent, other_parent)
    raise RuntimeError("identity resolution exceeded candidate bound")


async def _observed_candidates(
    connection: aiosqlite.Connection,
    source_parent: int,
    source_id: int,
    source_job: JobPosting,
) -> dict[int, int]:
    candidates: dict[int, int] = {}
    for identifier in source_identifiers(source_job):
        await connection.execute(
            "INSERT OR IGNORE INTO real_job_identifiers("
            "real_job_id,provider,scope,native_id,evidence_job_id,observed_url) "
            "VALUES(?,?,?,?,?,?)",
            (
                source_parent,
                identifier.provider,
                identifier.scope,
                identifier.native_id,
                source_id,
                identifier.observed_url,
            ),
        )
        cursor = await connection.execute(
            "SELECT real_job_id,evidence_job_id FROM real_job_identifiers "
            "WHERE provider=? AND scope=? AND native_id=?",
            (identifier.provider, identifier.scope, identifier.native_id),
        )
        owned = await cursor.fetchone()
        await cursor.close()
        if owned is None or int(owned["real_job_id"]) == source_parent:
            continue
        other = await _source_row(connection, int(owned["evidence_job_id"]))
        if other is None:
            continue
        other_job = _job_from_row(other)
        if compatible_role_facts(source_job, other_job):
            candidates[int(owned["real_job_id"])] = int(other["id"])
        else:
            await _review(
                connection,
                (source_parent, int(owned["real_job_id"])),
                (source_id, int(other["id"])),
                "conflicting_role_facts",
            )
    return candidates


async def _content_candidates(
    connection: aiosqlite.Connection,
    source_parent: int,
    source_job: JobPosting,
) -> dict[int, int]:
    cursor = await connection.execute(
        "SELECT * FROM jobs WHERE company_norm=? AND title_norm=? "
        "AND real_job_id<>? ORDER BY id LIMIT ?",
        (
            normalize_company(source_job.company),
            normalize(source_job.title),
            source_parent,
            _CANDIDATE_LIMIT + 1,
        ),
    )
    rows = await cursor.fetchall()
    await cursor.close()
    if len(rows) > _CANDIDATE_LIMIT:
        return {}
    return {
        int(other["real_job_id"]): int(other["id"])
        for other in rows
        if strict_content_equivalent(source_job, _job_from_row(other))
    }


async def _source_row(
    connection: aiosqlite.Connection, source_id: int
) -> aiosqlite.Row | None:
    cursor = await connection.execute("SELECT * FROM jobs WHERE id=?", (source_id,))
    row = await cursor.fetchone()
    await cursor.close()
    return row


async def _explicit_statuses(
    connection: aiosqlite.Connection, left: int, right: int
) -> set[str]:
    cursor = await connection.execute(
        "SELECT DISTINCT s.status FROM job_status s JOIN jobs j ON j.id=s.job_id "
        "WHERE j.real_job_id IN (?,?) AND s.status NOT IN (?,?)",
        (left, right, *_NEUTRAL),
    )
    statuses = {str(row[0]) for row in await cursor.fetchall()}
    await cursor.close()
    cursor = await connection.execute(
        "SELECT DISTINCT status FROM real_job_status WHERE real_job_id IN (?,?) "
        "AND status NOT IN (?,?)",
        (left, right, *_NEUTRAL),
    )
    statuses.update(str(row[0]) for row in await cursor.fetchall())
    await cursor.close()
    return statuses


async def _conflicting_requisitions(
    connection: aiosqlite.Connection, left: int, right: int
) -> bool:
    providers = tuple(sorted(ATS_REQUISITION_PROVIDERS))
    placeholders = ",".join("?" for _ in providers)
    cursor = await connection.execute(
        "SELECT 1 FROM real_job_identifiers a JOIN real_job_identifiers b "
        "ON b.real_job_id=? AND b.provider=a.provider AND b.scope=a.scope "
        "AND b.native_id<>a.native_id WHERE a.real_job_id=? "
        f"AND a.provider IN ({placeholders}) LIMIT 1",
        (right, left, *providers),
    )
    found = await cursor.fetchone()
    await cursor.close()
    return found is not None


async def _review(
    connection: aiosqlite.Connection,
    pair: tuple[int, int],
    sources: tuple[int, int],
    reason: str,
) -> None:
    left, right = pair
    left_source, right_source = sources
    if left > right:
        left, right = right, left
        left_source, right_source = right_source, left_source
    await connection.execute(
        "INSERT INTO real_job_review_cases("
        "left_real_job_id,right_real_job_id,left_job_id,right_job_id,reason) "
        "SELECT ?,?,?,?,? WHERE NOT EXISTS(SELECT 1 FROM real_job_review_cases "
        "WHERE left_real_job_id=? AND right_real_job_id=? AND reason=?)",
        (left, right, left_source, right_source, reason, left, right, reason),
    )


async def _merge(connection: aiosqlite.Connection, left: int, right: int) -> None:
    winner, loser = sorted((left, right))
    winner_status = await _source_row_status(connection, winner)
    loser_status = await _source_row_status(connection, loser)
    if winner_status is None and loser_status is not None:
        await connection.execute(
            "UPDATE real_job_status SET real_job_id=? WHERE real_job_id=?",
            (winner, loser),
        )
    if (
        winner_status is not None
        and loser_status is not None
        and winner_status["status"] in _NEUTRAL
        and loser_status["status"] not in _NEUTRAL
    ):
        await connection.execute(
            "UPDATE real_job_status SET status=?,next_followup_at=?,"
            "resume_variant=?,notes=?,last_status_change_at=? WHERE real_job_id=?",
            (*loser_status, winner),
        )
    await connection.execute(
        "UPDATE real_job_status_history SET real_job_id=? WHERE real_job_id=?",
        (winner, loser),
    )
    cursor = await connection.execute(
        "SELECT COALESCE(MAX(round_index),0) FROM real_job_interview_rounds "
        "WHERE real_job_id=?",
        (winner,),
    )
    offset = int((await cursor.fetchone())[0])
    await cursor.close()
    await connection.execute(
        "UPDATE real_job_interview_rounds SET real_job_id=?,"
        "round_index=round_index+? WHERE real_job_id=?",
        (winner, offset, loser),
    )
    await connection.execute(
        "UPDATE real_job_applications SET real_job_id=? WHERE real_job_id=?",
        (winner, loser),
    )
    winner_eval = await (
        await connection.execute(
            "SELECT 1 FROM real_job_evaluations WHERE real_job_id=?", (winner,)
        )
    ).fetchone()
    if winner_eval is None:
        await connection.execute(
            "UPDATE real_job_evaluations SET real_job_id=? WHERE real_job_id=?",
            (winner, loser),
        )
    else:
        await connection.execute(
            """INSERT INTO real_job_evaluation_history(
                   real_job_id,source_job_id,input_revision,input_facts_json,stage_a_status,
                   stage_a_score,stage_b_status,stage_b_verdict,stage_b_json,
                   archived_at,reason)
               SELECT ?,source_job_id,input_revision,input_facts_json,stage_a_status,
                      stage_a_score,stage_b_status,stage_b_verdict,stage_b_json,
                      strftime('%Y-%m-%dT%H:%M:%f000Z','now'),'identity_merge'
               FROM real_job_evaluations WHERE real_job_id=?""",
            (winner, loser),
        )
        await connection.execute(
            "DELETE FROM real_job_evaluations WHERE real_job_id=?", (loser,)
        )
    await connection.execute(
        "UPDATE real_job_evaluation_history SET real_job_id=? WHERE real_job_id=?",
        (winner, loser),
    )
    await connection.execute(
        "UPDATE jobs SET real_job_id=? WHERE real_job_id=?", (winner, loser)
    )
    await connection.execute(
        "UPDATE real_job_identifiers SET real_job_id=? WHERE real_job_id=?",
        (winner, loser),
    )
    await _rehome_reviews(connection, winner, loser)
    await connection.execute("DELETE FROM real_jobs WHERE id=?", (loser,))
    cursor = await connection.execute(
        "SELECT * FROM jobs WHERE real_job_id=? ORDER BY id", (winner,)
    )
    cursor.row_factory = aiosqlite.Row
    jobs = [_job_from_row(row) for row in await cursor.fetchall()]
    await cursor.close()
    closure = official_closed_at(jobs)
    first_discovered, canonical_posted = canonical_sort_dates(jobs)
    await connection.execute(
        "UPDATE real_jobs SET representative_job_id=?,official_closed_at=?,"
        "first_discovered_at=?,canonical_posted_at=? WHERE id=?",
        (
            representative_source_id(str(winner), jobs),
            _utc_text(closure) if closure else None,
            _utc_text(first_discovered),
            _utc_text(canonical_posted),
            winner,
        ),
    )


async def _rehome_reviews(
    connection: aiosqlite.Connection, winner: int, loser: int
) -> None:
    await connection.execute(
        "DELETE FROM real_job_review_cases WHERE "
        "left_real_job_id=? AND right_real_job_id=?",
        (winner, loser),
    )
    cursor = await connection.execute(
        "SELECT id,left_real_job_id,right_real_job_id FROM real_job_review_cases "
        "WHERE left_real_job_id=? OR right_real_job_id=?",
        (loser, loser),
    )
    cases = await cursor.fetchall()
    await cursor.close()
    for case in cases:
        left, right = sorted(
            winner if int(value) == loser else int(value) for value in case[1:3]
        )
        await connection.execute(
            "UPDATE real_job_review_cases SET left_real_job_id=?,"
            "right_real_job_id=? WHERE id=?",
            (left, right, int(case[0])),
        )


async def _source_row_status(
    connection: aiosqlite.Connection, real_id: int
) -> aiosqlite.Row | None:
    cursor = await connection.execute(
        "SELECT status,next_followup_at,resume_variant,notes,last_status_change_at "
        "FROM real_job_status WHERE real_job_id=?",
        (real_id,),
    )
    row = await cursor.fetchone()
    await cursor.close()
    return row
