"""Bounded, read-only application navigation to independently verified ATS jobs."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from urllib.parse import urldefrag, urljoin, urlsplit

import httpx

from jobfeed.adapters.sources._application_route_html import (
    RouteDocument,
    parse_route_document,
    verify_route_target,
)
from jobfeed.adapters.sources._ats_workday import _build_cxs_url, _parse_csrf_token
from jobfeed.adapters.sources._http import html_to_text
from jobfeed.domain.application_route import (
    ApplicationPageReadError,
    ApplicationRouteHop,
    ApplicationRouteOutcome,
    HopKind,
    RenderedApplicationPage,
    RouteStatus,
)
from jobfeed.domain.external_identity import external_identity
from jobfeed.domain.intermediary import trusted_ats_url
from jobfeed.domain.models import JobPosting

_MAX_EDGES = 5
_MAX_PAGE_BYTES = 2_000_000
_HTTP_BUDGET = 20.0
_CHROME_BUDGET = 30.0
_HTTP_WORKERS = 10
_HTTP_OK = 200
_HTTP_REDIRECTION = 300
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_BLOCKED_STATUSES = {401, 403, 429}
ChromeReader = Callable[[JobPosting, str], Awaitable[RenderedApplicationPage]]
URLGuard = Callable[[str], Awaitable[bool]]
Pace = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class _Page:
    html: str
    status: int
    location: str | None = None


class _RouteError(Exception):
    def __init__(self, status: RouteStatus, reason: str) -> None:
        self.status, self.reason = status, reason
        super().__init__(reason)


async def public_application_url(url: str) -> bool:
    """Reject local/private HTTP destinations, including resolved DNS addresses.

    Args:
        url: Actual HTTP destination to validate before any request.

    Returns:
        Whether its syntax and all currently resolved addresses are public.
    """
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 80, 443}
            or host.casefold() == "localhost"
            or host.endswith(".localhost")
        ):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            addresses = await asyncio.get_running_loop().getaddrinfo(
                host,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        return bool(addresses) and all(
            ipaddress.ip_address(str(item[4][0])).is_global for item in addresses
        )
    except (ValueError, OSError):
        return False


class ApplicationRouteResolver:
    """One shared resolver; HTTP waits overlap the independent Chrome tab pool."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        chrome_reader: ChromeReader | None = None,
        url_guard: URLGuard = public_application_url,
        pace: Pace | None = None,
    ) -> None:
        self.client = client
        self.chrome_reader = chrome_reader
        self.url_guard = url_guard
        self.pace = pace
        self._http_slots = asyncio.Semaphore(_HTTP_WORKERS)

    async def __call__(self, job: JobPosting) -> ApplicationRouteOutcome:
        """Resolve this source's observed Apply URL without mutating its facts."""
        return await self.resolve(job)

    async def resolve(
        self, job: JobPosting, apply_url: str | None = None
    ) -> ApplicationRouteOutcome:
        """Resolve at most five observed navigation edges; errors stay per item.

        Args:
            job: Source posting whose facts must own the application chain.
            apply_url: Optional observed destination overriding the source Apply URL.

        Returns:
            Bounded route outcome with verified ATS URL only on success.
        """
        original = apply_url or job.apply_url
        if not original:
            return ApplicationRouteOutcome("unresolved", reason="apply_url_missing")
        hops = [ApplicationRouteHop(original, "original_apply_url")]
        if trusted_ats_url(original):
            return ApplicationRouteOutcome(
                "resolved", original, tuple(hops), "direct_ats_identity"
            )
        try:
            if self.client is not None:
                return await self._walk(self.client, job, original, hops)
            async with httpx.AsyncClient(
                headers={"User-Agent": "jobfeed/1.0"}, follow_redirects=False
            ) as client:
                return await self._walk(client, job, original, hops)
        except _RouteError as exc:
            return ApplicationRouteOutcome(
                exc.status, hops=tuple(hops), reason=exc.reason
            )
        except (httpx.HTTPError, ValueError, TimeoutError) as exc:
            reason = (
                "http_time_budget"
                if isinstance(exc, TimeoutError)
                else (
                    "http_error" if isinstance(exc, httpx.HTTPError) else "invalid_page"
                )
            )
            return ApplicationRouteOutcome("failed", hops=tuple(hops), reason=reason)

    async def _read(
        self,
        client: httpx.AsyncClient,
        url: str,
        deadline: float,
        headers: dict[str, str] | None = None,
    ) -> _Page:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise _RouteError("failed", "http_time_budget")
        async with asyncio.timeout(remaining):
            if not await self.url_guard(url):
                raise _RouteError("blocked", "non_public_destination")
            if self.pace:
                await self.pace(url)
            async with (
                self._http_slots,
                client.stream(
                    "GET",
                    url,
                    headers=headers,
                    timeout=remaining,
                    follow_redirects=False,
                ) as response,
            ):
                if response.status_code in _REDIRECT_STATUSES:
                    return _Page(
                        "", response.status_code, response.headers.get("location")
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_PAGE_BYTES:
                        raise _RouteError("failed", "page_size_limit")
                return _Page(
                    body.decode("utf-8", errors="replace"), response.status_code
                )

    async def _render(
        self,
        job: JobPosting,
        url: str,
        hops: list[ApplicationRouteHop],
        *,
        remaining_budget: float | None = None,
    ) -> RenderedApplicationPage:
        budget = _CHROME_BUDGET if remaining_budget is None else remaining_budget
        if budget <= 0:
            raise _RouteError("blocked", "chrome_time_budget") from None
        if self.chrome_reader is None:
            raise _RouteError("blocked", "chrome_reader_unavailable")
        try:
            async with asyncio.timeout(budget):
                if not await self.url_guard(url):
                    raise _RouteError("blocked", "non_public_destination")
                snapshot = await self.chrome_reader(job, url)
                if not await self.url_guard(snapshot.url):
                    raise _RouteError("blocked", "non_public_destination")
        except TimeoutError as exc:
            raise _RouteError("blocked", "chrome_time_budget") from exc
        except ApplicationPageReadError as exc:
            raise _RouteError(exc.status, exc.reason) from exc
        except _RouteError:
            raise
        except Exception as exc:
            raise _RouteError("failed", "chrome_reader_error") from exc
        if len(snapshot.html.encode("utf-8")) > _MAX_PAGE_BYTES:
            raise _RouteError("failed", "page_size_limit")
        hops.append(ApplicationRouteHop(snapshot.url, "rendered_page"))
        return snapshot

    async def _workday_document(
        self,
        client: httpx.AsyncClient,
        job: JobPosting,
        url: str,
        page: _Page,
        deadline: float,
    ) -> RouteDocument:
        doc = parse_route_document(url, page.html, job)
        built = _build_cxs_url(url)
        if not built or "postingAvailable: false" in page.html:
            return doc
        token = _parse_csrf_token(page.html)
        headers = {"Referer": url, "Accept": "application/json"}
        if token:
            headers["X-CALYPSO-CSRF-TOKEN"] = token
        # Workday Apply actions identify the same posting, but CXS only accepts
        # the job detail path. Preserve the observed Apply URL in route evidence.
        details_url = built[0].rstrip("/").removesuffix("/apply")
        api = await self._read(client, details_url, deadline, headers)
        if api.status != _HTTP_OK:
            return doc
        try:
            info = json.loads(api.html).get("jobPostingInfo", {})
        except (ValueError, AttributeError):
            return doc
        if not isinstance(info, dict):
            return doc
        title = info.get("title", "")
        description = html_to_text(str(info.get("jobDescription", "")))
        # Employer comes from observed page/body facts, never a fabricated URL.
        company = info.get("company") or doc.company
        if not company:
            company_doc = parse_route_document(
                url, f"<main><h1>{title}</h1>{description}</main>", job
            )
            company = company_doc.company
        synthetic = (
            '<script type="application/ld+json">'
            + json.dumps(
                {
                    "@type": "JobPosting",
                    "title": title,
                    "description": description,
                    "hiringOrganization": {"name": company},
                    "identifier": {"value": info.get("jobReqId", "")},
                }
            )
            + "</script>"
        )
        return parse_route_document(url, synthetic, job)

    async def _walk(  # noqa: C901 - explicit bounded navigation state machine
        self,
        client: httpx.AsyncClient,
        job: JobPosting,
        original: str,
        hops: list[ApplicationRouteHop],
    ) -> ApplicationRouteOutcome:
        """Observe at most five edges without retaining database write locks.

        Time complexity: O(e * (b + c) + w^2) for bounded edges e, document
        bytes b, candidate links c, and final JD words w; network waits are bounded.
        """
        deadline = monotonic() + _HTTP_BUDGET
        chrome_remaining = _CHROME_BUDGET
        current = original
        visited: set[str] = set()
        requisitions: set[str] = set()
        for edge_count in range(_MAX_EDGES + 1):
            rendered = False
            key = urldefrag(current)[0]
            if key in visited:
                raise _RouteError("unresolved", "navigation_loop")
            visited.add(key)
            try:
                page = await self._read(client, current, deadline)
            except httpx.HTTPError:
                render_started = monotonic()
                snapshot = await self._render(
                    job, current, hops, remaining_budget=chrome_remaining
                )
                render_elapsed = monotonic() - render_started
                chrome_remaining -= render_elapsed
                deadline += render_elapsed
                if chrome_remaining < 0:
                    raise _RouteError("blocked", "chrome_time_budget") from None
                current, page = snapshot.url, _Page(snapshot.html, _HTTP_OK)
                rendered = True
            if page.status in _REDIRECT_STATUSES:
                if not page.location:
                    raise _RouteError("unresolved", "redirect_location_missing")
                if edge_count == _MAX_EDGES:
                    break
                current = urljoin(current, page.location)
                hops.append(ApplicationRouteHop(current, "http_redirect"))
                continue
            if page.status in _BLOCKED_STATUSES:
                render_started = monotonic()
                snapshot = await self._render(
                    job, current, hops, remaining_budget=chrome_remaining
                )
                render_elapsed = monotonic() - render_started
                chrome_remaining -= render_elapsed
                deadline += render_elapsed
                if chrome_remaining < 0:
                    raise _RouteError("blocked", "chrome_time_budget") from None
                current, page = snapshot.url, _Page(snapshot.html, _HTTP_OK)
                rendered = True
            if not _HTTP_OK <= page.status < _HTTP_REDIRECTION:
                raise _RouteError("failed", "http_status")
            doc = parse_route_document(current, page.html, job)
            if trusted_ats_url(current) and not doc.owned:
                doc = await self._workday_document(client, job, current, page, deadline)
            if doc.ambiguous:
                raise _RouteError("ambiguous", "multiple_target_job_blocks")
            if not rendered and (
                (not doc.owned and not doc.has_job_facts)
                or (doc.owned and not doc.candidates and not trusted_ats_url(current))
            ):
                render_started = monotonic()
                snapshot = await self._render(
                    job, current, hops, remaining_budget=chrome_remaining
                )
                render_elapsed = monotonic() - render_started
                chrome_remaining -= render_elapsed
                deadline += render_elapsed
                if chrome_remaining < 0:
                    raise _RouteError("blocked", "chrome_time_budget") from None
                current, page = snapshot.url, _Page(snapshot.html, _HTTP_OK)
                rendered = True
                doc = parse_route_document(current, page.html, job)
            if trusted_ats_url(current):
                reason = verify_route_target(job, doc, current, requisitions)
                if reason:
                    raise _RouteError("unresolved", reason)
                return ApplicationRouteOutcome(
                    "resolved", current, tuple(hops), "verified_application_route"
                )
            if not doc.owned:
                raise _RouteError("unresolved", "target_ownership_missing")
            if doc.ambiguous:
                raise _RouteError("ambiguous", "multiple_target_job_blocks")
            observed_reqs = set(doc.requisitions)
            if len(observed_reqs) > 1:
                raise _RouteError("ambiguous", "multiple_target_requisitions")
            if requisitions and observed_reqs and requisitions != observed_reqs:
                raise _RouteError("unresolved", "requisition_mismatch")
            requisitions.update(observed_reqs)
            unique_candidates: dict[str, tuple[str, HopKind]] = {}
            for target, kind in doc.candidates:
                key = (
                    (external_identity(target) or target)
                    if trusted_ats_url(target)
                    else target
                )
                unique_candidates.setdefault(key, (target, kind))
            candidates = list(unique_candidates.values())
            if len(candidates) > 1:
                raise _RouteError("ambiguous", "multiple_application_candidates")
            if not candidates:
                raise _RouteError("unresolved", "target_apply_link_missing")
            if edge_count == _MAX_EDGES:
                break
            current, kind = candidates[0]
            hops.append(ApplicationRouteHop(current, kind))
        raise _RouteError("unresolved", "navigation_edge_limit")
