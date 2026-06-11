"""Jobs routes tests (PG-backed): list composition + detail aggregation.

Pins the Task 3 acceptance criteria over real HTTP + PostgreSQL:

* Queue-tab request with hard filters + fold + require_verdict returns the
  legacy-Today-equivalent set for a seeded matrix.
* Verdict-group ordering (apply -> consider -> skip -> derived
  below-threshold -> unscored; score-desc inside groups).
* In-memory pagination with true post-processing totals; tab_counts stay
  SQL-prefilter counts.
* sort honored on Library tabs, ignored on triage tabs; post-processing
  flags default to false.
* Detail aggregation (Stage B blocks incl. the three hook keys, twins with
  platform+status, interviews, snapshot refs) and the 404 error shape.

Routes-no-logic is structural (composition lives in
``services/jobs_view.py``); these tests pin the observable contract instead.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import (
    FitAnalysis,
    GapItem,
    MatchItem,
    QualityBand,
    StageAResult,
    StageBResult,
    TransitionRequest,
    Verdict,
)
from jobfeed.observability import get_logger
from jobfeed.services.application import (
    ApplicationService,
    ApplicationStore,
    ApplyRequest,
)
from jobfeed.web.app import create_web_app
from tests.support.factories import make_job
from tests.web.test_app_skeleton import open_client

pytestmark = pytest.mark.postgres

HTTP_OK = 200
HTTP_NOT_FOUND = 404

_STAGE_A_SCORE = 80
_STAGE_B_FIT_SCORE = 77

_QUEUE_STATUSES = ["new", "scored", "shortlisted", "awaiting_referral"]

# Today-matrix sizes (see _seed_today_matrix).
_MATRIX_SQL_QUEUE_COUNT = 5  # verdict rows the SQL prefilter keeps
_MATRIX_TRUE_TOTAL = 3  # after hard filters + fold
_MATRIX_UNFLAGGED_COUNT = 6  # no flags: blocked + both twins + unscored stay

# Ordering-corpus sizes (see _seed_ordering_corpus).
_CORPUS_TOTAL = 7
_PAGE_LIMIT = 2
_PAGE_OFFSET = 2


def _write_config(
    tmp_path: Path,
    dsn: str,
    *,
    company_blocklist: tuple[str, ...] = (),
) -> Path:
    """Write a TOML config pointing at the test database.

    Args:
        tmp_path: Temporary directory owned by pytest.
        dsn: PostgreSQL DSN.
        company_blocklist: Optional hard-filter company blocklist.

    Returns:
        Path to the written config file.
    """
    lines = [
        "[db]",
        f'url = "{dsn}"',
        "",
        "[observability]",
        'log_level = "warning"',
        'log_format = "human"',
    ]
    if company_blocklist:
        items = ", ".join(json.dumps(item) for item in company_blocklist)
        lines += ["", "[hard_filters]", f"company_blocklist = [{items}]"]
    config_path = tmp_path / "config.toml"
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config_path


@asynccontextmanager
async def _seed_store(dsn: str) -> AsyncIterator[PostgresStore]:
    """Yield a connected store for seeding the already-migrated test database.

    Args:
        dsn: PostgreSQL DSN of a freshly migrated database (``fresh_pg_dsn``).

    Yields:
        Connected PostgresStore.
    """
    store = PostgresStore(dsn)
    await store.connect()
    try:
        yield store
    finally:
        await store.close()


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


def _stage_b(
    verdict: Verdict = Verdict.APPLY,
    score: int = _STAGE_B_FIT_SCORE,
    *,
    raw_blocks: dict[str, object] | None = None,
) -> StageBResult:
    """Build a minimal completed Stage B result."""
    return StageBResult(
        verdict=verdict,
        jd_summary="summary",
        fit_analysis=FitAnalysis(score=score, strengths=[], gaps=[]),
        resume_hooks=[],
        model="mock/stage-b",
        prompt_hash="prompt-b",
        resume_hash="resume-b",
        raw_blocks=raw_blocks,
    )


async def _insert(
    store: PostgresStore,
    canonical_id: str,
    *,
    status: str | None = None,
    **overrides: object,
) -> str:
    """Save a job fixture, optionally force-transitioning its status."""
    saved = await store.save_job(make_job(canonical_id, **overrides))
    if status is not None:
        await store.transition_status(
            TransitionRequest(job_id=saved.job_id, new_status=status, force=True)
        )
    return saved.job_id


async def _score(
    store: PostgresStore,
    job_id: str,
    verdict: Verdict,
    fit_score: int,
) -> None:
    """Give a job a completed Stage A + Stage B evaluation."""
    await store.save_stage_a(job_id, _stage_a())
    await store.save_stage_b(job_id, _stage_b(verdict, fit_score))


def _ordered_ids(
    payload: dict[str, object], id_by_canonical: dict[str, str]
) -> list[str]:
    """Map a response's job ids back to seeded canonical names, in order."""
    canonical_by_id = {v: k for k, v in id_by_canonical.items()}
    jobs = cast(list[dict[str, object]], payload["jobs"])
    return [canonical_by_id[cast(str, job["id"])] for job in jobs]


