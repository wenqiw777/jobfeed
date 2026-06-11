"""Tests for closed-posting signal propagation through SpeedyApply routing.

Covers the mapping from Workday fetch_jd_result outcomes and ATSFetchError
status codes into ``JobPosting.closed_at`` / ``enrich_error`` fields.

All HTTP is mocked — no real network.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import respx

from jobfeed.adapters.sources import _speedyapply_routing as routing
from jobfeed.adapters.sources._ats_workday import WorkdayFetch
from jobfeed.adapters.sources._http import ATSFetchError, create_http_client
from jobfeed.adapters.sources._speedyapply_markdown import canonical_id_for
from jobfeed.adapters.sources._speedyapply_routing import RouteResult
from jobfeed.adapters.sources.speedyapply import SpeedyApplySource
from jobfeed.config import SourcesSpeedyApplyConfig
from jobfeed.observability import get_logger
from jobfeed.ports.source import ClosedJobLookup

TIMEOUT = 30.0
_LONG_JD = "Engineering role. " * 30

_WORKDAY_APPLY = (
    "https://leidos.wd5.myworkdayjobs.com/en-US/external/job/"
    "Fort-Belvoir-VA/Data-Engineer-Intern_R-00180867"
)
_WORKDAY_CXS = (
    "https://leidos.wd5.myworkdayjobs.com/wday/cxs/leidos/external/job/"
    "Fort-Belvoir-VA/Data-Engineer-Intern_R-00180867"
)
_TOKEN = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


def _make_html(*, available: bool) -> str:
    flag = "true" if available else "false"
    return (
        "<html><body><script>var c = {"
        f'token: "{_TOKEN}", postingAvailable: {flag},'
        "};</script></body></html>"
    )


def _source(
    *,
    closed_lookup: ClosedJobLookup | None = None,
    logger: MagicMock | None = None,
) -> SpeedyApplySource:
    return SpeedyApplySource(
        client=create_http_client(),
        config=SourcesSpeedyApplyConfig(
            search_urls=["https://lists.example.test/speedyapply.md"],
            enabled=True,
        ),
        logger=logger if logger is not None else get_logger(),
        closed_lookup=closed_lookup,
    )


class _FakeClosedLookup:
    """ClosedJobLookup stub returning a fixed id set and recording calls."""

    def __init__(self, ids: set[str]) -> None:
        self._ids = ids
        self.platforms: list[str] = []

    async def get_closed_canonical_ids(self, *, platform: str) -> set[str]:
        self.platforms.append(platform)
        return set(self._ids)


class _RaisingClosedLookup:
    """ClosedJobLookup stub whose query raises a transient store error."""

    async def get_closed_canonical_ids(self, *, platform: str) -> set[str]:
        raise RuntimeError(f"connection refused ({platform})")


def _two_row_md(url_a: str, url_b: str) -> str:
    """Two-row greenhouse markdown table for skip-filter tests."""
    return (
        "| Company | Position | Location | Posting | Age |\n"
        "|---|---|---|---|---|\n"
        f'| <a><strong>Alpha</strong></a> | SWE | SF | <a href="{url_a}">'
        '<img src="x"/></a> | 1d |\n'
        f'| <a><strong>Beta</strong></a> | SWE | NY | <a href="{url_b}">'
        '<img src="x"/></a> | 2d |\n'
    )


def _recording_route(routed: list[str]):
    """Build a fake route_and_fetch that records URLs and returns a live JD."""

    async def fake_route_and_fetch(
        _client: object, url: str, **_kwargs: object
    ) -> RouteResult:
        routed.append(url)
        return RouteResult(jd_text=_LONG_JD, enrich_source="speedyapply-greenhouse")

    return fake_route_and_fetch


# ---------------------------------------------------------------------------
# RouteResult dataclass shape
# ---------------------------------------------------------------------------


def test_route_result_defaults() -> None:
    """RouteResult carries closed_at and enrich_error with None defaults."""
    r = RouteResult(jd_text="hello", enrich_source="speedyapply-greenhouse")
    assert r.closed_at is None
    assert r.enrich_error is None


def test_route_result_closed_fields() -> None:
    """RouteResult accepts non-None closed_at and enrich_error."""
    now = datetime.now(UTC)
    r = RouteResult(
        jd_text="",
        enrich_source="speedyapply-error",
        closed_at=now,
        enrich_error="closed:posting-unavailable:workday",
    )
    assert r.closed_at == now
    assert r.enrich_error == "closed:posting-unavailable:workday"


# ---------------------------------------------------------------------------
# route_and_fetch returns RouteResult
# ---------------------------------------------------------------------------


@respx.mock
async def test_route_and_fetch_returns_route_result_for_unrouted() -> None:
    """Unrouted URL yields a RouteResult with closed_at=None, enrich_error=None."""
    async with create_http_client() as client:
        result = await routing.route_and_fetch(
            client,
            "https://uber.com/careers/9",
            slug_cache={},
            timeout=TIMEOUT,
        )
    assert isinstance(result, RouteResult)
    assert result.jd_text == ""
    assert result.enrich_source == "speedyapply-unrouted"
    assert result.closed_at is None
    assert result.enrich_error is None


@respx.mock
async def test_route_and_fetch_greenhouse_closed_at_none() -> None:
    """Greenhouse success result has closed_at=None, enrich_error=None."""
    respx.get(
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs/777?content=true"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 777,
                "title": "SWE Intern",
                "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/777",
                "content": f"<p>{_LONG_JD}</p>",
                "location": {"name": "SF"},
            },
        )
    )
    async with create_http_client() as client:
        result = await routing.route_and_fetch(
            client,
            "https://job-boards.greenhouse.io/acme/jobs/777",
            slug_cache={},
            timeout=TIMEOUT,
        )
    assert isinstance(result, RouteResult)
    assert result.enrich_source == "speedyapply-greenhouse"
    assert result.closed_at is None
    assert result.enrich_error is None


# ---------------------------------------------------------------------------
# Workday: closed signal propagation via route_and_fetch
# ---------------------------------------------------------------------------


async def test_workday_posting_unavailable_closed_at_set() -> None:
    """Workday postingAvailable=false → closed_at set, enrich_error set."""
    fake_result = WorkdayFetch(
        jd_text="",
        is_closed=True,
        reason="closed:posting-unavailable:workday",
    )
    with patch(
        "jobfeed.adapters.sources._ats_workday.fetch_jd_result",
        new=AsyncMock(return_value=fake_result),
    ):
        async with create_http_client() as client:
            result = await routing.route_and_fetch(
                client,
                _WORKDAY_APPLY,
                slug_cache={},
                timeout=TIMEOUT,
            )
    assert isinstance(result, RouteResult)
    assert result.jd_text == ""
    assert result.enrich_source == "speedyapply-error"
    assert result.closed_at is not None
    assert result.enrich_error == "closed:posting-unavailable:workday"


async def test_workday_gone_404_closed_at_set() -> None:
    """Workday gone:404 → closed_at set, enrich_error=gone:404:workday."""
    fake_result = WorkdayFetch(
        jd_text="",
        is_closed=True,
        reason="gone:404:workday",
    )
    with patch(
        "jobfeed.adapters.sources._ats_workday.fetch_jd_result",
        new=AsyncMock(return_value=fake_result),
    ):
        async with create_http_client() as client:
            result = await routing.route_and_fetch(
                client,
                _WORKDAY_APPLY,
                slug_cache={},
                timeout=TIMEOUT,
            )
    assert result.closed_at is not None
    assert result.enrich_error == "gone:404:workday"
    assert result.enrich_source == "speedyapply-error"


async def test_workday_recovered_jd_no_closed_at() -> None:
    """Workday JD recovered → jd_text set, closed_at=None, enrich_error=None."""
    fake_result = WorkdayFetch(
        jd_text=_LONG_JD,
        is_closed=False,
        reason=None,
    )
    with patch(
        "jobfeed.adapters.sources._ats_workday.fetch_jd_result",
        new=AsyncMock(return_value=fake_result),
    ):
        async with create_http_client() as client:
            result = await routing.route_and_fetch(
                client,
                _WORKDAY_APPLY,
                slug_cache={},
                timeout=TIMEOUT,
            )
    assert result.jd_text == _LONG_JD
    assert result.closed_at is None
    assert result.enrich_error is None
    assert result.enrich_source == "speedyapply-workday"


async def test_workday_transient_no_closed_at() -> None:
    """Workday transient (is_closed=False, empty jd_text) → closed_at=None."""
    fake_result = WorkdayFetch(jd_text="", is_closed=False, reason=None)
    with patch(
        "jobfeed.adapters.sources._ats_workday.fetch_jd_result",
        new=AsyncMock(return_value=fake_result),
    ):
        async with create_http_client() as client:
            result = await routing.route_and_fetch(
                client,
                _WORKDAY_APPLY,
                slug_cache={},
                timeout=TIMEOUT,
            )
    assert result.jd_text == ""
    assert result.closed_at is None
    assert result.enrich_error is None
    assert result.enrich_source == "speedyapply-error"


# ---------------------------------------------------------------------------
# ATSFetchError status_code mapping in _route (speedyapply.py)
# ---------------------------------------------------------------------------


async def test_ats_fetch_error_404_sets_closed_at_in_posting() -> None:
    """ATSFetchError with status_code=404 → posting closed_at set, enrich_error set."""
    gh_url = "https://job-boards.greenhouse.io/acme/jobs/1"
    md = (
        "| Company | Position | Location | Posting | Age |\n"
        "|---|---|---|---|---|\n"
        f'| <a><strong>Acme</strong></a> | SWE | SF | <a href="{gh_url}">'
        '<img src="x"/></a> | 2d |\n'
    )

    async def fake_route_and_fetch(*_args: object, **_kwargs: object) -> RouteResult:
        raise ATSFetchError(
            "HTTP 404 from greenhouse/acme",
            slug="acme",
            vendor="greenhouse",
            status_code=404,
        )

    with (
        patch(
            "jobfeed.adapters.sources.speedyapply.fetch_text",
            new=AsyncMock(return_value=md),
        ),
        patch(
            "jobfeed.adapters.sources.speedyapply.routing.route_and_fetch",
            new=fake_route_and_fetch,
        ),
    ):
        source = _source()
        postings = await source.fetch_jobs({})

    assert len(postings) == 1
    p = postings[0]
    assert p.closed_at is not None
    assert p.enrich_error == "gone:404:greenhouse"
    assert p.enrich_source == "speedyapply-error"
    assert p.jd_text is None


async def test_ats_fetch_error_410_sets_closed_at_in_posting() -> None:
    """ATSFetchError with status_code=410 → posting closed_at set."""
    gh_url = "https://job-boards.greenhouse.io/beta/jobs/2"
    md = (
        "| Company | Position | Location | Posting | Age |\n"
        "|---|---|---|---|---|\n"
        f'| <a><strong>Beta</strong></a> | SWE | NY | <a href="{gh_url}">'
        '<img src="x"/></a> | 1d |\n'
    )

    async def fake_route_and_fetch(*_args: object, **_kwargs: object) -> RouteResult:
        raise ATSFetchError(
            "HTTP 410 from greenhouse/beta",
            slug="beta",
            vendor="greenhouse",
            status_code=410,
        )

    with (
        patch(
            "jobfeed.adapters.sources.speedyapply.fetch_text",
            new=AsyncMock(return_value=md),
        ),
        patch(
            "jobfeed.adapters.sources.speedyapply.routing.route_and_fetch",
            new=fake_route_and_fetch,
        ),
    ):
        source = _source()
        postings = await source.fetch_jobs({})

    assert len(postings) == 1
    p = postings[0]
    assert p.closed_at is not None
    assert p.enrich_error == "gone:410:greenhouse"
    assert p.enrich_source == "speedyapply-error"


async def test_ats_fetch_error_403_no_closed_at() -> None:
    """ATSFetchError with status_code=403 → closed_at=None, enrich_error=None."""
    gh_url = "https://job-boards.greenhouse.io/gamma/jobs/3"
    md = (
        "| Company | Position | Location | Posting | Age |\n"
        "|---|---|---|---|---|\n"
        f'| <a><strong>Gamma</strong></a> | SWE | LA | <a href="{gh_url}">'
        '<img src="x"/></a> | 3d |\n'
    )

    async def fake_route_and_fetch(*_args: object, **_kwargs: object) -> RouteResult:
        raise ATSFetchError(
            "HTTP 403 from greenhouse/gamma",
            slug="gamma",
            vendor="greenhouse",
            status_code=403,
        )

    with (
        patch(
            "jobfeed.adapters.sources.speedyapply.fetch_text",
            new=AsyncMock(return_value=md),
        ),
        patch(
            "jobfeed.adapters.sources.speedyapply.routing.route_and_fetch",
            new=fake_route_and_fetch,
        ),
    ):
        source = _source()
        postings = await source.fetch_jobs({})

    assert len(postings) == 1
    p = postings[0]
    assert p.closed_at is None
    assert p.enrich_error is None
    assert p.enrich_source == "speedyapply-error"


async def test_ats_fetch_error_none_status_no_closed_at() -> None:
    """ATSFetchError with status_code=None (timeout) → closed_at=None."""
    gh_url = "https://job-boards.greenhouse.io/delta/jobs/4"
    md = (
        "| Company | Position | Location | Posting | Age |\n"
        "|---|---|---|---|---|\n"
        f'| <a><strong>Delta</strong></a> | SWE | TX | <a href="{gh_url}">'
        '<img src="x"/></a> | 4d |\n'
    )

    async def fake_route_and_fetch(*_args: object, **_kwargs: object) -> RouteResult:
        raise ATSFetchError(
            "Timeout for greenhouse/delta",
            slug="delta",
            vendor="greenhouse",
            status_code=None,
        )

    with (
        patch(
            "jobfeed.adapters.sources.speedyapply.fetch_text",
            new=AsyncMock(return_value=md),
        ),
        patch(
            "jobfeed.adapters.sources.speedyapply.routing.route_and_fetch",
            new=fake_route_and_fetch,
        ),
    ):
        source = _source()
        postings = await source.fetch_jobs({})

    assert len(postings) == 1
    p = postings[0]
    assert p.closed_at is None
    assert p.enrich_error is None
    assert p.enrich_source == "speedyapply-error"


# ---------------------------------------------------------------------------
# Workday end-to-end via mocked HTML (respx, no patch)
# ---------------------------------------------------------------------------


@respx.mock
async def test_workday_closed_posting_end_to_end() -> None:
    """Workday postingAvailable=false via mocked HTML → posting closed_at set."""
    respx.get(_WORKDAY_APPLY).mock(
        return_value=httpx.Response(200, text=_make_html(available=False))
    )
    async with create_http_client() as client:
        result = await routing.route_and_fetch(
            client,
            _WORKDAY_APPLY,
            slug_cache={},
            timeout=TIMEOUT,
        )
    assert result.jd_text == ""
    assert result.enrich_source == "speedyapply-error"
    assert result.closed_at is not None
    assert result.enrich_error == "closed:posting-unavailable:workday"


@respx.mock
async def test_workday_recovered_jd_end_to_end() -> None:
    """Workday JD recovered via respx → posting jd_text set, closed_at=None."""
    respx.get(_WORKDAY_APPLY).mock(
        return_value=httpx.Response(200, text=_make_html(available=True))
    )
    respx.get(_WORKDAY_CXS).mock(
        return_value=httpx.Response(
            200,
            json={"jobPostingInfo": {"jobDescription": f"<p>{_LONG_JD}</p>"}},
        )
    )
    async with create_http_client() as client:
        result = await routing.route_and_fetch(
            client,
            _WORKDAY_APPLY,
            slug_cache={},
            timeout=TIMEOUT,
        )
    assert result.closed_at is None
    assert result.enrich_error is None
    assert result.enrich_source == "speedyapply-workday"
    assert "Engineering role" in result.jd_text


@respx.mock
async def test_workday_cxs_500_transient_no_closed_at() -> None:
    """Workday CXS 500 (transient) → closed_at=None, enrich_source=speedyapply-error."""
    respx.get(_WORKDAY_APPLY).mock(
        return_value=httpx.Response(200, text=_make_html(available=True))
    )
    respx.get(_WORKDAY_CXS).mock(return_value=httpx.Response(500))
    async with create_http_client() as client:
        result = await routing.route_and_fetch(
            client,
            _WORKDAY_APPLY,
            slug_cache={},
            timeout=TIMEOUT,
        )
    assert result.closed_at is None
    assert result.enrich_source == "speedyapply-error"


# ---------------------------------------------------------------------------
# closed_lookup: skip re-fetching JDs for already-closed canonical_ids
# ---------------------------------------------------------------------------


async def test_fetch_jobs_skips_closed_canonical_ids_before_routing() -> None:
    """A row whose canonical_id is already closed is never re-routed/fetched."""
    url_live = "https://job-boards.greenhouse.io/live/jobs/1"
    url_dead = "https://job-boards.greenhouse.io/dead/jobs/2"
    md = _two_row_md(url_live, url_dead)
    routed: list[str] = []
    lookup = _FakeClosedLookup({canonical_id_for(url_dead)})

    with (
        patch(
            "jobfeed.adapters.sources.speedyapply.fetch_text",
            new=AsyncMock(return_value=md),
        ),
        patch(
            "jobfeed.adapters.sources.speedyapply.routing.route_and_fetch",
            new=_recording_route(routed),
        ),
    ):
        source = _source(closed_lookup=lookup)
        postings = await source.fetch_jobs({})

    assert routed == [url_live]  # dead URL never fetched
    assert [p.canonical_id for p in postings] == [canonical_id_for(url_live)]
    assert lookup.platforms == ["speedyapply"]


async def test_fetch_jobs_routes_all_when_no_closed_lookup() -> None:
    """With no closed_lookup, every row is routed (backward compatible)."""
    url_a = "https://job-boards.greenhouse.io/aaa/jobs/1"
    url_b = "https://job-boards.greenhouse.io/bbb/jobs/2"
    md = _two_row_md(url_a, url_b)
    routed: list[str] = []

    with (
        patch(
            "jobfeed.adapters.sources.speedyapply.fetch_text",
            new=AsyncMock(return_value=md),
        ),
        patch(
            "jobfeed.adapters.sources.speedyapply.routing.route_and_fetch",
            new=_recording_route(routed),
        ),
    ):
        source = _source(closed_lookup=None)
        postings = await source.fetch_jobs({})

    assert set(routed) == {url_a, url_b}
    assert {p.canonical_id for p in postings} == {
        canonical_id_for(url_a),
        canonical_id_for(url_b),
    }


async def test_fetch_jobs_routes_all_when_closed_set_empty() -> None:
    """An empty closed set routes every row and still queries the lookup once."""
    url_a = "https://job-boards.greenhouse.io/ccc/jobs/1"
    url_b = "https://job-boards.greenhouse.io/ddd/jobs/2"
    md = _two_row_md(url_a, url_b)
    routed: list[str] = []
    lookup = _FakeClosedLookup(set())

    with (
        patch(
            "jobfeed.adapters.sources.speedyapply.fetch_text",
            new=AsyncMock(return_value=md),
        ),
        patch(
            "jobfeed.adapters.sources.speedyapply.routing.route_and_fetch",
            new=_recording_route(routed),
        ),
    ):
        source = _source(closed_lookup=lookup)
        postings = await source.fetch_jobs({})

    assert set(routed) == {url_a, url_b}
    assert {p.canonical_id for p in postings} == {
        canonical_id_for(url_a),
        canonical_id_for(url_b),
    }
    assert lookup.platforms == ["speedyapply"]


async def test_fetch_jobs_survives_closed_lookup_failure() -> None:
    """A transient closed-lookup error fails open: warn and route every row."""
    url_a = "https://job-boards.greenhouse.io/eee/jobs/1"
    url_b = "https://job-boards.greenhouse.io/fff/jobs/2"
    md = _two_row_md(url_a, url_b)
    routed: list[str] = []
    logger = MagicMock()

    with (
        patch(
            "jobfeed.adapters.sources.speedyapply.fetch_text",
            new=AsyncMock(return_value=md),
        ),
        patch(
            "jobfeed.adapters.sources.speedyapply.routing.route_and_fetch",
            new=_recording_route(routed),
        ),
    ):
        source = _source(closed_lookup=_RaisingClosedLookup(), logger=logger)
        postings = await source.fetch_jobs({})

    assert set(routed) == {url_a, url_b}
    assert {p.canonical_id for p in postings} == {
        canonical_id_for(url_a),
        canonical_id_for(url_b),
    }
    logger.warning.assert_called_once_with(
        "speedyapply_closed_lookup_failed",
        error="connection refused (speedyapply)",
    )
