"""Read-only application documents through the dedicated extension tab pool."""

import asyncio
import json
from typing import Literal
from urllib.parse import urlsplit

from jobfeed.domain.application_route import (
    ApplicationPageReadError,
    RenderedApplicationPage,
)
from jobfeed.domain.models import JobPosting
from jobfeed.services.jobright_bridge import JobrightBridge, JobrightBridgeError

_BROWSER_TIMEOUT = 30.0
_MAX_PAGE_BYTES = 2_000_000
_BLOCKED_CODES = {
    "missing_permission",
    "auth_required",
    "rate_limited",
    "page_timeout",
    "blocked",
}


class ChromeApplicationPageReader:
    """Task-correlated snapshots; canonical identity remains the resolver's job."""

    def __init__(self, bridge: JobrightBridge) -> None:
        self.bridge = bridge

    async def __call__(self, job: JobPosting, url: str) -> RenderedApplicationPage:
        """Read one target without rewriting source facts or discovering extra jobs."""
        target_id = json.dumps(
            [job.platform, job.canonical_id, url], ensure_ascii=False
        )
        try:
            async with asyncio.timeout(_BROWSER_TIMEOUT):
                rows = await self.bridge.run_scan(
                    source="application-resolution",
                    targets=[
                        {
                            "id": target_id,
                            "url": url,
                            "title": job.title,
                            "company": job.company,
                        }
                    ],
                    max_jobs=1,
                    batch_size=1,
                    pacing_s=0,
                    timeout_s=_BROWSER_TIMEOUT,
                    on_progress=lambda _: None,
                )
        except JobrightBridgeError as exc:
            raise ApplicationPageReadError(
                "blocked", "chrome_bridge_unavailable"
            ) from exc
        except TimeoutError as exc:
            raise ApplicationPageReadError("blocked", "chrome_time_budget") from exc
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise ApplicationPageReadError("failed", "chrome_snapshot_missing")
        row = rows[0]
        if row.get("id") != target_id or row.get("source") != "application-resolution":
            raise ApplicationPageReadError(
                "failed", "chrome_snapshot_identity_mismatch"
            )
        if row.get("error"):
            code = str(row.get("error_code") or "page_error")
            status: Literal["blocked", "failed"] = (
                "blocked" if code in _BLOCKED_CODES else "failed"
            )
            raise ApplicationPageReadError(status, f"chrome_{code}")
        html, observed = row.get("html"), row.get("url")
        if (
            not isinstance(html, str)
            or not html.strip()
            or not isinstance(observed, str)
        ):
            raise ApplicationPageReadError("failed", "chrome_snapshot_unreadable")
        try:
            parsed = urlsplit(observed)
        except ValueError as exc:
            raise ApplicationPageReadError(
                "failed", "chrome_snapshot_invalid_url"
            ) from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ApplicationPageReadError("failed", "chrome_snapshot_invalid_url")
        if len(html.encode("utf-8")) > _MAX_PAGE_BYTES:
            raise ApplicationPageReadError("failed", "page_size_limit")
        return RenderedApplicationPage(observed, html)
