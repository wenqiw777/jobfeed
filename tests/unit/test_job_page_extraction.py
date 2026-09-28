import copy
import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from jobfeed.domain.models import LLMResponse
from jobfeed.services.job_page_extraction import JobPageExtractor


def real_linkedin_snapshot():
    root = Path(__file__).resolve().parents[2]
    script = """(async()=>{const fs=require('node:fs');
    const {Window}=await import('./web-ui/node_modules/happy-dom/lib/index.js');
    const capture=require('./extensions/jobright-source/job-page-snapshot.js').capture;
    const doc=new (new Window().DOMParser)().parseFromString(fs.readFileSync(
    'tests/extension/fixtures/linkedin-repost-4188979310.html','utf8'),'text/html');
    console.log(JSON.stringify(capture(doc,'https://www.linkedin.com/jobs/view/4188979310/')));})();"""
    return json.loads(
        subprocess.check_output(["node", "-e", script], cwd=root, text=True)
    )


def real_target():
    return {
        "id": "4188979310",
        "title": "Staff Full Stack Engineer (Frontend) - Time Products",
        "company": "Rippling",
        "url": "https://www.linkedin.com/jobs/view/4188979310/",
    }


async def test_real_linkedin_header_repost_needs_no_model():
    c = client(decision())
    row = {"description": "Existing JD", "page_snapshot": real_linkedin_snapshot()}
    result = await JobPageExtractor(c, model="mock").enrich_row(
        row, target=real_target(), need_description=False
    )
    assert result["isRepost"] is True
    assert "Reposted 1 day ago" in result["repostEvidence"]
    assert result["description"] == "Existing JD"
    c.complete.assert_not_awaited()


async def test_recommendation_repost_does_not_belong_to_target_header():
    s = real_linkedin_snapshot()
    s["blocks"][2]["text"] = "New York, NY · 1 day ago"
    s["blocks"].append(
        {
            "id": 8,
            "text": "Reposted today",
            "kind": "text",
            "path": [1, 99],
            "links": ["https://www.linkedin.com/jobs/view/999/"],
        }
    )
    c = client(decision())
    result = await JobPageExtractor(c, model="mock").enrich_row(
        {"description": "Existing JD", "page_snapshot": s},
        target=real_target(),
        need_description=False,
    )
    assert result.get("isRepost") is not True
    c.complete.assert_not_awaited()


@pytest.mark.parametrize("change", ["company", "redirect", "truncated", "other_job"])
async def test_uncertain_identity_never_uses_repost_fast_path(change):
    s, target = real_linkedin_snapshot(), real_target()
    if change == "company":
        target["company"] = "Other Company"
    elif change == "redirect":
        s["url"] = "https://www.linkedin.com/jobs/view/999/"
    elif change == "truncated":
        s["truncated"] = True
    else:
        s["blocks"][2]["links"] = ["https://www.linkedin.com/jobs/view/999/"]
    c = client(
        {
            "identity_status": "ambiguous",
            "status": "unavailable",
            "identity_block_ids": [],
            "description_block_ids": [],
            "repost_block_ids": [],
        }
    )
    result = await JobPageExtractor(c, model="mock").enrich_row(
        {"description": "Existing JD", "page_snapshot": s},
        target=target,
        need_description=False,
    )
    assert result.get("isRepost") is not True


async def test_scoped_repost_cache_ignores_recommendations_but_not_target_changes():
    s = real_linkedin_snapshot()
    s["blocks"][2]["text"] = "This role was reposted recently"
    state = {}
    store = AsyncMock()
    store.get_state.side_effect = state.get

    async def save(key, value):
        state[key] = value

    store.set_state.side_effect = save
    c = client(
        {
            "identity_status": "matched",
            "status": "unavailable",
            "identity_block_ids": [0, 1],
            "description_block_ids": [],
            "repost_block_ids": [2],
        }
    )
    e = JobPageExtractor(c, model="mock", store=store)
    for suffix in ["one", "two"]:
        changed = copy.deepcopy(s)
        changed["title"] = "Unrelated notification " + suffix
        changed["blocks"][-1]["text"] = "Recommendation " + suffix
        await e.enrich_row(
            {"description": "JD", "page_snapshot": changed},
            target=real_target(),
            need_description=False,
        )
    assert c.complete.await_count == 1
    request = c.complete.call_args.args[0]
    assert "description_block_ids" not in request.messages[0].content
    assert "Recommendation" not in request.messages[1].content
    s["blocks"][2]["text"] = "This role was reposted again recently"
    await e.enrich_row(
        {"description": "JD", "page_snapshot": s},
        target=real_target(),
        need_description=False,
    )
    assert c.complete.await_count == len(["original target", "changed target"])


def snapshot():
    return {
        "url": "https://example.com/jobs/123",
        "title": "Engineer",
        "truncated": False,
        "blocks": [
            {
                "id": 0,
                "text": "Engineer",
                "kind": "heading",
                "path": [1, 2],
                "links": [],
            },
            {
                "id": 1,
                "text": "Reposted 1 day ago",
                "kind": "text",
                "path": [1, 2],
                "links": [],
            },
            {
                "id": 2,
                "text": "Build systems. " * 40,
                "kind": "text",
                "path": [1, 3],
                "links": [],
            },
            {
                "id": 3,
                "text": "Qualifications: Python and distributed systems.",
                "kind": "text",
                "path": [1, 3],
                "links": [],
            },
            {
                "id": 4,
                "text": "Recommended: Nurse — Reposted today",
                "kind": "text",
                "path": [1, 4],
                "links": [],
            },
        ],
    }


