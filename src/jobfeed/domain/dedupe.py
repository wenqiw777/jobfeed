"""Pure twin-clustering + representative-selection primitive (no IO).

The DB keeps every source row (no scan-time dedup). This module clusters rows
that describe the SAME real job across sources by the already-persisted
``(company_norm, title_norm)`` soft key, and picks one representative per
cluster so downstream stages (Phase 5 candidate selection) score the job once.

NOTE: status-priority is deliberately NOT a key in ``pick_representatives`` —
that primitive operates on ``JobPosting`` (pre-status) for the Phase 5 scoring
funnel. The Phase 8 status-aware display fold is the separate
``pick_display_representatives``, which layers a status-priority class (an
``applied``/``shortlisted`` twin wins) AHEAD of the quality key below,
mirroring legacy ``web/routes/jobs.py:_dedup_rep_order``. Do not add a status
key to ``pick_representatives``.

Representative decision ladder (first decisive key wins)::

    cluster (members sharing a twin_key)
        │
        ├─ 1. highest JD quality        (quality_rank: FULL=5 … ABANDONED=0, None=-1)
        ├─ 2. source priority           (ATS family {greenhouse,ashby,lever} >
        │                                speedyapply > linkedin >
        │                                {linkedin_guest, linkedin_jobspy
        │                                (historical)} > indeed > unknown-last)
        ├─ 3. most recent posted_at      (NULLS-LAST; NOT discovered_at)
        └─ 4. stable (platform, canonical_id)
                │
                └─► representative
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from jobfeed.domain.models import JobPosting
from jobfeed.domain.normalize import normalize, normalize_company
from jobfeed.domain.quality import quality_rank

# Source-priority ranks over REAL platform values (lower rank = higher priority).
# There is NO "ats" platform value — ATS rows carry the vendor name. The ATS
# family shares the top tier. Unknown platforms fall back to a last-place rank.
_ATS_FAMILY_RANK = 0
_PLATFORM_RANK: dict[str, int] = {
    "greenhouse": _ATS_FAMILY_RANK,
    "ashby": _ATS_FAMILY_RANK,
    "lever": _ATS_FAMILY_RANK,
    "speedyapply": 1,
    "linkedin": 2,
    "linkedin_guest": 3,
    "jobright": 3,
    # Tombstone: the linkedin_jobspy SOURCE is removed, but rows persisted
    # before the removal keep their historical priority (shared tier with
    # linkedin_guest, like the ATS family shares rank 0). Do not delete this
    # entry while such rows can exist in a database.
    "linkedin_jobspy": 3,
    "indeed": 4,
}
_UNKNOWN_PLATFORM_RANK = 99

# Phase 8 display fold: status-priority classes layered AHEAD of the Decision 8
# key. In-flight applications outrank the shortlist tier, which outranks
# everything else; missing/unknown statuses take the lowest-priority class.
_APPLICATION_CLASS = 0
_SHORTLIST_CLASS = 1
_DEFAULT_STATUS_CLASS = 2
_STATUS_CLASS: dict[str, int] = {
    "applied": _APPLICATION_CLASS,
    "interviewing": _APPLICATION_CLASS,
    "offer": _APPLICATION_CLASS,
    "shortlisted": _SHORTLIST_CLASS,
    "awaiting_referral": _SHORTLIST_CLASS,
}

#: The in-flight application statuses — the top display-fold class. The
#: jobs-view fold pulls twins in these statuses into a tab-filtered corpus
#: so an applied posting suppresses its still-in-queue siblings (plan D9).
INFLIGHT_STATUSES: frozenset[str] = frozenset(
    status
    for status, status_class in _STATUS_CLASS.items()
    if status_class == _APPLICATION_CLASS
)


@dataclass(frozen=True)
class TwinCluster:
    """A group of postings that describe the same real job, plus its winner."""

    key: tuple[str, str]
    members: list[JobPosting]
    representative: JobPosting


def twin_key(job: JobPosting) -> tuple[str, str]:
    """Return the soft dedup key ``(company_norm, title_norm)`` for a posting.

    Args:
        job: Posting to key.

    Returns:
        Tuple of normalized company and normalized title.
    """
    return (normalize_company(job.company), normalize(job.title))


def _platform_rank(platform: str) -> int:
    """Return the source-priority rank for a platform (lower = higher priority).

    Args:
        platform: The posting's ``platform`` tag.

    Returns:
        Rank from the platform map, or a last-place rank for unknown platforms.
    """
    return _PLATFORM_RANK.get(platform, _UNKNOWN_PLATFORM_RANK)


def _recency_key(job: JobPosting) -> float:
    """Return a sortable recency key (smaller = more recent, NULLS-LAST).

    Uses ``posted_at`` only — ``discovered_at`` is the near-constant scan-start
    time and is a useless tiebreak. A missing ``posted_at`` sorts last.

    Args:
        job: Posting whose recency to key.

    Returns:
        Negated POSIX timestamp when ``posted_at`` is set, else ``+inf``.
    """
    if job.posted_at is None:
        return float("inf")
    return -job.posted_at.timestamp()


def _representative_sort_key(job: JobPosting) -> tuple[int, int, float, str, str]:
    """Build the ordered representative key; the cluster's ``min`` wins.

    Keys in decisive order: highest quality, highest source priority, most
    recent ``posted_at`` (NULLS-LAST), then the stable ``(platform,
    canonical_id)`` final tiebreak. Smaller tuples win, so quality and priority
    are negated/normalized accordingly.

    Args:
        job: Candidate posting.

    Returns:
        Sort key tuple (smaller is a better representative).
    """
    return (
        -quality_rank(job.jd_quality),
        _platform_rank(job.platform),
        _recency_key(job),
        job.platform,
        job.canonical_id,
    )


def _pick_representative(members: list[JobPosting]) -> JobPosting:
    """Select the representative of a cluster via the Decision 8 key.

    Args:
        members: Non-empty list of clustered postings.

    Returns:
        The winning posting.
    """
    return min(members, key=_representative_sort_key)


def cluster_twins(jobs: Iterable[JobPosting]) -> list[TwinCluster]:
    """Group postings into twin clusters and pick each cluster's representative.

    Postings are grouped by ``twin_key`` EXCEPT any posting whose normalized
    company OR title is blank (``""``): such a posting forms its OWN singleton
    cluster and is never folded with other blank-norm rows. This mirrors the
    legacy ``PARTITION BY COALESCE(company_norm, '__row_'||id)`` guard — a
    per-job unique discriminator keeps unrelated blank-norm rows (symbol-only
    names, null-company JobSpy rows) from collapsing into one bogus cluster.

    Cluster order follows first-seen grouping order for determinism.

    Args:
        jobs: Postings to cluster (any iterable).

    Returns:
        One ``TwinCluster`` per group, each with its members and representative.
    """
    groups: dict[object, list[JobPosting]] = {}
    keys: dict[object, tuple[str, str]] = {}
    for index, job in enumerate(jobs):
        key = twin_key(job)
        company_norm, title_norm = key
        # Blank company OR title → unique per-job discriminator so blanks never
        # fold together; folded rows share the twin_key itself.
        has_both_norms = bool(company_norm and title_norm)
        group_id: object = key if has_both_norms else ("__blank__", index)
        bucket = groups.get(group_id)
        if bucket is None:
            groups[group_id] = [job]
            keys[group_id] = key
        else:
            bucket.append(job)
    return [
        TwinCluster(
            key=keys[group_id],
            members=members,
            representative=_pick_representative(members),
        )
        for group_id, members in groups.items()
    ]


def pick_representatives(jobs: Iterable[JobPosting]) -> list[JobPosting]:
    """Return exactly one representative posting per twin cluster.

    Args:
        jobs: Postings to cluster and reduce.

    Returns:
        The representative of each cluster, in cluster order.
    """
    return [cluster.representative for cluster in cluster_twins(jobs)]


def _status_class(job: JobPosting, status_by_id: Mapping[str, str]) -> int:
    """Return the display-fold status-priority class for a posting.

    Args:
        job: Posting whose class to resolve.
        status_by_id: Mapping of job id → current workflow status.

    Returns:
        0 for in-flight applications ({applied, interviewing, offer}), 1 for
        the shortlist tier ({shortlisted, awaiting_referral}), 2 for every
        other status and for id-less or unmapped postings.
    """
    if job.id is None:
        return _DEFAULT_STATUS_CLASS
    status = status_by_id.get(job.id)
    if status is None:
        return _DEFAULT_STATUS_CLASS
    return _STATUS_CLASS.get(status, _DEFAULT_STATUS_CLASS)


def pick_display_representatives(
    jobs: Iterable[JobPosting],
    status_by_id: Mapping[str, str],
) -> list[JobPosting]:
    """Return one status-aware display representative per twin cluster.

    The Phase 8 display fold reserved by this module's header note: a
    status-priority class is layered AHEAD of ``_representative_sort_key``,
    so an in-flight ``applied`` twin wins its cluster even when another twin
    has a better Decision 8 (quality/source/recency) key. Ties inside one
    status class fall through to the existing key unchanged.

    Args:
        jobs: Postings to cluster and reduce.
        status_by_id: Mapping of job id → current workflow status. Postings
            whose id is missing from the mapping (or is None) take the
            lowest-priority class.

    Returns:
        The display representative of each cluster, in cluster order.
    """

    def display_key(
        job: JobPosting,
    ) -> tuple[int, tuple[int, int, float, str, str]]:
        return (_status_class(job, status_by_id), _representative_sort_key(job))

    return [min(cluster.members, key=display_key) for cluster in cluster_twins(jobs)]


__all__ = [
    "INFLIGHT_STATUSES",
    "TwinCluster",
    "cluster_twins",
    "pick_display_representatives",
    "pick_representatives",
    "twin_key",
]
