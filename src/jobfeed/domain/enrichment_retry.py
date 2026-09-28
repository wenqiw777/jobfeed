"""Bounded retry scheduling for incomplete job descriptions."""

import re
from datetime import datetime, timedelta


def retry_policy(
    error: str, now: datetime, *, code: str | None = None
) -> tuple[str, datetime]:
    """Classify an unsuccessful attempt and choose its next eligible time.

    Args:
        error: Failure detail from the enrichment attempt.
        now: Current timestamp used for age or retry calculations.
        code: Optional source-specific failure category.

    Returns:
        Failure category and the earliest next retry timestamp.
    """
    detail = error.lower()
    if code == "missing_permission" or "permission" in detail:
        # No timed native retry: each explicit scan performs the extension's
        # local permission preflight and only opens an authorized target.
        return "missing_permission", datetime.max.replace(tzinfo=now.tzinfo)
    if code in {
        "auth_required",
        "script_unavailable",
        "parse_failed",
        "no_complete_jd",
    }:
        return code, now + timedelta(days=7)
    if code in {"page_timeout", "rate_limited", "transient"}:
        return code, now + timedelta(hours=6)
    if any(
        word in detail
        for word in (
            "timeout",
            "timed out",
            "network",
            "disconnect",
        )
    ) or re.search(r"\bhttp\s+(?:429|500|502|503|504)\b", detail):
        return "transient", now + timedelta(hours=6)
    if "identity" in detail:
        return "identity_not_found", now + timedelta(days=7)
    return "no_complete_jd", now + timedelta(days=7)