def decision(**kwargs):
    return dict(
        identity_status="matched",
        status="complete",
        identity_block_ids=[0],
        description_block_ids=[2, 3],
        repost_block_ids=[1],
        **kwargs,
    )


def client(value):
    c = AsyncMock()
    c.complete.return_value = LLMResponse(
        content=json.dumps(value),
        model="mock",
        input_tokens=10,
        output_tokens=10,
        cost_usd=0,
    )
    return c


async def test_exact_original_text_and_repost_evidence_are_returned():
    c = client(decision())
    e = JobPageExtractor(c, model="mock")
    r = await e.extract(
        {"id": "123", "title": "Engineer", "url": snapshot()["url"]}, snapshot()
    )
    assert (
        r["description"]
        == snapshot()["blocks"][2]["text"] + "\n\n" + snapshot()["blocks"][3]["text"]
    )
    assert r["repostEvidence"] == "Reposted 1 day ago"
    assert "Recommended" not in r["description"]


@pytest.mark.parametrize(
    "patch",
    [
        {"description_block_ids": [999]},
        {"description_block_ids": [2, 2]},
        {"identity_block_ids": []},
        {"repost_block_ids": [3]},
    ],
)
async def test_invalid_evidence_never_becomes_a_success(patch):
    d = decision()
    d.update(patch)
    e = JobPageExtractor(client(d), model="mock")
    with pytest.raises(ValueError):
        await e.extract(
            {"id": "123", "title": "Engineer", "url": snapshot()["url"]}, snapshot()
        )


async def test_partial_and_truncated_pages_are_not_upgraded():
    d = decision()
    d["status"] = "partial"
    e = JobPageExtractor(client(d), model="mock")
    assert not (
        await e.extract(
            {"id": "123", "title": "Engineer", "url": snapshot()["url"]}, snapshot()
        )
    ).get("description")
    s = snapshot()
    s["truncated"] = True
    with pytest.raises(ValueError):
        await e.extract({"id": "123", "title": "Engineer", "url": s["url"]}, s)


async def test_same_snapshot_reuses_persisted_selection_without_model_call():
    state = {}
    store = AsyncMock()
    store.get_state.side_effect = state.get

    async def save(k, v):
        state[k] = v

    store.set_state.side_effect = save
    c = client(decision())
    e = JobPageExtractor(c, model="mock", store=store)
    target = {"id": "123", "title": "Engineer", "url": snapshot()["url"]}
    await e.extract(target, snapshot())
    await e.extract(target, snapshot())
    assert c.complete.await_count == 1
    assert store.record_llm_usage_with_cost.await_count == 1


async def test_repost_only_skips_model_when_page_has_no_repost_word():
    c = client(decision())
    e = JobPageExtractor(c, model="mock")
    s = snapshot()
    s["blocks"] = [b for b in s["blocks"] if "Reposted" not in b["text"]]
    row = {"description": "Existing complete JD", "page_snapshot": s}
    assert (
        await e.enrich_row(row, target={"url": s["url"]}, need_description=False) == row
    )
    c.complete.assert_not_awaited()


async def test_page_failure_does_not_discard_existing_job_or_stop_other_sources():
    c = AsyncMock()
    c.complete.side_effect = OSError("provider unavailable")
    row = {"description": "Existing JD", "page_snapshot": snapshot()}
    r = await JobPageExtractor(c, model="mock").enrich_row(
        row, target={"url": snapshot()["url"]}, need_description=False
    )
    assert r["description"] == "Existing JD"
    assert r["extraction_error"] == "provider unavailable"


async def test_header_only_page_can_prove_repost_without_fabricating_a_jd():
    d = decision()
    d["status"] = "unavailable"
    d["description_block_ids"] = []
    result = await JobPageExtractor(client(d), model="mock").extract(
        {"url": snapshot()["url"]}, snapshot()
    )
    assert result["isRepost"] is True
    assert "description" not in result


async def test_mismatched_job_cannot_supply_jd_or_repost():
    d = decision()
    d["identity_status"] = "mismatch"
    with pytest.raises(ValueError):
        await JobPageExtractor(client(d), model="mock").extract(
            {"url": snapshot()["url"]}, snapshot()
        )


async def test_complete_original_short_description_is_not_rejected_for_length():
    s = snapshot()
    s["blocks"][2]["text"] = "Develop iOS applications."
    d = decision()
    d["description_block_ids"] = [2]
    result = await JobPageExtractor(client(d), model="mock").extract(
        {"url": s["url"]}, s
    )
    assert result["description"] == "Develop iOS applications."


async def test_failed_attempt_revision_is_persisted_and_does_not_unlock_every_scan():
    state = {}
    store = AsyncMock()
    store.get_state.side_effect = state.get

    async def save(k, v):
        state[k] = v

    store.set_state.side_effect = save
    e = JobPageExtractor(client(decision()), model="mock", store=store)
    assert await e.needs_retry_upgrade("existing-job")
    await e.record_retry_upgrade("existing-job")
    assert not await e.needs_retry_upgrade("existing-job")
    assert await e.needs_retry_upgrade("another-job")


async def test_empty_browser_snapshot_is_retryable_without_model_interpretation():
    c = client(decision())
    s = snapshot()
    s["blocks"] = []
    result = await JobPageExtractor(c, model="mock").enrich_row(
        {"page_snapshot": s}, target={"url": s["url"]}
    )
    assert result["error_code"] == "page_timeout"
    c.complete.assert_not_awaited()
