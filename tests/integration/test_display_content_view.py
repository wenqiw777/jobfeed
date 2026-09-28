"""Results keep independent postings even when their JD content is identical."""

# ruff: noqa: PLR2004

from dataclasses import replace

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.filtering import HardFilters
from jobfeed.domain.models import BulkTransitionRequest
from jobfeed.domain.models_views import JobsViewQuery
from jobfeed.services.jobs_view import JobsViewService
from tests.unit.test_display_content_dedupe import posting


async def test_status_aliases_are_identity_and_location_scoped(tmp_path):
    store = SQLiteStore(tmp_path / "scoped.sqlite")
    await store.connect()
    try:
        saved = []
        for job in [
            posting("1", external_identity="linkedin:123", location="New York"),
            posting("2", external_identity="linkedin:123", location=" new  YORK "),
            posting("3", external_identity="linkedin:123", location="Seattle"),
            posting("4", external_identity="linkedin:456", location="New York"),
            posting("5", external_identity=None, location="New York"),
            posting("6", external_identity=None, location="New York"),
        ]:
            saved.append((await store.save_job(replace(job, id=None))).job_id)
        twins = await store.list_twin_statuses(saved[0])
        assert [twin.job_id for twin in twins] == [saved[1]]
        assert await store.list_twin_statuses(saved[4]) == []
        result = await store.transition_status_bulk(
            BulkTransitionRequest(
                items=[(saved[0], "ignored"), (saved[4], "ignored")],
                reason_selected="test",
                reason_cascade="test-alias",
            )
        )
        assert result.cascaded == 1
        states = [(await store.get_status(identifier)).status for identifier in saved]
        assert states == ["ignored", "ignored", "new", "new", "ignored", "new"]
    finally:
        await store.close()


async def test_sqlite_content_copies_fold_in_exact_and_fast_display(tmp_path):
    store = SQLiteStore(tmp_path / "content.sqlite")
    await store.connect()
    try:
        for job in [
            posting("1"),
            posting("2", "handshake"),
            posting("3", company="Another employer"),
        ]:
            await store.save_job(replace(job, id=None))
        service = JobsViewService(store, HardFilters())
        page = await service.list_jobs(JobsViewQuery(tab="queue", limit=1), dedupe=True)
        assert page.total == 2
        assert len(page.rows) == 1
        assert page.rows[0].job.jd_text is None
        page = await service.list_jobs(
            JobsViewQuery(tab="queue", limit=50), dedupe=True, fast=True
        )
        assert len(page.rows) == 2
        assert all(row.job.jd_text is None for row in page.rows)
    finally:
        await store.close()


async def test_inflight_identity_lookup_can_omit_body(tmp_path):
    store = SQLiteStore(tmp_path / "metadata.sqlite")
    await store.connect()
    try:
        await store.save_job(replace(posting("1"), id=None))
        rows = await store.list_twin_rows_by_status(
            [("__external_identity__", "linkedin:1")],
            statuses=["new"],
            limit=50,
            include_jd_text=False,
        )
        assert len(rows) == 1
        assert rows[0].job.jd_text is None
        full = await store.list_twin_rows_by_status(
            [("__external_identity__", "linkedin:1")],
            statuses=["new"],
            limit=50,
        )
        assert full[0].job.jd_text == posting("1").jd_text
    finally:
        await store.close()


async def test_applied_identical_content_with_different_posting_id_keeps_queue(
    tmp_path,
):
    store = SQLiteStore(tmp_path / "applied.sqlite")
    await store.connect()
    try:
        await store.save_job(replace(posting("1", company="Unknown"), id=None))
        applied = await store.save_job(replace(posting("2", "handshake"), id=None))
        async with store._lifecycle.connection() as connection:
            await connection.execute(
                "UPDATE job_status SET status='applied' WHERE job_id=?",
                (applied.job_id,),
            )
            await connection.commit()
        page = await JobsViewService(store, HardFilters()).list_jobs(
            JobsViewQuery(tab="queue"),
            dedupe=True,
        )
        assert page.total == 1
    finally:
        await store.close()


async def test_applied_jobright_source_suppresses_speedyapply_alias(tmp_path):
    store = SQLiteStore(tmp_path / "jobright-alias.sqlite")
    await store.connect()
    try:
        native_id = "6ab2c17a1508734c1530bb8c"
        official = posting(
            native_id,
            "jobright",
            external_identity=None,
            url="https://careers.southwestair.com/us/en/job/RECRUITING123/example",
            location="United States",
        )
        alias = posting(
            "alias",
            "speedyapply",
            external_identity=f"jobright:{native_id}",
            url=f"https://jobright.ai/jobs/info/{native_id}",
            location="United States",
        )
        official_id = (await store.save_job(replace(official, id=None))).job_id
        await store.save_job(replace(alias, id=None))
        async with store._lifecycle.connection() as connection:
            await connection.execute(
                "UPDATE job_status SET status='applied' WHERE job_id=?",
                (official_id,),
            )
            await connection.commit()
        page = await JobsViewService(store, HardFilters()).list_jobs(
            JobsViewQuery(tab="queue"), dedupe=True
        )
        assert page.total == 0
    finally:
        await store.close()


async def test_ignored_requisition_hides_formatted_and_summary_copies(tmp_path):
    store = SQLiteStore(tmp_path / "commure-copies.sqlite")
    await store.connect()
    try:
        shared = "Build clinical APIs and reliable data pipelines. " * 8
        full = shared + "\nAI & Agents:\nOwn monitoring and deployment."
        formatted = shared + "\nAI & Agents: Own monitoring and deployment."
        summary = "Build clinical APIs and reliable data pipelines. " * 5
        common = {
            "title": "Software Engineer, Early Career 2027",
            "location": "Mountain View, CA",
        }
        records = [
            posting(
                "official",
                "jobright",
                company="Commure",
                external_identity="ashby:commure-123",
                jd_text=full,
                **common,
            ),
            posting(
                "official-summary",
                "speedyapply",
                company="Commure",
                external_identity="ashby:commure-123",
                jd_text=summary,
                **common,
            ),
            posting(
                "jobright-copy",
                "speedyapply",
                company="Commure",
                external_identity="jobright:other-listing",
                jd_text=summary,
                **common,
            ),
            posting(
                "handshake-copy",
                "handshake",
                company="Commure + Athelas",
                external_identity="handshake:123",
                jd_text=formatted,
                **common,
            ),
        ]
        saved = [
            (await store.save_job(replace(job, id=None))).job_id for job in records
        ]
        async with store._lifecycle.connection() as connection:
            await connection.execute(
                "UPDATE job_status SET status='ignored' WHERE job_id=?", (saved[0],)
            )
            await connection.commit()
        page = await JobsViewService(store, HardFilters()).list_jobs(
            JobsViewQuery(tab="queue"), dedupe=True
        )
        assert page.total == 0
    finally:
        await store.close()
