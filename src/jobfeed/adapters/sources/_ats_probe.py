"""Auto-probe mechanism for detecting which ATS vendor a company uses."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from jobfeed.adapters.sources import _ats_ashby as ashby
from jobfeed.adapters.sources import _ats_greenhouse as greenhouse
from jobfeed.adapters.sources import _ats_lever as lever
from jobfeed.adapters.sources._http import ProbeIndeterminateError, ProbeNetworkError

_logger = logging.getLogger(__name__)


class ProbeFunc(Protocol):
    """Callable protocol for a vendor probe function."""

    async def __call__(
        self,
        client: httpx.AsyncClient,
        slug: str,
        *,
        timeout: float = 5.0,
    ) -> bool: ...


# Probe order by market share among YC companies: ~70/15/10%
PROBE_ORDER: list[tuple[str, ProbeFunc]] = [
    ("greenhouse", greenhouse.probe),
    ("ashby", ashby.probe),
    ("lever", lever.probe),
]


@dataclass
class _ProbeState:
    """Accumulator for per-vendor probe outcomes during a single probe run."""

    network_errors: list[ProbeNetworkError] = field(default_factory=list)
    indeterminate_errors: list[ProbeIndeterminateError] = field(default_factory=list)


@dataclass
class _ProbeContext:
    """Read-only inputs for a single vendor probe invocation."""

    vendor_name: str
    probe_fn: ProbeFunc
    client: httpx.AsyncClient
    slug: str
    timeout: float


async def probe_company(
    client: httpx.AsyncClient,
    slug: str,
    *,
    timeout: float = 5.0,
) -> str | None:
    """Try each vendor probe in order. Returns vendor name or None.

    Probes Greenhouse, then Ashby, then Lever in market-share order.
    Returns the name of the first vendor that reports the board is live.

    A partial network failure is tolerated when another vendor hits — for
    example, if Greenhouse times out but Ashby returns 2xx, returns "ashby".

    Args:
        client: Shared async HTTP client.
        slug: Company board slug to probe on each vendor.
        timeout: Per-probe timeout in seconds.

    Returns:
        Vendor name string on first hit, or None when all vendors return
        a definitive 404/410 miss.

    Raises:
        ProbeNetworkError: When all vendor probes fail at the network level
            (timeout, DNS) with no hits and no indeterminate responses.
        ProbeIndeterminateError: When no vendor hits and at least one vendor
            returned an ambiguous response (partial network failure, 403, 429,
            5xx, invalid Lever 2xx JSON, etc.). Prevents caching ambiguous
            outcomes as "unknown" or escalating to mark_company_removed().
    """
    state = _ProbeState()

    for vendor_name, probe_fn in PROBE_ORDER:
        ctx = _ProbeContext(
            vendor_name=vendor_name,
            probe_fn=probe_fn,
            client=client,
            slug=slug,
            timeout=timeout,
        )
        result = await _try_probe(ctx, state)
        if result is not None:
            return result

    return _resolve_no_hit(slug, state)


async def _try_probe(ctx: _ProbeContext, state: _ProbeState) -> str | None:
    """Invoke one vendor probe and record the outcome in state.

    Args:
        ctx: Probe inputs (vendor name, probe fn, client, slug, timeout).
        state: Shared accumulator for non-hit outcomes.

    Returns:
        Vendor name string on a hit, or None to continue probing.
    """
    try:
        hit = await ctx.probe_fn(ctx.client, ctx.slug, timeout=ctx.timeout)
    except ProbeIndeterminateError as exc:
        _logger.debug(
            "Indeterminate probe for %s/%s: %s", ctx.vendor_name, ctx.slug, exc
        )
        state.indeterminate_errors.append(exc)
        return None
    except ProbeNetworkError as exc:
        _logger.debug("Network error probing %s/%s: %s", ctx.vendor_name, ctx.slug, exc)
        state.network_errors.append(exc)
        return None

    if hit:
        _logger.debug("Probe hit on %s for %s", ctx.vendor_name, ctx.slug)
        return ctx.vendor_name

    _logger.debug("Definitive miss on %s for %s", ctx.vendor_name, ctx.slug)
    return None


def _resolve_no_hit(slug: str, state: _ProbeState) -> str | None:
    """Determine the outcome when no vendor probe returned a hit.

    Args:
        slug: Company board slug (for logging context).
        state: Accumulated probe outcomes from all vendor probes.

    Returns:
        None when all vendors returned definitive misses.

    Raises:
        ProbeNetworkError: When all failures were network-level.
        ProbeIndeterminateError: When at least one response was ambiguous.
    """
    has_network = bool(state.network_errors)
    has_indeterminate = bool(state.indeterminate_errors)

    if not has_network and not has_indeterminate:
        # Every vendor returned a definitive 404/410.
        _logger.debug("All vendors returned definitive miss for %s", slug)
        return None

    if has_indeterminate:
        _logger.debug("Indeterminate probe outcome for %s", slug)
        raise state.indeterminate_errors[0]

    # All errors were network-level.
    _logger.debug("All vendor probes failed at network level for %s", slug)
    raise state.network_errors[0]


async def resolve_dead_slug(
    client: httpx.AsyncClient,
    slug: str,
    *,
    timeout: float = 5.0,
) -> str | None:
    """Re-probe a slug that repeatedly returned 404/410 on its cached vendor.

    Returns same/new vendor, or None if truly dead. Re-probes all three
    supported vendors, including the originally cached vendor, because
    same-vendor recovery is possible and the company may have migrated ATS.

    Callers must not mark the company removed when this raises — an error
    means the outcome is unresolved, not confirmed dead.

    Args:
        client: Shared async HTTP client.
        slug: Company board slug to re-probe.
        timeout: Per-probe timeout in seconds.

    Returns:
        Vendor name string (same or different) if any vendor is live, or
        None only when all vendors return a definitive 404/410 miss.

    Raises:
        ProbeNetworkError: When all vendor probes fail at the network level.
        ProbeIndeterminateError: When no vendor hits and at least one probe
            is unresolved or ambiguous.
    """
    return await probe_company(client, slug, timeout=timeout)


__all__ = ["PROBE_ORDER", "ProbeFunc", "probe_company", "resolve_dead_slug"]
