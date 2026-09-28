"""Display-fold step of the jobs list composition (plan D9).

Split out of ``services/jobs_view.py`` to keep both modules under the
300-line gate. Results use trusted posting identity and location, without JD
comparison; this module adds the corpus plumbing —
including pulling in-flight (applied/interviewing/offer) twins of the
corpus rows into the fold input even when the tab excludes them, so a
posting applied on one platform suppresses its still-in-queue siblings.
"""

from __future__ import annotations

from collections import defaultdict
from threading import Lock

from jobfeed.domain.dedupe import (
    INFLIGHT_STATUSES,
    pick_display_representatives,
)
from jobfeed.domain.display_posting import (
    pick_posting_representatives,
    posting_identity,
)
from jobfeed.domain.models_views import JobsViewRow
from jobfeed.domain.normalize import normalize, normalize_company
from jobfeed.ports.store_views import StoreViewsMixin

_HIDDEN_RESULTS_STATUSES = INFLIGHT_STATUSES | {"ignored", "archived"}


class DisplayFoldCache:
    """Bounded, exact-input reuse of expensive content grouping across page reads.

    Only representative IDs are cached. Current row scores and decisions are
    always returned. Every comparison checks actual input values, in order;
    edits, new rows, status changes and freshness filtering invalidate reuse.
    """

    def __init__(self) -> None:
        self._entries: list[tuple[list[tuple[object, ...]], list[str | None]]] = []
        self._lock = Lock()

    def fold(self, rows: list[JobsViewRow]) -> list[JobsViewRow]:
        signature: list[tuple[object, ...]] = [
            (
                row.job.id,
                row.job.platform,
                row.job.canonical_id,
                row.job.url,
                row.job.external_identity,
                row.job.company,
                row.job.title,
                row.job.location,
                row.job.jd_text,
                row.job.jd_quality,
                row.job.posted_at,
                row.status,
            )
            for row in rows
        ]
        with self._lock:
            for previous, ids in self._entries:
                if previous == signature:
                    current = {row.job.id: row for row in rows}
                    return [current[identifier] for identifier in ids]
            result = _fold_to_display_representatives(rows)
            self._entries.append((signature, [row.job.id for row in result]))
            # Four supported Triage orders, with bounded retained JD content.
            del self._entries[:-4]
            return result


async def fold_with_inflight_twins(
    store: StoreViewsMixin,
    rows: list[JobsViewRow],
    *,
    twin_limit: int,
) -> list[JobsViewRow]:
    """Fold twin clusters, letting in-flight out-of-corpus twins win (D9).

    The corpus is tab-filtered, so an applied/interviewing/offer twin of a
    queue row is never in it — folding the corpus alone could not suppress
    a posting whose twin is already applied. Pull those twins into the fold
    input, then keep only representatives that are corpus rows: a cluster
    whose winner is in-flight drops off the page.

    Args:
        store: Jobs-view store capability (twin lookup).
        rows: Corpus rows to fold (store rows always carry a job id).
        twin_limit: Bound on the in-flight twin lookup.

    Returns:
        One corpus row per twin cluster whose representative is in the
        corpus, in cluster (first-seen) order.
    """
    keys = sorted(
        {
            ("__external_identity__", identity)
            for row in rows
            if (identity := posting_identity(row.job))
        }
    )
    corpus_ids = {row.job.id for row in rows}
    twins: list[JobsViewRow] = []
    if keys:
        twins = await store.list_twin_rows_by_status(
            keys,
            statuses=sorted(_HIDDEN_RESULTS_STATUSES),
            limit=twin_limit,
            include_jd_text=False,
        )
    # A non-queue corpus (e.g. tab=all) can already contain in-flight rows;
    # drop the overlap so each posting enters the fold once.
    extras = [row for row in twins if row.job.id not in corpus_ids]
    await _load_candidate_bodies(store, rows + extras)
    folded = _fold_to_posting_representatives(rows + extras)
    for row in rows + extras:
        row.job.jd_text = None
    return [row for row in folded if row.job.id in corpus_ids]


async def fold_provisional(
    store: StoreViewsMixin, rows: list[JobsViewRow]
) -> list[JobsViewRow]:
    """Fold the first page with content while exact cross-status lookup runs."""
    await _load_candidate_bodies(store, rows)
    folded = _fold_to_posting_representatives(rows)
    for row in rows:
        row.job.jd_text = None
    return folded


async def _load_candidate_bodies(
    store: StoreViewsMixin, rows: list[JobsViewRow]
) -> None:
    """Hydrate only same-title/employer candidates for content comparison."""
    groups: dict[tuple[str, str], list[JobsViewRow]] = defaultdict(list)
    for row in rows:
        title = normalize(row.job.title)
        company = normalize_company(row.job.company.split("+", 1)[0].strip())
        if title and company:
            groups[(title, company)].append(row)
    candidates = [
        row for members in groups.values() if len(members) > 1 for row in members
    ]
    missing_ids = [
        row.job.id
        for row in candidates
        if row.job.id is not None and row.job.jd_text is None
    ]
    if not missing_ids:
        return
    bodies = await store.load_display_bodies(missing_ids)
    for row in candidates:
        if row.job.id in bodies:
            row.job.jd_text = bodies[row.job.id]


def _fold_to_posting_representatives(rows: list[JobsViewRow]) -> list[JobsViewRow]:
    """Select known same-location posting aliases without loading JD bodies."""
    row_by_id = {row.job.id: row for row in rows if row.job.id is not None}
    representatives = pick_posting_representatives(
        [row.job for row in rows],
        {job_id: row.status for job_id, row in row_by_id.items()},
    )
    return [row_by_id[job.id] for job in representatives if job.id is not None]


def _fold_to_display_representatives(rows: list[JobsViewRow]) -> list[JobsViewRow]:
    """Fold twin clusters to one status-aware display representative each.

    Delegates clustering and selection to the pure domain fold (plan D9) over
    the rows' jobs, then maps the winning jobs back to their rows via an
    id-keyed dict. Time complexity: O(n) over the corpus.

    Args:
        rows: View rows to fold (store rows always carry a job id).

    Returns:
        One row per twin cluster, in cluster (first-seen) order.
    """
    row_by_id = {row.job.id: row for row in rows if row.job.id is not None}
    representatives = pick_display_representatives(
        [row.job for row in rows],
        {job_id: row.status for job_id, row in row_by_id.items()},
    )
    return [row_by_id[job.id] for job in representatives if job.id is not None]


__all__ = ["fold_with_inflight_twins"]
