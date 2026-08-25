"""SQLite jobs-view, twins, and pipeline-run read contracts."""

# ruff: noqa: PLR2004

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from jobfeed.adapters.store._sqlite_views import _jobs_view_rows_query
from jobfeed.domain.models import PipelineRun
from jobfeed.domain.models_views import VALID_TABS, JobsViewQuery
from tests.support.sqlite_views_performance import (
    NOW,
    insert_job,
    insert_run,
    open_views_performance,
    rows,
    set_evaluation,
    set_unified_evaluation,
    utc_text,
)


async def test_jobs_view_default_query_uses_bounded_discovery_index(
    tmp_path: Path,
) -> None:
    """The production page query stays bounded and index-backed."""
    lifecycle, _store = await open_views_performance(tmp_path / "plan.db")
    try:
        sql, params = _jobs_view_rows_query(JobsViewQuery(tab="all"), NOW)
        plan = await rows(lifecycle, f"EXPLAIN QUERY PLAN {sql}", tuple(params))
        details = [str(row[3]) for row in plan]

        assert any("idx_jobs_discovered_at" in detail for detail in details)
        assert "LEFT JOIN evaluation_results" in sql
        assert any("INTEGER PRIMARY KEY" in detail for detail in details)
    finally:
        await lifecycle.close()


async def test_jobs_view_unicode_literal_search_and_shared_counts(
    tmp_path: Path,
) -> None:
    """Unicode casefold and escaped wildcard filters narrow every tab count."""
    lifecycle, store = await open_views_performance(tmp_path / "views.db")
    try:
        await insert_job(
            lifecycle,
            "unicode",
            discovered_at=utc_text(NOW),
            company="Straße 100%_\\ Labs",
            company_norm="strasse labs",
            status="scored",
        )
        await insert_job(
            lifecycle,
            "wildcard-decoy",
            discovered_at=utc_text(NOW - timedelta(minutes=1)),
            company="Strasse 100xx Labs",
            status="scored",
        )
        await insert_job(
            lifecycle,
            "other",
            discovered_at=utc_text(NOW - timedelta(minutes=2)),
            company="Other",
            status="archived",
        )

        page = await store.query_jobs_view(
            JobsViewQuery(tab="all", search="STRASSE 100%_\\")
        )

        assert [row.job.canonical_id for row in page.rows] == ["unicode"]
        assert page.total == 1
        for tab in VALID_TABS:
            rerun = await store.query_jobs_view(
                JobsViewQuery(tab=tab, search="STRASSE 100%_\\")
            )
            assert page.tab_counts[tab] == rerun.total
    finally:
        await lifecycle.close()


async def test_jobs_view_uses_discovered_time_for_missing_posted_at(
    tmp_path: Path,
) -> None:
    """Posted sort falls back to added time and remains deterministic."""
    lifecycle, store = await open_views_performance(tmp_path / "sorts.db")
    try:
        newest = await insert_job(
            lifecycle,
            "none",
            discovered_at=utc_text(NOW),
            company="No Norm",
            company_norm=None,
        )
        middle = await insert_job(
            lifecycle,
            "middle",
            discovered_at=utc_text(NOW),
            company="Beta",
            company_norm="beta",
            posted_at=utc_text(NOW - timedelta(days=2)),
        )
        oldest = await insert_job(
            lifecycle,
            "oldest",
            discovered_at=utc_text(NOW - timedelta(minutes=1)),
            company="Alpha",
            company_norm="alpha",
            posted_at=utc_text(NOW - timedelta(days=1)),
        )
        await set_unified_evaluation(
            lifecycle, middle, match_score=79, match_tier="possible_match"
        )
        await set_unified_evaluation(
            lifecycle, oldest, match_score=77, match_tier="possible_match"
        )

        discovered = await store.query_jobs_view(
            JobsViewQuery(tab="all", limit=1, offset=1)
        )
        posted = await store.query_jobs_view(
            JobsViewQuery(tab="all", sort="posted_desc")
        )
        score = await store.query_jobs_view(JobsViewQuery(tab="all", sort="score_desc"))
        company = await store.query_jobs_view(
            JobsViewQuery(tab="all", sort="company_asc")
        )

        assert discovered.rows[0].job.id == str(newest)
        assert discovered.total == 3
        assert [row.job.canonical_id for row in posted.rows] == [
            "none",
            "oldest",
            "middle",
        ]
        assert [row.job.canonical_id for row in score.rows] == [
            "middle",
            "oldest",
            "none",
        ]
        assert [row.job.canonical_id for row in company.rows] == [
            "oldest",
            "middle",
            "none",
        ]
        assert newest < middle
    finally:
        await lifecycle.close()


