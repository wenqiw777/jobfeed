"""Observed requisition IDs are scoped to their vendor tenant."""

from datetime import UTC, datetime

from jobfeed.domain.external_identity import observed_identifier
from jobfeed.domain.models import JobPosting
from jobfeed.domain.real_job_identity import strict_content_equivalent


def _posting(jd: str, *, location: str = "San Diego, CA") -> JobPosting:
    return JobPosting(
        platform="linkedin",
        canonical_id="one",
        url="https://example.test/one",
        title="Backend Software Engineer",
        company="Qualcomm",
        location=location,
        discovered_at=datetime.now(UTC),
        jd_text=jd,
    )


def test_observed_ats_ids_keep_tenant_scope() -> None:
    southwest = observed_identifier(
        "https://careers.southwestair.com/us/en/job/"
        "SOUTUSR202672886ENUSEXTERNAL/Associate-Software-Engineer"
    )
    qualcomm = observed_identifier(
        "https://qualcomm.eightfold.ai/careers?pid=446721162271"
    )
    assert southwest is not None
    assert (southwest.provider, southwest.scope, southwest.native_id) == (
        "phenom",
        "careers.southwestair.com",
        "SOUTUSR202672886ENUSEXTERNAL",
    )
    assert qualcomm is not None
    assert (qualcomm.provider, qualcomm.scope, qualcomm.native_id) == (
        "eightfold",
        "qualcomm.eightfold.ai",
        "446721162271",
    )


def test_ats_homepage_has_no_requisition_identity() -> None:
    assert observed_identifier("https://qualcomm.eightfold.ai/careers") is None
    assert observed_identifier("https://careers.southwestair.com/us/en/") is None


def test_strict_content_accepts_formatting_and_extra_role_heading() -> None:
    body = "Requirements: Python, distributed systems, and API ownership. " * 9
    assert strict_content_equivalent(
        _posting("Role\n" + body), _posting(body.replace(",", " , "))
    )


def test_strict_content_rejects_distinct_team_or_location() -> None:
    body = "Requirements: Python, distributed systems, and API ownership. " * 9
    other = body.replace("distributed systems", "mobile devices")
    assert not strict_content_equivalent(_posting(body), _posting(other))
    assert not strict_content_equivalent(
        _posting(body, location="San Diego, CA"),
        _posting(body, location="Dallas, TX"),
    )
