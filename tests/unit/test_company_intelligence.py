"""Company intelligence source parsing, merging, and safe refresh behavior."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from jobfeed.company_intelligence import (
    COMPANY_INTELLIGENCE_SOURCES,
    CompanyIntelligenceCache,
    CompanyIntelligenceSync,
    build_company_index,
    parse_ai_watchlist,
    parse_sec_companies,
    parse_startup_portfolios,
    parse_yc_companies,
)

_STRIPE_TEAM_SIZE = 7000


def test_real_sources_parse_without_trusting_funding_claims() -> None:
    sec = parse_sec_companies(
        {"0": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"}}
    )
    yc = parse_yc_companies(
        [
            {
                "name": "Stripe",
                "website": "https://stripe.com",
                "top_company": True,
                "isHiring": True,
                "team_size": 7000,
                "batch": "S09",
                "status": "Active",
            }
        ]
    )
    portfolios = parse_startup_portfolios(
        [
            {
                "name": "Zipline",
                "website": "https://flyzipline.com",
                "source": "techstars",
                "program": "Techstars Seattle Accelerator",
                "isUnicorn": True,
                "isExit": False,
            }
        ]
    )
    watchlist = parse_ai_watchlist(
        "| 1 | [Braintrust](https://braintrust.dev) | AI developer tools | "
        "$80M Series B |"
    )

    assert sec[0].public_company is True
    assert yc[0].yc_top_company is True
    assert yc[0].team_size == _STRIPE_TEAM_SIZE
    assert portfolios[0].claimed_unicorn is True
    assert portfolios[0].accelerator == "Techstars Seattle Accelerator"
    assert watchlist[0].watchlist_only is True
    assert not hasattr(watchlist[0], "funding")


def test_index_merges_by_domain_and_never_uses_substring_name_matching() -> None:
    records = [
        *parse_yc_companies(
            [{"name": "Ramp", "website": "https://ramp.com", "batch": "S15"}]
        ),
        *parse_ai_watchlist(
            "| 1 | [Ramp](https://www.ramp.com) | Fintech AI | claimed funding |\n"
            "| 2 | [Ramp Health](https://ramphealth.com) | Health | seed |"
        ),
    ]

    index = build_company_index(records)

    ramp = index.lookup("Ramp")
    assert ramp is not None
    assert ramp.domain == "ramp.com"
    assert ramp.sources == ("ai_startups_hiring", "yc")
    assert index.lookup("Ramp Health") is not None
    assert index.lookup("Ramp Healthcare") is None
    assert index.lookup("") is None


def test_same_name_with_different_domains_stays_ambiguous() -> None:
    records = parse_startup_portfolios(
        [
            {"name": "Archer", "website": "https://archermoney.com"},
            {"name": "Archer", "website": "https://archer.com"},
        ]
    )

    index = build_company_index(records)

    assert index.lookup("Archer") is None
    assert index.lookup("Archer", website="https://archer.com") is not None


def test_public_company_brand_names_resolve_to_sec_legal_names() -> None:
    index = build_company_index(
        parse_sec_companies(
            {
                "0": {"cik_str": 1652044, "ticker": "GOOG", "title": "Alphabet Inc."},
                "1": {
                    "cik_str": 1321655,
                    "ticker": "PLTR",
                    "title": "Palantir Technologies Inc.",
                },
            }
        )
    )

    assert index.lookup("Google").public_company is True  # type: ignore[union-attr]
    assert index.lookup("Palantir").public_company is True  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_failed_source_refresh_keeps_last_successful_records(tmp_path) -> None:
    cache = CompanyIntelligenceCache(tmp_path / "company-intelligence.json")
    cache.write_source_records(
        {
            "yc": parse_yc_companies(
                [{"name": "Existing YC", "website": "https://existing.test"}]
            )
        },
        fetched_at=datetime(2026, 9, 1, tzinfo=UTC),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(COMPANY_INTELLIGENCE_SOURCES["yc"]):
            return httpx.Response(503)
        if request.url == httpx.URL(COMPANY_INTELLIGENCE_SOURCES["startup_portfolios"]):
            return httpx.Response(
                200,
                json=[
                    {
                        "name": "New Portfolio Co",
                        "website": "https://new.test",
                        "source": "antler",
                    }
                ],
            )
        return httpx.Response(200, text="")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await CompanyIntelligenceSync(cache, client=client).sync()

    snapshot = json.loads(cache.path.read_text())
    names = {
        row["name"]
        for source_rows in snapshot["source_records"].values()
        for row in source_rows
    }
    assert names == {"Existing YC", "New Portfolio Co"}
    assert report.sources["yc"].status == "stale"
    assert report.sources["startup_portfolios"].status == "fresh"
