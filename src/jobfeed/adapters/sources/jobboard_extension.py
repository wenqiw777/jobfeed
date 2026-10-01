"""Signed-in LinkedIn and Handshake API batches from the existing extension."""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import structlog

from jobfeed.adapters.sources._http import html_to_text
from jobfeed.adapters.sources._linkedin_company_filter import blocked_linkedin_company
from jobfeed.adapters.sources.jobright import _datetime
from jobfeed.config_sources import SourcesBoardExtensionConfig
from jobfeed.domain.models import JobPosting, QualityBand
from jobfeed.domain.quality import assess_quality
from jobfeed.ports.source import (
    PartialSourceFetchError,
    SourceFetchProgress,
    SourceFetchProgressCallback,
)
from jobfeed.services.job_page_extraction import JobPageExtractor
from jobfeed.services.jobright_bridge import JobrightBridge, JobrightBridgeError
from jobfeed.services.pipeline_context import POSTING


class JobboardExtensionSource:
    """Fetch platform descriptions without employer-page enrichment."""

    def __init__(
        self,
        *,
        source: str,
        config: SourcesBoardExtensionConfig,
        bridge: JobrightBridge,
        store: Any = None,
        page_extractor: JobPageExtractor | None = None,
    ) -> None:
        self.page_extractor = page_extractor
        self.source, self.config, self.bridge, self.store = (
            source,
            config,
            bridge,
            store,
        )

    async def fetch_jobs(self, config: dict[str, object]) -> list[JobPosting]:
        """Collect signed-in board postings.

        Args:
            config: Source-specific request options.

        Returns:
            Collected postings with available descriptions.
        """
        return await self.fetch_jobs_with_progress(config, lambda _: None)

    async def fetch_jobs_with_progress(
        self,
        config: dict[str, object],
        on_progress: SourceFetchProgressCallback,
    ) -> list[JobPosting]:
        """Collect board postings and publish scan progress.

        Args:
            config: Source request options.
            on_progress: Callback receiving collection progress.

        Returns:
            Collected postings deduplicated by source identity.
        """
        return await self._fetch_jobs(config, on_progress)

    async def fetch_jobs_streaming(
        self,
        config: dict[str, object],
        on_progress: SourceFetchProgressCallback,
        on_batch: Callable[[list[JobPosting]], Awaitable[None]],
    ) -> list[JobPosting]:
        """Publish interpreted batches while the extension continues discovery.

        Args:
            config: Source request options.
            on_progress: Callback receiving collection progress.
            on_batch: Awaited callback receiving each interpreted source batch.

        Returns:
            All collected postings, including those delivered through on_batch.
        """
        return await self._fetch_jobs(config, on_progress, on_batch)

    async def _fetch_jobs(  # noqa: C901 - sequential fetch and recovery phases
        self,
        config: dict[str, object],  # noqa: ARG002
        on_progress: SourceFetchProgressCallback,
        on_batch: Callable[[list[JobPosting]], Awaitable[None]] | None = None,
    ) -> list[JobPosting]:
        """Collect board postings and optionally deliver committed browser batches.

        Complexity: O(Q * J), for queries Q and returned jobs J.

        Args:
            config: Source-specific request options.
            on_progress: Callback receiving scan progress updates.

        Returns:
            Collected postings, deduplicated by source identity.

        Raises:
            PartialSourceFetchError: If a source fails after producing partial results.
        """

        def blocked(company: str | None) -> bool:
            return self.source == "linkedin" and blocked_linkedin_company(company)

        postings: dict[str, JobPosting] = {}
        seen_discovery: set[str] = set()

        async def gate(rows: list[dict[str, Any]]) -> dict[str, Any]:
            observations = {str(row["id"]): _repost_fields(row) for row in rows}
            companies = {str(row["id"]): row.get("employer") for row in rows}
            ids = list(dict.fromkeys(str(row["id"]) for row in rows))
            skip = [
                key
                for key in ids
                if key in seen_discovery
                or key in postings
                or blocked((companies.get(key) or {}).get("name"))
            ]
            fresh = [key for key in ids if key not in skip]
            stored = (
                await self.store.get_jobs_by_canonical_ids(
                    platform=self.source, canonical_ids=fresh
                )
                if self.store is not None and fresh
                else {}
            )
            reused = []
            for key in fresh:
                job = stored.get(key)
                if job and blocked(job.company):
                    skip.append(key)
                    continue
                if (
                    job
                    and job.jd_quality in {QualityBand.GOOD, QualityBand.FULL}
                    and (job.jd_text or "").strip()
                ):
                    skip.append(key)
                    reused.append(
                        {
                            "source": self.source,
                            "id": key,
                            "_stored_posting": POSTING.dump_python(
                                replace(job, **observations[key]), mode="json"
                            ),
                            "employer": companies.get(key),
                        }
                    )
            seen_discovery.update(ids)
            return {"skip_ids": skip, "reused_jobs": reused}

        filters = None
        queries = self.config.queries
        search_urls = self.config.search_urls if self.source == "linkedin" else []
        if search_urls:
            queries = [
                parse_qs(urlparse(url).query)["keywords"][0] for url in search_urls
            ]
        if self.source == "handshake" and self.config.search_url:
            params = parse_qs(urlparse(self.config.search_url).query)
            filters = {
                target: [part for item in params[key] for part in item.split(",")]
                for key, target in (
                    ("jobRoleGroups", "jobRoleGroupIds"),
                    ("employmentTypes", "employmentTypeIds"),
                    ("jobType", "jobTypeIds"),
                )
            }
            queries = [""]
        for index, query in enumerate(queries):
            phases = ["newest"]
            key = f"jobboard:handshake:bootstrap:{query}"
            if (
                self.source == "handshake"
                and filters is None
                and not await self._bootstrap_persisted(key)
            ):
                phases.insert(0, "relevance")
            for sort in phases:
                bootstrap = self.source == "handshake" and sort == "relevance"
                budget = (
                    100000
                    if bootstrap
                    else self.config.max_jobs
                    if self.source == "linkedin"
                    else self.config.max_jobs // len(queries)
                    + (index < self.config.max_jobs % len(queries))
                )
                if budget == 0:
                    continue
                accepted = len(postings)

                def progress(
                    update: SourceFetchProgress,
                    accepted: int = accepted,
                    bootstrap: bool = bootstrap,
                    budget: int = budget,
                ) -> None:
                    on_progress(
                        SourceFetchProgress(
                            processed=accepted + update.processed,
                            total=(
                                None
                                if bootstrap or update.total is None
                                else accepted + update.total
                            )
                            if update.phase != "fetching"
                            else (None if bootstrap else accepted + budget),
                            current_job_id=update.current_job_id,
                            phase=update.phase,
                        )
                    )

                try:

                    async def streamed(
                        rows: list[dict[str, Any]], query: str = query
                    ) -> None:
                        """Map and deliver new rows.

                        Complexity: O(J) for J rows in the received batch.
                        """
                        pending = [
                            row for row in rows if str(row.get("id")) not in postings
                        ]
                        if self.page_extractor is not None:
                            pending = await self._interpret_rows(
                                pending, query, on_progress
                            )
                        jobs = []
                        for row in pending:
                            job = map_board_job(row, discovered_at=datetime.now(UTC))
                            if not blocked(job.company):
                                postings[job.canonical_id] = job
                                jobs.append(job)
                        if jobs and on_batch is not None:
                            await on_batch(jobs)

                    rows = await self.bridge.run_scan(
                        source=self.source,
                        query=query,
                        sort=sort,
                        **({"search_url": search_urls[index]} if search_urls else {}),
                        **({"filters": filters} if filters is not None else {}),
                        max_jobs=budget,
                        batch_size=self.config.batch_size,
                        pacing_s=self.config.pacing_s,
                        timeout_s=self.config.timeout_s,
                        on_progress=progress,
                        on_discovery=gate,
                        **({"on_batch": streamed} if on_batch is not None else {}),
                    )
                except JobrightBridgeError as exc:
                    for row in exc.partial_jobs:
                        if str(row.get("id")) in postings:
                            continue
                        job = map_board_job(row, discovered_at=datetime.now(UTC))
                        if not blocked(job.company):
                            postings[job.canonical_id] = job
                    raise PartialSourceFetchError(
                        str(exc), list(postings.values()), warning=exc.warning
                    ) from exc
                if on_batch is not None:
                    await streamed(rows)
                elif self.page_extractor is not None:
                    rows = await self._interpret_rows(rows, query, on_progress)
                for row in [] if on_batch is not None else rows:
                    job = map_board_job(row, discovered_at=datetime.now(UTC))
                    if not blocked(job.company):
                        postings[job.canonical_id] = job
                if bootstrap:
                    if len(rows) >= budget:
                        raise PartialSourceFetchError(
                            "Handshake relevance scan reached its observation ceiling "
                            "before exhaustion",
                            list(postings.values()),
                        )
                    if self.store is not None:
                        # The next scan verifies these IDs actually persisted before
                        # trusting this marker, including recovery after process exit.
                        await self.store.set_state(
                            key, json.dumps([str(row["id"]) for row in rows])
                        )
                on_progress(
                    SourceFetchProgress(processed=len(postings), phase="fetched")
                )
        return list(postings.values())

    async def _interpret_rows(
        self,
        rows: list[dict[str, Any]],
        query: str,
        on_progress: SourceFetchProgressCallback,
    ) -> list[dict[str, Any]]:
        """Report the bounded model stage separately from browser requests."""
        extractor = self.page_extractor
        assert extractor is not None
        prepared = []
        for row in rows:
            target = {
                "id": row.get("id"),
                "title": row.get("title"),
                "company": (row.get("employer") or {}).get("name"),
                "url": row.get("url"),
            }
            need_description = not bool(
                row.get("description") or row.get("_stored_posting")
            )
            candidate = JobPageExtractor.needs_interpretation(
                row, target, need_description=need_description
            )
            prepared.append((row, target, need_description, candidate))
        total = sum(item[3] for item in prepared)
        processed = 0
        started = time.monotonic()
        logger = structlog.get_logger(__name__)
        logger.info(
            "scan_interpreting_started",
            source=self.source,
            query=query,
            candidate_count=total,
        )
        if total:
            on_progress(
                SourceFetchProgress(phase="interpreting", processed=0, total=total)
            )

        async def interpret(
            item: tuple[dict[str, Any], dict[str, Any], bool, bool],
        ) -> dict[str, Any]:
            nonlocal processed
            row, target, need_description, candidate = item
            result = await extractor.enrich_row(
                row, target=target, need_description=need_description
            )
            if candidate:
                processed += 1
                on_progress(
                    SourceFetchProgress(
                        phase="interpreting",
                        processed=processed,
                        total=total,
                        current_job_id=str(row["id"]),
                    )
                )
            return result

        try:
            return await asyncio.gather(*(interpret(item) for item in prepared))
        finally:
            logger.info(
                "scan_interpreting_finished",
                source=self.source,
                query=query,
                candidate_count=total,
                processed=processed,
                elapsed_ms=round((time.monotonic() - started) * 1000),
            )

    async def _bootstrap_persisted(self, key: str) -> bool:
        if self.store is None:
            return False
        value = await self.store.get_state(key)
        if value is None:
            return False
        ids = json.loads(value)
        if not ids:
            return False
        for job_id in ids:
            stored = await self.store.get_enrichment(
                platform="handshake", canonical_id=job_id
            )
            if stored is None or not stored.jd_text:
                return False
        return True


