"""Unit tests for bootstrap aggregator parsing and the bootstrap-companies CLI.

Parser coverage runs on inline fixture markdown (no network, no files). CLI
coverage injects an AsyncMock store as the Click context object and stubs the
HTTP client factory on the command module, mirroring the conventions in
tests/unit/test_cli_companies.py.
"""

from __future__ import annotations

import importlib
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from click.testing import CliRunner

from jobfeed.adapters.sources._bootstrap_aggregators import (
    BOOTSTRAP_SOURCES,
    extract_ats_slugs,
    extract_ats_slugs_with_age,
)
from jobfeed.cli import cli
from jobfeed.cli.bootstrap import bootstrap_companies
from jobfeed.config import Settings
from jobfeed.domain.models import CompanyRecord

# Resolve the command module via importlib (mirrors test_cli_companies.py,
# where the Click group shadows the package attribute of the same name).
_BOOTSTRAP_MODULE = importlib.import_module("jobfeed.cli.bootstrap")

# Generous tripwire for the pathological-input test: the linear row scan
# takes milliseconds; the old quadratic regex took ~15s on the same input.
_ROW_SCAN_BUDGET_SECONDS = 5.0

# ---------------------------------------------------------------------------
# Fixture markdown
# ---------------------------------------------------------------------------

_ALL_VENDORS_DOC = """
# Jobs
- [Acme](https://boards.greenhouse.io/acme/jobs/123)
- [Beta](https://job-boards.greenhouse.io/BetaCorp/jobs/9)
- [Gamma](https://boards.eu.greenhouse.io/gamma)
- [Delta](https://jobs.ashbyhq.com/Delta/some-uuid)
- [Echo](https://jobs.lever.co/echo/posting-1)
- [Acme again](https://boards.greenhouse.io/acme/jobs/456)
- [Widget](https://boards.greenhouse.io/embed/job_app?for=widget)
"""

_ALL_VENDORS_EXPECTED = {
    ("acme", "greenhouse"),
    ("betacorp", "greenhouse"),
    ("gamma", "greenhouse"),
    ("delta", "ashby"),
    ("echo", "lever"),
}

_NO_MATCH_DOC = """
# About greenhouses
Visit https://www.greenhouse.io/customers and https://lever.co/about for
marketing pages; neither carries a board slug.
"""

_AGED_DOC = """
<table>
<tr><th>Company</th><th>Role</th><th>Age</th></tr>
<tr><td><a href="https://boards.greenhouse.io/fresh">F</a></td><td>0d</td></tr>
<tr><td><a href="https://jobs.ashbyhq.com/weekold">W</a></td><td>7d</td></tr>
<tr><td><a href="https://jobs.lever.co/stale">S</a></td><td>12d</td></tr>
<tr><td><a href="https://boards.greenhouse.io/ancient">A</a></td><td>2mo</td></tr>
<tr><td><a href="https://jobs.lever.co/ageless">No age cell</a></td></tr>
</table>
<a href="https://boards.greenhouse.io/outside">Outside any row</a>
"""

# ---------------------------------------------------------------------------
# BOOTSTRAP_SOURCES parity
# ---------------------------------------------------------------------------


def test_bootstrap_sources_lists_the_seven_legacy_aggregators() -> None:
    assert sorted(BOOTSTRAP_SOURCES) == [
        "cvrve-newgrad",
        "pittcsc-summer",
        "simplifyjobs-newgrad",
        "simplifyjobs-summer-internships",
        "speedyapply-swe",
        "vanshb03-newgrad",
        "vanshb03-summer",
    ]
    assert all(
        url.startswith("https://raw.githubusercontent.com/")
        for url in BOOTSTRAP_SOURCES.values()
    )


# ---------------------------------------------------------------------------
# extract_ats_slugs
# ---------------------------------------------------------------------------


class TestExtractAtsSlugs:
    """Vendor URL shapes, lowercase + dedupe, denylist, no-match."""

    def test_extracts_all_three_vendor_url_shapes(self) -> None:
        """Covers boards/job-boards/boards.eu greenhouse, ashby, and lever."""
        assert extract_ats_slugs(_ALL_VENDORS_DOC) == _ALL_VENDORS_EXPECTED

    def test_lowercases_and_dedupes_slugs(self) -> None:
        found = extract_ats_slugs(_ALL_VENDORS_DOC)
        assert ("betacorp", "greenhouse") in found
        assert ("delta", "ashby") in found
        assert len([pair for pair in found if pair[0] == "acme"]) == 1

    def test_greenhouse_embed_infrastructure_path_is_dropped(self) -> None:
        assert ("embed", "greenhouse") not in extract_ats_slugs(_ALL_VENDORS_DOC)

    def test_no_match_document_returns_empty_set(self) -> None:
        assert extract_ats_slugs(_NO_MATCH_DOC) == set()


# ---------------------------------------------------------------------------
# extract_ats_slugs_with_age
# ---------------------------------------------------------------------------


