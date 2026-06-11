"""Unit tests for the LinkedIn guest JD enricher (JobEnricher port).

Covers the full outcome classification: 200 with a usable JD (quality from
``assess_quality``, posted-at backdated from the ``posted-time-ago__text``
marker), 429/999 rate-limit blocks, definitive 404/410 gone postings, the
too-short/at-threshold JD error path, non-200 statuses including the
retry-exhausted ``(0, "")`` sentinel (an error, never a block), and bare-id
derivation from the canonical id (legacy ``li-`` prefixes stripped, digitless
ids rejected without a fetch).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jobfeed.adapters.sources._linkedin_guest_http import GuestResponse
from jobfeed.adapters.sources.linkedin_guest import (
    MIN_JD_CHARS,
    LinkedInGuestEnricher,
)
from jobfeed.domain.models import QualityBand
from jobfeed.domain.quality import assess_quality
from jobfeed.ports.enrich import JobEnricher

_NOW = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
_JOB_ID = "4012345678"
_POSTING_PATH = f"/jobPosting/{_JOB_ID}"

_HTTP_OK = 200
_SHORT_JD_LEN = 42
_TWO_WEEKS_DAYS = 14

_LONG_JD_TEXT = (
    "Design, build, and operate distributed ingestion pipelines. " * 6
).strip()
_SHORT_JD_TEXT = "x" * _SHORT_JD_LEN
_THRESHOLD_JD_TEXT = "x" * MIN_JD_CHARS

_POSTED_MARKER = '<span class="posted-time-ago__text">2 weeks ago</span>'


def _posting_html(jd_text: str, *, marker: str = "") -> str:
    """Build a guest posting fragment with a JD body and optional marker."""
    return (
        f"<section>{marker}"
        f'<div class="show-more-less-html__markup"><p>{jd_text}</p></div>'
        "</section>"
    )


class ScriptedFetcher:
    """Fake fetcher returning queued responses and recording request URLs."""

    def __init__(self, responses: list[GuestResponse]) -> None:
        self._responses = list(responses)
        self.urls: list[str] = []

    async def __call__(self, url: str) -> GuestResponse:
        """Record the URL and pop the next scripted response."""
        self.urls.append(url)
        return self._responses.pop(0)


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


def _enricher(
    fetcher: ScriptedFetcher,
    *,
    logger: RecordingLogger | None = None,
) -> LinkedInGuestEnricher:
    """Build an enricher with a fake fetcher and a frozen clock."""
    return LinkedInGuestEnricher(
        fetcher=fetcher,
        logger=logger or RecordingLogger(),
        now=lambda: _NOW,
    )


def _ok(html: str) -> ScriptedFetcher:
    """Fetcher answering one 200 with the given posting fragment."""
    return ScriptedFetcher([GuestResponse(status=_HTTP_OK, text=html)])


def test_enricher_satisfies_job_enricher_protocol() -> None:
    """LinkedInGuestEnricher is a JobEnricher."""
    assert isinstance(_enricher(ScriptedFetcher([])), JobEnricher)


async def test_200_with_full_jd_enriches() -> None:
    """200 + a JD above the threshold yields a populated EnrichResult."""
    fetcher = _ok(_posting_html(_LONG_JD_TEXT))
    outcome = await _enricher(fetcher).enrich(canonical_id=_JOB_ID, url="unused")

    assert outcome.result is not None
    assert outcome.result.jd_text == _LONG_JD_TEXT
    assert outcome.result.quality == assess_quality(_LONG_JD_TEXT)
    assert outcome.result.quality == QualityBand.PARTIAL
    assert outcome.result.enrich_source == "linkedin_guest"
    assert outcome.is_blocked is False
    assert outcome.is_gone is False
    assert outcome.error is None


async def test_posted_marker_backdates_posted_at_from_injected_now() -> None:
    """A "2 weeks ago" marker yields posted_at == now - 14 days."""
    fetcher = _ok(_posting_html(_LONG_JD_TEXT, marker=_POSTED_MARKER))
    outcome = await _enricher(fetcher).enrich(canonical_id=_JOB_ID, url="unused")

    assert outcome.result is not None
    assert outcome.result.posted_at == _NOW - timedelta(days=_TWO_WEEKS_DAYS)


async def test_missing_posted_marker_yields_none_posted_at() -> None:
    """No marker: posted_at stays None but the JD is still enriched."""
    fetcher = _ok(_posting_html(_LONG_JD_TEXT))
    outcome = await _enricher(fetcher).enrich(canonical_id=_JOB_ID, url="unused")

    assert outcome.result is not None
    assert outcome.result.posted_at is None
    assert outcome.result.jd_text == _LONG_JD_TEXT


@pytest.mark.parametrize("status", [429, 999])
async def test_rate_limit_statuses_are_blocked(status: int) -> None:
    """429 and LinkedIn's 999 mean the IP is rate-limited: back off."""
    logger = RecordingLogger()
    fetcher = ScriptedFetcher([GuestResponse(status=status, text="")])
    outcome = await _enricher(fetcher, logger=logger).enrich(
        canonical_id=_JOB_ID, url="unused"
    )

    assert outcome.is_blocked is True
    assert outcome.result is None
    assert outcome.is_gone is False
    assert outcome.error is None
    assert logger.warnings == []