async def _seed_today_matrix(store: PostgresStore) -> dict[str, str]:
    """Seed the legacy-Today matrix: kept rows, a hard-filtered row, twins,
    an unscored row, plus tab-excluded applied/closed rows.

    Returns:
        Mapping of canonical name -> store job id.
    """
    ids: dict[str, str] = {}
    ids["a-apply"] = await _insert(store, "a-apply", company="Acme")
    await _score(store, ids["a-apply"], Verdict.APPLY, 90)
    ids["b-consider"] = await _insert(
        store, "b-consider", status="scored", company="Beta Labs"
    )
    await _score(store, ids["b-consider"], Verdict.CONSIDER, 80)
    ids["c-blocked"] = await _insert(store, "c-blocked", company="Blocked Corp")
    await _score(store, ids["c-blocked"], Verdict.APPLY, 88)
    ids["d-twin-1"] = await _insert(
        store,
        "d-twin-1",
        platform="greenhouse",
        company="TwinCo",
        title="Platform Engineer",
        jd_quality=QualityBand.FULL,
    )
    await _score(store, ids["d-twin-1"], Verdict.APPLY, 85)
    ids["d-twin-2"] = await _insert(
        store,
        "d-twin-2",
        platform="indeed",
        company="TwinCo",
        title="Platform Engineer",
    )
    await _score(store, ids["d-twin-2"], Verdict.APPLY, 84)
    ids["e-unscored"] = await _insert(store, "e-unscored", company="Unscored Inc")
    ids["f-applied"] = await _insert(store, "f-applied", company="Applied Inc")
    await _score(store, ids["f-applied"], Verdict.APPLY, 91)
    await store.transition_status(
        TransitionRequest(job_id=ids["f-applied"], new_status="applied", force=True)
    )
    ids["g-closed"] = await _insert(
        store, "g-closed", status="scored", company="Closed Inc"
    )
    await _score(store, ids["g-closed"], Verdict.APPLY, 92)
    await store.mark_job_closed(
        job_id=ids["g-closed"], closed_at=make_job("g-closed").discovered_at
    )
    return ids


