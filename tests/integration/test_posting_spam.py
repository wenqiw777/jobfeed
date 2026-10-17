"""Real SQLite coverage for automatic exclusions and reversible human decisions."""

import json
from datetime import UTC, datetime

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.posting_spam import RULE_VERSION
from tests.unit.test_intermediary_resolution import official

EXISTING_SCORE = 92


def publisher(index=1, **changes):
    values = {
        "company": "New Publisher",
        "canonical_id": f"p{index}",
        "url": f"https://www.linkedin.com/jobs/view/{index}",
        "title": f"Engineer {index}",
        "jd_text": "We are sharing this opportunity on behalf of Acme. Build systems.",
        "apply_url": f"https://fetchjobs.co/job/{index}",
    }
    values.update(changes)
    return official(id=None, **values)


async def test_saved_spam_hidden_claim_rejected_review_and_override(tmp_path):
    store = SQLiteStore(tmp_path / "test.sqlite")
    await store.connect()
    try:
        saved = await store.save_job(publisher())
        parent = await store.resolve_real_job_id(saved.job_id)
        assert (await store.query_real_jobs_view(decision="results"))["total"] == 0
        assert await store.claim_real_job_stage_a_by_ids([parent]) == []
        queue = await store.spam_review(status="blocked")
        assert queue["total"] == 1
        assert queue["items"][0]["evidence"]
        await store.spam_decide(saved.job_id, scope="job", decision="allow")
        assert (await store.query_real_jobs_view(decision="results"))["total"] == 1
        assert len(await store.claim_real_job_stage_a_by_ids([parent])) == 1
        await store.save_job(publisher())
        assert (await store.spam_review(status="reviewed"))["total"] == 1
        await store.spam_decide(saved.job_id, scope="job", decision="reset")
        assert (await store.query_real_jobs_view(decision="results"))["total"] == 0
        assert await store.get_job(saved.job_id)
    finally:
        await store.close()


async def test_uncertainty_remains_visible(tmp_path):
    store = SQLiteStore(tmp_path / "review.sqlite")
    await store.connect()
    try:
        await store.save_job(publisher(apply_url=None))
        assert (await store.spam_review(status="pending"))["total"] == 1
        assert (await store.query_real_jobs_view(decision="results"))["total"] == 1
    finally:
        await store.close()


async def test_company_requires_independent_titles_and_unanimous_evidence(tmp_path):
    store = SQLiteStore(tmp_path / "company.sqlite")
    await store.connect()
    try:
        for i in range(1, 4):
            await store.save_job(publisher(i))
        assert await store.get_state("spam:company:new publisher") == "auto_block"
        await store.save_job(
            publisher(4, jd_text="Build our own production APIs.", apply_url=None)
        )
        assert await store.get_state("spam:company:new publisher") is None
        assert (await store.query_real_jobs_view(decision="results"))["total"] == 1
    finally:
        await store.close()


async def test_same_title_locations_never_promote_and_company_allow_survives(tmp_path):
    store = SQLiteStore(tmp_path / "duplicates.sqlite")
    await store.connect()
    try:
        for i in range(1, 5):
            saved = await store.save_job(
                publisher(i, title="Software Engineer", location=f"City {i}")
            )
        assert await store.get_state("spam:company:new publisher") is None
        await store.spam_decide(saved.job_id, scope="company", decision="allow")
        await store.save_job(publisher(8))
        assert await store.get_state("spam:company:new publisher") == "allow"
        assert (await store.query_real_jobs_view(decision="results"))["total"] > 0
        await store.spam_decide(saved.job_id, scope="company", decision="block")
        assert (await store.query_real_jobs_view(decision="results"))["total"] == 0
    finally:
        await store.close()