async def test_jobs_view_tabs_filters_and_evaluation_shape(tmp_path: Path) -> None:
    """Tabs, freshness, status, verdict, and pending-JD predicates compose."""
    lifecycle, store = await open_views_performance(tmp_path / "filters.db")
    try:
        pending = await insert_job(
            lifecycle,
            "pending",
            discovered_at=utc_text(NOW - timedelta(days=1)),
            quality="missing",
        )
        scored = await insert_job(
            lifecycle,
            "scored",
            discovered_at=utc_text(NOW - timedelta(days=1)),
            status="scored",
        )
        await set_unified_evaluation(
            lifecycle,
            scored,
            match_score=88,
            match_tier="strong_match",
        )
        await insert_job(
            lifecycle,
            "closed",
            discovered_at=utc_text(NOW - timedelta(days=1)),
            status="scored",
            closed_at=utc_text(NOW),
        )
        await insert_job(
            lifecycle,
            "old",
            discovered_at=utc_text(NOW - timedelta(days=20)),
            status="scored",
        )

        pending_page = await store.query_jobs_view(JobsViewQuery(tab="pending_jd"))
        queue = await store.query_jobs_view(JobsViewQuery(tab="queue"))
        filtered = await store.query_jobs_view(
            JobsViewQuery(
                tab="all",
                statuses=("scored",),
                posted_within_days=7,
                require_verdict=True,
            )
        )

        assert [row.job.id for row in pending_page.rows] == [str(pending)]
        assert "closed" not in {row.job.canonical_id for row in queue.rows}
        assert [row.job.id for row in filtered.rows] == [str(scored)]
        assert filtered.rows[0].evaluation_score == 88
        assert filtered.rows[0].evaluation_verdict == "strong_match"
    finally:
        await lifecycle.close()


async def test_jobs_view_never_falls_back_to_conflicting_legacy_scores(
    tmp_path: Path,
) -> None:
    """Canonical list reads ignore legacy Stage A/B even when they are higher."""
    lifecycle, store = await open_views_performance(tmp_path / "canonical.db")
    try:
        unified = await insert_job(
            lifecycle,
            "unified",
            discovered_at=utc_text(NOW),
            status="scored",
        )
        legacy_only = await insert_job(
            lifecycle,
            "legacy-only",
            discovered_at=utc_text(NOW - timedelta(minutes=1)),
            status="scored",
        )
        await set_evaluation(
            lifecycle,
            unified,
            stage_a_score=99,
            fit_score=99,
            verdict="apply",
            stage_b_status="completed",
        )
        await set_evaluation(
            lifecycle,
            legacy_only,
            stage_a_score=99,
            fit_score=99,
            verdict="apply",
            stage_b_status="completed",
        )
        await set_unified_evaluation(
            lifecycle,
            unified,
            match_score=20,
            match_tier="weak_match",
            status="completed",
            evaluator_version="unified-v2",
        )

        page = await store.query_jobs_view(JobsViewQuery(tab="all", sort="score_desc"))
        required = await store.query_jobs_view(
            JobsViewQuery(tab="all", require_verdict=True)
        )

        assert [row.job.canonical_id for row in page.rows] == [
            "unified",
            "legacy-only",
        ]
        canonical = page.rows[0]
        assert (
            canonical.evaluation_score,
            canonical.evaluation_verdict,
            canonical.evaluation_status,
            canonical.evaluator_version,
        ) == (20, "weak_match", "completed", "unified-v2")
        legacy = page.rows[1]
        assert (
            legacy.evaluation_score,
            legacy.evaluation_verdict,
            legacy.evaluation_status,
            legacy.evaluator_version,
        ) == (None, None, None, None)
        assert [row.job.canonical_id for row in required.rows] == ["unified"]
    finally:
        await lifecycle.close()


