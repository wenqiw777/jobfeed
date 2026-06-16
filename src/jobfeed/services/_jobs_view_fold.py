"""Display-fold step of the jobs list composition (plan D9).

Split out of ``services/jobs_view.py`` to keep both modules under the
300-line gate. The fold itself is the pure domain
``pick_display_representatives``; this module adds the corpus plumbing —
including pulling in-flight (applied/interviewing/offer) twins of the
corpus rows into the fold input even when the tab excludes them, so a
posting applied on one platform suppresses its still-in-queue siblings.
"""

from __future__ import annotations

from jobfeed.domain.dedupe import INFLIGHT_STATUSES, pick_display_representatives
from jobfeed.domain.models_views import JobsViewRow
from jobfeed.ports.store_views import StoreViewsMixin


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
            (row.company_norm, row.title_norm)
            for row in rows
            if row.company_norm and row.title_norm
        }
    )
    corpus_ids = {row.job.id for row in rows}
    twins: list[JobsViewRow] = []
    if keys:
        twins = await store.list_twin_rows_by_status(
            keys, statuses=sorted(INFLIGHT_STATUSES), limit=twin_limit
        )
    # A non-queue corpus (e.g. tab=all) can already contain in-flight rows;
    # drop the overlap so each posting enters the fold once.
    extras = [row for row in twins if row.job.id not in corpus_ids]
    folded = _fold_to_display_representatives(rows + extras)
    return [row for row in folded if row.job.id in corpus_ids]


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
