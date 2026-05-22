"""Deterministic hard filters for source job postings."""

from __future__ import annotations

from dataclasses import dataclass

from jobfeed.domain.models import JobPosting


@dataclass(kw_only=True)
class HardFilters:
    """Case-insensitive title and company blocklists."""

    title_blocklist: list[str]
    company_blocklist: list[str]


def apply_hard_filters(job: JobPosting, filters: HardFilters) -> str | None:
    """Apply blocklist filters to one job posting.

    Args:
        job: Job posting to inspect.
        filters: Blocklist configuration.

    Returns:
        None when the job passes; otherwise a human-readable filter reason.
    """
    title_match = _first_match(job.title, filters.title_blocklist)
    if title_match is not None:
        return f'title contains "{title_match}"'
    company_match = _first_match(job.company, filters.company_blocklist)
    if company_match is not None:
        return f'company contains "{company_match}"'
    return None


def _first_match(value: str, blocklist: list[str]) -> str | None:
    normalized_value = value.casefold()
    for item in blocklist:
        normalized_item = item.casefold()
        if normalized_item and normalized_item in normalized_value:
            return item
    return None


__all__ = ["HardFilters", "apply_hard_filters"]