def map_board_job(raw: dict[str, Any], *, discovered_at: datetime) -> JobPosting:
    """Preserve the platform's complete text and native job identifier.

    Args:
        raw: Source response row.
        discovered_at: Discovered at supplied by the caller.

    Returns:
        Normalized source posting.

    Raises:
        ValueError: If the source row lacks required identity fields.
    """
    source = raw.get("source")
    if source not in {"linkedin", "handshake"}:
        raise ValueError("Unsupported board source")
    if "_stored_posting" in raw:
        job = POSTING.validate_python(raw["_stored_posting"])
        if job.platform != source or job.canonical_id != str(raw.get("id")):
            raise ValueError("Stored posting identity mismatch")
        employer = raw.get("employer")
        name = employer.get("name") if isinstance(employer, dict) else None
        if (not job.company.strip() or job.company.strip().lower() == "unknown") and (
            isinstance(name, str) and name.strip() and name.strip().lower() != "unknown"
        ):
            job = replace(job, company=name.strip())
        return replace(job, discovered_at=discovered_at, **_repost_fields(raw))
    for key in ("id", "title", "url"):
        if not raw.get(key):
            raise ValueError(f"Missing board job {key}")
    description = raw.get("description") or ""
    if raw.get("descriptionFormat") == "html":
        description = html_to_text(description)
    locations = raw.get("locations")
    location = (
        "; ".join(
            str(x.get("displayName", "")) for x in locations if isinstance(x, dict)
        )
        if isinstance(locations, list)
        else locations
    )
    employer = raw.get("employer") or {}
    return JobPosting(
        platform=source,
        canonical_id=str(raw["id"]),
        url=raw["url"],
        title=raw["title"],
        company=employer.get("name") or "Unknown",
        apply_url=_observed_apply_url(raw.get("applyUrl")),
        location=location or "Unknown",
        discovered_at=discovered_at,
        jd_text=description or None,
        jd_quality=assess_quality(description),
        posted_at=_datetime(raw.get("postedAt")),
        enriched_at=discovered_at if description else None,
        enrich_source=f"{source}_extension",
        enrich_error=raw.get("extraction_error"),
        enrich_error_code=raw.get("error_code")
        if raw.get("extraction_error")
        else None,
        **_repost_fields(raw),
    )


def _observed_apply_url(value: Any) -> str | None:
    """Preserve only an observed external HTTP(S) destination."""
    if not isinstance(value, str):
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or (host == "linkedin.com" or host.endswith(".linkedin.com"))
    ):
        return None
    return value


def _repost_fields(raw: dict[str, Any]) -> dict[str, Any]:
    """Require an affirmative source observation, not a date inference."""
    evidence = raw.get("repostEvidence")
    observed = _datetime(raw.get("repostObservedAt"))
    if (
        raw.get("isRepost") is not True
        or not isinstance(evidence, str)
        or not evidence.strip()
        or observed is None
    ):
        return {}
    return {
        "is_repost": True,
        "repost_evidence": evidence.strip()[:200],
        "repost_observed_at": observed,
    }
