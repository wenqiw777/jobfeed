"""Results identity: trusted requisitions and guarded JD copies."""

import re
from collections.abc import Mapping

from jobfeed.domain.dedupe import _representative_sort_key, _status_class
from jobfeed.domain.display_content import _Prepared, _same_prepared
from jobfeed.domain.external_identity import external_identity
from jobfeed.domain.models import JobPosting
from jobfeed.domain.normalize import normalize, normalize_company

_LINKEDIN = frozenset({"linkedin", "linkedin_guest", "linkedin_jobspy"})
_MIN_EXACT_BODY_LENGTH = 200


def posting_location(location: str | None) -> str:
    """Conservative shared location normalization for display and workflow.

    Args:
        location: Source location text.

    Returns:
        Case-normalized location with collapsed whitespace.
    """
    return " ".join((location or "").casefold().split())


def posting_identity(job: JobPosting) -> str | None:
    """Use only explicit vendor identity; never guess from employer and title.

    Args:
        job: Posting to inspect.

    Returns:
        Explicit source identity, or None when unavailable.
    """
    if job.external_identity:
        return job.external_identity
    if job.platform == "jobright" and job.canonical_id:
        return f"jobright:{job.canonical_id}"
    return external_identity(job.url)


def pick_posting_representatives(
    jobs: list[JobPosting], status_by_id: Mapping[str, str]
) -> list[JobPosting]:
    """Fold aliases of one known requisition, preferring workflow state.

    A single vendor requisition can advertise multiple locations and title
    variants. Missing identities remain separate unless long JD content
    supports an exact or guarded near-copy match.

    Args:
        jobs: Postings to group or persist.
        status_by_id: Current workflow status by stored job ID.

    Returns:
        One representative per compatible posting group.
    """
    groups = _posting_groups(jobs)
    return [
        min(
            members,
            key=lambda job: (
                1
                if job.id is not None
                and status_by_id.get(job.id) in {"ignored", "archived"}
                else 2 * _status_class(job, status_by_id),
                job.platform not in _LINKEDIN,
                _representative_sort_key(job),
            ),
        )
        for members in groups.values()
    ]


def _posting_groups(  # noqa: C901 - identity and guarded content joins share roots
    jobs: list[JobPosting],
) -> dict[int, list[JobPosting]]:
    """Join trusted IDs and long content copies without changing stored IDs.

    Time complexity: O(N² * T) worst case, for postings N and description length T.
    """
    parents = list(range(len(jobs)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def join(left: int, right: int) -> None:
        parents[root(right)] = root(left)

    by_identity: dict[str, int] = {}
    by_title_company: dict[tuple[str, str], list[int]] = {}
    for index, job in enumerate(jobs):
        if identity := posting_identity(job):
            if identity in by_identity:
                join(by_identity[identity], index)
            else:
                by_identity[identity] = index
        if job.jd_text:
            title = normalize(job.title)
            company = normalize_company(job.company.split("+", 1)[0].strip())
            if title and company:
                by_title_company.setdefault((title, company), []).append(index)

    prepared = [_Prepared(job) for job in jobs]
    word_counts = [len(re.findall(r"\w+", job.jd_text or "")) for job in jobs]
    for candidates in by_title_company.values():
        for position, left in enumerate(candidates):
            for right in candidates[position + 1 :]:
                if root(left) == root(right):
                    continue
                if (
                    jobs[left].jd_text == jobs[right].jd_text
                    and len(prepared[left].normalized_body()) >= _MIN_EXACT_BODY_LENGTH
                ):
                    join(left, right)
                    continue
                smaller = min(word_counts[left], word_counts[right])
                larger = max(word_counts[left], word_counts[right])
                if smaller >= 0.97 * larger and _same_prepared(
                    prepared[left], prepared[right]
                ):
                    join(left, right)

    groups: dict[int, list[JobPosting]] = {}
    for index, job in enumerate(jobs):
        groups.setdefault(root(index), []).append(job)
    return groups