async def _seed_ordering_corpus(store: PostgresStore) -> dict[str, str]:
    """Seed one row per verdict group plus score variation inside groups.

    Returns:
        Mapping of canonical name -> store job id.
    """
    ids: dict[str, str] = {}
    ids["o-apply-hi"] = await _insert(store, "o-apply-hi")
    await _score(store, ids["o-apply-hi"], Verdict.APPLY, 90)
    ids["o-apply-lo"] = await _insert(store, "o-apply-lo")
    await _score(store, ids["o-apply-lo"], Verdict.APPLY, 70)
    ids["o-consider"] = await _insert(store, "o-consider")
    await _score(store, ids["o-consider"], Verdict.CONSIDER, 95)
    ids["o-skip"] = await _insert(store, "o-skip")
    await _score(store, ids["o-skip"], Verdict.SKIP, 99)
    ids["o-below-hi"] = await _insert(store, "o-below-hi")
    await store.save_stage_a(ids["o-below-hi"], _stage_a(score=65))
    ids["o-below-lo"] = await _insert(store, "o-below-lo")
    await store.save_stage_a(ids["o-below-lo"], _stage_a(score=45))
    await store.mark_stage_b_skipped_batch([ids["o-below-hi"], ids["o-below-lo"]])
    ids["o-unscored"] = await _insert(store, "o-unscored")
    return ids


