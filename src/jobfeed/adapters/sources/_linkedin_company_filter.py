"""User-excluded intermediary publishers in LinkedIn discovery."""

from jobfeed.domain.intermediary import BLOCKED_PUBLISHERS

_BLOCKED_COMPANIES = frozenset(
    {
        "jobright.ai",
        "jobright",
        "jobwright",
        "jobs via dice",
        "dice",
        *BLOCKED_PUBLISHERS,
    }
)


def blocked_linkedin_company(company: str | None) -> bool:
    """Match whole company names, ignoring case and repeated whitespace.

    Args:
        company: Company name to check.

    Returns:
        Whether the company is on the configured exclusion list.
    """
    return " ".join((company or "").casefold().split()) in _BLOCKED_COMPANIES
