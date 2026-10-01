"""Application link evidence is committed with source identity atomically."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.models import PipelineRun
from jobfeed.domain.normalize import normalize, normalize_company
from jobfeed.domain.real_job_identity import normalized_jd_body
from tests.support.sqlite_jobs_evaluations import FIXED_NOW, make_job

_APPLY = "https://careers.example.test/jobs/engineer"
_ATS = "https://boards.greenhouse.io/example/jobs/123456"
_SECOND_SOURCE = 2


async def _source(store, canonical_id, *, apply_url=_APPLY, jd_text="Source JD"):
    return await store.save_job(
        replace(make_job(canonical_id, jd_text=jd_text), apply_url=apply_url)
    )


async def test_concurrent_batches_join_one_parent_and_preserve_source(contract_store):
    store = contract_store
    first = await _source(store, "batch-one")
    second = await _source(store, "batch-two", jd_text="Second source JD")
    third = await _source(store, "batch-three", jd_text="Third source JD")
    sources = (first, second, third)
    accepted = await asyncio.gather(
        *[
            store.record_application_identity(
                job_id=saved.job_id,
                expected_apply_url=_APPLY,
                ats_url=_ATS,
                state_key=f"application:{saved.job_id}",
                state_value="resolved",
            )
            for saved in sources
        ]
    )
    assert accepted == [True, True, True]
    assert (
        len(await store.resolve_real_job_ids([saved.job_id for saved in sources])) == 1
    )
    loaded = await store.get_job(first.job_id)
    assert loaded.jd_text == "Source JD"
    assert loaded.url == "https://example.test/batch-one"
    assert loaded.apply_url == _APPLY
    assert loaded.discovered_at == FIXED_NOW
    assert await store.get_state(f"application:{first.job_id}") == "resolved"
    if isinstance(store, SQLiteStore):
        async with store._lifecycle.connection() as conn:
            cursor = await conn.execute(
                "SELECT observed_url FROM real_job_identifiers "
                "WHERE provider='greenhouse' AND native_id='123456'"
            )
            assert (await cursor.fetchone())[0] == _ATS
            await cursor.close()


async def test_changed_apply_or_deleted_source_rejects_stale_result(contract_store):
    store = contract_store
    first = await _source(store, "stale", apply_url="https://new.example.test/jobs/1")
    for job_id in (first.job_id, "999999"):
        assert not await store.record_application_identity(
            job_id=job_id,
            expected_apply_url=_APPLY,
            ats_url=_ATS,
            state_key="stale-result",
            state_value="resolved",
        )
    assert await store.get_state("stale-result") is None


async def test_negative_outcome_commits_without_identity_evidence(contract_store):
    store = contract_store
    saved = await _source(store, "unresolved")
    assert await store.record_application_identity(
        job_id=saved.job_id,
        expected_apply_url=_APPLY,
        ats_url=None,
        state_key="unresolved-result",
        state_value="timeout",
    )
    assert await store.get_state("unresolved-result") == "timeout"
    assert (await store.get_job(saved.job_id)).apply_url == _APPLY


async def test_evidence_and_outcome_roll_back_together(contract_store, monkeypatch):
    store = contract_store
    saved = await _source(store, "rollback")

    async def fail(*_args, **_kwargs):
        raise RuntimeError("injected projection failure")

    module = (
        "_sqlite_application_identity.sync_sqlite_real_job_input"
        if isinstance(store, SQLiteStore)
        else "postgres.sync_postgres_real_job_input"
    )
    monkeypatch.setattr(f"jobfeed.adapters.store.{module}", fail)
    with pytest.raises(RuntimeError, match="injected projection failure"):
        await store.record_application_identity(
            job_id=saved.job_id,
            expected_apply_url=_APPLY,
            ats_url=_ATS,
            state_key="rolled-back",
            state_value="resolved",
        )
    assert await store.get_state("rolled-back") is None
    if isinstance(store, SQLiteStore):
        async with store._lifecycle.connection() as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM real_job_identifiers WHERE provider='greenhouse'"
            )
            assert (await cursor.fetchone())[0] == 0
            await cursor.close()
    else:
        async with store._get_pool().acquire() as conn:
            assert (
                await conn.fetchval(
                    "SELECT COUNT(*) FROM real_job_identifiers "
                    "WHERE provider='greenhouse'"
                )
                == 0
            )


async def test_enrichment_persists_observed_apply_url_and_missing_preserves_it(
    contract_store,
):
    store = contract_store
    saved = await _source(store, "enriched", apply_url=None, jd_text=None)
    for apply_url in (_APPLY, None):
        await store.record_enrichment(
            job_id=saved.job_id,
            jd_text="Full body",
            jd_quality="full",
            enriched_at=FIXED_NOW,
            enrich_source="linkedin_guest",
            apply_url=apply_url,
        )
    loaded = await store.get_job(saved.job_id)
    assert loaded.apply_url == _APPLY
    assert loaded.jd_text == "Full body"
    snapshot = await store.get_enrichment(platform="mock", canonical_id="enriched")
    assert snapshot.apply_url == _APPLY


async def test_stale_scan_fence_cannot_commit_evidence_or_receipt(contract_store):
    store = contract_store
    saved = await _source(store, "fenced")
    run_id = "019b1251-a26e-7466-a744-28b881a19cd5"
    owner_id = "019b1251-a26e-7466-a744-28b881a19cd6"
    generation = 999
    if isinstance(store, SQLiteStore):
        now = datetime.now(UTC)
        generation = await store.start_run_with_lease(
            PipelineRun(run_id=run_id, source="mock", started_at=now),
            kind="scan",
            owner_id=owner_id,
            now=now,
        )
        assert generation is not None
    with pytest.raises(RuntimeError, match="Scan write lease lost"):
        await store.record_application_identity(
            job_id=saved.job_id,
            expected_apply_url=_APPLY,
            ats_url=_ATS,
            state_key="stale-fence",
            state_value="resolved",
            run_id=run_id,
            owner_id=owner_id,
            generation=generation + 1,
        )
    assert await store.get_state("stale-fence") is None
    assert (await store.get_job(saved.job_id)).apply_url == _APPLY

    if isinstance(store, SQLiteStore):
        assert await store.record_application_identity(
            job_id=saved.job_id,
            expected_apply_url=_APPLY,
            ats_url=_ATS,
            state_key="live-fence",
            state_value="resolved",
            run_id=run_id,
            owner_id=owner_id,
            generation=generation,
        )
        assert await store.get_state("live-fence") == "resolved"


async def test_resolution_preserves_enrichment_updated_during_browser_visit(
    contract_store,
):
    store = contract_store
    saved = await _source(store, "fresh-jd")
    await store.record_enrichment(
        job_id=saved.job_id,
        jd_text="New JD saved while resolution was loading",
        jd_quality="full",
        enriched_at=FIXED_NOW,
        enrich_source="linkedin_guest",
        apply_url=_APPLY,
    )
    assert await store.record_application_identity(
        job_id=saved.job_id,
        expected_apply_url=_APPLY,
        ats_url=_ATS,
        state_key="fresh-jd-resolution",
        state_value="resolved",
    )
    loaded = await store.get_job(saved.job_id)
    assert loaded.jd_text == "New JD saved while resolution was loading"
    assert loaded.enriched_at == FIXED_NOW
    assert loaded.enrich_source == "linkedin_guest"


async def test_two_linkedin_wrappers_reuse_persisted_ats_evidence(contract_store):
    store = contract_store
    saved_sources = []
    for index in (1, 2):
        posting = replace(
            make_job(str(index), jd_text=f"Distinct source body {index}"),
            platform="linkedin",
            url=f"https://www.linkedin.com/jobs/view/{index}/",
            apply_url=f"https://careers.example.test/jobs/wrapper-{index}",
        )
        saved = await store.save_job(posting)
        saved_sources.append(saved.job_id)
        assert await store.record_application_identity(
            job_id=saved.job_id,
            expected_apply_url=posting.apply_url,
            ats_url=_ATS,
            state_key=f"resolved-linkedin:{index}",
            state_value="resolved",
        )
    assert len(await store.resolve_real_job_ids(saved_sources)) == 1


@pytest.mark.parametrize("changed_field", ["company", "title", "jd_text"])
async def test_positive_evidence_rejects_source_facts_changed_during_resolution(
    contract_store,
    changed_field,
):
    store = contract_store
    posting = replace(make_job("facts-changed"), apply_url=_APPLY)
    saved = await store.save_job(posting)
    verification_facts = {
        "company": normalize_company(posting.company),
        "title": normalize(posting.title),
        "jd_body": normalized_jd_body(posting.jd_text),
    }
    await store.save_job(replace(posting, **{changed_field: "Different current value"}))
    assert not await store.record_application_identity(
        job_id=saved.job_id,
        expected_apply_url=_APPLY,
        ats_url=_ATS,
        state_key="stale-facts",
        state_value=json.dumps({"verification_facts": verification_facts}),
    )
    assert await store.get_state("stale-facts") is None
    assert getattr(await store.get_job(saved.job_id), changed_field) == (
        "Different current value"
    )


@pytest.mark.parametrize("conflict_kind", ["requisition", "status"])
async def test_durable_wrapper_evidence_preserves_conflict_review(
    contract_store, conflict_kind
):
    store = contract_store
    workday_one = "https://acme.wd5.myworkdayjobs.com/external/job/US/Engineer_R1"
    workday_two = "https://acme.wd5.myworkdayjobs.com/external/job/US/Engineer_R2"
    saved_sources = []
    for index in (1, 2):
        apply_url = (
            workday_two
            if index == _SECOND_SOURCE and conflict_kind == "requisition"
            else f"https://careers.example.test/jobs/wrapper-{index}"
        )
        posting = replace(
            make_job(str(index), jd_text=f"Distinct source body {index}"),
            platform="linkedin",
            url=f"https://www.linkedin.com/jobs/view/{index}/",
            apply_url=apply_url,
        )
        saved = await store.save_job(posting)
        saved_sources.append(saved.job_id)
        if index == _SECOND_SOURCE and conflict_kind == "status":
            if isinstance(store, SQLiteStore):
                async with store._lifecycle.connection() as conn:
                    for source_id, status in zip(
                        saved_sources, ("applied", "ignored"), strict=True
                    ):
                        await conn.execute(
                            "UPDATE job_status SET status=? WHERE job_id=?",
                            (status, int(source_id)),
                        )
            else:
                async with store._get_pool().acquire() as conn:
                    for source_id, status in zip(
                        saved_sources, ("applied", "ignored"), strict=True
                    ):
                        await conn.execute(
                            "UPDATE job_status SET status=$1 WHERE job_id=$2",
                            status,
                            int(source_id),
                        )
        assert await store.record_application_identity(
            job_id=saved.job_id,
            expected_apply_url=apply_url,
            ats_url=workday_one,
            state_key=f"conflicting-linkedin:{index}",
            state_value="resolved",
        )
    expected_parents = 2
    assert len(await store.resolve_real_job_ids(saved_sources)) == expected_parents
    expected_reason = (
        "conflicting_requisition_ids"
        if conflict_kind == "requisition"
        else "explicit_status_conflict"
    )
    if isinstance(store, SQLiteStore):
        async with store._lifecycle.connection() as conn:
            cursor = await conn.execute("SELECT reason FROM real_job_review_cases")
            assert expected_reason in [row[0] for row in await cursor.fetchall()]
            await cursor.close()
    else:
        async with store._get_pool().acquire() as conn:
            reviews = await conn.fetch("SELECT reason FROM real_job_review_cases")
            assert expected_reason in [row["reason"] for row in reviews]
