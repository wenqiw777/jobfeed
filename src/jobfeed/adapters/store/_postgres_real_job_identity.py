"""Bounded PostgreSQL real-job resolver inside the source-write transaction."""

from __future__ import annotations

import asyncpg

from jobfeed.domain.models import JobPosting, QualityBand
from jobfeed.domain.normalize import normalize, normalize_company
from jobfeed.domain.real_job_evaluation import (
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


class IdentityParentChanged(RuntimeError):
    """A candidate moved while this transaction waited for its parent lock."""


def _posting(row: asyncpg.Record) -> JobPosting:
    return JobPosting(
        id=str(row["id"]),
        platform=row["platform"],
        canonical_id=row["canonical_id"],
        url=row["url"],
        title=row["title"],
        company=row["company"],
        location=row["location"],
        discovered_at=row["discovered_at"],
        jd_text=row["jd_text"],
        jd_quality=QualityBand(row["jd_quality"]) if row["jd_quality"] else None,
        closed_at=row["closed_at"],
        apply_url=row.get("apply_url"),
    )


async def resolve_postgres_real_job(
    conn: asyncpg.Connection,
    source_id: int,
    incoming: JobPosting,
    *,
    include_content: bool = True,
    remaining_merges: int = _CANDIDATE_LIMIT,
) -> bool:
    """Resolve one source by observed ID or exact substantive content.

    Args:
        conn: Open source-write transaction on the PostgreSQL store.
        source_id: Stored source row to resolve.
        incoming: Posting with any newly observed Apply or identity URL.
        include_content: Also search bounded same-company/title JD candidates.
        remaining_merges: Maximum remaining recursive parent joins.

    Returns:
        Whether a parent was merged during this resolution.

    Raises:
        RuntimeError: If the source disappears or the merge bound is exceeded.
        IdentityParentChanged: If a candidate moves while the merge waits.
    """
    row = await conn.fetchrow("SELECT * FROM jobs WHERE id=$1", source_id)
    if row is None:
        raise RuntimeError("source disappeared during identity resolution")
    source_parent = int(row["real_job_id"])
    source = _posting(row)
    source.apply_url = incoming.apply_url or source.apply_url
    source.identity_evidence_url = incoming.identity_evidence_url
    observed = await _observed_candidates(conn, source_parent, source_id, source)
    candidates = dict(observed)
    if include_content:
        candidates.update(await _content_candidates(conn, source_parent, source))
    if len(candidates) > 1:
        if len(observed) == 1:
            candidates = observed
        else:
            other_parent, other_source = min(candidates.items())
            await _review(
                conn,
                (source_parent, other_parent),
                (source_id, other_source),
                "multiple_identity_candidates",
            )
            return False
    if candidates:
        if remaining_merges < 1:
            raise RuntimeError("identity resolution exceeded candidate bound")
        other_parent, other_source = next(iter(candidates.items()))
        if await _conflicting_requisitions(conn, source_parent, other_parent):
            await _review(
                conn,
                (source_parent, other_parent),
                (source_id, other_source),
                "conflicting_requisition_ids",
            )
            return False
        if await _status_conflict(conn, source_parent, other_parent):
            await _review(
                conn,
                (source_parent, other_parent),
                (source_id, other_source),
                "explicit_status_conflict",
            )
            return False
        merged = await _merge(
            conn,
            source_parent,
            other_parent,
            source_id=source_id,
            other_source_id=other_source,
        )
        if not merged:
            return False
        await resolve_postgres_real_job(
            conn,
            source_id,
            incoming,
            include_content=include_content,
            remaining_merges=remaining_merges - 1,
        )
        return True
    return False


async def _merge(
    conn: asyncpg.Connection,
    source_parent: int,
    other_parent: int,
    *,
    source_id: int | None = None,
    other_source_id: int | None = None,
) -> bool:
    winner, loser = sorted((source_parent, other_parent))
    # Lock both identities in a stable order before reading any child state.
    # A concurrent A<-B merge may delete B while this transaction waits.
    for parent_id in (winner, loser):
        if await conn.fetchval(
            "SELECT id FROM real_jobs WHERE id=$1 FOR UPDATE", parent_id
        ) is None:
            raise IdentityParentChanged("real-job parent changed during merge")
    if source_id is not None and other_source_id is not None:
        rows = await conn.fetch(
            "SELECT id,real_job_id FROM jobs WHERE id=ANY($1::int[])",
            [source_id, other_source_id],
        )
        assigned = {int(row["id"]): row["real_job_id"] for row in rows}
        if assigned != {source_id: source_parent, other_source_id: other_parent}:
            raise IdentityParentChanged("real-job parent changed during merge")
    winner_state = await conn.fetchrow(
        "SELECT * FROM real_job_status WHERE real_job_id=$1 FOR UPDATE",
        winner,
    )
    loser_state = await conn.fetchrow(
        "SELECT * FROM real_job_status WHERE real_job_id=$1 FOR UPDATE",
        loser,
    )
    await conn.fetch(
        "SELECT s.job_id FROM job_status s JOIN jobs j ON j.id=s.job_id "
        "WHERE j.real_job_id=ANY($1::bigint[]) ORDER BY s.job_id FOR UPDATE OF s",
        [winner, loser],
    )
    if await _status_conflict(conn, winner, loser):
        if source_id is None or other_source_id is None:
            sources = await conn.fetch(
                "SELECT real_job_id,MIN(id) AS source_id FROM jobs "
                "WHERE real_job_id=ANY($1::bigint[]) GROUP BY real_job_id",
                [winner, loser],
            )
            by_parent = {
                int(row["real_job_id"]): int(row["source_id"])
                for row in sources
            }
            source_id = by_parent[winner]
            other_source_id = by_parent[loser]
        await _review(
            conn, (source_parent, other_parent), (source_id, other_source_id),
            "explicit_status_conflict",
        )
        return False
    if winner_state is None and loser_state is not None:
        await conn.execute(
            "UPDATE real_job_status SET real_job_id=$1 WHERE real_job_id=$2",
            winner,
            loser,
        )
    if (
        winner_state is not None
        and loser_state is not None
        and winner_state["status"] in _NEUTRAL
        and loser_state["status"] not in _NEUTRAL
    ):
        await conn.execute(
            "UPDATE real_job_status SET status=$1,next_followup_at=$2,"
            "resume_variant=$3,notes=$4,last_status_change_at=$5 "
            "WHERE real_job_id=$6",
            loser_state["status"],
            loser_state["next_followup_at"],
            loser_state["resume_variant"],
            loser_state["notes"],
            loser_state["last_status_change_at"],
            winner,
        )
    await conn.execute(
        "UPDATE real_job_status_history SET real_job_id=$1 WHERE real_job_id=$2",
        winner,
        loser,
    )
    offset = await conn.fetchval(
        "SELECT COALESCE(MAX(round_index),0) FROM real_job_interview_rounds "
        "WHERE real_job_id=$1",
        winner,
    )
    await conn.execute(
        "UPDATE real_job_interview_rounds SET real_job_id=$1,"
        "round_index=round_index+$2 WHERE real_job_id=$3",
        winner,
        offset,
        loser,
    )
    await conn.execute(
        "UPDATE real_job_applications SET real_job_id=$1 WHERE real_job_id=$2",
        winner,
        loser,
    )
    winner_eval = await conn.fetchval(
        "SELECT 1 FROM real_job_evaluations WHERE real_job_id=$1", winner
    )
    if winner_eval is None:
        await conn.execute(
            "UPDATE real_job_evaluations SET real_job_id=$1 WHERE real_job_id=$2",
            winner,
            loser,
        )
    else:
        await conn.execute(
            """INSERT INTO real_job_evaluation_history(
                   real_job_id,source_job_id,input_revision,input_facts_json,stage_a_status,
                   stage_a_score,stage_b_status,stage_b_verdict,stage_b_json,
                   archived_at,reason)
               SELECT $1,source_job_id,input_revision,input_facts_json,stage_a_status,
                      stage_a_score,stage_b_status,stage_b_verdict,stage_b_json,
                      now(),'identity_merge'
               FROM real_job_evaluations WHERE real_job_id=$2""",
            winner,
            loser,
        )
        await conn.execute(
            "DELETE FROM real_job_evaluations WHERE real_job_id=$1", loser
        )
    await conn.execute(
        "UPDATE real_job_evaluation_history SET real_job_id=$1 WHERE real_job_id=$2",
        winner,
        loser,
    )
    await conn.execute(
        "UPDATE jobs SET real_job_id=$1 WHERE real_job_id=$2", winner, loser
    )
    await conn.execute(
        "UPDATE real_job_identifiers SET real_job_id=$1 WHERE real_job_id=$2",
        winner,
        loser,
    )
    await _rehome_reviews(conn, winner, loser)
    await conn.execute("DELETE FROM real_jobs WHERE id=$1", loser)
    jobs = [_posting(row) for row in await conn.fetch(
        "SELECT * FROM jobs WHERE real_job_id=$1 ORDER BY id", winner
    )]
    await conn.execute(
        "UPDATE real_jobs SET representative_job_id=$1,official_closed_at=$2 "
        "WHERE id=$3",
        representative_source_id(str(winner), jobs),
        official_closed_at(jobs),
        winner,
    )
    return True


async def _status_conflict(conn: asyncpg.Connection, left: int, right: int) -> bool:
    parents = [left, right]
    source_statuses = await conn.fetch(
        "SELECT DISTINCT s.status FROM job_status s JOIN jobs j ON j.id=s.job_id "
        "WHERE j.real_job_id=ANY($1::bigint[]) "
        "AND s.status NOT IN ($2,$3)",
        parents,
        *_NEUTRAL,
    )
    canonical_statuses = await conn.fetch(
        "SELECT DISTINCT status FROM real_job_status "
        "WHERE real_job_id=ANY($1::bigint[]) AND status NOT IN ($2,$3)",
        parents,
        *_NEUTRAL,
    )
    return len({row["status"] for row in (*source_statuses, *canonical_statuses)}) > 1


async def _conflicting_requisitions(
    conn: asyncpg.Connection, left: int, right: int
) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT 1 FROM real_job_identifiers a JOIN real_job_identifiers b "
            "ON b.real_job_id=$2 AND b.provider=a.provider AND b.scope=a.scope "
            "AND b.native_id<>a.native_id WHERE a.real_job_id=$1 "
            "AND a.provider=ANY($3::text[]) LIMIT 1",
            left,
            right,
            list(ATS_REQUISITION_PROVIDERS),
        )
    )


