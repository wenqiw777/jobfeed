"""SQL predicates for the current canonical Results hard-filter policy."""

from __future__ import annotations

from datetime import datetime, timedelta

from jobfeed.domain.ai_data_work import AI_DATA_COMPANY_PATTERN, AI_DATA_TITLE_PATTERN
from jobfeed.domain.filtering import _US_NAMES, _US_STATE_CODES, HardFilters
from jobfeed.domain.intermediary import DOMAINS, PUBLISHERS
from jobfeed.domain.normalize import normalize_company

_US_PATTERN = r"\m(" + "|".join(sorted(_US_STATE_CODES)) + r")\M"
_SQLITE_COMPANY = (
    "unicode_casefold(COALESCE(json_extract(e.input_facts_json,'$.company'),j.company))"
)
_SQLITE_LOCATION = (
    "COALESCE(json_extract(e.input_facts_json,'$.location'),j.location,'')"
)
_PG_COMPANY = "lower(COALESCE(e.input_facts_json::jsonb->>'company',j.company))"
_PG_LOCATION = "COALESCE(e.input_facts_json::jsonb->>'location',j.location,'')"
_SQLITE_FIRST_SEEN = "r.first_discovered_at"
_PG_FIRST_SEEN = "(SELECT MIN(d.discovered_at) FROM jobs d WHERE d.real_job_id=r.id)"
_SQLITE_ORIGINAL = "r.canonical_posted_at"
_PG_ORIGINAL = (
    "(SELECT MIN(d.posted_at) FROM jobs d WHERE d.real_job_id=r.id "
    "AND d.posted_at IS NOT NULL AND COALESCE(d.is_repost,0)=0)"
)
SQLITE_CANONICAL_DATE = _SQLITE_ORIGINAL
PG_CANONICAL_DATE = (
    f"COALESCE(CASE WHEN {_PG_ORIGINAL}<={_PG_FIRST_SEEN} "
    f"THEN {_PG_ORIGINAL} END,{_PG_FIRST_SEEN})"
)
SQLITE_FIRST_SEEN = _SQLITE_FIRST_SEEN
PG_FIRST_SEEN = _PG_FIRST_SEEN


def _direct_source_predicate() -> str:
    """Exclude intermediary representatives.

    Time complexity: O(d) for d configured intermediary domains.
    """
    names = ",".join("'" + normalize_company(name) + "'" for name in PUBLISHERS)
    clauses = [f"COALESCE(j.company_norm,'') NOT IN ({names})"]
    for field in ("j.url", "j.apply_url"):
        for domain in DOMAINS:
            for scheme in ("https", "http"):
                for authority in (domain, "%." + domain):
                    clauses.append(
                        f"lower(COALESCE({field},'')) NOT LIKE '{scheme}://{authority}/%'"
                    )
    return " AND ".join(clauses)


def _active(filters: HardFilters | None) -> bool:
    return filters is not None and any(
        (
            filters.company_blocklist,
            filters.location_allowlist,
            filters.location_blocklist,
            filters.posted_within_hours is not None,
            filters.posted_within_days is not None,
        )
    )


def _postgres_contributor_predicate() -> str:
    """Translate Python word boundaries to PostgreSQL's equivalent syntax."""
    title = AI_DATA_TITLE_PATTERN.replace(r"\b", r"\y")
    return (
        f"COALESCE(j.title,'') !~* '{title}' AND "
        f"COALESCE(j.company,'') !~* '{AI_DATA_COMPANY_PATTERN}'"
    )


def sqlite_hard_filter(  # noqa: C901 - mirrors configured filter dimensions
    filters: HardFilters | None, now: datetime
) -> tuple[str, list[object]]:
    """Build the SQLite predicate shared by Results row, count, and selection.

    Args:
        filters: Current configured company, location, and posting-age rules.
        now: Fixed clock value used to calculate age cutoffs consistently.

    Returns:
        A SQL predicate and its positional bind values in placeholder order.
    """
    intrinsic = (
        "jobfeed_intermediary(j.company,j.url,j.apply_url)=0 AND "
        "jobfeed_ai_data_work(j.title,j.company) IS NULL"
    )
    if not _active(filters):
        return intrinsic, []
    assert filters is not None
    clauses: list[str] = [intrinsic]
    args: list[object] = []
    for item in filters.company_blocklist:
        if item:
            clauses.append(f"instr({_SQLITE_COMPANY},?)=0")
            args.append(item.casefold())
    loc_lower = f"unicode_casefold({_SQLITE_LOCATION})"
    if any(filters.location_allowlist):
        allow: list[str] = []
        for item in filters.location_allowlist:
            if item:
                allow.append(f"instr({loc_lower},?)>0")
                args.append(item.casefold())
        if any(
            item.casefold() == "united states" for item in filters.location_allowlist
        ):
            allow.append(f"jobfeed_us_location({_SQLITE_LOCATION})=1")
        clauses.append(f"({_SQLITE_LOCATION}='' OR ({' OR '.join(allow)}))")
    for item in filters.location_blocklist:
        if item:
            clauses.append(f"instr({loc_lower},?)=0")
            args.append(item.casefold())
    if filters.posted_within_hours is not None:
        normal = (now - timedelta(hours=filters.posted_within_hours)).isoformat()
        indeed = (
            now - timedelta(hours=max(filters.posted_within_hours, 48))
        ).isoformat()
        cutoff = "CASE WHEN unicode_casefold(j.platform)='indeed' THEN ? ELSE ? END"
        args.extend((indeed, normal))
        clauses.append(_sqlite_date_clause(cutoff))
    elif filters.posted_within_days is not None:
        normal = (now - timedelta(days=filters.posted_within_days)).isoformat()
        big = (now - timedelta(days=filters.big_company_days)).isoformat()
        cutoff, date_args = _sqlite_day_cutoff(filters, big, normal)
        args.extend(date_args)
        clauses.append(_sqlite_date_clause(cutoff))
    return " AND ".join(clauses) if clauses else "1=1", args