async def test_pending_jd_ignores_legacy_stage_a_completion(tmp_path: Path) -> None:
    """A legacy-only score cannot hide a job from the canonical pending-JD tab."""
    lifecycle, store = await open_views_performance(tmp_path / "pending.db")
    try:
        legacy_only = await insert_job(
            lifecycle,
            "legacy-pending",
            discovered_at=utc_text(NOW),
            quality="missing",
        )
        await set_evaluation(lifecycle, legacy_only, stage_a_score=99)

        page = await store.query_jobs_view(JobsViewQuery(tab="pending_jd"))

        assert [row.job.canonical_id for row in page.rows] == ["legacy-pending"]
    finally:
        await lifecycle.close()


async def test_twin_queries_use_exact_pairs_and_stable_order(tmp_path: Path) -> None:
    """Twin reads reject cross-products/blanks, exclude self, and order stably."""
    lifecycle, store = await open_views_performance(tmp_path / "twins.db")
    try:
        main = await insert_job(
            lifecycle,
            "main",
            discovered_at=utc_text(NOW),
            company_norm="alpha",
            title_norm="engineer",
        )
        twin = await insert_job(
            lifecycle,
            "twin",
            discovered_at=utc_text(NOW - timedelta(minutes=1)),
            company_norm="alpha",
            title_norm="engineer",
            status="applied",
        )
        await insert_job(
            lifecycle,
            "cross",
            discovered_at=utc_text(NOW - timedelta(minutes=2)),
            company_norm="alpha",
            title_norm="manager",
            status="applied",
        )
        blank = await insert_job(
            lifecycle,
            "blank",
            discovered_at=utc_text(NOW - timedelta(minutes=3)),
            company_norm="",
            title_norm="engineer",
            status="applied",
        )

        rows = await store.list_twin_rows_by_status(
            [("alpha", "engineer"), ("beta", "manager")],
            statuses=["applied"],
            limit=10,
        )
        statuses = await store.list_twin_statuses(str(main))

        assert [row.job.id for row in rows] == [str(twin)]
        assert [row.job_id for row in statuses] == [str(twin)]
        assert await store.list_twin_statuses(str(blank)) == []
        empty = await store.list_twin_rows_by_status([], statuses=["applied"], limit=1)
        assert empty == []

        large_keys = [("missing", str(index)) for index in range(1_100)]
        large_keys.append(("alpha", "engineer"))
        large = await store.list_twin_rows_by_status(
            large_keys,
            statuses=["applied"],
            limit=10,
        )
        assert [row.job.id for row in large] == [str(twin)]
    finally:
        await lifecycle.close()


async def test_pipeline_runs_filter_total_and_page_with_tie_break(
    tmp_path: Path,
) -> None:
    """Run listing applies one cutoff, total-before-page, and run-id tie-break."""
    lifecycle, store = await open_views_performance(tmp_path / "runs.db")
    try:
        for run_id, started_at in (
            ("old", NOW - timedelta(days=10)),
            ("tie-a", NOW - timedelta(hours=1)),
            ("tie-b", NOW - timedelta(hours=1)),
            ("new", NOW),
        ):
            await insert_run(
                lifecycle,
                PipelineRun(
                    run_id=run_id,
                    started_at=started_at,
                    finished_at=started_at + timedelta(seconds=1),
                    source="scan",
                    status="succeeded",
                ),
            )

        rows, total = await store.list_pipeline_runs(limit=2, offset=1, days=7)

        assert [run.run_id for run in rows] == ["tie-b", "tie-a"]
        assert total == 3
    finally:
        await lifecycle.close()