@pytest.mark.parametrize("status", [404, 410])
async def test_definitive_missing_statuses_are_gone(status: int) -> None:
    """404 and 410 mean the posting is removed: mark it closed."""
    logger = RecordingLogger()
    fetcher = ScriptedFetcher([GuestResponse(status=status, text="")])
    outcome = await _enricher(fetcher, logger=logger).enrich(
        canonical_id=_JOB_ID, url="unused"
    )

    assert outcome.is_gone is True
    assert outcome.result is None
    assert outcome.is_blocked is False
    assert outcome.error is None
    assert logger.warnings == []


async def test_200_with_short_jd_is_an_error() -> None:
    """A parseable-but-short JD yields an informative error, no result."""
    logger = RecordingLogger()
    fetcher = _ok(_posting_html(_SHORT_JD_TEXT))
    outcome = await _enricher(fetcher, logger=logger).enrich(
        canonical_id=_JOB_ID, url="unused"
    )

    assert outcome.result is None
    assert outcome.error == f"jd_too_short:len={_SHORT_JD_LEN}"
    assert outcome.is_blocked is False
    assert outcome.is_gone is False
    [(event, attrs)] = logger.warnings
    assert event == "guest_enrich_jd_too_short"
    assert attrs["canonical_id"] == _JOB_ID


async def test_200_with_jd_exactly_at_threshold_is_an_error() -> None:
    """The threshold is strict: a JD of exactly MIN_JD_CHARS is too short."""
    fetcher = _ok(_posting_html(_THRESHOLD_JD_TEXT))
    outcome = await _enricher(fetcher).enrich(canonical_id=_JOB_ID, url="unused")

    assert outcome.result is None
    assert outcome.error == f"jd_too_short:len={MIN_JD_CHARS}"


async def test_200_with_jd_one_char_above_threshold_enriches() -> None:
    """A JD of MIN_JD_CHARS + 1 chars clears the strict threshold."""
    jd_text = "x" * (MIN_JD_CHARS + 1)
    fetcher = _ok(_posting_html(jd_text))
    outcome = await _enricher(fetcher).enrich(canonical_id=_JOB_ID, url="unused")

    assert outcome.result is not None
    assert outcome.result.jd_text == jd_text
    assert outcome.error is None


async def test_200_without_jd_markup_is_an_error() -> None:
    """A 200 body missing the JD markup div parses to "" and errors."""
    fetcher = _ok("<section><p>authwall teaser</p></section>")
    outcome = await _enricher(fetcher).enrich(canonical_id=_JOB_ID, url="unused")

    assert outcome.result is None
    assert outcome.error == "jd_too_short:len=0"


async def test_other_status_is_an_error() -> None:
    """An unexpected status (e.g. 503) is an error, not blocked/gone."""
    fetcher = ScriptedFetcher([GuestResponse(status=503, text="")])
    outcome = await _enricher(fetcher).enrich(canonical_id=_JOB_ID, url="unused")

    assert outcome.result is None
    assert outcome.error == "http_status:503"
    assert outcome.is_blocked is False
    assert outcome.is_gone is False


async def test_sentinel_status_zero_is_an_error_not_a_block() -> None:
    """The retry-exhausted (0, "") sentinel is a transport error, no backoff."""
    fetcher = ScriptedFetcher([GuestResponse(status=0, text="")])
    outcome = await _enricher(fetcher).enrich(canonical_id=_JOB_ID, url="unused")

    assert outcome.result is None
    assert outcome.error == "http_status:0"
    assert outcome.is_blocked is False


async def test_request_url_uses_the_bare_numeric_id() -> None:
    """The posting GET targets jobPosting/{bare_id} with no li- prefix."""
    fetcher = _ok(_posting_html(_LONG_JD_TEXT))
    await _enricher(fetcher).enrich(canonical_id=_JOB_ID, url="unused")

    [requested] = fetcher.urls
    assert requested.endswith(_POSTING_PATH)
    assert "li-" not in requested


async def test_legacy_li_prefixed_canonical_id_is_stripped() -> None:
    """A legacy li-{id} canonical id still requests the bare numeric id."""
    fetcher = _ok(_posting_html(_LONG_JD_TEXT))
    outcome = await _enricher(fetcher).enrich(
        canonical_id=f"li-{_JOB_ID}", url="unused"
    )

    [requested] = fetcher.urls
    assert requested.endswith(_POSTING_PATH)
    assert "li-" not in requested
    assert outcome.result is not None


async def test_canonical_id_without_digits_errors_without_fetching() -> None:
    """A digitless canonical id is rejected up front; nothing is fetched."""
    fetcher = ScriptedFetcher([])
    outcome = await _enricher(fetcher).enrich(canonical_id="li-", url="unused")

    assert outcome.result is None
    assert outcome.error == "invalid_canonical_id:li-"
    assert outcome.is_blocked is False
    assert outcome.is_gone is False
    assert fetcher.urls == []