async def _rehome_reviews(conn: asyncpg.Connection, winner: int, loser: int) -> None:
    await conn.execute(
        "DELETE FROM real_job_review_cases WHERE "
        "left_real_job_id=$1 AND right_real_job_id=$2",
        winner,
        loser,
    )
    cases = await conn.fetch(
        "SELECT id,left_real_job_id,right_real_job_id FROM real_job_review_cases "
        "WHERE left_real_job_id=$1 OR right_real_job_id=$1",
        loser,
    )
    for case in cases:
        left, right = sorted(
            winner if int(value) == loser else int(value)
            for value in (case["left_real_job_id"], case["right_real_job_id"])
        )
        await conn.execute(
            "UPDATE real_job_review_cases SET left_real_job_id=$1,"
            "right_real_job_id=$2 WHERE id=$3",
            left,
            right,
            int(case["id"]),
        )


async def _observed_candidates(
    conn: asyncpg.Connection,
    source_parent: int,
    source_id: int,
    source: JobPosting,
) -> dict[int, int]:
    candidates: dict[int, int] = {}
    for identifier in source_identifiers(source):
        await conn.execute(
            "INSERT INTO real_job_identifiers("
            "real_job_id,provider,scope,native_id,evidence_job_id,observed_url) "
            "VALUES($1,$2,$3,$4,$5,$6) "
            "ON CONFLICT(provider,scope,native_id) DO NOTHING",
            source_parent,
            identifier.provider,
            identifier.scope,
            identifier.native_id,
            source_id,
            identifier.observed_url,
        )
        owned = await conn.fetchrow(
            "SELECT real_job_id,evidence_job_id FROM real_job_identifiers "
            "WHERE provider=$1 AND scope=$2 AND native_id=$3",
            identifier.provider,
            identifier.scope,
            identifier.native_id,
        )
        if owned is None or int(owned["real_job_id"]) == source_parent:
            continue
        other = await conn.fetchrow(
            "SELECT * FROM jobs WHERE id=$1", int(owned["evidence_job_id"])
        )
        if other is None:
            continue
        if compatible_role_facts(source, _posting(other)):
            candidates[int(owned["real_job_id"])] = int(other["id"])
        else:
            await _review(
                conn,
                (source_parent, int(owned["real_job_id"])),
                (source_id, int(other["id"])),
                "conflicting_role_facts",
            )
    return candidates


