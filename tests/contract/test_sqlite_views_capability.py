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
        assert any("evaluations_1" in detail for detail in details)
        assert any("INTEGER PRIMARY KEY" in detail for detail in details)
    finally:
        await lifecycle.close()


def test_jobs_view_page_query_does_not_materialize_full_job_descriptions() -> None:
    """List pages hydrate summaries, not thousands of complete JD payloads."""
    sql, _ = _jobs_view_rows_query(JobsViewQuery(tab="queue"), NOW)

    assert "j.*" not in sql
    assert "j.jd_text" not in sql


def test_provisional_page_query_starts_from_the_small_evaluated_corpus() -> None:
    """Fast first paint scans evaluated jobs and changes no DB schema."""
    sql, _ = _jobs_view_rows_query(
        JobsViewQuery(
            tab="queue",
            require_verdict=True,
            sort="discovered_desc",
            include_counts=False,
        ),
        NOW,
    )

    assert "FROM evaluations AS e INDEXED BY idx_eval_stage_b_completed" in sql
    assert "e.stage_b_status='completed'" in sql


def test_exact_verdict_page_query_starts_from_the_verdict_corpus() -> None:
    """Exact Triage rows must not fan out from the much larger status table."""
    sql, _ = _jobs_view_rows_query(
        JobsViewQuery(
            tab="queue",
            require_verdict=True,
            sort="posted_desc",
            include_counts=True,
        ),
        NOW,
    )

    assert "FROM evaluations AS e INDEXED BY idx_eval_verdict_job" in sql
    assert "e.stage_b_verdict IS NOT NULL" in sql


async def test_exact_verdict_query_plan_drives_from_the_verdict_index(
    tmp_path: Path,
) -> None:
    """INDEXED BY alone is insufficient: the verdict index must drive the join."""
    lifecycle, _store = await open_views_performance(tmp_path / "verdict-plan.db")
    try:
        sql, params = _jobs_view_rows_query(
            JobsViewQuery(
                tab="queue",
                require_verdict=True,
                sort="posted_desc",
                limit=10_000,
                include_counts=True,
                include_total=False,
            ),
            NOW,
        )
        plan = await rows(lifecycle, f"EXPLAIN QUERY PLAN {sql}", tuple(params))
        details = [str(row[3]) for row in plan]

        assert "idx_eval_verdict_job" in details[0]
    finally:
        await lifecycle.close()


async def test_scan_run_reports_exact_new_jobs_by_configured_source(
    tmp_path: Path,
) -> None:
    """First-insert history reconstructs source counts for old scan runs."""
    lifecycle, store = await open_views_performance(tmp_path / "run-sources.db")
    try:
        run = PipelineRun(
            run_id="run-source-breakdown",
            started_at=NOW - timedelta(minutes=10),
            finished_at=NOW + timedelta(minutes=10),
            source="all",
            status="succeeded",
            jobs_discovered=4,
            jobs_inserted=4,
        )
        await insert_run(lifecycle, run)
        for platform in ("greenhouse", "lever", "indeed", "linkedin_guest"):
            await insert_job(
                lifecycle,
                platform,
                platform=platform,
                discovered_at=utc_text(NOW),
            )

        counts = await store.get_new_job_source_counts(run.run_id)

        assert counts == {"ats": 2, "indeed": 1, "linkedin_guest": 1}
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
        await set_evaluation(lifecycle, middle, stage_a_score=79)
        await set_evaluation(lifecycle, oldest, stage_a_score=80, fit_score=77)

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
        await set_evaluation(
            lifecycle,
            scored,
            stage_a_score=80,
            fit_score=88,
            verdict="apply",
            stage_b_status="completed",
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
        assert filtered.rows[0].stage_b_fit_score == 88
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
