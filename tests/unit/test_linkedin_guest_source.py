"""Unit tests for the LinkedIn guest discovery source (SimpleSource).

Covers correct pagination (``start`` advances by the page's RAW card count —
parse-skipped cards included, NOT the JobSpy accumulated-total bug, and an
all-skipped non-empty page keeps paginating), dedupe by bare job id across pages and
URLs, the ``max_jobs`` / ``start < 1000`` guards, graceful 429-and-sentinel
termination, keyword-less URL skips, and the injected between-page pacing
sleep, and the fetcher-less production path (real client + ``fetch`` closure)
over an ``httpx.MockTransport`` — no network either way.
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from jobfeed.adapters.sources import linkedin_guest
from jobfeed.adapters.sources._linkedin_guest_http import GuestResponse
from jobfeed.adapters.sources.linkedin_guest import (
    GuestSourceSettings,
    LinkedInGuestSource,
)
from jobfeed.ports.source import SimpleSource

_SEARCH_URL = (
    "https://www.linkedin.com/jobs/search/?keywords=python+engineer"
    "&location=United%20States&f_TPR=r86400"
)
_PAGE_SIZE = 10
_START_CAP = 1000
_BIG_PAGE = 250
_HTTP_TOO_MANY = 429
_FAIR_SHARE_SEARCH_COUNT = 2


def _card(job_id: str, *, location: str | None = "SF, CA") -> str:
    """Build one minimal guest search card whose href trails the job id."""
    location_html = (
        f'<span class="job-search-card__location">{location}</span>'
        if location is not None
        else ""
    )
    return (
        '<div class="base-search-card">'
        '<a class="base-card__full-link" '
        f'href="https://www.linkedin.com/jobs/view/engineer-at-acme-{job_id}">'
        "<h3>Engineer</h3></a><h4>Acme</h4>"
        f"{location_html}"
        '<time datetime="2026-06-09"></time>'
        "</div>"
    )


def _invalid_card() -> str:
    """Build a card whose href has no numeric id, so parsing skips it."""
    return (
        '<div class="base-search-card">'
        '<a class="base-card__full-link" '
        'href="https://www.linkedin.com/jobs/view/promoted-role-at-acme">'
        "<h3>Engineer</h3></a><h4>Acme</h4>"
        "</div>"
    )


def _page(job_ids: list[str], *, location: str | None = "SF, CA") -> str:
    """Build a search-results fragment holding one card per job id."""
    return "".join(_card(job_id, location=location) for job_id in job_ids)


def _ids(start: int, count: int) -> list[str]:
    """Generate distinct numeric job ids offset by ``start``."""
    return [str(4_000_000_000 + start + i) for i in range(count)]


def _starts(urls: list[str]) -> list[int]:
    """Extract the ``start`` query offset of every requested page, in order."""
    return [int(parse_qs(urlsplit(url).query)["start"][0]) for url in urls]


class ScriptedFetcher:
    """Fake fetcher returning queued responses and recording request URLs."""

    def __init__(self, responses: list[GuestResponse]) -> None:
        self._responses = list(responses)
        self.urls: list[str] = []

    async def __call__(self, url: str) -> GuestResponse:
        """Record the URL and pop the next scripted response."""
        self.urls.append(url)
        return self._responses.pop(0)


class EndlessFetcher:
    """Fake fetcher inventing ``page_size`` fresh cards on every call."""

    def __init__(self, page_size: int) -> None:
        self._page_size = page_size
        self.urls: list[str] = []

    async def __call__(self, url: str) -> GuestResponse:
        """Record the URL and return a page of never-before-seen cards."""
        first = len(self.urls) * self._page_size
        self.urls.append(url)
        return GuestResponse(status=200, text=_page(_ids(first, self._page_size)))


class RecordingSleeper:
    """Async sleep double that records delays without waiting."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        """Record the requested delay instead of sleeping."""
        self.delays.append(seconds)


class RecordingLogger:
    """Logger double recording warning events for assertions."""

    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict[str, object]]] = []

    def info(self, _event: str, **_kwargs: object) -> None:
        """Accept info logs."""

    def warning(self, event: str, **kwargs: object) -> None:
        """Record warning events."""
        self.warnings.append((event, dict(kwargs)))

    def error(self, _event: str, **_kwargs: object) -> None:
        """Accept error logs."""

    def debug(self, _event: str, **_kwargs: object) -> None:
        """Accept debug logs."""


def _source(
    fetcher: ScriptedFetcher | EndlessFetcher,
    *,
    settings: GuestSourceSettings | None = None,
    sleeper: RecordingSleeper | None = None,
    logger: RecordingLogger | None = None,
) -> LinkedInGuestSource:
    """Build a source with a fake fetcher and no-wait sleeper."""
    return LinkedInGuestSource(
        settings=settings or GuestSourceSettings(search_urls=(_SEARCH_URL,)),
        logger=logger or RecordingLogger(),
        fetcher=fetcher,
        sleeper=sleeper or RecordingSleeper(),
    )


def _three_pages_then_empty() -> ScriptedFetcher:
    """Three full pages of distinct cards followed by an empty page."""
    return ScriptedFetcher(
        [
            GuestResponse(status=200, text=_page(_ids(0, _PAGE_SIZE))),
            GuestResponse(status=200, text=_page(_ids(10, _PAGE_SIZE))),
            GuestResponse(status=200, text=_page(_ids(20, _PAGE_SIZE))),
            GuestResponse(status=200, text=""),
        ]
    )


def test_source_satisfies_simple_source_protocol() -> None:
    """LinkedInGuestSource is a SimpleSource."""
    assert isinstance(_source(ScriptedFetcher([])), SimpleSource)


async def test_hour_window_overrides_pasted_linkedin_time_range() -> None:
    """Runtime freshness keeps LinkedIn discovery at the exact hour window."""
    fetcher = ScriptedFetcher([GuestResponse(status=200, text="")])
    settings = GuestSourceSettings(
        search_urls=(_SEARCH_URL,),
        posted_within_hours=36,
    )

    await _source(fetcher, settings=settings).fetch_jobs({})

    assert parse_qs(urlsplit(fetcher.urls[0]).query)["f_TPR"] == ["r129600"]


async def test_three_pages_then_empty_yields_all_unique_postings() -> None:
    """3 pages of 10 distinct cards + empty page -> 30 guest postings."""
    fetcher = _three_pages_then_empty()
    postings = await _source(fetcher).fetch_jobs({})

    assert len(postings) == 3 * _PAGE_SIZE
    assert {p.platform for p in postings} == {"linkedin_guest"}
    assert all(p.jd_text is None for p in postings)
    assert all(p.enriched_at is None for p in postings)
    assert all(p.enrich_source is None for p in postings)
    assert len({p.discovered_at for p in postings}) == 1


async def test_posting_fields_map_from_the_parsed_card() -> None:
    """Card fields land on the JobPosting (id, view URL, posted_at, ...)."""
    fetcher = ScriptedFetcher(
        [
            GuestResponse(status=200, text=_page(_ids(0, 1))),
            GuestResponse(status=200, text=""),
        ]
    )
    [posting] = await _source(fetcher).fetch_jobs({})

    assert posting.canonical_id == _ids(0, 1)[0]
    assert posting.url == f"https://www.linkedin.com/jobs/view/{_ids(0, 1)[0]}"
    assert posting.title == "Engineer"
    assert posting.company == "Acme"
    assert posting.location == "SF, CA"
    assert posting.posted_at == datetime(2026, 6, 9, tzinfo=UTC)


async def test_card_missing_location_maps_to_empty_string() -> None:
    """A card without the location span yields location == ""."""
    fetcher = ScriptedFetcher(
        [
            GuestResponse(status=200, text=_page(_ids(0, 1), location=None)),
            GuestResponse(status=200, text=""),
        ]
    )
    [posting] = await _source(fetcher).fetch_jobs({})
    assert posting.location == ""


async def test_start_advances_by_cards_returned_per_page() -> None:
    """start goes 0 -> 10 -> 20 -> 30: += len(cards), not the JobSpy bug."""
    fetcher = _three_pages_then_empty()
    await _source(fetcher).fetch_jobs({})
    assert _starts(fetcher.urls) == [0, _PAGE_SIZE, 2 * _PAGE_SIZE, 3 * _PAGE_SIZE]


async def test_start_advances_by_raw_count_when_some_cards_are_skipped() -> None:
    """A 10-raw/5-valid page advances start by 10 (raw), not 5 (parsed)."""
    half = _PAGE_SIZE // 2
    mixed = _page(_ids(0, half)) + _invalid_card() * half
    fetcher = ScriptedFetcher(
        [
            GuestResponse(status=200, text=mixed),
            GuestResponse(status=200, text=""),
        ]
    )
    postings = await _source(fetcher).fetch_jobs({})

    assert len(postings) == half
    assert _starts(fetcher.urls) == [0, _PAGE_SIZE]


async def test_all_skipped_page_warns_and_keeps_paginating() -> None:
    """A non-empty page whose cards all parse-skip does NOT end the URL."""
    fetcher = ScriptedFetcher(
        [
            GuestResponse(status=200, text=_invalid_card() * _PAGE_SIZE),
            GuestResponse(status=200, text=_page(_ids(0, _PAGE_SIZE))),
            GuestResponse(status=200, text=""),
        ]
    )
    logger = RecordingLogger()
    postings = await _source(fetcher, logger=logger).fetch_jobs({})

    assert len(postings) == _PAGE_SIZE
    assert _starts(fetcher.urls) == [0, _PAGE_SIZE, 2 * _PAGE_SIZE]
    [(event, attrs)] = logger.warnings
    assert event == "guest_search_page_all_cards_skipped"
    assert attrs["start"] == 0
    assert attrs["raw"] == _PAGE_SIZE


async def test_duplicate_ids_across_pages_collapse() -> None:
    """Repeated ids on later pages dedupe; start still advances by card count."""
    page_one = _ids(0, _PAGE_SIZE)
    page_two = page_one[:5] + _ids(10, 5)
    fetcher = ScriptedFetcher(
        [
            GuestResponse(status=200, text=_page(page_one)),
            GuestResponse(status=200, text=_page(page_two)),
            GuestResponse(status=200, text=""),
        ]
    )
    postings = await _source(fetcher).fetch_jobs({})

    assert len(postings) == len(set(page_one + page_two))
    assert sorted(p.canonical_id for p in postings) == sorted(set(page_one + page_two))
    assert _starts(fetcher.urls) == [0, _PAGE_SIZE, 2 * _PAGE_SIZE]


async def test_stops_at_max_jobs_and_trims_overshoot() -> None:
    """Pagination halts once unique >= max_jobs; the result trims to it."""
    fetcher = ScriptedFetcher(
        [
            GuestResponse(status=200, text=_page(_ids(0, _PAGE_SIZE))),
            GuestResponse(status=200, text=_page(_ids(10, _PAGE_SIZE))),
        ]
    )
    settings = GuestSourceSettings(search_urls=(_SEARCH_URL,), max_jobs=15)
    postings = await _source(fetcher, settings=settings).fetch_jobs({})

    assert len(postings) == settings.max_jobs
    assert _starts(fetcher.urls) == [0, _PAGE_SIZE]


async def test_never_requests_start_at_or_beyond_1000() -> None:
    """Endless full pages stop at the endpoint's start < 1000 cap."""
    fetcher = EndlessFetcher(page_size=_BIG_PAGE)
    settings = GuestSourceSettings(search_urls=(_SEARCH_URL,), max_jobs=5000)
    postings = await _source(fetcher, settings=settings).fetch_jobs({})

    assert len(postings) == _START_CAP
    assert len(fetcher.urls) == _START_CAP // _BIG_PAGE
    assert max(_starts(fetcher.urls)) < _START_CAP