async def _content_candidates(
    conn: asyncpg.Connection,
    source_parent: int,
    source: JobPosting,
) -> dict[int, int]:
    others = await conn.fetch(
        "SELECT * FROM jobs WHERE company_norm=$1 AND title_norm=$2 "
        "AND real_job_id<>$3 ORDER BY id LIMIT $4",
        normalize_company(source.company),
        normalize(source.title),
        source_parent,
        _CANDIDATE_LIMIT + 1,
    )
    if len(others) > _CANDIDATE_LIMIT:
        return {}
    return {
        int(other["real_job_id"]): int(other["id"])
        for other in others
        if strict_content_equivalent(source, _posting(other))
    }


async def _review(
    conn: asyncpg.Connection,
    pair: tuple[int, int],
    sources: tuple[int, int],
    reason: str,
) -> None:
    left, right = pair
    left_source, right_source = sources
    if left > right:
        left, right = right, left
        left_source, right_source = right_source, left_source
    await conn.execute(
        "INSERT INTO real_job_review_cases("
        "left_real_job_id,right_real_job_id,left_job_id,right_job_id,reason) "
        "SELECT $1,$2,$3,$4,$5 WHERE NOT EXISTS("
        "SELECT 1 FROM real_job_review_cases WHERE "
        "left_real_job_id=$1 AND right_real_job_id=$2 AND reason=$5)",
        left,
        right,
        left_source,
        right_source,
        reason,
    )
