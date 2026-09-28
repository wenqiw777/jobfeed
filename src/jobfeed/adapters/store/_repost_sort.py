"""Consistent Triage ordering before SQL pagination, including snapshots."""


def triage_sorts(
    *, job: str, score: str, postgres: bool = False, date_expr: str | None = None
) -> dict[str, str]:
    date = date_expr or f"{job}.discovered_at"
    day = (
        f"({date} AT TIME ZONE 'America/Detroit')::date"
        if postgres
        else f"jobfeed_discovery_day({date})"
    )
    band = (
        f"LEAST(({score}) / 10, 9)"
        if postgres
        else f"MIN(CAST(({score}) / 10 AS INTEGER), 9)"
    )
    repost = f"CASE WHEN {job}.is_repost=1 THEN 1 ELSE 0 END"
    tie = f"{date} DESC, {job}.id DESC"
    result = {}
    for direction in ("asc", "desc"):
        order = direction.upper()
        result[f"triage_posted_{direction}"] = (
            f"{day} {order}, {repost}, {date} {order}, {job}.id DESC"
        )
        result[f"triage_score_{direction}"] = (
            f"({score}) IS NULL, {band} {order}, {repost}, {score} {order}, {tie}"
        )
    return result
