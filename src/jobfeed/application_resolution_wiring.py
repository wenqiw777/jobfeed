"""Wire public-page resolution and independent Chrome extension readers."""

import asyncio
import time
from urllib.parse import urlsplit

from jobfeed.adapters.sources.application_route_browser import (
    ChromeApplicationPageReader,
)
from jobfeed.adapters.sources.application_routes import ApplicationRouteResolver
from jobfeed.services.jobright_bridge import JobrightBridge


class ApplicationRequestPacing:
    """Space website requests by host without holding a database write lock."""

    def __init__(self) -> None:
        self._slots: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    async def __call__(self, url: str) -> None:
        """Wait for a one-second same-host request start interval."""
        host = (urlsplit(url).hostname or "").lower()
        async with self._slots.setdefault(host, asyncio.Lock()):
            remaining = 1 - (time.monotonic() - self._last.get(host, 0))
            if remaining > 0:
                await asyncio.sleep(remaining)
            self._last[host] = time.monotonic()


def build_application_resolver(
    bridge: JobrightBridge | None = None,
) -> ApplicationRouteResolver:
    """Use the existing extension channel only when a rendered page is needed.

    Args:
        bridge: Existing Chrome extension bridge, if available.

    Returns:
        A public-page resolver with host pacing and optional browser reading.
    """
    return ApplicationRouteResolver(
        chrome_reader=ChromeApplicationPageReader(bridge) if bridge else None,
        pace=ApplicationRequestPacing(),
    )