async def test_mid_stream_429_ends_url_and_keeps_collected() -> None:
    """A 429 page ends that URL's pagination; prior postings survive."""
    fetcher = ScriptedFetcher(
        [
            GuestResponse(status=200, text=_page(_ids(0, _PAGE_SIZE))),
            GuestResponse(status=_HTTP_TOO_MANY, text=""),
        ]
    )
    logger = RecordingLogger()
    postings = await _source(fetcher, logger=logger).fetch_jobs({})

    assert len(postings) == _PAGE_SIZE
    [(event, attrs)] = logger.warnings
    assert event == "guest_search_page_failed"
    assert attrs["status"] == _HTTP_TOO_MANY


async def test_sentinel_status_zero_ends_url_gracefully() -> None:
    """The retry-exhausted (0, "") sentinel ends the URL like any non-200."""
    fetcher = ScriptedFetcher(
        [
            GuestResponse(status=200, text=_page(_ids(0, _PAGE_SIZE))),
            GuestResponse(status=0, text=""),
        ]
    )
    postings = await _source(fetcher).fetch_jobs({})
    assert len(postings) == _PAGE_SIZE


async def test_url_without_keywords_is_skipped_not_fatal() -> None:
    """A keyword-less URL logs a warning and the next URL still runs."""
    no_keywords = "https://www.linkedin.com/jobs/search/?location=USA"
    fetcher = ScriptedFetcher(
        [
            GuestResponse(status=200, text=_page(_ids(0, _PAGE_SIZE))),
            GuestResponse(status=200, text=""),
        ]
    )
    logger = RecordingLogger()
    settings = GuestSourceSettings(search_urls=(no_keywords, _SEARCH_URL))
    postings = await _source(fetcher, settings=settings, logger=logger).fetch_jobs({})

    assert len(postings) == _PAGE_SIZE
    [(event, attrs)] = logger.warnings
    assert event == "guest_search_url_missing_keywords"
    assert attrs["url"] == no_keywords
    assert all("keywords=python" in url for url in fetcher.urls)


