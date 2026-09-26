import httpx
import pytest

from jobfeed.adapters.sources._speedyapply_observed import (
    enrich_observed,
    greenhouse_target,
)


def test_application_url_identifies_same_official_job():
    assert greenhouse_target(
        "https://boards.greenhouse.io/embed/job_app?token=12345",
        "https://job-boards.greenhouse.io/embed/job_app?for=example&token=12345",
    ) == ("example", "12345")


def test_board_bootstrap_uses_requested_job_id():
    assert greenhouse_target(
        "https://company.example/careers?gh_jid=12345",
        "https://boards.greenhouse.io/embed/job_board/js?for=example",
    ) == ("example", "12345")


def test_conflicting_id_and_untrusted_host_are_rejected():
    assert (
        greenhouse_target(
            "https://company.example/?gh_jid=12345",
            "https://boards.greenhouse.io/embed/job_app?for=example&token=99999",
        )
        is None
    )
    assert (
        greenhouse_target(
            "https://company.example/?gh_jid=12345",
            "https://boards.greenhouse.io.evil.example/?for=example&token=12345",
        )
        is None
    )


@pytest.mark.parametrize(("returned_id", "accepted"), [(12345, True), (99999, False)])
async def test_observed_api_returns_original_body_and_checks_response_id(
    returned_id, accepted
):
    def respond(_request):
        return httpx.Response(
            200,
            json={
                "id": returned_id,
                "title": "Engineer",
                "absolute_url": "https://job-boards.greenhouse.io/example/jobs/12345",
                "content": "<p>Build and maintain reliable systems.</p>",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        target = {"id": "source-id", "url": "https://company.example/?gh_jid=12345"}
        results = {
            "source-id": {
                "ats_urls": [
                    "https://boards.greenhouse.io/embed/job_board/js?for=example"
                ]
            }
        }
        await enrich_observed(client, [target], results)
    if accepted:
        assert (
            results["source-id"]["description"]
            == "Build and maintain reliable systems."
        )
    else:
        assert "description" not in results["source-id"]
