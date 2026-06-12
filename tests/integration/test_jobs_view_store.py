"""Jobs view query integration tests against PostgreSQL (Phase 8).

Pins ``query_jobs_view``: the pending-JD predicate (one fixture row per
exclusion reason), case-insensitive search, statuses+freshness AND-composition,
closed-row handling per tab, require_verdict, tab_counts agreement with
per-tab totals, and bounded pagination with a true total.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import (
    FitAnalysis,
    QualityBand,
    StageAResult,
    StageBResult,
    TransitionRequest,
    Verdict,
)
from jobfeed.domain.models_views import VALID_TABS, JobsViewPage, JobsViewQuery
from tests.support.factories import make_job

pytestmark = pytest.mark.postgres

_STAGE_A_SCORE = 80
_STAGE_B_FIT_SCORE = 77
_MATRIX_TOTAL = 5
_PAGE_LIMIT = 2
_PAGE_OFFSET = 2
# Stripe rows in the status matrix: new, scored, shortlisted, archived,
# closed-scored — split per tab below.
_STRIPE_QUEUE_COUNT = 3  # new + scored + shortlisted (closed row excluded)
_STRIPE_SCORED_COUNT = 2  # scored + closed-scored (Library tabs keep closed)
_STRIPE_ALL_COUNT = 5


def _stage_a(score: int = _STAGE_A_SCORE) -> StageAResult:
    """Build a minimal completed Stage A result."""
    return StageAResult(
        score=score,
        one_line="fits",
        timing_eligible="yes",
        model="mock/stage-a",
        prompt_hash="prompt-a",
        resume_hash="resume-a",
    )


def _stage_b(score: int = _STAGE_B_FIT_SCORE) -> StageBResult:
    """Build a minimal completed Stage B result with an apply verdict."""
    return StageBResult(
        verdict=Verdict.APPLY,
        jd_summary="summary",
        fit_analysis=FitAnalysis(score=score, strengths=[], gaps=[]),
        resume_hooks=[],
        model="mock/stage-b",
        prompt_hash="prompt-b",
        resume_hash="resume-b",
    )


async def _insert(
    store: PostgresStore,
    canonical_id: str,
    *,
    status: str | None = None,
    **overrides: object,
) -> str:
    """Save a job fixture, optionally force-transitioning its status.

    Args:
        store: Connected store.
        canonical_id: Natural identity (also used as default title suffix).
        status: Optional status to force-set after insert.
        **overrides: JobPosting field overrides for ``make_job``.

    Returns:
        Store-assigned job id.
    """
    saved = await store.save_job(make_job(canonical_id, **overrides))
    if status is not None:
        await store.transition_status(
            TransitionRequest(job_id=saved.job_id, new_status=status, force=True)
        )
    return saved.job_id


def _ids(page: JobsViewPage) -> set[str]:
    """Collect the canonical ids of a page's rows."""
    return {row.job.canonical_id for row in page.rows}


async def test_pending_jd_excludes_each_reason(store: PostgresStore) -> None:
    """pending_jd keeps only open, unscored, non-archived rows with no usable JD."""
    await _insert(store, "pj-null")  # jd_quality NULL → pending
    await _insert(store, "pj-missing", jd_quality=QualityBand.MISSING)  # pending
    await _insert(store, "pj-good", jd_quality=QualityBand.GOOD)  # quality ≥ stub
    scored_id = await _insert(store, "pj-stage-a")
    await store.save_stage_a(scored_id, _stage_a())  # has Stage A score
    await _insert(store, "pj-archived", status="archived")
    await _insert(store, "pj-ignored", status="ignored")
    closed_id = await _insert(store, "pj-closed")
    await store.mark_job_closed(job_id=closed_id, closed_at=datetime.now(UTC))

    page = await store.query_jobs_view(JobsViewQuery(tab="pending_jd"))

    assert _ids(page) == {"pj-null", "pj-missing"}
    assert page.total == len(page.rows)


async def test_search_is_case_insensitive_on_company_and_title(
    store: PostgresStore,
) -> None:
    """'stripe' matches company 'Stripe' OR a title containing it."""
    await _insert(store, "se-company", company="Stripe")
    await _insert(store, "se-title", company="Acme", title="Stripe API Engineer")
    await _insert(store, "se-other", company="Datadog")

    page = await store.query_jobs_view(JobsViewQuery(tab="all", search="stripe"))

    assert _ids(page) == {"se-company", "se-title"}


async def test_search_treats_like_wildcards_literally(store: PostgresStore) -> None:
    """A '%' in the search term matches a literal percent, not any-sequence."""
    await _insert(store, "wc-percent", company="100% Remote")
    await _insert(store, "wc-other", company="100x Remote")

    matched = await store.query_jobs_view(JobsViewQuery(tab="all", search="0% r"))
    unmatched = await store.query_jobs_view(JobsViewQuery(tab="all", search="0%x"))

    assert _ids(matched) == {"wc-percent"}
    assert _ids(unmatched) == set()


async def test_statuses_and_posted_within_and_compose(store: PostgresStore) -> None:
    """The explicit statuses filter ANDs with the discovered_at freshness cut."""
    now = datetime.now(UTC)
    await _insert(
        store, "sw-fresh-scored", status="scored", discovered_at=now - timedelta(days=1)
    )
    await _insert(
        store, "sw-old-scored", status="scored", discovered_at=now - timedelta(days=30)
    )
    await _insert(store, "sw-fresh-new", discovered_at=now - timedelta(days=1))

    page = await store.query_jobs_view(
        JobsViewQuery(tab="all", statuses=("scored",), posted_within_days=7)
    )

    assert _ids(page) == {"sw-fresh-scored"}
    assert page.total == 1


