"""Unit tests for the companies CLI group (Phase 7, Task 5).

Offline coverage: the store is an AsyncMock injected as the Click context
object (invoking the ``companies`` group directly bypasses the root group's
``create_app`` wiring), and the auto-probe is monkeypatched on the command
module. DB-backed add/list/remove flows land in the Task 8 e2e parity suite.
"""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner

from jobfeed.adapters.sources._http import ProbeNetworkError
from jobfeed.cli import cli
from jobfeed.cli.companies import companies
from jobfeed.config import Settings
from jobfeed.domain.models import CompanyRecord

# The package attribute ``jobfeed.cli.companies`` is shadowed by the Click
# group of the same name (cli/__init__.py re-exports it), so resolve the real
# module for monkeypatching via importlib.
_COMPANIES_MODULE = importlib.import_module("jobfeed.cli.companies")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(**overrides: Any) -> AsyncMock:
    """Build a mock ops store with sensible defaults."""
    store = AsyncMock()
    store.upsert_company.return_value = None
    store.list_companies.return_value = overrides.get("list_companies", [])
    store.mark_company_removed.return_value = overrides.get(
        "mark_company_removed", True
    )
    return store


def _make_app(store: AsyncMock) -> dict[str, Any]:
    """Build a minimal AppContext-shaped dict for direct group invocation."""
    return {
        "settings": Settings.model_validate({}),
        "store": store,
        "sources": {},
        "scan_service": MagicMock(),
        "digest_service": MagicMock(),
        "logger": MagicMock(),
        "verbose": False,
    }


def _company(
    slug: str,
    vendor: str | None = "greenhouse",
    failures: int = 0,
) -> CompanyRecord:
    return CompanyRecord(
        slug=slug,
        ats_vendor=vendor,
        consecutive_discover_failures=failures,
    )


def _patch_probe(
    monkeypatch: pytest.MonkeyPatch,
    result: str | None = None,
    error: Exception | None = None,
) -> list[str]:
    """Replace probe_company on the command module; record probed slugs."""
    probed: list[str] = []

    async def _fake_probe(
        client: Any,  # noqa: ARG001 - signature mirrors probe_company
        slug: str,
        *,
        timeout: float = 5.0,  # noqa: ARG001 - signature mirrors probe_company
    ) -> str | None:
        probed.append(slug)
        if error is not None:
            raise error
        return result

    monkeypatch.setattr(_COMPANIES_MODULE, "probe_company", _fake_probe)
    return probed


# ---------------------------------------------------------------------------
# companies add
# ---------------------------------------------------------------------------


