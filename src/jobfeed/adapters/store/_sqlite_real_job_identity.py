"""Bounded SQLite real-job resolver; caller owns the source-write transaction."""

from __future__ import annotations

from itertools import combinations

import aiosqlite

from jobfeed.adapters.store._identity_review import kept_separate, rehome_separations
from jobfeed.adapters.store._posting_spam import SpamDB
from jobfeed.adapters.store._sqlite_values import _job_from_row, _utc_text
from jobfeed.domain.intermediary import intermediary_posting, trusted_ats_url
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
from jobfeed.domain.requisition_alias import PREFIX, Aliases, alias_id

_CANDIDATE_LIMIT = 50
_NEUTRAL = ("new", "scored")


async def resolve_sqlite_real_job(  # noqa: C901 - bounded evidence and status checks
    connection: aiosqlite.Connection,
    source_id: int,
    job: JobPosting,
    *,
    include_content: bool = True,
) -> bool:
    """Join one source only when a scoped ID or exact substantive body proves it.

    Time complexity: O(b * (n + c^2)) for n source rows and c parent candidates.

    Args:
        connection: Open source-write transaction on an identity-enabled DB.
        source_id: Stored source row to resolve.
        job: Incoming posting with any newly observed Apply or identity URL.
        include_content: Also search same-company/title JD candidates in batches.

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
    aliases = await _aliases(connection)
    merged = False
    for _ in range(_CANDIDATE_LIMIT):
        observed, conflict = await _observed_candidates(
            connection, source_parent, source_id, source_job, aliases
        )
        if conflict:
            return merged
        candidates = dict(observed)
        if include_content:
            candidates.update(
                await _content_candidates(
                    connection, source_parent, source_job, aliases
                )
            )
        for candidate in list(candidates):
            if await kept_separate(SpamDB(connection), source_parent, candidate):
                candidates.pop(candidate)
                observed.pop(candidate, None)
        if len(candidates) > 1:
            if observed and await _compatible_candidates(
                connection, [source_parent, *observed], aliases
            ):
                candidates = dict([min(observed.items())])
            elif len(observed) == 1:
                candidates = observed
            else:
                for other_parent, other_source in candidates.items():
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
        if await _conflicting_requisitions(
            connection, source_parent, other_parent, aliases
        ):
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
        other = await _source_row(connection, other_source)
        prefer_official = (
            intermediary_posting(source_job)
            and other is not None
            and trusted_ats_url(str(other["url"]))
        )
        winner = other_parent if prefer_official else min(source_parent, other_parent)
        loser = source_parent if winner == other_parent else other_parent
        await _merge(connection, winner, loser)
        merged = True
        source_parent = winner
    raise RuntimeError("identity resolution exceeded candidate bound")


async def _observed_candidates(
    connection: aiosqlite.Connection,
    source_parent: int,
    source_id: int,
    source_job: JobPosting,
    aliases: Aliases,
) -> tuple[dict[int, int], bool]:
    candidates: dict[int, int] = {}
    conflict = False
    for identifier in source_identifiers(source_job, aliases):
        await connection.execute(
            "INSERT INTO real_job_identifiers("
            "real_job_id,provider,scope,native_id,evidence_job_id,observed_url) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(provider,scope,native_id) DO NOTHING",
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
            "SELECT real_job_id,evidence_job_id,observed_url FROM real_job_identifiers "
            "WHERE provider=? AND scope=? AND native_id=?",
            (identifier.provider, identifier.scope, identifier.native_id),
        )
        owned = await cursor.fetchone()
        await cursor.close()
        if owned is None or int(owned["real_job_id"]) == source_parent:
            continue
        if await kept_separate(
            SpamDB(connection), source_parent, int(owned["real_job_id"])
        ):
            continue
        other = await _source_row(connection, int(owned["evidence_job_id"]))
        if other is None:
            continue
        other_job = _job_from_row(other)
        # Wrapper sources retain the original Apply URL in jobs. Rehydrate
        # their verified ATS evidence from the identifier that owns this match.
        if identifier.provider in ATS_REQUISITION_PROVIDERS:
            other_job.identity_evidence_url = str(owned["observed_url"])
        if compatible_role_facts(source_job, other_job, aliases):
            candidates[int(owned["real_job_id"])] = int(other["id"])
        else:
            conflict = True
            await _review(
                connection,
                (source_parent, int(owned["real_job_id"])),
                (source_id, int(other["id"])),
                "conflicting_requisition_ids"
                if any(
                    i.provider == identifier.provider
                    and i.scope == identifier.scope
                    and i.native_id != identifier.native_id
                    for i in source_identifiers(source_job, aliases)
                )
                else "conflicting_role_facts",
            )
    return candidates, conflict


async def _content_candidates(
    connection: aiosqlite.Connection,
    source_parent: int,
    source_job: JobPosting,
    aliases: Aliases,
) -> dict[int, int]:
    """Compare same-company/title sources in bounded read batches.

    Time complexity: O(n * d) for n source rows and d description length.
    Each source is compared once; batching does not multiply comparisons.
    """
    cursor = await connection.execute(
        "SELECT * FROM jobs WHERE company_norm=? AND title_norm=? "
        "AND real_job_id<>? ORDER BY id",
        (
            normalize_company(source_job.company),
            normalize(source_job.title),
            source_parent,
        ),
    )
    candidates: dict[int, int] = {}
    try:
        while rows := await cursor.fetchmany(_CANDIDATE_LIMIT):
            for other in rows:
                if strict_content_equivalent(source_job, _job_from_row(other), aliases):
                    candidates[int(other["real_job_id"])] = int(other["id"])
    finally:
        await cursor.close()
    return candidates


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
    connection: aiosqlite.Connection, left: int, right: int, aliases: Aliases
) -> bool:
    providers = tuple(sorted(ATS_REQUISITION_PROVIDERS))
    placeholders = ",".join("?" for _ in providers)
    cursor = await connection.execute(
        "SELECT a.provider,a.scope,a.native_id,b.native_id "
        "FROM real_job_identifiers a JOIN real_job_identifiers b "
        "ON b.real_job_id=? AND b.provider=a.provider AND b.scope=a.scope "
        "AND b.native_id<>a.native_id WHERE a.real_job_id=? "
        f"AND a.provider IN ({placeholders})",
        (right, left, *providers),
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return any(
        alias_id(p, s, a, aliases) != alias_id(p, s, b, aliases) for p, s, a, b in rows
    )


async def _review(
    connection: aiosqlite.Connection,
    pair: tuple[int, int],
    sources: tuple[int, int],
    reason: str,
) -> None:
    if await kept_separate(SpamDB(connection), *pair):
        return
    await connection.execute(
        "UPDATE real_jobs SET identity_review_state='identity_conflict' "
        "WHERE id IN (?,?) AND identity_review_state='clear'",
        pair,
    )
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
    winner, loser = left, right
    await rehome_separations(SpamDB(connection), winner, loser)
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
    offset_row = await cursor.fetchone()
    assert offset_row is not None
    offset = int(offset_row[0])
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
        tuple(sorted((winner, loser))),
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


async def _aliases(connection: aiosqlite.Connection) -> dict[tuple[str, str], str]:
    cursor = await connection.execute(
        "SELECT key,value FROM state WHERE key GLOB ?", (PREFIX + "*",)
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return {tuple(key[len(PREFIX) :].split(":", 1)): value for key, value in rows}


async def _compatible_candidates(
    connection: aiosqlite.Connection, parents: list[int], aliases: Aliases
) -> bool:
    for left, right in combinations(parents, 2):
        if await _conflicting_requisitions(connection, left, right, aliases):
            return False
        if len(await _explicit_statuses(connection, left, right)) > 1:
            return False
    return True
