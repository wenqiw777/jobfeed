"""Unit tests for the scan CLI's default post-scan LinkedIn-guest enrich pass.

Offline coverage: source building, the scan service, and the guest enrich
pass are all stubbed — these tests pin the wiring (when the pass runs, which
config it gets, how failures are contained), not the enrich behavior itself.
"""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner

from jobfeed.config import Settings
from jobfeed.services.enrich import EnrichSummary
from jobfeed.services.runs import start_pipeline_run

# ``jobfeed.cli/__init__`` rebinds the package attribute ``scan`` to the Click
# command (``from jobfeed.cli.scan import scan``), so the MODULE must be
# resolved explicitly for monkeypatching.
scan_module = importlib.import_module("jobfeed.cli.scan")
scan = scan_module.scan

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _guest_settings(**overrides: Any) -> Settings:
    """Settings with an enabled linkedin_guest source (plus overrides)."""
    config: dict[str, Any] = {
        "enabled": True,
        "search_urls": ["https://www.linkedin.com/jobs/search/?keywords=swe"],
        **overrides,
    }
    return Settings.model_validate({"sources": {"linkedin_guest": config}})


def _make_app(settings: Settings | None = None) -> dict[str, Any]:
    scan_service = MagicMock()
    scan_service.run = AsyncMock(return_value=start_pipeline_run("test"))
    return {
        "settings": settings or Settings.model_validate({}),
        "store": AsyncMock(),
        "sources": {},
        "scan_service": scan_service,
        "digest_service": MagicMock(),
        "logger": MagicMock(),
        "verbose": False,
    }


def _summary() -> EnrichSummary:
    return EnrichSummary(
        enriched=3, closed=1, blocked=0, skipped=2, stopped_early=False
    )


def _stub_sources(monkeypatch: pytest.MonkeyPatch, source_names: list[str]) -> None:
    """Make build_scan_sources return one stub spec per requested name."""

    async def fake_build(*args: Any) -> list[Any]:  # noqa: ARG001
        return [(name, MagicMock(), {}) for name in source_names]

    monkeypatch.setattr(scan_module, "build_scan_sources", fake_build)


def _stub_enrich_pass(
    monkeypatch: pytest.MonkeyPatch,
    summary: EnrichSummary,
    error: Exception | None = None,
) -> list[Any]:
    """Stub run_guest_enrich_pass, recording the config of each call."""
    calls: list[Any] = []

    async def fake_pass(app: Any, config: Any) -> EnrichSummary:  # noqa: ARG001
        calls.append(config)
        if error is not None:
            raise error
        return summary

    monkeypatch.setattr(scan_module, "run_guest_enrich_pass", fake_pass)
    return calls


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestScanGuestEnrichDefault:
    """The post-scan guest enrich pass and its config flag."""

    def test_guest_scan_runs_enrich_pass_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Scanning linkedin_guest triggers one enrich pass with its config."""
        _stub_sources(monkeypatch, ["linkedin_guest"])
        calls = _stub_enrich_pass(monkeypatch, _summary())
        settings = _guest_settings()

        result = CliRunner().invoke(scan, [], obj=_make_app(settings))

        assert result.exit_code == 0, result.output
        assert calls == [settings.sources.linkedin_guest]
        assert "LinkedIn guest enrich: Enriched 3" in result.output

    def test_flag_off_skips_enrich_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """enrich_after_scan = false keeps scan discover-only."""
        _stub_sources(monkeypatch, ["linkedin_guest"])
        calls = _stub_enrich_pass(monkeypatch, _summary())
        settings = _guest_settings(enrich_after_scan=False)

        result = CliRunner().invoke(scan, [], obj=_make_app(settings))

        assert result.exit_code == 0, result.output
        assert calls == []
        assert "LinkedIn guest enrich" not in result.output

    def test_non_guest_scan_never_enriches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A scan without the guest source never builds the enrich pass."""
        _stub_sources(monkeypatch, ["ats", "indeed"])
        calls = _stub_enrich_pass(monkeypatch, _summary())

        result = CliRunner().invoke(scan, [], obj=_make_app(_guest_settings()))

        assert result.exit_code == 0, result.output
        assert calls == []
        assert "LinkedIn guest enrich" not in result.output

    def test_enrich_failure_is_contained(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An enrich crash is logged; scan counters still print, exit 0."""
        _stub_sources(monkeypatch, ["linkedin_guest"])
        _stub_enrich_pass(monkeypatch, _summary(), error=RuntimeError("boom"))
        app = _make_app(_guest_settings())

        result = CliRunner().invoke(scan, [], obj=app)

        assert result.exit_code == 0, result.output
        assert "Discovered" in result.output
        assert "LinkedIn guest enrich" not in result.output
        app["logger"].error.assert_called_once()
