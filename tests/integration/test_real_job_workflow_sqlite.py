"""Canonical workflow must keep source-row decisions as audit evidence."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from jobfeed.adapters.store._sqlite_real_job_identity import _merge
from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.adapters.store.sqlite_schema import _backfill_real_job_workflow
from jobfeed.domain.filtering import HardFilters
from jobfeed.domain.models import (
    ApplicationRecord,
    BulkTransitionRequest,
    JobPosting,
    QualityBand,
    StatusFilter,
    TransitionRequest,
)

SOURCE_COUNT = 2
EXPECTED_MERGED_HISTORY_ROWS = 2
REAL_VIEW_PAGE_SIZE = 25
REAL_VIEW_RESULTS = 26
FILTERED_REAL_RESULTS = 2
_REPRESENTATIVE_JD = "Requirements: Python, distributed systems, and APIs. " * 12
_REPRESENTATIVE_ATS = "https://qualcomm.eightfold.ai/careers?pid=446721162271"


@pytest.mark.parametrize("with_current_eval", [False, True])
@pytest.mark.parametrize("alias_platform", ["linkedin", "jobright"])
async def test_later_official_source_drives_card_and_canonical_closure_sqlite(
    tmp_path: Path,
    with_current_eval: bool,
    alias_platform: str,
) -> None:
    store = SQLiteStore(tmp_path / "representative-closure.db")
    await store.connect()
    now = datetime.now(UTC)
    try:
        alias = JobPosting(
            platform=alias_platform,
            canonical_id="alias-representative",
            url=(
                "https://www.linkedin.com/jobs/view/4469009469/"
                if alias_platform == "linkedin"
                else "https://jobright.ai/jobs/info/4469009469"
            ),
            apply_url=_REPRESENTATIVE_ATS,
            title="Backend SWE",
            company="Qualcomm",
            location="San Diego, CA",
            discovered_at=now,
            jd_text=_REPRESENTATIVE_JD,
            jd_quality=QualityBand.FULL,
            closed_at=now,
        )
        first = await store.save_job(alias)
        parent = await store.resolve_real_job_id(first.job_id)
        assert parent is not None
        assert (await store.query_real_jobs_view(decision="results"))["total"] == 1
        if with_current_eval:
            assert len(await store.claim_real_job_stage_a_by_ids([parent])) == 1
        official = replace(
            alias,
            platform="jobright",
            canonical_id="jr-representative",
            url=_REPRESENTATIVE_ATS,
            apply_url=None,
            closed_at=None,
        )
        second = await store.save_job(official)
        page = await store.query_real_jobs_view(decision="results", limit=1)
        detail = await store.get_real_job_view(parent)
        assert page["total"] == page["tab_counts"]["results"] == 1
        assert page["jobs"][0]["source_job_id"] == int(second.job_id)
        assert detail is not None
        assert detail["row"]["id"] == int(second.job_id)
        assert detail["row"]["jd_text"] == _REPRESENTATIVE_JD
        assert detail["row"]["url"] == _REPRESENTATIVE_ATS
        library = await store.query_source_library(
            decision=None, sort="discovered_desc", search=None, limit=10, offset=0
        )
        assert library["total"] == SOURCE_COUNT
        assert {row["url"] for row in library["jobs"]} == {
            alias.url,
            _REPRESENTATIVE_ATS,
        }
        await store.mark_job_closed(job_id=first.job_id, closed_at=now)
        assert (await store.query_real_jobs_view(decision="results"))["total"] == 1
        async with aiosqlite.connect(tmp_path / "representative-closure.db") as db:
            await db.execute(
                "UPDATE real_jobs SET representative_job_id=?,official_closed_at=? "
                "WHERE id=?",
                (int(first.job_id), now.isoformat(), int(parent)),
            )
            await db.commit()
        assert (await store.backfill_real_job_identifiers(limit=10))[1] == SOURCE_COUNT
        page = await store.query_real_jobs_view(decision="results")
        assert page["total"] == 1
        assert page["jobs"][0]["source_job_id"] == int(second.job_id)
        await store.mark_job_closed(job_id=second.job_id, closed_at=now)
        async with aiosqlite.connect(tmp_path / "representative-closure.db") as db:
            await db.execute(
                "UPDATE real_jobs SET official_closed_at=NULL WHERE id=?",
                (int(parent),),
            )
            await db.commit()
        assert (await store.backfill_real_job_identifiers(limit=10))[1] == SOURCE_COUNT
        closed = await store.query_real_jobs_view(decision="results", limit=10)
        assert closed["total"] == closed["tab_counts"]["results"] == 0
        assert closed["jobs"] == []
        await store.record_enrichment(
            job_id=second.job_id,
            jd_text=_REPRESENTATIVE_JD,
            jd_quality="full",
            enriched_at=now,
            enrich_source="test-reopen",
        )
        reopened = await store.query_real_jobs_view(decision="results", limit=10)
        assert reopened["total"] == reopened["tab_counts"]["results"] == 1
    finally:
        await store.close()


async def test_representative_change_keeps_canonical_dates_sqlite(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "canonical-dates.db")
    await store.connect()
    now = datetime(2026, 9, 24, 12, tzinfo=UTC)
    old = now - timedelta(days=10)
    middle = now - timedelta(days=5)
    try:
        linkedin = JobPosting(
            platform="linkedin",
            canonical_id="date-old",
            url="https://www.linkedin.com/jobs/view/4469009469/",
            apply_url=_REPRESENTATIVE_ATS,
            title="Backend SWE",
            company="Qualcomm",
            location="San Diego, CA",
            discovered_at=old,
            posted_at=old,
            jd_text=_REPRESENTATIVE_JD,
            jd_quality=QualityBand.FULL,
        )
        first = await store.save_job(linkedin)
        parent = await store.resolve_real_job_id(first.job_id)
        await store.save_job(
            replace(
                linkedin,
                platform="jobright",
                canonical_id="date-official",
                url=_REPRESENTATIVE_ATS,
                apply_url=None,
                discovered_at=now,
                posted_at=now,
            )
        )
        middle_result = await store.save_job(
            replace(
                linkedin,
                platform="linkedin",
                canonical_id="date-middle",
                url="https://www.linkedin.com/jobs/view/4469009470/",
                apply_url=None,
                company="Another Company",
                discovered_at=middle,
                posted_at=middle,
            )
        )
        page = await store.query_real_jobs_view(
            decision="results",
            sort="triage_posted_desc",
            now=now,
        )
        assert [row["real_job_id"] for row in page["jobs"]] == [
            int(await store.resolve_real_job_id(middle_result.job_id)),
            int(parent),
        ]
        old_row = page["jobs"][1]
        assert datetime.fromisoformat(old_row["posted_at"]) == old
        assert datetime.fromisoformat(old_row["discovered_at"]) == old
        detail = await store.get_real_job_view(parent)
        assert detail is not None
        assert datetime.fromisoformat(detail["row"]["posted_at"]) == old
        assert datetime.fromisoformat(detail["row"]["discovered_at"]) == old
        filtered = await store.query_real_jobs_view(
            decision="results",
            now=now,
            hard_filters=HardFilters(posted_within_days=7),
        )
        assert filtered["total"] == filtered["tab_counts"]["results"] == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_real_results_hard_filters_use_original_date_and_exact_page(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "filtered-real.db")
    await store.connect()
    now = datetime(2026, 9, 24, 12, tzinfo=UTC)
    try:
        real_ids: dict[str, str] = {}
        for key, company, location, age_days in (
            ("fresh-one", "Acme", "Seattle, WA", 1),
            ("fresh-two", "Acme", "Seattle, WA", 1),
            ("blocked-company", "Blocked Corp", "Seattle, WA", 1.5),
            ("blocked-location", "Acme", "Toronto, ON", 1),
            ("old", "Acme", "Seattle, WA", 10),
            ("missing-date", "Acme", "Seattle, WA", 10),
            ("wait", "Acme", "Seattle, WA", 10),
        ):
            saved = await store.save_job(
                JobPosting(
                    platform="indeed" if key == "blocked-company" else "linkedin",
                    canonical_id=key,
                    url=f"https://example.test/{key}",
                    title="Engineer",
                    company=company,
                    location=location,
                    posted_at=(
                        None
                        if key == "missing-date"
                        else now - timedelta(days=age_days)
                    ),
                    discovered_at=now - timedelta(days=age_days),
                )
            )
            real_id = await store.resolve_real_job_id(saved.job_id)
            assert real_id is not None
            real_ids[key] = real_id
        repost = await store.save_job(
            JobPosting(
                platform="jobright",
                canonical_id="old-repost",
                url="https://example.test/old-repost",
                title="Engineer",
                company="Acme",
                location="Seattle, WA",
                posted_at=now,
                discovered_at=now,
                is_repost=True,
            )
        )
        async with aiosqlite.connect(tmp_path / "filtered-real.db") as db:
            await db.execute(
                "UPDATE jobs SET real_job_id=? WHERE id=?",
                (int(real_ids["old"]), int(repost.job_id)),
            )
            await db.commit()
        await store.transition_real_job_status(
            TransitionRequest(job_id=real_ids["wait"], new_status="shortlisted")
        )
        filters = HardFilters(
            company_blocklist=["Blocked"],
            location_allowlist=["United States"],
            posted_within_days=3,
        )
        first = await store.query_real_jobs_view(
            decision="results", hard_filters=filters, now=now, limit=1
        )
        second = await store.query_real_jobs_view(
            decision="results", hard_filters=filters, now=now, limit=1, offset=1
        )
        assert first["total"] == second["total"] == FILTERED_REAL_RESULTS
        assert first["tab_counts"] == {
            "results": 2,
            "wait": 1,
            "applied": 0,
            "ignored": 0,
        }
        assert {first["jobs"][0]["real_job_id"], second["jobs"][0]["real_job_id"]} == {
            int(real_ids["fresh-one"]),
            int(real_ids["fresh-two"]),
        }
        selected = await store.select_real_job_ids(
            decision="results",
            search="Engineer",
            hard_filters=filters,
            now=now,
        )
        assert selected["total"] == first["total"]
        assert set(selected["real_job_ids"]) == {
            real_ids["fresh-one"],
            real_ids["fresh-two"],
        }
        waiting = await store.select_real_job_ids(
            decision="wait",
            hard_filters=filters,
            now=now,
        )
        assert waiting == {"real_job_ids": [real_ids["wait"]], "total": 1}
        searched = await store.query_real_jobs_view(
            decision="results", search="Engineer", hard_filters=filters, now=now
        )
        assert searched["total"] == FILTERED_REAL_RESULTS
        assert searched["tab_counts"]["results"] == FILTERED_REAL_RESULTS
        assert (
            await store.query_real_jobs_view(
                decision="wait", hard_filters=filters, now=now
            )
        )["total"] == 1
        hours = await store.query_real_jobs_view(
            decision="results",
            hard_filters=HardFilters(posted_within_hours=24),
            now=now,
        )
        assert int(real_ids["blocked-company"]) in {
            row["real_job_id"] for row in hours["jobs"]
        }
        assert int(real_ids["old"]) not in {row["real_job_id"] for row in hours["jobs"]}
        assert int(real_ids["missing-date"]) not in {
            row["real_job_id"] for row in hours["jobs"]
        }
        big = await store.query_real_jobs_view(
            decision="results",
            hard_filters=HardFilters(
                posted_within_days=3, big_company_list=["Acme"], big_company_days=15
            ),
            now=now,
        )
        assert int(real_ids["old"]) in {row["real_job_id"] for row in big["jobs"]}
        blocked_location = await store.query_real_jobs_view(
            decision="results",
            hard_filters=HardFilters(location_blocklist=["Seattle"]),
            now=now,
        )
        assert [row["real_job_id"] for row in blocked_location["jobs"]] == [
            int(real_ids["blocked-location"])
        ]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_real_job_transition_does_not_rewrite_source_status(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "jobs.db")
    await store.connect()
    try:
        saved = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="one",
                url="https://example.com/one",
                title="Engineer",
                company="Acme",
                location="Remote",
                discovered_at=datetime.now(UTC),
            )
        )
        async with aiosqlite.connect(tmp_path / "jobs.db") as db:
            row = await (
                await db.execute(
                    "SELECT real_job_id FROM jobs WHERE id=?", (int(saved.job_id),)
                )
            ).fetchone()
            assert row is not None
            real_id = str(row[0])
        assert (
            await store.transition_real_job_status(
                TransitionRequest(
                    job_id=real_id,
                    new_status="shortlisted",
                )
            )
            == "shortlisted"
        )
        assert (await store.get_real_job_status(real_id)).status == "shortlisted"
        assert (await store.get_status(saved.job_id)).status == "new"
        assert (await store.get_real_job_status_history(real_id))[0] == "shortlisted"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_conflicting_source_decisions_hold_canonical_backfill(
    tmp_path: Path,
) -> None:
    path = tmp_path / "conflict.db"
    store = SQLiteStore(path)
    await store.connect()
    try:
        ids = []
        for key in ("one", "two"):
            saved = await store.save_job(
                JobPosting(
                    platform="linkedin",
                    canonical_id=key,
                    url=f"https://example.com/{key}",
                    title="Engineer",
                    company="Acme",
                    location="Remote",
                    discovered_at=datetime.now(UTC),
                )
            )
            ids.append(int(saved.job_id))
        await store.transition_status(
            TransitionRequest(
                job_id=str(ids[0]),
                new_status="applied",
            )
        )
        await store.transition_status(
            TransitionRequest(
                job_id=str(ids[1]),
                new_status="ignored",
            )
        )
        async with aiosqlite.connect(path) as db:
            first = (
                await (
                    await db.execute(
                        "SELECT real_job_id FROM jobs WHERE id=?", (ids[0],)
                    )
                ).fetchone()
            )[0]
            await db.execute(
                "UPDATE jobs SET real_job_id=? WHERE id=?", (first, ids[1])
            )
            await db.execute(
                "DELETE FROM real_job_status WHERE real_job_id=?", (first,)
            )
            await _backfill_real_job_workflow(db)
            await db.commit()
            assert (
                await (
                    await db.execute(
                        "SELECT identity_review_state FROM real_jobs WHERE id=?",
                        (first,),
                    )
                ).fetchone()
            )[0] == "status_conflict"
            assert (
                await (
                    await db.execute(
                        "SELECT COUNT(*) FROM real_job_status WHERE real_job_id=?",
                        (first,),
                    )
                ).fetchone()
            )[0] == 0
            assert (
                await (
                    await db.execute(
                        "SELECT COUNT(*) FROM job_status WHERE job_id IN (?,?)", ids
                    )
                ).fetchone()
            )[0] == SOURCE_COUNT
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_canonical_bulk_and_interview_lifecycle(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "lifecycle.db")
    await store.connect()
    try:
        saved = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="lifecycle",
                url="https://example.test/lifecycle",
                title="Engineer",
                company="Acme",
                location="Remote",
                discovered_at=datetime.now(UTC),
            )
        )
        real_id = await store.resolve_real_job_id(saved.job_id)
        assert real_id is not None
        bulk = await store.transition_real_jobs_bulk(
            BulkTransitionRequest(
                items=[(real_id, "applied"), (real_id, "applied")],
                reason_selected="test",
                reason_cascade="test",
            )
        )
        assert bulk.succeeded == 1
        assert bulk.failed == []
        round_ = await store.add_real_job_interview(real_job_id=real_id, label="Phone")
        assert round_.round_index == 1
        assert (await store.get_real_job_status(real_id)).status == "interviewing"
        done = await store.complete_real_job_interview(
            real_job_id=real_id,
            round_index=1,
            notes="done",
        )
        assert done.completed_at is not None
        await store.transition_real_job_status(
            TransitionRequest(
                job_id=real_id,
                new_status="ghosted",
            )
        )
        assert await store.restore_real_job(real_id) == "interviewing"
        assert (await store.get_status(saved.job_id)).status == "new"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_backfill_upgrades_neutral_parent_after_source_decision(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "resync.db")
    await store.connect()
    try:
        saved = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="resync",
                url="https://example.test/resync",
                title="Engineer",
                company="Acme",
                location="Remote",
                discovered_at=datetime.now(UTC),
            )
        )
        real_id = await store.resolve_real_job_id(saved.job_id)
        assert real_id is not None
        await store.transition_status(
            TransitionRequest(
                job_id=saved.job_id,
                new_status="applied",
            )
        )
        await store.backfill_real_job_workflow()
        assert (await store.get_real_job_status(real_id)).status == "applied"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_historical_application_backfill_preserves_unknown_target(
    tmp_path: Path,
) -> None:
    path = tmp_path / "historical-apply.db"
    store = SQLiteStore(path)
    await store.connect()
    try:
        saved = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="historical-apply",
                url="https://example.test/posting",
                apply_url="https://ats.example.test/target",
                title="Engineer",
                company="Acme",
                location="Remote",
                discovered_at=datetime.now(UTC),
            )
        )
        await store.transition_status(
            TransitionRequest(job_id=saved.job_id, new_status="applied")
        )
        async with aiosqlite.connect(path) as db:
            await db.execute(
                "INSERT INTO applied(job_id,notes) VALUES(?,?)",
                (int(saved.job_id), "legacy audit"),
            )
            await db.commit()
        await store.backfill_real_job_workflow()
        async with aiosqlite.connect(path) as db:
            row = await (
                await db.execute(
                    "SELECT real_job_id,source_job_id,apply_url,notes "
                    "FROM real_job_applications"
                )
            ).fetchone()
            assert row == (
                int(await store.resolve_real_job_id(saved.job_id)),
                int(saved.job_id),
                None,
                "legacy audit",
            )
            assert (
                await (await db.execute("SELECT COUNT(*) FROM applied")).fetchone()
            )[0] == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_canonical_application_records_target_without_source_mutation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "canonical-apply.db"
    store = SQLiteStore(path)
    await store.connect()
    try:
        saved = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="canonical-apply",
                url="https://example.test/posting",
                title="Engineer",
                company="Acme",
                location="Remote",
                discovered_at=datetime.now(UTC),
            )
        )
        real_id = await store.resolve_real_job_id(saved.job_id)
        assert real_id is not None
        record = ApplicationRecord(job_id=saved.job_id, applied_at=datetime.now(UTC))
        assert await store.record_real_job_application_with_snapshots(
            record,
            real_job_id=real_id,
            source_job_id=saved.job_id,
            apply_url="https://ats.example.test/submit",
        )
        assert not await store.record_real_job_application_with_snapshots(
            record,
            real_job_id=real_id,
            source_job_id=saved.job_id,
            apply_url="https://ats.example.test/submit",
        )
        assert (await store.get_real_job_status(real_id)).status == "applied"
        assert (await store.get_status(saved.job_id)).status == "new"
        assert [
            row.job_id
            for row in await store.list_real_job_statuses(
                StatusFilter(statuses=frozenset({"applied"}))
            )
        ] == [real_id]
        async with aiosqlite.connect(path) as db:
            rows = await (
                await db.execute(
                    "SELECT source_job_id,apply_url FROM real_job_applications"
                )
            ).fetchall()
        assert rows == [(int(saved.job_id), "https://ats.example.test/submit")]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_canonical_attention_stats_and_decay_count_one_parent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "consumer.db"
    store = SQLiteStore(path)
    await store.connect()
    try:
        saved = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="consumer",
                url="https://example.test/consumer",
                title="Engineer",
                company="Acme",
                location="Remote",
                discovered_at=datetime.now(UTC),
            )
        )
        real_id = await store.resolve_real_job_id(saved.job_id)
        assert real_id is not None
        await store.record_real_job_application_with_snapshots(
            ApplicationRecord(job_id=saved.job_id, applied_at=datetime.now(UTC)),
            real_job_id=real_id,
            source_job_id=saved.job_id,
            apply_url=None,
        )
        assert (await store.real_job_application_stats()).applied_count == 1
        await store.set_real_job_followup(
            real_job_id=real_id, at=datetime(2020, 1, 1, tzinfo=UTC)
        )
        attention = await store.real_job_workflow_attention()
        assert [item.job_id for item in attention.follow_up_today] == [real_id]
        async with aiosqlite.connect(path) as db:
            await db.execute(
                "UPDATE real_job_status SET last_status_change_at=? "
                "WHERE real_job_id=?",
                ("2020-01-01T00:00:00.000000Z", int(real_id)),
            )
            await db.commit()
        result = await store.auto_decay_real_jobs()
        assert result.ghosted == 1
        assert (await store.get_real_job_status(real_id)).status == "ghosted"
        assert (await store.get_status(saved.job_id)).status == "new"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_application_stats_require_submission_event_and_later_response(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "stats.db")
    await store.connect()
    try:
        ids = []
        for key in ("manual-only", "submitted"):
            saved = await store.save_job(
                JobPosting(
                    platform="linkedin",
                    canonical_id=key,
                    url=f"https://example.test/{key}",
                    title="Engineer",
                    company="Acme",
                    location="Remote",
                    discovered_at=datetime.now(UTC),
                )
            )
            real_id = await store.resolve_real_job_id(saved.job_id)
            assert real_id is not None
            ids.append((saved.job_id, real_id))
            for status in ("applied", "interviewing"):
                await store.transition_real_job_status(
                    TransitionRequest(job_id=real_id, new_status=status)
                )

        source_id, real_id = ids[1]
        await store.record_real_job_application_with_snapshots(
            ApplicationRecord(job_id=source_id, applied_at=datetime.now(UTC)),
            real_job_id=real_id,
            source_job_id=source_id,
            apply_url="https://ats.example.test/apply",
            resume_variant="tailored",
        )
        await store.record_real_job_application_with_snapshots(
            ApplicationRecord(job_id=source_id, applied_at=datetime.now(UTC)),
            real_job_id=real_id,
            source_job_id=source_id,
            apply_url="https://ats.example.test/second",
        )
        stats = await store.real_job_application_stats(by_resume=True)
        assert (stats.applied_count, stats.response_count) == (1, 0)
        assert stats.by_resume is not None
        assert stats.by_resume["tailored"].sent == 1

        await store.transition_real_job_status(
            TransitionRequest(job_id=real_id, new_status="offer")
        )
        stats = await store.real_job_application_stats()
        assert (stats.applied_count, stats.response_count, stats.offer_count) == (
            1,
            1,
            1,
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_real_job_view_pages_and_four_decisions_are_exact(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "real-pages.db")
    await store.connect()
    try:
        real_ids = []
        for index in range(29):
            saved = await store.save_job(
                JobPosting(
                    platform="linkedin",
                    canonical_id=f"page-{index}",
                    url=f"https://example.test/page-{index}",
                    title=f"Engineer {index}",
                    company="Acme",
                    location="Remote",
                    discovered_at=datetime.now(UTC),
                )
            )
            real_id = await store.resolve_real_job_id(saved.job_id)
            assert real_id is not None
            real_ids.append(real_id)
        for index, status in ((26, "shortlisted"), (27, "applied"), (28, "ignored")):
            await store.transition_real_job_status(
                TransitionRequest(job_id=real_ids[index], new_status=status)
            )
        first = await store.query_real_jobs_view(
            decision="results", limit=REAL_VIEW_PAGE_SIZE
        )
        second = await store.query_real_jobs_view(
            decision="results", limit=REAL_VIEW_PAGE_SIZE, offset=REAL_VIEW_PAGE_SIZE
        )
        assert first["total"] == second["total"] == REAL_VIEW_RESULTS
        assert len(first["jobs"]) == REAL_VIEW_PAGE_SIZE
        assert len(second["jobs"]) == 1
        assert first["tab_counts"] == {
            "results": 26,
            "wait": 1,
            "applied": 1,
            "ignored": 1,
        }
        assert {row["real_job_id"] for row in first["jobs"]}.isdisjoint(
            {row["real_job_id"] for row in second["jobs"]}
        )
        for decision in ("wait", "applied", "ignored"):
            assert (await store.query_real_jobs_view(decision=decision))["total"] == 1
        searched = await store.query_real_jobs_view(
            decision="results", search="Engineer 20", sort="triage_score_desc"
        )
        assert searched["total"] == 1
        assert searched["jobs"][0]["real_job_id"] == int(real_ids[20])
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_selection_snapshot_survives_offset_shift_sqlite(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "selection.db")
    await store.connect()
    try:
        ids = []
        for index in range(3):
            saved = await store.save_job(
                JobPosting(
                    platform="linkedin",
                    canonical_id=f"selection-{index}",
                    url=f"https://example.test/selection-{index}",
                    title=f"Engineer {index}",
                    company="Acme",
                    location="Remote",
                    discovered_at=datetime.now(UTC),
                )
            )
            parent = await store.resolve_real_job_id(saved.job_id)
            assert parent is not None
            ids.append(parent)
        first = await store.query_real_jobs_view(
            decision="results",
            sort="discovered_desc",
            limit=1,
        )
        selected = await store.select_real_job_ids(
            decision="results",
            sort="discovered_desc",
        )
        assert selected["total"] == len(selected["real_job_ids"]) == len(ids)
        assert set(selected["real_job_ids"]) == set(ids)
        await store.transition_real_job_status(
            TransitionRequest(
                job_id=str(first["jobs"][0]["real_job_id"]),
                new_status="ignored",
            )
        )
        second = await store.query_real_jobs_view(
            decision="results",
            sort="discovered_desc",
            limit=1,
            offset=1,
        )
        assert {
            str(first["jobs"][0]["real_job_id"]),
            str(second["jobs"][0]["real_job_id"]),
        } != set(selected["real_job_ids"])
        shifted_first = await store.query_real_jobs_view(
            decision="results",
            sort="discovered_desc",
            limit=1,
        )
        inserted = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="selection-new",
                url="https://example.test/selection-new",
                title="Engineer new",
                company="Acme",
                location="Remote",
                discovered_at=datetime.now(UTC),
            )
        )
        inserted_parent = await store.resolve_real_job_id(inserted.job_id)
        assert inserted_parent not in selected["real_job_ids"]
        shifted_second = await store.query_real_jobs_view(
            decision="results",
            sort="discovered_desc",
            limit=1,
            offset=1,
        )
        assert (
            shifted_first["jobs"][0]["real_job_id"]
            == (shifted_second["jobs"][0]["real_job_id"])
        )
        assert set(selected["real_job_ids"]) == set(ids)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_merge_retains_canonical_children_when_winner_has_no_status(
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing-winner-status.db"
    store = SQLiteStore(path)
    await store.connect()
    try:
        ids = []
        for key in ("winner", "loser"):
            saved = await store.save_job(
                JobPosting(
                    platform="linkedin",
                    canonical_id=key,
                    url=f"https://example.test/{key}",
                    title="Engineer",
                    company="Acme",
                    location="Remote",
                    discovered_at=datetime.now(UTC),
                )
            )
            ids.append(saved.job_id)
        winner, loser = [int(await store.resolve_real_job_id(i)) for i in ids]
        await store.transition_real_job_status(
            TransitionRequest(job_id=str(loser), new_status="applied")
        )
        await store.add_real_job_interview(real_job_id=str(loser), label="Phone")
        async with aiosqlite.connect(path) as db:
            await db.execute(
                "DELETE FROM real_job_status WHERE real_job_id=?", (winner,)
            )
            await _merge(db, winner, loser)
            await db.commit()
            assert (
                await (
                    await db.execute(
                        "SELECT COUNT(*) FROM real_job_status_history "
                        "WHERE real_job_id=?",
                        (winner,),
                    )
                ).fetchone()
            )[0] >= EXPECTED_MERGED_HISTORY_ROWS
            assert (
                await (
                    await db.execute(
                        "SELECT COUNT(*) FROM real_job_interview_rounds "
                        "WHERE real_job_id=?",
                        (winner,),
                    )
                ).fetchone()
            )[0] == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_reapply_notice_excludes_alias_of_same_parent(tmp_path: Path) -> None:
    path = tmp_path / "reapply.db"
    store = SQLiteStore(path)
    await store.connect()
    try:
        ids = []
        for key in ("first", "alias", "other"):
            saved = await store.save_job(
                JobPosting(
                    platform="linkedin",
                    canonical_id=key,
                    url=f"https://example.test/{key}",
                    title=key,
                    company="Acme",
                    location="Remote",
                    discovered_at=datetime.now(UTC),
                )
            )
            ids.append(saved.job_id)
        parent = await store.resolve_real_job_id(ids[0])
        other_parent = await store.resolve_real_job_id(ids[2])
        assert parent is not None and other_parent is not None
        async with aiosqlite.connect(path) as db:
            await db.execute(
                "UPDATE jobs SET real_job_id=? WHERE id=?",
                (int(parent), int(ids[1])),
            )
            await db.commit()
        await store.transition_real_job_status(
            TransitionRequest(job_id=parent, new_status="applied")
        )
        assert await store.compute_real_job_reapply_notice(job_id=ids[1]) is None
        await store.transition_real_job_status(
            TransitionRequest(job_id=other_parent, new_status="applied")
        )
        notice = await store.compute_real_job_reapply_notice(job_id=ids[1])
        assert notice is not None and f"real job {other_parent}" in notice
    finally:
        await store.close()