def _sqlite_date_clause(cutoff: str) -> str:
    return f"julianday({SQLITE_CANONICAL_DATE})>=julianday({cutoff})"


def _sqlite_day_cutoff(
    filters: HardFilters, big: str, normal: str
) -> tuple[str, list[object]]:
    """Bind the optional big-company exception before the normal day cutoff."""
    if not filters.big_company_list:
        return "?", [normal]
    checks: list[str] = []
    args: list[object] = []
    for item in filters.big_company_list:
        if item:
            checks.append(f"instr({_SQLITE_COMPANY},?)>0")
            args.append(item.casefold())
    cutoff = f"CASE WHEN {' OR '.join(checks) if checks else '0'} THEN ? ELSE ? END"
    return cutoff, [*args, big, normal]


def postgres_hard_filter(  # noqa: C901 - mirrors configured filter dimensions
    filters: HardFilters | None, now: datetime, *, first_param: int
) -> tuple[str, list[object]]:
    """Build the PostgreSQL equivalent of the canonical Results predicate.

    Args:
        filters: Current configured company, location, and posting-age rules.
        now: Fixed clock value used to calculate age cutoffs consistently.
        first_param: Number of the first PostgreSQL bind variable emitted here.

    Returns:
        A SQL predicate and its ordered PostgreSQL bind values.
    """
    intrinsic = f"{_direct_source_predicate()} AND {_postgres_contributor_predicate()}"
    if not _active(filters):
        return intrinsic, []
    assert filters is not None
    clauses: list[str] = [intrinsic]
    args: list[object] = []

    def param(value: object) -> str:
        args.append(value)
        return f"${first_param + len(args) - 1}"

    for item in filters.company_blocklist:
        if item:
            clauses.append(f"strpos({_PG_COMPANY},{param(item.casefold())})=0")
    loc_lower = f"lower({_PG_LOCATION})"
    if any(filters.location_allowlist):
        allow = [
            f"strpos({loc_lower},{param(item.casefold())})>0"
            for item in filters.location_allowlist
            if item
        ]
        if any(
            item.casefold() == "united states" for item in filters.location_allowlist
        ):
            allow.extend(f"strpos({loc_lower},{param(name)})>0" for name in _US_NAMES)
            allow.append(
                f"regexp_replace(upper({_PG_LOCATION}), '\\.', '', 'g') "
                f"~ {param(_US_PATTERN)}"
            )
        clauses.append(f"({_PG_LOCATION}='' OR ({' OR '.join(allow)}))")
    for item in filters.location_blocklist:
        if item:
            clauses.append(f"strpos({loc_lower},{param(item.casefold())})=0")
    if filters.posted_within_hours is not None:
        normal = now - timedelta(hours=filters.posted_within_hours)
        indeed = now - timedelta(hours=max(filters.posted_within_hours, 48))
        cutoff = (
            f"CASE WHEN lower(j.platform)='indeed' "
            f"THEN {param(indeed)}::timestamptz "
            f"ELSE {param(normal)}::timestamptz END"
        )
        clauses.append(_pg_date_clause(cutoff))
    elif filters.posted_within_days is not None:
        normal = now - timedelta(days=filters.posted_within_days)
        if filters.big_company_list:
            checks = [
                f"strpos({_PG_COMPANY},{param(item.casefold())})>0"
                for item in filters.big_company_list
                if item
            ]
            big = now - timedelta(days=filters.big_company_days)
            big_match = " OR ".join(checks) if checks else "false"
            cutoff = (
                f"CASE WHEN {big_match} THEN {param(big)}::timestamptz "
                f"ELSE {param(normal)}::timestamptz END"
            )
        else:
            cutoff = param(normal)
        clauses.append(_pg_date_clause(cutoff))
    return " AND ".join(clauses) if clauses else "1=1", args


def _pg_date_clause(cutoff: str) -> str:
    return f"{PG_CANONICAL_DATE}>={cutoff}"
