"""LinkedIn Playwright SessionSource adapter."""

from __future__ import annotations

import asyncio
import importlib
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

from jobfeed.config import SourcesLinkedInConfig
from jobfeed.observability import JobfeedLogger
from jobfeed.ports.source import EnrichmentLookup

from ._linkedin_dom import USER_AGENT, VIEWPORTS
from ._linkedin_enrich import LinkedInScanSession
from ._linkedin_lock import LinkedInEnrichLock

Sleeper = Callable[[float], Awaitable[None]]


class LinkedInSource:
    """Authenticated LinkedIn source backed by a Playwright persistent context.

    A single ``session()`` holds ONE cross-process enrich lock and ONE browser
    context for the whole scan — both discovery and enrichment run inside it.
    Holding the lock across discovery (not just enrichment) is the entire point:
    it stops two concurrent processes from driving authenticated LinkedIn
    sessions against the shared profile, the anti-bot / profile-corruption risk
    the lock exists to prevent.
    """

    def __init__(
        self,
        *,
        config: SourcesLinkedInConfig,
        logger: JobfeedLogger,
        sleeper: Sleeper | None = None,
        freshness: EnrichmentLookup | None = None,
    ) -> None:
        """Create a LinkedIn SessionSource.

        Args:
            config: LinkedIn source configuration.
            logger: Structured logger.
            sleeper: Optional async sleep hook for tests.
            freshness: Optional read-only store probe; lets enrichment skip
                postings whose JD is already fresh in the store (cross-run).
        """
        self.config = config
        self.logger = logger
        self.sleeper = sleeper or asyncio.sleep
        self.freshness = freshness
        self.profile_dir = Path(config.profile_dir).expanduser()
        self.lock_path = Path(config.lock_path).expanduser()

    def session(self) -> AbstractAsyncContextManager[LinkedInScanSession]:
        """Open one locked browser session covering discovery and enrichment.

        Returns:
            Async context manager that owns the enrich lock and the persistent
            browser context for the whole session.
        """
        return self._session_manager()

    @asynccontextmanager
    async def _session_manager(self) -> AsyncIterator[LinkedInScanSession]:
        # Acquire BEFORE launching a browser: on contention the lock raises
        # EnrichLocked and no authenticated context is ever opened.
        lock = LinkedInEnrichLock(self.lock_path)
        lock.acquire()
        try:
            async with _persistent_context(
                profile_dir=self.profile_dir,
                headless=self.config.headless,
            ) as context:
                page = await context.new_page()
                yield LinkedInScanSession(
                    page=page,
                    config=self.config,
                    sleeper=self.sleeper,
                    logger=self.logger,
                    freshness=self.freshness,
                )
        finally:
            lock.release()


@asynccontextmanager
async def _persistent_context(
    *,
    profile_dir: Path,
    headless: bool,
) -> AsyncIterator[Any]:
    profile_dir.mkdir(parents=True, exist_ok=True)
    playwright = await _start_playwright()
    context: Any = None
    try:
        context = await playwright.chromium.launch_persistent_context(
            str(profile_dir),
            headless=headless,
            locale="en-US",
            user_agent=USER_AGENT,
            viewport=dict(random.choice(VIEWPORTS)),
        )
        yield context
    finally:
        # ``stop()`` must run even when launch_persistent_context raises (e.g.
        # Chromium not installed); otherwise the node driver subprocess leaks.
        if context is not None:
            await context.close()
        await playwright.stop()


async def _start_playwright() -> Any:
    module = importlib.import_module("playwright.async_api")
    return await module.async_playwright().start()


__all__ = ["LinkedInSource"]