class TestCompaniesAdd:
    """Tests for ``companies add``."""

    def test_add_with_ats_writes_pinned_row_without_probe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--ats upserts directly with ats_override=True and never probes."""
        store = _make_store()
        probed = _patch_probe(monkeypatch, result="ashby")

        result = CliRunner().invoke(
            companies, ["add", "acme", "--ats", "greenhouse"], obj=_make_app(store)
        )

        assert result.exit_code == 0, result.output
        assert probed == []  # no network path taken
        store.upsert_company.assert_awaited_once()
        record = store.upsert_company.await_args.args[0]
        assert record.slug == "acme"
        assert record.ats_vendor == "greenhouse"
        assert record.ats_override is True
        assert "acme" in result.output
        assert "greenhouse" in result.output

    def test_add_rejects_unsupported_vendor(self) -> None:
        """--ats only accepts the supported vendor choices."""
        store = _make_store()

        result = CliRunner().invoke(
            companies, ["add", "acme", "--ats", "workday"], obj=_make_app(store)
        )

        assert result.exit_code != 0
        store.upsert_company.assert_not_awaited()

    def test_add_probe_path_writes_detected_vendor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without --ats the probe result is written with ats_override=False."""
        store = _make_store()
        probed = _patch_probe(monkeypatch, result="ashby")

        result = CliRunner().invoke(companies, ["add", "acme"], obj=_make_app(store))

        assert result.exit_code == 0, result.output
        assert probed == ["acme"]
        record = store.upsert_company.await_args.args[0]
        assert record.slug == "acme"
        assert record.ats_vendor == "ashby"
        assert record.ats_override is False
        assert record.last_verified_at is not None
        assert record.last_verified_at.tzinfo is not None
        assert record.last_probe_attempt_at == record.last_verified_at
        assert "ashby" in result.output

    def test_add_unresolved_probe_errors_without_writing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A definitive all-vendor miss exits nonzero and writes no row."""
        store = _make_store()
        _patch_probe(monkeypatch, result=None)

        result = CliRunner().invoke(companies, ["add", "ghost"], obj=_make_app(store))

        assert result.exit_code != 0
        assert "ghost" in result.output
        store.upsert_company.assert_not_awaited()

    def test_add_probe_error_errors_without_writing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A network-level probe failure exits nonzero and writes no row."""
        store = _make_store()
        _patch_probe(
            monkeypatch,
            error=ProbeNetworkError("timeout", slug="acme", vendor="greenhouse"),
        )

        result = CliRunner().invoke(companies, ["add", "acme"], obj=_make_app(store))

        assert result.exit_code != 0
        store.upsert_company.assert_not_awaited()

    def test_readd_removed_slug_restores_via_upsert(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Re-adding a removed slug upserts a fresh row over the sentinel."""
        store = _make_store()
        _patch_probe(monkeypatch, result="lever")

        result = CliRunner().invoke(companies, ["add", "back"], obj=_make_app(store))

        assert result.exit_code == 0, result.output
        record = store.upsert_company.await_args.args[0]
        assert record.ats_vendor == "lever"  # sentinel 'removed' is overwritten


# ---------------------------------------------------------------------------
# companies list
# ---------------------------------------------------------------------------


class TestCompaniesList:
    """Tests for ``companies list``."""

    def test_list_default_hides_removed(self) -> None:
        """Default call passes include_removed=False and prints rows."""
        store = _make_store(
            list_companies=[
                _company("acme", "greenhouse", failures=2),
                _company("globex", "lever"),
            ]
        )

        result = CliRunner().invoke(companies, ["list"], obj=_make_app(store))

        assert result.exit_code == 0, result.output
        store.list_companies.assert_awaited_once_with(
            vendor=None, include_removed=False
        )
        assert "acme" in result.output
        assert "greenhouse" in result.output
        assert "2" in result.output
        assert "globex" in result.output

    def test_list_include_removed_passes_flag(self) -> None:
        """--include-removed surfaces sentinel rows."""
        store = _make_store(list_companies=[_company("gone", "removed")])

        result = CliRunner().invoke(
            companies, ["list", "--include-removed"], obj=_make_app(store)
        )

        assert result.exit_code == 0, result.output
        store.list_companies.assert_awaited_once_with(vendor=None, include_removed=True)
        assert "gone" in result.output
        assert "removed" in result.output

    def test_list_vendor_filter_passes_through(self) -> None:
        """--vendor forwards the filter to the store."""
        store = _make_store(list_companies=[_company("globex", "lever")])

        result = CliRunner().invoke(
            companies, ["list", "--vendor", "lever"], obj=_make_app(store)
        )

        assert result.exit_code == 0, result.output
        store.list_companies.assert_awaited_once_with(
            vendor="lever", include_removed=False
        )

    def test_list_vendor_removed_is_not_a_choice(self) -> None:
        """The 'removed' sentinel is not a valid --vendor value."""
        store = _make_store()

        result = CliRunner().invoke(
            companies, ["list", "--vendor", "removed"], obj=_make_app(store)
        )

        assert result.exit_code != 0
        store.list_companies.assert_not_awaited()

    def test_list_unknown_vendor_shows_unknown_label(self) -> None:
        """A never-probed company (vendor None) prints an explicit label."""
        store = _make_store(list_companies=[_company("fresh", None)])

        result = CliRunner().invoke(companies, ["list"], obj=_make_app(store))

        assert result.exit_code == 0, result.output
        assert "unknown" in result.output

    def test_list_empty_prints_message(self) -> None:
        """No rows prints a friendly message and exits zero."""
        store = _make_store(list_companies=[])

        result = CliRunner().invoke(companies, ["list"], obj=_make_app(store))

        assert result.exit_code == 0, result.output
        assert "No companies" in result.output


# ---------------------------------------------------------------------------
# companies remove
# ---------------------------------------------------------------------------


class TestCompaniesRemove:
    """Tests for ``companies remove``."""

    def test_remove_existing_reports_success(self) -> None:
        """A matched slug echoes confirmation and exits zero."""
        store = _make_store(mark_company_removed=True)

        result = CliRunner().invoke(companies, ["remove", "acme"], obj=_make_app(store))

        assert result.exit_code == 0, result.output
        store.mark_company_removed.assert_awaited_once_with("acme")
        assert "acme" in result.output

    def test_remove_missing_slug_exits_nonzero(self) -> None:
        """An unmatched slug (unknown or already removed) exits nonzero."""
        store = _make_store(mark_company_removed=False)

        result = CliRunner().invoke(companies, ["remove", "nope"], obj=_make_app(store))

        assert result.exit_code != 0
        assert "company not tracked (unknown or already removed): nope" in (
            result.output
        )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestCompaniesRegistration:
    """The group and its subcommands appear in --help."""

    def test_root_help_lists_companies_group(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0, result.output
        assert "companies" in result.output

    def test_companies_help_lists_subcommands(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["companies", "--help"])
        assert result.exit_code == 0, result.output
        for name in ("add", "list", "remove"):
            assert name in result.output

    def test_add_help_lists_sorted_vendor_choices(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["companies", "add", "--help"])
        assert result.exit_code == 0, result.output
        assert "ashby|greenhouse|lever" in result.output
