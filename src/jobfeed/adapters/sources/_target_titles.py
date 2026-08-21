"""Small deterministic title filter for broad ATS company boards."""

from __future__ import annotations

import re
from collections.abc import Iterable

from jobfeed.domain.models import JobPosting

_NON_ROLE_TOKENS = frozenset(
    {
        "entry",
        "graduate",
        "intern",
        "internship",
        "junior",
        "level",
        "new",
        "principal",
        "senior",
        "staff",
    }
)
_TOKEN_ALIASES = {
    "developers": "developer",
    "development": "developer",
    "engineers": "engineer",
    "engineering": "engineer",
    "sde": "software engineer",
    "swe": "software engineer",
}


def filter_target_titles(
    jobs: Iterable[JobPosting], target_titles: list[str]
) -> list[JobPosting]:
    """Keep jobs matching any user-confirmed role phrase.

    Seniority words are ignored because many ATS titles omit them. Empty target
    titles preserve the legacy unfiltered behavior for existing configs.

    Args:
        jobs: Candidate postings from one source fetch.
        target_titles: User-confirmed role phrases.

    Returns:
        Postings whose normalized title matches at least one target role.
    """
    targets = [_role_tokens(value) for value in target_titles if value.strip()]
    targets = [tokens for tokens in targets if tokens]
    if not targets:
        return list(jobs)
    return [
        job
        for job in jobs
        if any(target <= _role_tokens(job.title) for target in targets)
    ]


def _role_tokens(value: str) -> set[str]:
    normalized = value.lower()
    for source, replacement in _TOKEN_ALIASES.items():
        normalized = re.sub(rf"\b{source}\b", replacement, normalized)
    return {
        token
        for token in re.findall(r"[a-z0-9]+", normalized)
        if token not in _NON_ROLE_TOKENS and not token.isdigit()
    }


__all__ = ["filter_target_titles"]