async def test_queue_excludes_closed_rows_but_all_includes_them(
    store: PostgresStore,
) -> None:
    """Triage queue hides closed rows; the Library 'all' tab keeps them."""
    await _insert(store, "cl-open")
    closed_id = await _insert(store, "cl-closed")
    await store.mark_job_closed(job_id=closed_id, closed_at=datetime.now(UTC))

    queue = await store.query_jobs_view(JobsViewQuery(tab="queue"))
    all_tab = await store.query_jobs_view(JobsViewQuery(tab="all"))

    assert _ids(queue) == {"cl-open"}
    assert _ids(all_tab) == {"cl-open", "cl-closed"}


async def test_require_verdict_keeps_only_stage_b_rows(store: PostgresStore) -> None:
    """require_verdict filters on stage_b_verdict and rows hydrate eval fields."""
    evaluated_id = await _insert(store, "rv-evaluated")
    await store.save_stage_a(evaluated_id, _stage_a())
    await store.save_stage_b(evaluated_id, _stage_b())
    await _insert(store, "rv-unscored")

    page = await store.query_jobs_view(JobsViewQuery(tab="all", require_verdict=True))

    assert _ids(page) == {"rv-evaluated"}
    row = page.rows[0]
    assert row.verdict == "apply"
    assert row.stage_a_score == _STAGE_A_SCORE
    assert row.stage_b_fit_score == _STAGE_B_FIT_SCORE
    assert row.stage_b_status == "completed"
    assert row.status == "scored"


async def test_rows_carry_normalized_company_and_title(store: PostgresStore) -> None:
    """Rows expose the persisted company_norm/title_norm soft key."""
    await _insert(
        store, "nm-1", company="Stripe, Inc.", title="Senior Backend Engineer"
    )

    page = await store.query_jobs_view(JobsViewQuery(tab="all"))

    assert page.rows[0].company_norm == "stripe"
    assert page.rows[0].title_norm == "senior backend engineer"


async def _seed_status_matrix(store: PostgresStore) -> None:
    """Seed one row per tab-relevant state, plus a closed scored row."""
    await _insert(store, "tc-new", company="Stripe")
    await _insert(store, "tc-scored", status="scored", company="Stripe")
    await _insert(store, "tc-shortlisted", status="shortlisted", company="Stripe")
    await _insert(store, "tc-awaiting", status="awaiting_referral", company="Datadog")
    await _insert(store, "tc-archived", status="archived", company="Stripe")
    await _insert(store, "tc-ignored", status="ignored", company="Datadog")
    closed_id = await _insert(store, "tc-closed", status="scored", company="Stripe")
    await store.mark_job_closed(job_id=closed_id, closed_at=datetime.now(UTC))


async def test_tab_counts_agree_with_per_tab_totals(store: PostgresStore) -> None:
    """tab_counts[t] equals the total of re-running the same request with tab=t."""
    await _seed_status_matrix(store)

    page = await store.query_jobs_view(JobsViewQuery(tab="queue"))
    for tab in VALID_TABS:
        per_tab = await store.query_jobs_view(JobsViewQuery(tab=tab))
        assert page.tab_counts[tab] == per_tab.total, tab


async def test_tab_counts_share_request_filters(store: PostgresStore) -> None:
    """Shared filters (here: search) narrow every tab_counts bucket too."""
    await _seed_status_matrix(store)

    page = await store.query_jobs_view(JobsViewQuery(tab="queue", search="stripe"))
    for tab in VALID_TABS:
        per_tab = await store.query_jobs_view(JobsViewQuery(tab=tab, search="stripe"))
        assert page.tab_counts[tab] == per_tab.total, tab
    # Closed-row handling differs per tab even under the shared filter.
    assert page.tab_counts["queue"] == _STRIPE_QUEUE_COUNT
    assert page.tab_counts["scored"] == _STRIPE_SCORED_COUNT
    assert page.tab_counts["all"] == _STRIPE_ALL_COUNT


async def test_pagination_is_bounded_with_true_total(store: PostgresStore) -> None:
    """limit/offset slice the discovered_at DESC order; total counts all matches."""
    now = datetime.now(UTC)
    for index in range(_MATRIX_TOTAL):
        await _insert(
            store, f"pg-{index}", discovered_at=now - timedelta(minutes=index)
        )

    page = await store.query_jobs_view(
        JobsViewQuery(tab="all", limit=_PAGE_LIMIT, offset=_PAGE_OFFSET)
    )

    assert [row.job.canonical_id for row in page.rows] == ["pg-2", "pg-3"]
    assert page.total == _MATRIX_TOTAL
    assert page.tab_counts["all"] == _MATRIX_TOTAL


async def test_list_twin_statuses_excludes_self_and_blank_norms(
    store: PostgresStore,
) -> None:
    """Twins share non-blank norms, exclude the job itself, and carry status."""
    main_id = await _insert(store, "tw-main", company="Stripe", title="Engineer")
    twin_id = await _insert(
        store,
        "tw-twin",
        platform="greenhouse",
        company="Stripe",
        title="Engineer",
        status="shortlisted",
    )
    await _insert(store, "tw-other", company="Datadog", title="Engineer")
    blank_id = await _insert(store, "tw-blank-1", company="??", title="Engineer")
    await _insert(store, "tw-blank-2", company="!!", title="Engineer")

    twins = await store.list_twin_statuses(main_id)
    blank_twins = await store.list_twin_statuses(blank_id)

    assert [(t.job_id, t.platform, t.status) for t in twins] == [
        (twin_id, "greenhouse", "shortlisted")
    ]
    assert twins[0].url == "https://example.com/tw-twin"
    assert blank_twins == []
