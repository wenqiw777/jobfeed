"""Exact identity must tolerate URL aliases without merging requisitions."""

from datetime import UTC, datetime

import pytest

from jobfeed.domain.dedupe import cluster_twins
from jobfeed.domain.external_identity import external_identity
from jobfeed.domain.models import JobPosting


@pytest.mark.parametrize(
    ("url", "identity"),
    [
        (
            "https://www.hudsonrivertrading.com/careers/job/?gh_jid=8052122&utm_source=x",
            "greenhouse:8052122",
        ),
        (
            "https://boards.greenhouse.io/embed/job_app?for=hrt&token=8052122",
            "greenhouse:8052122",
        ),
        ("https://job-boards.greenhouse.io/hrt/jobs/8052122", "greenhouse:8052122"),
        ("https://www.linkedin.com/jobs/view/123456/?trackingId=x", "linkedin:123456"),
        (
            "https://jobs.ashbyhq.com/acme/12345678-1234-1234-1234-123456789abc",
            "ashby:12345678-1234-1234-1234-123456789abc",
        ),
        (
            "https://jobs.lever.co/acme/12345678-1234-1234-1234-123456789abc/apply",
            "lever:12345678-1234-1234-1234-123456789abc",
        ),
        (
            "https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/USA/Engineer_R123",
            "workday:acme:R123",
        ),
        ("https://jobright.ai/jobs/info/abc123", "jobright:abc123"),
        ("https://umich.joinhandshake.com/jobs/123", "handshake:123"),
        ("https://example.com/jobs/123", None),
        ("https://greenhouse.io.evil.test/jobs/123", None),
        ("https://example.com/?token=123", None),
    ],
)
def test_native_identity(url, identity):
    assert external_identity(url) == identity


def test_exact_twins_with_different_titles_and_distinct_requisitions():
    jobs = [
        JobPosting(
            platform="speedyapply",
            canonical_id=str(i),
            company="HRT",
            title=title,
            location="NY",
            discovered_at=datetime.now(UTC),
            url=url,
        )
        for i, (title, url) in enumerate(
            [
                ("Engineer", "https://hrt.com/job?gh_jid=1"),
                ("Software Engineer", "https://boards.greenhouse.io/hrt/jobs/1"),
                ("Engineer", "https://hrt.com/job?gh_jid=2"),
            ]
        )
    ]
    assert [
        {job.canonical_id for job in cluster.members} for cluster in cluster_twins(jobs)
    ] == [{"0", "1"}, {"2"}]