async def test_sleep_paces_between_page_fetches_only() -> None:
    """4 fetches -> 3 pacing sleeps: none before the first or after the last."""
    fetcher = _three_pages_then_empty()
    sleeper = RecordingSleeper()
    settings = GuestSourceSettings(search_urls=(_SEARCH_URL,), pacing_s=0.5)
    await _source(fetcher, settings=settings, sleeper=sleeper).fetch_jobs({})

    assert sleeper.delays == [settings.pacing_s] * 3


async def test_empty_first_page_returns_nothing_without_sleep() -> None:
    """An empty first page yields [] and never touches the sleeper."""
    fetcher = ScriptedFetcher([GuestResponse(status=200, text="")])
    sleeper = RecordingSleeper()
    postings = await _source(fetcher, sleeper=sleeper).fetch_jobs({})

    assert postings == []
    assert sleeper.delays == []


async def test_union_and_pacing_span_multiple_urls() -> None:
    """Ids dedupe across URLs and pacing covers every fetch boundary."""
    second_url = "https://www.linkedin.com/jobs/search/?keywords=python+golang"
    overlap = _ids(0, 5) + _ids(10, 5)
    fetcher = ScriptedFetcher(
        [
            GuestResponse(status=200, text=_page(_ids(0, _PAGE_SIZE))),
            GuestResponse(status=200, text=""),
            GuestResponse(status=200, text=_page(overlap)),
            GuestResponse(status=200, text=""),
        ]
    )
    sleeper = RecordingSleeper()
    settings = GuestSourceSettings(search_urls=(_SEARCH_URL, second_url))
    postings = await _source(fetcher, settings=settings, sleeper=sleeper).fetch_jobs({})

    assert len(postings) == len(set(_ids(0, _PAGE_SIZE) + overlap))
    assert len(sleeper.delays) == len(fetcher.urls) - 1