class TestExtractAtsSlugsWithAge:
    """Age-cell parsing, mo->days conversion, missing-age pass-through."""

    def test_max_age_days_excludes_older_rows(self) -> None:
        assert extract_ats_slugs_with_age(_AGED_DOC, 7) == {
            ("fresh", "greenhouse"),
            ("weekold", "ashby"),
            ("ageless", "lever"),
        }

    def test_mo_unit_converts_at_thirty_days_per_month(self) -> None:
        sixty = extract_ats_slugs_with_age(_AGED_DOC, 60)
        fiftynine = extract_ats_slugs_with_age(_AGED_DOC, 59)
        assert ("ancient", "greenhouse") in sixty  # 2mo == 60d
        assert ("ancient", "greenhouse") not in fiftynine

    def test_rows_without_parseable_age_pass_through(self) -> None:
        assert ("ageless", "lever") in extract_ats_slugs_with_age(_AGED_DOC, 0)

    def test_urls_outside_table_rows_are_ignored(self) -> None:
        found = extract_ats_slugs_with_age(_AGED_DOC, 9999)
        assert ("outside", "greenhouse") not in found

    def test_pathological_unclosed_tr_document_parses_in_linear_time(self) -> None:
        """~200KB of unclosed <tr> junk must not blow up the row scan.

        The old whole-document regex (lazy ``(.*?)`` + required ``</tr>``)
        backtracked quadratically on this input (measured ~15s); the linear
        scan must finish in normal unit-test time and still pull the one
        valid row while ignoring everything outside it.
        """
        junk = "<tr>" * 25_000  # ~100KB of unclosed rows per side
        junk_url = '<a href="https://jobs.lever.co/junkco">never in a row</a>'
        valid_row = (
            '<tr><td><a href="https://boards.greenhouse.io/survivor">S</a>'
            "</td><td>1d</td></tr>"
        )
        doc = junk_url + junk + valid_row + junk  # exactly one closed row
        started = time.perf_counter()

        found = extract_ats_slugs_with_age(doc, 7)

        elapsed = time.perf_counter() - started
        assert found == {("survivor", "greenhouse")}
        assert elapsed < _ROW_SCAN_BUDGET_SECONDS, (
            f"row scan took {elapsed:.1f}s on malformed input"
        )


# ---------------------------------------------------------------------------
# CLI: bootstrap-companies
# ---------------------------------------------------------------------------

_CVRVE_DOC = """
| Company | Role |
| [Acme](https://boards.greenhouse.io/acme) | SWE |
| [NewCo](https://jobs.ashbyhq.com/newco) | SWE |
"""

_DUP_VENDOR_DOC = """
| Company | Role |
| [Dup](https://jobs.lever.co/dupco) | SWE |
| [Dup](https://boards.greenhouse.io/dupco) | SWE |
"""


def _make_store(existing: list[CompanyRecord] | None = None) -> AsyncMock:
    store = AsyncMock()
    store.upsert_company.return_value = None
    store.list_companies.return_value = existing or []
    return store


def _make_app(store: AsyncMock) -> dict[str, Any]:
    return {
        "settings": Settings.model_validate({}),
        "store": store,
        "sources": {},
        "scan_service": MagicMock(),
        "digest_service": MagicMock(),
        "logger": MagicMock(),
        "verbose": False,
    }


def _company(slug: str, vendor: str | None = "greenhouse") -> CompanyRecord:
    return CompanyRecord(slug=slug, ats_vendor=vendor)


class _FakeResponse:
    """Minimal httpx.Response stand-in for the fetch path."""

    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        """Fixture responses are always 200."""


def _patch_http(monkeypatch: pytest.MonkeyPatch, bodies: dict[str, str]) -> None:
    """Serve fixture bodies for known source URLs; ConnectError otherwise."""
    url_to_body = {BOOTSTRAP_SOURCES[name]: body for name, body in bodies.items()}

    async def _fake_get(url: str) -> _FakeResponse:
        if url not in url_to_body:
            raise httpx.ConnectError(f"offline test refuses to fetch {url}")
        return _FakeResponse(url_to_body[url])

    client = AsyncMock()
    client.get = _fake_get

    def _fake_factory(timeout: float = 30.0) -> AsyncMock:  # noqa: ARG001
        return client

    monkeypatch.setattr(_BOOTSTRAP_MODULE, "create_http_client", _fake_factory)