async def test_enrichment_catches_new_evidence_and_keeps_existing_scores(tmp_path):

    store = SQLiteStore(tmp_path / "enrich.sqlite")
    await store.connect()
    try:
        saved = await store.save_job(
            publisher(jd_text="Build backend services.", apply_url=None)
        )
        parent = await store.resolve_real_job_id(saved.job_id)
        await store.claim_real_job_stage_a_by_ids([parent])
        async with store._lifecycle.connection() as db:
            await db.execute(
                "UPDATE real_job_evaluations SET stage_a_status='completed',"
                "stage_a_score=92 WHERE real_job_id=?",
                (int(parent),),
            )
        await store.record_enrichment(
            job_id=saved.job_id,
            jd_text=publisher().jd_text,
            jd_quality="full",
            enriched_at=datetime.now(UTC),
            enrich_source="test",
            jd_lang="en",
            posted_at=None,
            apply_url=publisher().apply_url,
        )
        assert (await store.query_real_jobs_view(decision="results"))["total"] == 0
        assert (await store.select_real_job_ids(decision="results"))[
            "real_job_ids"
        ] == []
        assert (
            await store.claim_real_job_stage_b_by_ids([parent], stage_a_threshold=50)
            == []
        )
        async with store._lifecycle.connection() as db:
            row = await (
                await db.execute(
                    "SELECT stage_a_score FROM real_job_evaluations "
                    "WHERE real_job_id=?",
                    (int(parent),),
                )
            ).fetchone()
        assert row[0] == EXISTING_SCORE
    finally:
        await store.close()


async def test_official_and_nearby_company_names_veto_promotion(tmp_path):
    store = SQLiteStore(tmp_path / "safe.sqlite")
    await store.connect()
    try:
        await store.save_job(
            publisher(
                9,
                url="https://boards.greenhouse.io/newpublisher/jobs/123",
                jd_text="Build our own APIs.",
                apply_url=None,
            )
        )
        for i in range(1, 4):
            await store.save_job(publisher(i))
        assert await store.get_state("spam:company:new publisher") is None
        saved = await store.save_job(
            publisher(
                10,
                company="New Publisher Labs",
                jd_text="Build our own APIs.",
                apply_url=None,
            )
        )
        other = await store.save_job(publisher(11))
        await store.spam_decide(other.job_id, scope="company", decision="block")
        page = await store.query_real_jobs_view(decision="results")
        assert page["total"] == 1
        assert page["jobs"][0]["company"] == "New Publisher Labs"
        assert await store.get_job(saved.job_id)
    finally:
        await store.close()


async def test_reaudit_clears_old_false_positive_blocks_agency_preserves_override(
    tmp_path,
):
    store = SQLiteStore(tmp_path / "reaudit.sqlite")
    await store.connect()
    try:
        normal = await store.save_job(
            publisher(
                10,
                company="Valon Tech",
                apply_url=None,
                jd_text="ABOUT THE COMPANY\nValon is building mortgage software.",
            )
        )
        agency = await store.save_job(
            publisher(
                11,
                apply_url=None,
                jd_text="About the Company\nOur client is building AI search.",
            )
        )
        for saved, company in [(normal, "Valon Tech"), (agency, "New Publisher")]:
            await store.set_state(
                "spam:job:" + saved.job_id,
                json.dumps(
                    {
                        "job_id": saved.job_id,
                        "company": company,
                        "company_key": company.lower(),
                        "disposition": "review",
                        "category": "possible_publisher_or_template",
                        "rule_version": 1,
                    }
                ),
            )
        await store.spam_audit_page(limit=100)
        assert (await store.spam_review(status="pending"))["total"] == 0
        blocked = await store.spam_review(status="blocked")
        assert [item["job_id"] for item in blocked["items"]] == [agency.job_id]
        assert blocked["items"][0]["rule_version"] == RULE_VERSION
        assert (await store.query_real_jobs_view(decision="results"))["total"] == 1
        assert await store.get_state("spam:company:new publisher") is None
        await store.spam_decide(agency.job_id, scope="job", decision="allow")
        await store.spam_audit_page(limit=100)
        assert (await store.spam_review(status="reviewed"))["total"] == 1
        assert (await store.query_real_jobs_view(decision="results"))["total"] == len(
            [normal, agency]
        )
    finally:
        await store.close()
