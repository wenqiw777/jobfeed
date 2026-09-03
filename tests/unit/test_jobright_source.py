from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import respx

from jobfeed.adapters.sources._http import create_http_client
from jobfeed.adapters.sources.jobright import JobrightSource, map_jobright_job
from jobfeed.config import SourcesJobrightConfig
from jobfeed.domain.models import QualityBand
from jobfeed.observability import get_logger
from jobfeed.services.jobright_bridge import JobrightBridge


def _job(job_id: str = "job-123") -> dict[str, object]:
    return {
        "jobResult": {
            "jobId": job_id,
            "jobTitle": "Software Engineer, Early Career",
            "jobLocation": "Detroit, MI",
            "publishTime": "2026-09-01T18:30:00Z",
            "applyLink": "https://example.com/apply/job-123",
            "jobSummary": "Build and operate software used by customers.",
            "coreResponsibilities": ["Develop backend services", "Review code"],
            "qualifications": ["0-2 years of software engineering experience"],
            "detailQualifications": ["Python", "SQL"],
            "jdCoreSkills": ["Python", "APIs"],
        },
        "companyResult": {"companyName": "Example Systems"},
    }


def test_maps_jobright_payload_to_job_posting() -> None:
    discovered_at = datetime(2026, 9, 1, 19, 0, tzinfo=UTC)

    posting = map_jobright_job(_job(), discovered_at=discovered_at)

    assert posting.platform == "jobright"
    assert posting.canonical_id == "job-123"
    assert posting.title == "Software Engineer, Early Career"
    assert posting.company == "Example Systems"
    assert posting.location == "Detroit, MI"
    assert posting.url == "https://example.com/apply/job-123"
    assert posting.posted_at == datetime(2026, 9, 1, 18, 30, tzinfo=UTC)
    assert posting.discovered_at == discovered_at
    assert posting.enrich_source == "jobright_summary"
    assert posting.jd_quality is QualityBand.PARTIAL
    assert posting.jd_text is not None
    assert "0-2 years" in posting.jd_text
    assert "Develop backend services" in posting.jd_text


async def test_bridge_batch_completes_source_and_reports_progress() -> None:
    bridge = JobrightBridge()
    connection = bridge.connect()
    source = JobrightSource(
        config=SourcesJobrightConfig(enabled=True, max_jobs=40, timeout_s=2),
        bridge=bridge,
        logger=get_logger(),
    )
    progress: list[tuple[int, int | None, str | None]] = []

    fetch = asyncio.create_task(
        source.fetch_jobs_with_progress(
            {},
            lambda update: progress.append(
                (update.processed, update.total, update.current_job_id)
            ),
        )
    )
    command = await connection.next_command()
    task_id = str(command["task_id"])
    assert command == {
        "type": "start_scan",
        "task_id": task_id,
        "max_jobs": 40,
        "batch_size": 20,
        "pacing_ms": 1000,
    }

    await bridge.receive(
        {
            "type": "batch",
            "task_id": task_id,
            "jobs": [_job("job-123"), _job("job-456")],
        }
    )
    await bridge.receive({"type": "complete", "task_id": task_id})

    postings = await fetch
    assert [posting.canonical_id for posting in postings] == ["job-123", "job-456"]
    assert progress == [(2, 40, "job-456")]


@respx.mock
async def test_source_marks_successful_official_ats_fetch_as_full() -> None:
    bridge = JobrightBridge()
    connection = bridge.connect()
    payload = _job()
    job = payload["jobResult"]
    assert isinstance(job, dict)
    job["originalUrl"] = "https://job-boards.greenhouse.io/acme/jobs/777"
    official_jd = "Official job description with responsibilities. " * 40
    respx.get(
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs/777?content=true"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 777,
                "title": "Software Engineer, Early Career",
                "absolute_url": job["originalUrl"],
                "content": f"<p>{official_jd}</p>",
                "location": {"name": "Detroit, MI"},
            },
        )
    )
    async with create_http_client() as client:
        source = JobrightSource(
            config=SourcesJobrightConfig(enabled=True, max_jobs=20, timeout_s=2),
            bridge=bridge,
            logger=get_logger(),
            client=client,
        )
        fetch = asyncio.create_task(source.fetch_jobs({}))
        command = await connection.next_command()
        await bridge.receive(
            {
                "type": "batch",
                "task_id": command["task_id"],
                "jobs": [payload],
            }
        )
        await bridge.receive({"type": "complete", "task_id": command["task_id"]})
        posting = (await fetch)[0]

    assert posting.jd_text is not None
    assert "Official job description" in posting.jd_text
    assert posting.url == job["originalUrl"]
    assert posting.jd_quality is QualityBand.FULL
    assert posting.enrich_source == "jobright_official_greenhouse"