class TestBootstrapCompaniesCli:
    """Dry-run vs --apply vs idempotent rerun, error isolation, age filter."""

    def test_dry_run_prints_plan_and_writes_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _make_store([_company("acme")])
        _patch_http(monkeypatch, {"cvrve-newgrad": _CVRVE_DOC})

        result = CliRunner().invoke(
            bootstrap_companies, ["--source", "cvrve-newgrad"], obj=_make_app(store)
        )

        assert result.exit_code == 0, result.output
        assert "would add newco" in result.output
        assert "1 new" in result.output
        assert "1 already tracked" in result.output
        store.upsert_company.assert_not_awaited()
        store.list_companies.assert_awaited_once_with(include_removed=True)

    def test_apply_upserts_new_and_skips_existing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _make_store([_company("acme")])
        _patch_http(monkeypatch, {"cvrve-newgrad": _CVRVE_DOC})

        result = CliRunner().invoke(
            bootstrap_companies,
            ["--source", "cvrve-newgrad", "--apply"],
            obj=_make_app(store),
        )

        assert result.exit_code == 0, result.output
        store.upsert_company.assert_awaited_once()
        record = store.upsert_company.await_args.args[0]
        assert record.slug == "newco"
        assert record.ats_vendor == "ashby"  # vendor comes from the URL pattern
        assert record.ats_override is False
        assert "Added 1" in result.output

    def test_rerun_after_apply_reports_all_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _make_store([_company("acme"), _company("newco", "ashby")])
        _patch_http(monkeypatch, {"cvrve-newgrad": _CVRVE_DOC})

        result = CliRunner().invoke(
            bootstrap_companies,
            ["--source", "cvrve-newgrad", "--apply"],
            obj=_make_app(store),
        )

        assert result.exit_code == 0, result.output
        store.upsert_company.assert_not_awaited()
        assert "Added 0" in result.output
        assert "2 already tracked" in result.output

    def test_same_slug_under_two_vendors_writes_once_deterministically(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Store conflict key is slug alone; first sorted vendor must win."""
        store = _make_store()
        _patch_http(monkeypatch, {"cvrve-newgrad": _DUP_VENDOR_DOC})

        result = CliRunner().invoke(
            bootstrap_companies,
            ["--source", "cvrve-newgrad", "--apply"],
            obj=_make_app(store),
        )

        assert result.exit_code == 0, result.output
        store.upsert_company.assert_awaited_once()
        record = store.upsert_company.await_args.args[0]
        assert record.slug == "dupco"
        assert record.ats_vendor == "greenhouse"  # alphabetically before lever
        assert "Added 1 new, 0 already tracked" in result.output

    def test_removed_companies_are_not_readded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Removal is a user decision bootstrap must not override."""
        store = _make_store([_company("acme"), _company("newco", "removed")])
        _patch_http(monkeypatch, {"cvrve-newgrad": _CVRVE_DOC})

        result = CliRunner().invoke(
            bootstrap_companies,
            ["--source", "cvrve-newgrad", "--apply"],
            obj=_make_app(store),
        )

        assert result.exit_code == 0, result.output
        store.upsert_company.assert_not_awaited()

    def test_unknown_source_lists_valid_names(self) -> None:
        store = _make_store()

        result = CliRunner().invoke(
            bootstrap_companies, ["--source", "bogus"], obj=_make_app(store)
        )

        assert result.exit_code != 0
        assert "cvrve-newgrad" in result.output
        assert "simplifyjobs-newgrad" in result.output
        store.list_companies.assert_not_awaited()

    def test_partial_fetch_failure_warns_and_continues(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _make_store([_company("acme")])
        _patch_http(monkeypatch, {"cvrve-newgrad": _CVRVE_DOC})  # other 6 fail

        result = CliRunner().invoke(bootstrap_companies, [], obj=_make_app(store))

        assert result.exit_code == 0, result.output
        assert "warning: fetch failed for simplifyjobs-newgrad" in result.output
        assert "would add newco" in result.output

    def test_all_sources_failed_exits_nonzero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _make_store()
        _patch_http(monkeypatch, {})

        result = CliRunner().invoke(bootstrap_companies, [], obj=_make_app(store))

        assert result.exit_code != 0
        assert "failed" in result.output
        store.upsert_company.assert_not_awaited()

    def test_max_age_days_filters_old_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _make_store()
        _patch_http(monkeypatch, {"cvrve-newgrad": _AGED_DOC})

        result = CliRunner().invoke(
            bootstrap_companies,
            ["--source", "cvrve-newgrad", "--max-age-days", "7"],
            obj=_make_app(store),
        )

        assert result.exit_code == 0, result.output
        assert "would add fresh" in result.output
        assert "would add ageless" in result.output  # no age cell passes through
        assert "stale" not in result.output
        assert "ancient" not in result.output

    def test_max_age_days_rejects_negative(self) -> None:
        """A negative --max-age-days fails fast via Click's IntRange check."""
        store = _make_store()

        result = CliRunner().invoke(
            bootstrap_companies, ["--max-age-days", "-1"], obj=_make_app(store)
        )

        assert result.exit_code != 0
        assert "--max-age-days" in result.output
        assert "range" in result.output
        store.list_companies.assert_not_awaited()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestBootstrapRegistration:
    """The command is registered top-level (legacy parity)."""

    def test_root_help_lists_bootstrap_companies(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0, result.output
        assert "bootstrap-companies" in result.output

    def test_bootstrap_help_lists_options(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["bootstrap-companies", "--help"])
        assert result.exit_code == 0, result.output
        for option in ("--source", "--apply", "--max-age-days"):
            assert option in result.output
