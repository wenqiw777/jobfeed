"""Unit tests for the LinkedIn guest HTML parsers.

Covers the search-card list fragment (``div.base-search-card`` extraction with
job-id/title/company/location/posted_at), the posting JD fragment
(``show-more-less-html__markup``), and the relative posted-time marker
(``posted-time-ago__text``) used for enrich-time date capture.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from jobfeed.adapters.sources._linkedin_guest_parse import (
    ParsedCard,
    parse_jd,
    parse_posting_posted_at,
    parse_search_cards,
)

_NOW = datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC)
_THREE_CARDS = 3


def _card(
    href: str,
    *,
    title: str = "Senior Engineer",
    company: str = "Acme",
    location: str | None = "SF, CA",
    datetime_attr: str | None = "2026-06-09",
) -> str:
    """Build one guest search-card HTML fragment with overridable parts."""
    title_html = f'<h3 class="base-search-card__title">{title}</h3>' if title else ""
    company_html = (
        f'<h4 class="base-search-card__subtitle"><a>{company}</a></h4>'
        if company
        else ""
    )
    location_html = (
        f'<span class="job-search-card__location">{location}</span>'
        if location is not None
        else ""
    )
    time_html = (
        f'<time class="job-search-card__listdate" datetime="{datetime_attr}"></time>'
        if datetime_attr is not None
        else ""
    )
    return (
        '<div class="base-search-card">'
        f'<a class="base-card__full-link" href="{href}">{title_html}</a>'
        f"{company_html}{location_html}{time_html}"
        "</div>"
    )


def test_parses_three_cards_with_all_fields() -> None:
    """A 3-card fragment yields 3 ParsedCards with correct fields."""
    html = (
        _card(
            "https://www.linkedin.com/jobs/view/senior-engineer-at-acme-4012345678"
            "?refId=x",
        )
        + _card(
            "https://www.linkedin.com/jobs/view/ml-engineer-at-globex-4099999999",
            title="ML Engineer",
            company="Globex",
            location="Remote",
        )
        + _card(
            "https://www.linkedin.com/jobs/view/4111111111",
            title="Data Engineer",
            company="Initech",
            location="NYC",
        )
    )
    cards = parse_search_cards(html)
    assert len(cards) == _THREE_CARDS
    assert cards[0] == ParsedCard(
        job_id="4012345678",
        title="Senior Engineer",
        company="Acme",
        location="SF, CA",
        posted_at=datetime(2026, 6, 9, tzinfo=UTC),
        url="https://www.linkedin.com/jobs/view/4012345678",
    )
    assert cards[1].job_id == "4099999999"
    assert cards[1].company == "Globex"
    assert cards[2].job_id == "4111111111"
    assert cards[2].url == "https://www.linkedin.com/jobs/view/4111111111"


def test_job_id_extracted_from_trailing_slash_href() -> None:
    """A href with a trailing slash after the id still yields the id."""
    html = _card(
        "https://www.linkedin.com/jobs/view/senior-eng-at-acme-4012345678/?refId=x"
    )
    [card] = parse_search_cards(html)
    assert card.job_id == "4012345678"


def test_job_id_is_bare_digits_and_url_is_canonical_view() -> None:
    """job_id has no prefix; url is the canonical /jobs/view/{id} form."""
    html = _card(
        "https://www.linkedin.com/jobs/view/staff-swe-at-acme-4222222222?trk=guest"
    )
    [card] = parse_search_cards(html)
    assert card.job_id == "4222222222"
    assert card.job_id.isdigit()
    assert card.url == "https://www.linkedin.com/jobs/view/4222222222"


def test_skips_card_with_non_numeric_trailing_segment() -> None:
    """A href whose last hyphen segment is not numeric is skipped."""
    html = _card("https://www.linkedin.com/jobs/view/senior-engineer-at-acme") + _card(
        "https://www.linkedin.com/jobs/view/good-role-4333333333"
    )
    cards = parse_search_cards(html)
    assert [c.job_id for c in cards] == ["4333333333"]


def test_skips_card_without_link_or_with_bare_domain_href() -> None:
    """Cards with no full-link anchor or a path-less href are skipped."""
    no_link = '<div class="base-search-card"><h3>Orphan</h3></div>'
    bare = _card("https://www.linkedin.com")
    assert parse_search_cards(no_link + bare) == []


def test_skips_card_missing_title_or_company() -> None:
    """Cards lacking a title or a company are skipped defensively."""
    html = _card("https://www.linkedin.com/jobs/view/a-4444444444", title="") + _card(
        "https://www.linkedin.com/jobs/view/b-4555555555", company=""
    )
    assert parse_search_cards(html) == []


def test_posted_at_date_only_becomes_midnight_utc() -> None:
    """A date-only datetime attr parses to midnight UTC."""
    html = _card(
        "https://www.linkedin.com/jobs/view/x-4666666666",
        datetime_attr="2026-06-09",
    )
    [card] = parse_search_cards(html)
    assert card.posted_at == datetime(2026, 6, 9, 0, 0, tzinfo=UTC)
    assert card.posted_at is not None
    assert card.posted_at.tzinfo is not None


def test_posted_at_offset_aware_datetime_converts_to_utc() -> None:
    """An offset-aware datetime attr is normalized to UTC."""
    html = _card(
        "https://www.linkedin.com/jobs/view/x-4822222222",
        datetime_attr="2026-06-09T10:30:00+02:00",
    )
    [card] = parse_search_cards(html)
    assert card.posted_at == datetime(2026, 6, 9, 8, 30, tzinfo=UTC)


def test_posted_at_missing_or_blank_time_is_none() -> None:
    """Missing time tag or blank/garbage datetime attr yields None."""
    missing = _card(
        "https://www.linkedin.com/jobs/view/x-4777777777", datetime_attr=None
    )
    blank = _card("https://www.linkedin.com/jobs/view/x-4788888888", datetime_attr="")
    garbage = _card(
        "https://www.linkedin.com/jobs/view/x-4799999999", datetime_attr="soon"
    )
    cards = parse_search_cards(missing + blank + garbage)
    assert [c.posted_at for c in cards] == [None, None, None]


def test_no_location_yields_none() -> None:
    """A card without the location span has location=None."""
    html = _card("https://www.linkedin.com/jobs/view/x-4811111111", location=None)
    [card] = parse_search_cards(html)
    assert card.location is None


def test_parse_jd_returns_newline_separated_markup_text() -> None:
    """JD text keeps paragraph/list structure via newline separators."""
    html = (
        '<section><div class="show-more-less-html__markup">'
        "<p>Build <strong>pipelines</strong>.</p><ul><li>Python</li></ul>"
        "</div></section>"
    )
    assert parse_jd(html) == "Build\npipelines\n.\nPython"


def test_parse_jd_without_markup_returns_empty_string() -> None:
    """HTML lacking the markup div yields an empty string."""
    assert parse_jd("<div><p>nothing here</p></div>") == ""


def test_posting_posted_at_days_and_weeks() -> None:
    """'3 days ago' -> now - 3d; '2 weeks ago' -> now - 14d (aware UTC)."""
    days = '<span class="posted-time-ago__text">3 days ago</span>'
    weeks = '<span class="posted-time-ago__text">2 weeks ago</span>'
    assert parse_posting_posted_at(days, now=_NOW) == _NOW - timedelta(days=3)
    assert parse_posting_posted_at(weeks, now=_NOW) == _NOW - timedelta(days=14)


def test_posting_posted_at_minutes_hours_months_and_singular() -> None:
    """Minutes/hours/months units and singular '1 day ago' all parse."""
    cases = {
        "30 minutes ago": _NOW - timedelta(minutes=30),
        "5 hours ago": _NOW - timedelta(hours=5),
        "1 day ago": _NOW - timedelta(days=1),
        "2 months ago": _NOW - timedelta(days=60),
        "Reposted 2 weeks ago": _NOW - timedelta(days=14),
    }
    for text, expected in cases.items():
        html = f'<span class="posted-time-ago__text">{text}</span>'
        assert parse_posting_posted_at(html, now=_NOW) == expected, text


def test_posting_posted_at_seconds_and_just_now() -> None:
    """'45 seconds ago' -> now - 45s; 'Just now'/'Just posted' -> exactly now."""
    cases = {
        "45 seconds ago": _NOW - timedelta(seconds=45),
        "Just now": _NOW,
        "Just posted": _NOW,
    }
    for text, expected in cases.items():
        html = f'<span class="posted-time-ago__text">{text}</span>'
        assert parse_posting_posted_at(html, now=_NOW) == expected, text


def test_posting_posted_at_uses_first_marker() -> None:
    """When the fragment repeats the marker, the first one wins."""
    html = (
        '<span class="posted-time-ago__text">3 days ago</span>'
        '<span class="posted-time-ago__text">2 weeks ago</span>'
    )
    assert parse_posting_posted_at(html, now=_NOW) == _NOW - timedelta(days=3)


def test_posting_posted_at_missing_or_unparseable_is_none() -> None:
    """No marker, or marker text with no relative delta, yields None."""
    assert parse_posting_posted_at("<div>no marker</div>", now=_NOW) is None
    garbage = '<span class="posted-time-ago__text">Posted recently</span>'
    assert parse_posting_posted_at(garbage, now=_NOW) is None