async def test_queue_request_returns_legacy_today_set(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Queue + hard filters + fold + require_verdict = the legacy Today set.

    The hard-filtered company drops, the twin pair folds to its best
    representative, unscored rows drop via require_verdict, and applied or
    closed rows never enter the queue tab. The reported total is the true
    post-processing total while tab_counts stay SQL-prefilter counts.
    """
    async with _seed_store(fresh_pg_dsn) as store:
        ids = await _seed_today_matrix(store)
    app = create_web_app(
        _write_config(tmp_path, fresh_pg_dsn, company_blocklist=("Blocked Corp",))
    )

    async with open_client(app) as client:
        response = await client.get(
            "/api/jobs",
            params={
                "tab": "queue",
                "statuses": _QUEUE_STATUSES,
                "require_verdict": "true",
                "apply_hard_filters": "true",
                "dedupe": "true",
            },
        )

    assert response.status_code == HTTP_OK, response.text
    payload = response.json()
    assert _ordered_ids(payload, ids) == ["a-apply", "d-twin-1", "b-consider"]
    assert payload["total"] == _MATRIX_TRUE_TOTAL
    assert payload["tab_counts"]["queue"] == _MATRIX_SQL_QUEUE_COUNT


async def test_post_processing_flags_default_off(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Without flags the queue keeps blocked companies, both twins, unscored."""
    async with _seed_store(fresh_pg_dsn) as store:
        ids = await _seed_today_matrix(store)
    app = create_web_app(
        _write_config(tmp_path, fresh_pg_dsn, company_blocklist=("Blocked Corp",))
    )

    async with open_client(app) as client:
        response = await client.get("/api/jobs", params={"tab": "queue"})

    assert response.status_code == HTTP_OK, response.text
    payload = response.json()
    names = set(_ordered_ids(payload, ids))
    assert names == {
        "a-apply",
        "b-consider",
        "c-blocked",
        "d-twin-1",
        "d-twin-2",
        "e-unscored",
    }
    assert payload["total"] == _MATRIX_UNFLAGGED_COUNT


async def test_verdict_group_ordering(tmp_path: Path, fresh_pg_dsn: str) -> None:
    """apply > consider > skip > below-threshold > unscored; score-desc inside."""
    async with _seed_store(fresh_pg_dsn) as store:
        ids = await _seed_ordering_corpus(store)
    app = create_web_app(_write_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.get("/api/jobs", params={"tab": "queue"})

    assert response.status_code == HTTP_OK, response.text
    assert _ordered_ids(response.json(), ids) == [
        "o-apply-hi",
        "o-apply-lo",
        "o-consider",
        "o-skip",
        "o-below-hi",
        "o-below-lo",
        "o-unscored",
    ]


async def test_in_memory_pagination_reports_true_totals(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Triage pagination windows the post-sort order with the full total."""
    async with _seed_store(fresh_pg_dsn) as store:
        ids = await _seed_ordering_corpus(store)
    app = create_web_app(_write_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.get(
            "/api/jobs",
            params={"tab": "queue", "limit": _PAGE_LIMIT, "offset": _PAGE_OFFSET},
        )

    assert response.status_code == HTTP_OK, response.text
    payload = response.json()
    assert _ordered_ids(payload, ids) == ["o-consider", "o-skip"]
    assert payload["total"] == _CORPUS_TOTAL
    assert payload["tab_counts"]["queue"] == _CORPUS_TOTAL


async def _seed_sortable_rows(store: PostgresStore) -> dict[str, str]:
    """Seed three Library rows whose company and score orders differ."""
    ids: dict[str, str] = {}
    ids["alpha"] = await _insert(store, "alpha", company="Alpha")
    ids["beta"] = await _insert(store, "beta", company="Beta")
    await _score(store, ids["beta"], Verdict.APPLY, 90)
    ids["gamma"] = await _insert(store, "gamma", company="Gamma")
    await _score(store, ids["gamma"], Verdict.CONSIDER, 80)
    return ids


async def test_sort_honored_on_library_ignored_on_queue(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Library tabs honor the sort param; triage keeps verdict-group order."""
    async with _seed_store(fresh_pg_dsn) as store:
        ids = await _seed_sortable_rows(store)
    app = create_web_app(_write_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        by_company = await client.get(
            "/api/jobs", params={"tab": "all", "sort": "company_asc"}
        )
        by_score = await client.get(
            "/api/jobs", params={"tab": "all", "sort": "score_desc"}
        )
        queue = await client.get(
            "/api/jobs", params={"tab": "queue", "sort": "company_asc"}
        )

    assert _ordered_ids(by_company.json(), ids) == ["alpha", "beta", "gamma"]
    assert _ordered_ids(by_score.json(), ids) == ["beta", "gamma", "alpha"]
    assert _ordered_ids(queue.json(), ids) == ["beta", "gamma", "alpha"]


_POSTED_NEW = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)
_POSTED_OLD = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


async def _seed_posted_rows(store: PostgresStore) -> dict[str, str]:
    """Seed Library rows whose posted order opposes the id tiebreak.

    Insertion order gives ascending ids, so the discovered/id tiebreak alone
    would return p-unposted, p-older, p-newer — the exact reverse of the
    expected ``posted_desc`` order. Only ``_key_posted_desc`` (including its
    NULLS-last branch for ``p-unposted``) produces the asserted order.
    """
    ids: dict[str, str] = {}
    ids["p-newer"] = await _insert(store, "p-newer", posted_at=_POSTED_NEW)
    ids["p-older"] = await _insert(store, "p-older", posted_at=_POSTED_OLD)
    ids["p-unposted"] = await _insert(store, "p-unposted")
    return ids


async def test_posted_desc_sorts_missing_posted_at_last(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """sort=posted_desc orders newest-posted first and unposted rows last."""
    async with _seed_store(fresh_pg_dsn) as store:
        ids = await _seed_posted_rows(store)
    app = create_web_app(_write_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.get(
            "/api/jobs", params={"tab": "all", "sort": "posted_desc"}
        )

    assert response.status_code == HTTP_OK, response.text
    assert _ordered_ids(response.json(), ids) == ["p-newer", "p-older", "p-unposted"]


_HOOKS_BLOCK = {
    "lead_with": "Lead X",
    "supporting": ["Sup 1"],
    "avoid_mentioning": ["Avoid X"],
}
_MASTER_RESUME = "master resume text"
_TAILORED_RESUME = "tailored resume text"


async def _seed_detail_job(store: PostgresStore) -> tuple[str, str]:
    """Seed a fully aggregated detail job: evaluation with hook blocks, a
    twin on another platform, a real apply, an interview round, and a note.

    Returns:
        Tuple of (job id, twin id).
    """
    job_id = await _insert(
        store, "dt-main", company="Stripe, Inc.", title="Senior Backend Engineer"
    )
    twin_id = await _insert(
        store,
        "dt-twin",
        platform="greenhouse",
        company="Stripe, Inc.",
        title="Senior Backend Engineer",
    )
    await store.save_stage_a(job_id, _stage_a())
    stage_b = StageBResult(
        verdict=Verdict.APPLY,
        jd_summary="summary",
        fit_analysis=FitAnalysis(
            score=_STAGE_B_FIT_SCORE,
            strengths=[MatchItem(requirement="Python", evidence="built APIs")],
            gaps=[
                GapItem(requirement="Kubernetes", severity="major", mitigation="ramp")
            ],
        ),
        resume_hooks=["Lead X", "Sup 1"],
        model="mock/stage-b",
        prompt_hash="prompt-b",
        resume_hash="resume-b",
        raw_blocks={"resume_hooks": _HOOKS_BLOCK},
    )
    await store.save_stage_b(job_id, stage_b)
    applications = ApplicationService(cast(ApplicationStore, store), get_logger())
    await applications.apply(
        ApplyRequest(
            job_id=job_id,
            master_resume=_MASTER_RESUME,
            tailored_resume=_TAILORED_RESUME,
            application_method="manual",
        )
    )
    await store.add_interview_round(job_id=job_id, label="Phone Screen")
    await store.append_note(job_id=job_id, text="great fit")
    return job_id, twin_id


async def test_detail_aggregates_evaluation_status_twins_interviews_snapshots(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """GET /api/jobs/{id} carries every detail section of the contract."""
    async with _seed_store(fresh_pg_dsn) as store:
        job_id, twin_id = await _seed_detail_job(store)
    app = create_web_app(_write_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.get(f"/api/jobs/{job_id}")

    assert response.status_code == HTTP_OK, response.text
    payload = response.json()
    assert payload["job"]["id"] == job_id
    assert payload["job"]["company"] == "Stripe, Inc."
    assert payload["job"]["canonical_id"] == "dt-main"
    assert payload["evaluation"]["stage_a"] == {
        "score": _STAGE_A_SCORE,
        "one_line": "fits",
    }
    stage_b = payload["evaluation"]["stage_b"]
    assert stage_b["verdict"] == "apply"
    assert stage_b["jd_summary"] == "summary"
    assert stage_b["fit_score"] == _STAGE_B_FIT_SCORE
    assert stage_b["strengths"] == [{"requirement": "Python", "evidence": "built APIs"}]
    assert stage_b["gaps"] == [
        {"requirement": "Kubernetes", "severity": "major", "mitigation": "ramp"}
    ]
    assert stage_b["hooks"] == _HOOKS_BLOCK
    assert payload["status"]["status"] == "applied"
    assert "great fit" in payload["status"]["notes"]
    assert payload["status"]["history"][0] == "applied"
    assert payload["twins"] == [
        {
            "job_id": twin_id,
            "platform": "greenhouse",
            "url": "https://example.com/dt-twin",
            "status": "new",
        }
    ]
    interviews = payload["interviews"]
    assert len(interviews) == 1
    assert interviews[0]["label"] == "Phone Screen"
    assert interviews[0]["round_index"] == 1
    assert interviews[0]["completed_at"] is None
    application = payload["application"]
    assert application["master_resume_hash"] == _sha256(_MASTER_RESUME)
    assert application["tailored_resume_hash"] == _sha256(_TAILORED_RESUME)
    assert application["application_method"] == "manual"
    assert application["applied_at"] is not None


def _sha256(text: str) -> str:
    """SHA-256 hex digest matching the application service's content hash."""
    return hashlib.sha256(text.encode()).hexdigest()


async def test_detail_unknown_id_returns_404_error_shape(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """An unknown job id yields the shared JSON error shape with a 404."""
    app = create_web_app(_write_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.get("/api/jobs/999999")

    assert response.status_code == HTTP_NOT_FOUND
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["message"]
    assert error["request_id"]
