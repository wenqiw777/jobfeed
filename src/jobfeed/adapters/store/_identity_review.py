"""Shared duplicate-review reads, decision snapshots and separation rules."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from jobfeed.adapters.store._posting_spam import SpamDB
from jobfeed.adapters.store._real_job_hard_filters import (
    postgres_hard_filter,
    sqlite_hard_filter,
)
from jobfeed.domain.filtering import HardFilters

_SEPARATE = "identity-separate:"
_HISTORY = "identity-review:"


def separation_key(left: int, right: int) -> str:
    """Build a stable pair key using existing canonical IDs.

    Args:
        left: First canonical ID.
        right: Second canonical ID.

    Returns:
        Ordered state key.
    """
    return _SEPARATE + ":".join(map(str, sorted((left, right))))


async def kept_separate(db: SpamDB, left: int, right: int) -> bool:
    """Check a user's retained independent identities.

    Args:
        db: Caller-owned connection adapter.
        left: First canonical ID.
        right: Second canonical ID.

    Returns:
        Whether automatic matching must skip this pair.
    """
    return bool(
        await db.rows("SELECT 1 FROM state WHERE key=?", separation_key(left, right))
    )


async def rehome_separations(db: SpamDB, winner: int, loser: int) -> None:
    """Preserve independence decisions when either identity merges elsewhere.

    Args:
        db: Caller-owned connection adapter.
        winner: Surviving canonical ID.
        loser: Merged canonical ID.
    """
    rows = await db.rows("SELECT key FROM state WHERE key LIKE ?", _SEPARATE + "%")
    for row in rows:
        pair = [int(v) for v in row["key"][len(_SEPARATE) :].split(":")]
        if loser not in pair:
            continue
        other = next(v for v in pair if v != loser)
        if other != winner:
            await db.execute(
                "INSERT INTO state(key,value) VALUES(?,'1') ON "
                "CONFLICT(key) DO NOTHING",
                separation_key(winner, other),
            )
        await db.execute("DELETE FROM state WHERE key=?", row["key"])


class IdentityReviewDB:
    """Review pairs and audit snapshots on either supported store."""

    def __init__(self, connection: Any, *, postgres: bool = False) -> None:
        self.db = SpamDB(connection, postgres=postgres)

    async def snapshot(self, real_id: int) -> dict[str, Any]:
        """Return source links and workflow context for one identity.

        Args:
            real_id: Canonical ID.

        Returns:
            Reviewable job facts and source postings.

        Raises:
            ValueError: The identity has already moved or disappeared.
        """
        rows = await self.db.rows(
            "SELECT r.id,j.company,j.title,j.location,j.url,j.apply_url,"
            "COALESCE(s.status,'new') AS status FROM real_jobs r "
            "JOIN jobs j ON j.id=r.representative_job_id "
            "LEFT JOIN real_job_status s ON s.real_job_id=r.id WHERE r.id=?",
            real_id,
        )
        if not rows:
            raise ValueError("Review changed. Refresh the page.")
        value = rows[0]
        value["id"] = str(real_id)
        value["sources"] = await self.db.rows(
            "SELECT id,platform,url,apply_url,title,location,jd_text,jd_quality "
            "FROM jobs WHERE real_job_id=? ORDER BY id",
            real_id,
        )
        return value

    async def review(
        self,
        status: str,
        limit: int,
        offset: int,
        hard_filters: HardFilters | None = None,
    ) -> dict[str, Any]:
        """List pending pairs or historical user decisions.

        Args:
            status: Pending or reviewed tab.
            limit: Maximum page size.
            offset: Page offset.
            hard_filters: Current Results visibility policy.

        Returns:
            Matching total and review cards.
        """
        if status == "reviewed":
            total = await self.db.rows(
                "SELECT count(*) AS n FROM state WHERE key LIKE ?", _HISTORY + "%"
            )
            rows = await self.db.rows(
                "SELECT value FROM state WHERE key LIKE ? ORDER BY key "
                "DESC LIMIT ? OFFSET ?",
                _HISTORY + "%",
                limit,
                offset,
            )
            return {
                "total": total[0]["n"],
                "items": [json.loads(r["value"]) for r in rows],
            }
        if self.db.postgres:
            hard, values = postgres_hard_filter(
                hard_filters, datetime.now(UTC), first_param=1
            )
            args = values
        else:
            hard, args = sqlite_hard_filter(hard_filters, datetime.now(UTC))

        async def pending_rows(sql: str, *page_args: object) -> list[dict[str, Any]]:
            if self.db.postgres:
                for index in range(len(args) + 1, len(args) + len(page_args) + 1):
                    sql = sql.replace("?", f"${index}", 1)
                return [
                    dict(row)
                    for row in await self.db.connection.fetch(
                        sql.replace("REVIEW_HARD_FILTER", hard), *args, *page_args
                    )
                ]
            return await self.db.rows(
                sql.replace("REVIEW_HARD_FILTER", hard), *args, *page_args
            )

        eligible = (
            "SELECT r.id FROM real_jobs r JOIN jobs j ON j.id=r.representative_job_id "
            "AND j.real_job_id=r.id JOIN real_job_status s ON s.real_job_id=r.id "
            "LEFT JOIN real_job_evaluations e ON e.real_job_id=r.id "
            "WHERE r.identity_review_state IN ('requirements_conflict',"
            "'evaluation_conflict','evaluation_input_conflict',"
            "'evaluation_input_missing','status_conflict') "
            "AND s.status IN ('new','scored') AND r.official_closed_at IS NULL "
            "AND (REVIEW_HARD_FILTER)"
        )
        cte = f"WITH eligible AS ({eligible}) "
        pairs = (
            " FROM real_job_review_cases WHERE "
            "left_real_job_id<>right_real_job_id AND "
            "(left_real_job_id IN (SELECT id FROM eligible) OR "
            "right_real_job_id IN (SELECT id FROM eligible)) GROUP BY "
            "left_real_job_id,right_real_job_id"
        )
        total = await pending_rows(
            cte
            + "SELECT count(*) AS n FROM (SELECT left_real_job_id"
            + pairs
            + ") pairs",
        )
        rows = await pending_rows(
            cte
            + "SELECT left_real_job_id,right_real_job_id,MAX(id) AS newest"
            + pairs
            + " ORDER BY newest DESC LIMIT ? OFFSET ?",
            limit,
            offset,
        )
        jobs = await pending_rows(
            cte + "SELECT id FROM eligible WHERE id IN "
            "(SELECT left_real_job_id FROM real_job_review_cases "
            "WHERE left_real_job_id<>right_real_job_id "
            "UNION SELECT right_real_job_id FROM real_job_review_cases "
            "WHERE left_real_job_id<>right_real_job_id)",
        )
        items = []
        for row in rows:
            left, right = row["left_real_job_id"], row["right_real_job_id"]
            reasons = await self.db.rows(
                "SELECT DISTINCT reason FROM real_job_review_cases "
                "WHERE left_real_job_id=? AND right_real_job_id=?",
                left,
                right,
            )
            items.append(
                {
                    "id": f"{left}:{right}",
                    "left": await self.snapshot(left),
                    "right": await self.snapshot(right),
                    "reasons": [r["reason"] for r in reasons],
                }
            )
        return {
            "total": total[0]["n"],
            "job_total": len(jobs),
            "pending_job_ids": [str(job["id"]) for job in jobs],
            "items": items,
        }

    async def prepare(
        self, left: int, right: int, decision: str, target: int | None
    ) -> dict[str, Any]:
        """Validate a current pair and capture the exact decision context.

        Args:
            left: First identity.
            right: Second identity.
            decision: Merge or separate.
            target: Identity retained by a merge.

        Returns:
            Immutable audit snapshot for the enclosing transaction.

        Raises:
            ValueError: Stale pair, invalid target, or unsafe active merge.
        """
        if decision not in {"merge", "separate"} or left == right:
            raise ValueError("Invalid review decision")
        pair = tuple(sorted((left, right)))
        if not await self.db.rows(
            "SELECT 1 FROM real_job_review_cases WHERE "
            "left_real_job_id=? AND right_real_job_id=?",
            *pair,
        ):
            raise ValueError("Review changed. Refresh the page.")
        if decision == "merge":
            if target not in pair:
                raise ValueError("Choose one of the compared jobs as the merge target")
            if await self.db.rows(
                "SELECT 1 FROM real_job_evaluations WHERE real_job_id "
                "IN (?,?) AND (stage_a_status='in_progress' OR "
                "stage_b_status='in_progress')",
                *pair,
            ):
                raise ValueError(
                    "Scoring is running. Retry the merge when it finishes."
                )
            statuses = await self.db.rows(
                "SELECT status FROM real_job_status WHERE real_job_id "
                "IN (?,?) UNION SELECT s.status FROM job_status s JOIN "
                "jobs j ON j.id=s.job_id WHERE j.real_job_id IN (?,?)",
                *pair,
                *pair,
            )
            if len({r["status"] for r in statuses} - {"new", "scored"}) > 1:
                raise ValueError(
                    "These jobs have different application statuses. "
                    "Align their statuses before merging."
                )
        return {
            "id": f"{pair[0]}:{pair[1]}",
            "left": await self.snapshot(pair[0]),
            "right": await self.snapshot(pair[1]),
            "decision": decision,
            "target_id": str(target) if target else None,
            "decided_at": datetime.now(UTC).isoformat(),
        }

    async def finish(self, left: int, right: int, snapshot: dict[str, Any]) -> None:
        """Persist the decision and clear only resolved pair review markers.

        Args:
            left: First original identity.
            right: Second original identity.
            snapshot: Validated decision snapshot.
        """
        if snapshot["decision"] == "separate":
            await self.db.execute(
                "INSERT INTO state(key,value) VALUES(?,'1') ON "
                "CONFLICT(key) DO NOTHING",
                separation_key(left, right),
            )
        await self.db.execute(
            "DELETE FROM real_job_review_cases WHERE "
            "left_real_job_id=? AND right_real_job_id=?",
            *sorted((left, right)),
        )
        key = _HISTORY + snapshot["decided_at"] + ":" + snapshot["id"]
        await self.db.execute(
            "INSERT INTO state(key,value) VALUES(?,?)", key, json.dumps(snapshot)
        )
        for real_id in (left, right):
            await self.db.execute(
                "DELETE FROM real_job_review_cases WHERE left_real_job_id=? "
                "AND right_real_job_id=? AND reason='requirements_conflict'",
                real_id,
                real_id,
            )
            await self.db.execute(
                "UPDATE real_jobs SET identity_review_state='clear' "
                "WHERE id=? AND identity_review_state IN "
                "('identity_conflict','requirements_conflict') AND NOT "
                "EXISTS(SELECT 1 FROM real_job_review_cases WHERE "
                "left_real_job_id=? OR right_real_job_id=?)",
                real_id,
                real_id,
                real_id,
            )
