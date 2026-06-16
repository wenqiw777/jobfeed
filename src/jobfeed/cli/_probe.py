"""Per-slug ATS vendor probe assembly shared by CLI commands and the web app.

Lives in the cli package because the cli is the composition root that may
import adapters; consumers (``companies add``, the web probe route) receive
the assembled callable and never manage the HTTP client lifecycle themselves.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

import httpx

from jobfeed.adapters.sources._ats_probe import probe_company
from jobfeed.adapters.sources._http import create_http_client
from jobfeed.config import Settings

# Per-slug ATS vendor probe: slug -> vendor name, or None on a definitive
# all-vendor miss. Assembled here (the composition root that may import
# adapters) and consumed by the web layer through the context (web modules
# must not import jobfeed.adapters).
ProbeVendorFn = Callable[[str], Awaitable[str | None]]


class ProbeCompanyFn(Protocol):
    """Client-taking probe shape so callers can substitute a fake in tests."""

    def __call__(
        self, client: httpx.AsyncClient, slug: str, *, timeout: float
    ) -> Awaitable[str | None]:
        """Probe all vendors for a slug using the given client."""
        ...


def build_probe_company(
    settings: Settings, *, probe_fn: ProbeCompanyFn = probe_company
) -> ProbeVendorFn:
    """Build the self-contained per-slug ATS vendor probe.

    Each invocation opens and closes its own HTTP client, so consumers carry
    no client lifecycle. Transport behavior is the probe module's as-is:
    ProbeNetworkError / ProbeIndeterminateError propagate unchanged and no
    retry layer is added.

    Args:
        settings: Loaded application settings (supplies the probe timeout).
        probe_fn: Client-taking probe implementation (tests pass a fake).

    Returns:
        Async callable mapping a slug to its vendor name, or None when all
        vendors return a definitive miss.
    """
    timeout = settings.sources.ats.probe_timeout_s

    async def probe(slug: str) -> str | None:
        client = create_http_client(timeout)
        try:
            return await probe_fn(client, slug, timeout=timeout)
        finally:
            await client.aclose()

    return probe


__all__ = ["ProbeCompanyFn", "ProbeVendorFn", "build_probe_company"]
