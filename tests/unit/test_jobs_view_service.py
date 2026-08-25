"""Unit tests for JobsViewService request routing (plan D10).

Plain Library requests (no triage tab, no post-processing flags) must pass
sort/limit/offset straight to SQL for ANY sort — the over-fetch path would
sort only a corpus-capped prefix in memory and cap the total. Triage and
folded requests keep the over-fetch path, and the display fold pulls
in-flight twins through the store so an applied sibling can suppress its
queue cluster (plan D9). A recording stub store pins the routing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from jobfeed.domain.dedupe import INFLIGHT_STATUSES
from jobfeed.domain.filtering import HardFilters
from jobfeed.domain.interview import InterviewRound
from jobfeed.domain.models import (
    ApplicationRecord,
    FitAnalysis,
    JobEvaluation,
    JobPosting,
    QualityBand,
    StageAResult,
    StageBResult,
    StatusInfo,
    Verdict,
)
from jobfeed.domain.models_views import (
    JobsViewPage,
    JobsViewQuery,
    JobsViewRow,
    TwinStatusRow,
)
from jobfeed.services.jobs_view import (
    JOBS_VIEW_CORPUS_LIMIT,
    JobsViewService,
    JobsViewStore,
)
from jobfeed.web.schemas.jobs_detail import job_detail_response

_DISCOVERED = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
_PAGE_LIMIT = 7
_PAGE_OFFSET = 3
_UNIFIED_SCORE = 20
_ATS_SCORE = 40


_TITLE = "Backend Engineer"


def _row(
    id: str,
    *,
    platform: str = "greenhouse",
    company: str = "Stripe",
    status: str = "new",
    jd_quality: QualityBand | None = None,
) -> JobsViewRow:
    """Build a view row pinning only routing/fold-relevant fields."""
    job = JobPosting(
        id=id,
        platform=platform,
        canonical_id=f"c-{id}",
        url=f"https://example.com/{platform}/{id}",
        title=_TITLE,
        company=company,
        location="Remote",
        discovered_at=_DISCOVERED,
        jd_quality=jd_quality,
    )
    return JobsViewRow(
        job=job,
        company_norm=company.lower(),
        title_norm=_TITLE.lower(),
        status=status,
        evaluation_score=None,
        evaluation_verdict=None,
        evaluation_status=None,
        evaluator_version=None,
    )


def _posted_row(id: str, posted_at: datetime | None) -> JobsViewRow:
    row = _row(id)
    row.job.posted_at = posted_at
    return row


def _fit_row(id: str, fit_score: int) -> JobsViewRow:
    row = _row(id)
    row.evaluation_score = fit_score
    return row


class _RecordingStore:
    """Stub jobs-view store: canned responses, recorded calls."""

    def __init__(
        self,
        rows: list[JobsViewRow] | None = None,
        twin_rows: list[JobsViewRow] | None = None,
    ) -> None:
        self.rows = rows or []
        self.twin_rows = twin_rows or []
        self.queries: list[JobsViewQuery] = []
        self.twin_calls: list[tuple[list[tuple[str, str]], tuple[str, ...], int]] = []

    async def query_jobs_view(self, query: JobsViewQuery) -> JobsViewPage:
        self.queries.append(query)
        return JobsViewPage(rows=list(self.rows), total=len(self.rows), tab_counts={})

    async def list_twin_rows_by_status(
        self,
        keys: list[tuple[str, str]],
        *,
        statuses: list[str],
        limit: int,
    ) -> list[JobsViewRow]:
        self.twin_calls.append((list(keys), tuple(statuses), limit))
        return list(self.twin_rows)


def _service(store: _RecordingStore) -> JobsViewService:
    return JobsViewService(cast(JobsViewStore, store), HardFilters())


async def test_plain_library_sort_routes_to_sql_pagination() -> None:
    """No flags + any sort → the store gets the window AND the sort."""
    store = _RecordingStore()

    await _service(store).list_jobs(
        JobsViewQuery(tab="all", limit=_PAGE_LIMIT, offset=_PAGE_OFFSET),
        sort="score_desc",
    )

    assert len(store.queries) == 1
    sent = store.queries[0]
    assert (sent.limit, sent.offset, sent.sort) == (
        _PAGE_LIMIT,
        _PAGE_OFFSET,
        "score_desc",
    )


@pytest.mark.parametrize(
    ("sort", "rows", "expected"),
    [
        (
            "posted_asc",
            [
                _posted_row("1", datetime(2026, 5, 1, tzinfo=UTC)),
                _posted_row("2", datetime(2026, 6, 1, tzinfo=UTC)),
                _posted_row("3", None),
            ],
            ["1", "3", "2"],
        ),
        (
            "posted_desc",
            [
                _posted_row("1", datetime(2026, 5, 1, tzinfo=UTC)),
                _posted_row("2", datetime(2026, 6, 1, tzinfo=UTC)),
                _posted_row("3", None),
            ],
            ["3", "2", "1"],
        ),
        (
            "score_asc",
            [_fit_row("1", 72), _fit_row("2", 95)],
            ["1", "2"],
        ),
        (
            "score_desc",
            [_fit_row("1", 72), _fit_row("2", 95)],
            ["2", "1"],
        ),
    ],
)
async def test_triage_overfetches_and_honors_explicit_sort(
    sort: str, rows: list[JobsViewRow], expected: list[str]
) -> None:
    """Triage sorts the complete over-fetched corpus before pagination."""
    store = _RecordingStore(rows=rows)

    page = await _service(store).list_jobs(
        JobsViewQuery(tab="queue", limit=_PAGE_LIMIT, offset=0),
        sort=sort,
    )

    sent = store.queries[0]
    assert (sent.limit, sent.offset, sent.sort) == (
        JOBS_VIEW_CORPUS_LIMIT,
        0,
        sort,
    )
    assert [row.job.id for row in page.rows] == expected


async def test_dedupe_pulls_inflight_twins_and_suppresses_their_clusters() -> None:
    """The fold asks the store for in-flight twins of the corpus keys; a
    cluster whose winner is in-flight (out of corpus) drops off the page.
    """
    queue_twin = _row("1", platform="greenhouse", jd_quality=QualityBand.FULL)
    control = _row("2", company="Datadog")
    applied_twin = _row("3", platform="indeed", status="applied")
    store = _RecordingStore(rows=[queue_twin, control], twin_rows=[applied_twin])

    page = await _service(store).list_jobs(JobsViewQuery(tab="queue"), dedupe=True)

    assert [row.job.id for row in page.rows] == ["2"]
    assert page.total == 1
    keys, statuses, limit = store.twin_calls[0]
    assert keys == [
        ("datadog", "backend engineer"),
        ("stripe", "backend engineer"),
    ]
    assert statuses == tuple(sorted(INFLIGHT_STATUSES))
    assert limit == JOBS_VIEW_CORPUS_LIMIT


async def test_unknown_sort_raises_value_error() -> None:
    """An unknown sort name fails fast before any store call."""
    store = _RecordingStore()

    with pytest.raises(ValueError, match="sort"):
        await _service(store).list_jobs(JobsViewQuery(tab="all"), sort="best_first")

    assert store.queries == []


class _DetailStore(_RecordingStore):
    """Detail stub containing conflicting legacy and unified evaluations."""

    def __init__(self) -> None:
        super().__init__()
        self.legacy_reads = 0

    async def get_job(self, job_id: str) -> JobPosting | None:
        return _row(job_id).job

    async def get_evaluation(self, job_id: str) -> JobEvaluation:
        self.legacy_reads += 1
        return JobEvaluation(
            job=_row(job_id).job,
            stage_a=StageAResult(
                score=99,
                one_line="Legacy top score.",
                timing_eligible="yes",
                model="legacy-a",
                prompt_hash="legacy",
                resume_hash="legacy",
            ),
            stage_b=StageBResult(
                verdict=Verdict.APPLY,
                jd_summary="Legacy apply summary.",
                fit_analysis=FitAnalysis(score=99, strengths=[], gaps=[]),
                resume_hooks=[],
                model="legacy-b",
                prompt_hash="legacy",
                resume_hash="legacy",
            ),
            stage_b_status="completed",
        )

    async def get_current_evaluation(self, job_id: str) -> dict[str, object]:
        return {
            "job_id": job_id,
            "status": "completed",
            "eligibility_status": "pass",
            "match_score": _UNIFIED_SCORE,
            "match_tier": "weak_match",
            "evaluator_version": "unified-v2",
            "model": "mock-unified",
            "result_json": {
                "summary": "Canonical summary.",
                "eligibility_checks": [
                    {
                        "kind": "work_authorization",
                        "requirement": "US work authorization",
                        "status": "met",
                        "candidate_evidence": "Authorized",
                        "reason": "Profile evidence",
                    }
                ],
                "requirements": [
                    {
                        "requirement": "Production Python",
                        "priority": "must_have",
                        "category": "skill",
                        "match": "strong",
                        "resume_evidence": "Built Python services",
                        "evidence_type": "explicit",
                    }
                ],
                "one_line": "Canonical weak match.",
                "ats_visibility_score": _ATS_SCORE,
            },
        }

    async def get_status(self, _job_id: str) -> StatusInfo | None:
        return None

    async def get_status_history(self, _job_id: str) -> list[str]:
        return []

    async def list_interview_rounds(self, _job_id: str) -> list[InterviewRound]:
        return []

    async def get_application(self, _job_id: str) -> ApplicationRecord | None:
        return None

    async def list_twin_statuses(self, _job_id: str) -> list[TwinStatusRow]:
        return []


async def test_job_detail_reads_only_canonical_unified_evaluation() -> None:
    store = _DetailStore()

    detail = await _service(store).get_job_detail("1")

    assert detail is not None
    assert store.legacy_reads == 0
    assert detail.evaluation is not None
    assert detail.evaluation["match_score"] == _UNIFIED_SCORE
    assert detail.evaluation["match_tier"] == "weak_match"
    response = job_detail_response(detail).model_dump()
    assert response["evaluation"]["match_score"] == _UNIFIED_SCORE
    assert response["evaluation"]["match_tier"] == "weak_match"
    assert response["evaluation"]["summary"] == "Canonical summary."
    assert response["evaluation"]["eligibility_status"] == "pass"
    assert response["evaluation"]["eligibility_checks"][0]["status"] == "met"
    assert response["evaluation"]["requirements"][0]["match"] == "strong"
    assert response["evaluation"]["ats_visibility_score"] == _ATS_SCORE
    assert response["evaluation"]["evaluator_version"] == "unified-v2"
    assert response["evaluation"]["model"] == "mock-unified"
    assert "stage_a" not in response["evaluation"]
    assert "stage_b" not in response["evaluation"]
    assert "Legacy" not in str(response)
    assert "apply" not in str(response["evaluation"])