async def test_max_jobs_is_split_evenly_across_search_urls() -> None:
    """One broad first search cannot consume the later search's allocation."""
    second_url = "https://www.linkedin.com/jobs/search/?keywords=rust"
    fetcher = ScriptedFetcher(
        [
            GuestResponse(status=200, text=_page(_ids(0, 10))),
            GuestResponse(status=200, text=_page(_ids(10, 10))),
        ]
    )
    settings = GuestSourceSettings(search_urls=(_SEARCH_URL, second_url), max_jobs=10)
    postings = await _source(fetcher, settings=settings).fetch_jobs({})

    assert len(postings) == settings.max_jobs
    assert [posting.canonical_id for posting in postings] == _ids(0, 5) + _ids(10, 5)
    assert len(fetcher.urls) == _FAIR_SHARE_SEARCH_COUNT
    assert "keywords=rust" in fetcher.urls[1]


async def test_without_fetcher_runs_real_client_over_mock_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No injected fetcher: create_client + the fetch closure scrape a page."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        is_first_page = request.url.params["start"] == "0"
        text = _page(_ids(0, _PAGE_SIZE)) if is_first_page else ""
        return httpx.Response(200, text=text)

    def mock_client(_proxies: str | None, _timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(linkedin_guest, "create_client", mock_client)
    source = LinkedInGuestSource(
        settings=GuestSourceSettings(search_urls=(_SEARCH_URL,)),
        logger=RecordingLogger(),
        sleeper=RecordingSleeper(),
    )
    postings = await source.fetch_jobs({})

    assert len(postings) == _PAGE_SIZE
    assert {p.platform for p in postings} == {"linkedin_guest"}
    assert all("jobs-guest/jobs/api/seeMoreJobPostings" in url for url in requested)
