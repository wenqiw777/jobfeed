"""Workday suffixes require official API evidence, never blind truncation."""

import httpx
import pytest

from jobfeed.domain.real_job_identity import (
    observed_identifiers,
    strict_content_equivalent,
)
from tests.unit.test_application_routes import BODY, page, posting, resolve

URL = "https://acme.wd5.myworkdayjobs.com/external/job/Boston/Engineer_JR123-1"
API = "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/external/job/Boston/Engineer_JR123-1"


@pytest.mark.parametrize("canonical", ["JR123", "JR999"])
async def test_direct_workday_suffix_is_verified(canonical):
    result, _ = await resolve(
        {
            URL: page(req="JR123-1"),
            API: httpx.Response(
                200,
                json={
                    "jobPostingInfo": {
                        "title": "Software Engineer",
                        "company": "Acme",
                        "jobDescription": BODY,
                        "jobReqId": canonical,
                    }
                },
            ),
        },
        posting(apply_url=URL),
    )
    if canonical == "JR123":
        assert result.status == "resolved"
        assert result.workday_requisition == "JR123"
    else:
        assert result.status != "resolved"


def test_alias_is_scoped_and_never_applied_to_another_tenant():
    aliases = {("acme", "JR123-1"): "JR123"}
    job = posting(url=URL.replace("acme.wd5", "other.wd5"), apply_url=None)
    assert observed_identifiers(job, aliases)[0].native_id == "JR123-1"


def test_identical_description_does_not_merge_different_workday_tenants():
    a = posting(url=URL, apply_url=None)
    b = posting(url=URL.replace("acme.wd5", "other.wd5"), apply_url=None)
    assert not strict_content_equivalent(a, b)
