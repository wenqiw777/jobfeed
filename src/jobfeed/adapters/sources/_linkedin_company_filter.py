"""User-excluded intermediary publishers in LinkedIn discovery."""

_BLOCKED_COMPANIES = frozenset(
    {"jobright.ai", "jobright", "jobwright", "yara ai", "jobs via dice", "dice"}
)


def blocked_linkedin_company(company: str | None) -> bool:
    """Match whole company names, ignoring case and repeated whitespace."""
    return " ".join((company or "").casefold().split()) in _BLOCKED_COMPANIES
