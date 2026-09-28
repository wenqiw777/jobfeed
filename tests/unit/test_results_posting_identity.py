"""Results reuse trusted posting identity, never JD similarity."""

from jobfeed.domain.models import QualityBand
from jobfeed.domain.models_views import JobsViewQuery
from jobfeed.web.schemas.jobs_list import jobs_list_response
from tests.unit.test_jobs_view_service import _RecordingStore, _row, _service


async def test_results_dto_carries_location():
    row = _row("1")
    row.job.location = "Seattle, WA"
    page = await _service(_RecordingStore(rows=[row])).list_jobs(
        JobsViewQuery(tab="queue")
    )
    assert jobs_list_response(page).model_dump()["jobs"][0]["location"] == "Seattle, WA"


async def test_results_fold_locations_of_same_requisition_but_keep_distinct_ids():
    rows = [_row(str(index)) for index in range(3)]
    for row in rows:
        row.job.jd_text = "Identical responsibilities and qualifications."
    rows[0].job.external_identity = "greenhouse:123"
    rows[1].job.external_identity = "greenhouse:123"
    rows[1].job.location = "Seattle"
    rows[2].job.external_identity = "greenhouse:456"
    rows[2].job.jd_text = "A different team owns infrastructure and deployment."
    store = _RecordingStore(rows=rows)
    page = await _service(store).list_jobs(JobsViewQuery(tab="queue"), dedupe=True)
    assert {row.job.id for row in page.rows} == {"0", "2"}
    assert store.queries[0].include_jd_text is False


async def test_unknown_posting_identity_does_not_collapse_same_title_or_content():
    rows = [_row("1"), _row("2")]
    store = _RecordingStore(rows=rows)
    page = await _service(store).list_jobs(JobsViewQuery(tab="queue"), dedupe=True)
    assert page.total == len(rows)
    assert store.twin_calls == []


async def test_jobright_source_and_speedyapply_alias_fold_in_results():
    official = _row("1", platform="jobright")
    official.job.canonical_id = "6ab2c17a1508734c1530bb8c"
    official.job.url = "https://careers.southwestair.com/us/en/job/SOUTUSR202672886ENUSEXTERNAL/example"
    alias = _row("2", platform="speedyapply")
    alias.job.url = "https://jobright.ai/jobs/info/6ab2c17a1508734c1530bb8c"
    alias.job.external_identity = "jobright:6ab2c17a1508734c1530bb8c"
    page = await _service(_RecordingStore(rows=[official, alias])).list_jobs(
        JobsViewQuery(tab="queue"), dedupe=True
    )
    assert page.total == 1


async def test_known_same_location_posting_prefers_linkedin_even_with_lower_quality():
    ats = _row("1", jd_quality=QualityBand.FULL)
    linkedin = _row("2", platform="linkedin", jd_quality=QualityBand.PARTIAL)
    for row in (ats, linkedin):
        row.job.external_identity = "greenhouse:123"
    store = _RecordingStore(rows=[ats, linkedin])
    page = await _service(store).list_jobs(JobsViewQuery(tab="queue"), dedupe=True)
    assert [row.job.id for row in page.rows] == ["2"]


async def test_applied_posting_suppresses_same_requisition_across_locations():
    new_york, seattle = _row("1"), _row("2")
    applied = _row("3", status="applied")
    for row in (new_york, seattle, applied):
        row.job.external_identity = "greenhouse:123"
    seattle.job.location = "Seattle"
    store = _RecordingStore(rows=[new_york, seattle], twin_rows=[applied])
    page = await _service(store).list_jobs(JobsViewQuery(tab="queue"), dedupe=True)
    assert page.rows == []


async def test_ignored_posting_suppresses_same_requisition_in_results():
    queued = _row("1", platform="speedyapply")
    ignored = _row("2", platform="jobright", status="ignored")
    queued.job.external_identity = "ashby:requisition-123"
    ignored.job.external_identity = "ashby:requisition-123"
    queued.job.location = "Los Angeles, CA"
    ignored.job.location = "Mountain View, CA"
    store = _RecordingStore(rows=[queued], twin_rows=[ignored])
    page = await _service(store).list_jobs(JobsViewQuery(tab="queue"), dedupe=True)
    assert page.rows == []


async def test_content_aliases_bridge_known_requisition_and_ignored_decision():
    body = (
        "Build clinical APIs and reliable data pipelines for healthcare teams. "
        "Own production monitoring, debugging, and deployment. "
        "Required: Bachelor's degree and Python experience. "
    ) * 3
    official = _row("1", platform="jobright", status="ignored")
    official.job.external_identity = "ashby:commure-123"
    official.job.jd_text = body
    official.job.location = "Mountain View, CA"
    summary = _row("2", platform="speedyapply")
    summary.job.external_identity = "ashby:commure-123"
    summary.job.jd_text = body
    summary.job.location = "Los Angeles, CA"
    alias = _row("3", platform="speedyapply")
    alias.job.external_identity = "jobright:other-listing"
    alias.job.jd_text = body
    alias.job.location = "Los Angeles, CA"
    store = _RecordingStore(rows=[summary, alias], twin_rows=[official])
    page = await _service(store).list_jobs(JobsViewQuery(tab="queue"), dedupe=True)
    assert page.rows == []


async def test_similar_title_with_different_team_requirement_stays_separate():
    shared = "Build reliable production software. " * 20
    clinical = _row("1", platform="jobright")
    payments = _row("2", platform="speedyapply")
    clinical.job.external_identity = "jobright:clinical"
    payments.job.external_identity = "jobright:payments"
    clinical.job.jd_text = shared + "Our team owns clinical workflows."
    payments.job.jd_text = shared + "Our team owns payment workflows."
    page = await _service(_RecordingStore(rows=[clinical, payments])).list_jobs(
        JobsViewQuery(tab="queue"), dedupe=True
    )
    assert {row.job.id for row in page.rows} == {"1", "2"}


async def test_short_placeholder_jds_do_not_merge_distinct_postings():
    first = _row("1", platform="jobright")
    second = _row("2", platform="speedyapply")
    first.job.external_identity = "jobright:first"
    second.job.external_identity = "jobright:second"
    first.job.jd_text = second.job.jd_text = "Apply now"
    page = await _service(_RecordingStore(rows=[first, second])).list_jobs(
        JobsViewQuery(tab="queue"), dedupe=True
    )
    assert {row.job.id for row in page.rows} == {"1", "2"}


async def test_fast_results_do_not_request_jd_text():
    store = _RecordingStore(rows=[_row("1")])
    await _service(store).list_jobs(JobsViewQuery(tab="queue"), dedupe=True, fast=True)
    assert store.queries[0].include_jd_text is False
