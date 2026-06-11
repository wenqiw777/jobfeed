"""Unit tests for the enrich CLI commands (enrich-paste, enrich-linkedin-guest).

Offline coverage: the store is an AsyncMock injected as the Click context
object. DB-backed paste flows land in the Task 8 e2e parity suite; the
guest command's service is stubbed so no network or pacing sleeps run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner

from jobfeed.adapters.sources.linkedin_guest import LinkedInGuestEnricher
from jobfeed.cli import cli
from jobfeed.cli import enrich as enrich_module
from jobfeed.cli.enrich import enrich_linkedin_guest, enrich_paste
from jobfeed.config import Settings
from jobfeed.services.enrich import EnrichSummary

_JD_TEXT = "Senior Backend Engineer. " * 50  # > 1000 chars -> FULL band

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(**overrides: Any) -> AsyncMock:
    store = AsyncMock()
    store.enrich_paste.return_value = overrides.get("enrich_paste", "77")
    return store


def _make_app(store: AsyncMock, settings: Settings | None = None) -> dict[str, Any]:
    return {
        "settings": settings or Settings.model_validate({}),
        "store": store,
        "sources": {},
        "scan_service": MagicMock(),
        "digest_service": MagicMock(),
        "logger": MagicMock(),
        "verbose": False,
    }


# ---------------------------------------------------------------------------
# enrich-paste
# ---------------------------------------------------------------------------


class TestEnrichPaste:
    """Tests for ``enrich-paste``."""

    def test_stdin_paste_defaults_to_linkedin(self) -> None:
        """JD text from stdin is stored under the default linkedin platform."""
        store = _make_store()

        result = CliRunner().invoke(
            enrich_paste, ["abc123"], input=_JD_TEXT, obj=_make_app(store)
        )

        assert result.exit_code == 0, result.output
        store.enrich_paste.assert_awaited_once_with(
            platform="linkedin",
            canonical_id="abc123",
            jd_text=_JD_TEXT,
        )
        assert "77" in result.output
        assert "full" in result.output

    def test_from_file_reads_path_not_stdin(self, tmp_path: Path) -> None:
        """--from-file reads the JD from the file."""
        jd_file = tmp_path / "jd.txt"
        jd_file.write_text(_JD_TEXT, encoding="utf-8")
        store = _make_store()

        result = CliRunner().invoke(
            enrich_paste,
            ["abc123", "--from-file", str(jd_file)],
            obj=_make_app(store),
        )

        assert result.exit_code == 0, result.output
        kwargs = store.enrich_paste.await_args.kwargs
        assert kwargs["jd_text"] == _JD_TEXT

    def test_platform_indeed_passes_through(self) -> None:
        """--platform indeed is forwarded to the store."""
        store = _make_store()

        result = CliRunner().invoke(
            enrich_paste,
            ["xyz", "--platform", "indeed"],
            input=_JD_TEXT,
            obj=_make_app(store),
        )

        assert result.exit_code == 0, result.output
        assert store.enrich_paste.await_args.kwargs["platform"] == "indeed"

    def test_platform_linkedin_guest_passes_through(self) -> None:
        """--platform linkedin_guest is forwarded to the store."""
        store = _make_store()

        result = CliRunner().invoke(
            enrich_paste,
            ["xyz", "--platform", "linkedin_guest"],
            input=_JD_TEXT,
            obj=_make_app(store),
        )

        assert result.exit_code == 0, result.output
        assert store.enrich_paste.await_args.kwargs["platform"] == "linkedin_guest"

    def test_unsupported_platform_rejected(self) -> None:
        """--platform only accepts linkedin, linkedin_guest, and indeed."""
        store = _make_store()

        result = CliRunner().invoke(
            enrich_paste,
            ["xyz", "--platform", "monster"],
            input=_JD_TEXT,
            obj=_make_app(store),
        )

        assert result.exit_code != 0
        store.enrich_paste.assert_not_awaited()

    def test_empty_stdin_is_usage_error(self) -> None:
        """Whitespace-only JD text exits nonzero without touching the store."""
        store = _make_store()

        result = CliRunner().invoke(
            enrich_paste, ["abc123"], input="  \n\t ", obj=_make_app(store)
        )

        assert result.exit_code != 0
        assert "empty" in result.output.lower()
        store.enrich_paste.assert_not_awaited()

    def test_unknown_canonical_id_exits_nonzero(self) -> None:
        """The store's ValueError surfaces as a clean nonzero exit."""
        store = _make_store()
        store.enrich_paste.side_effect = ValueError("job not found: linkedin/missing")

        result = CliRunner().invoke(
            enrich_paste, ["missing"], input=_JD_TEXT, obj=_make_app(store)
        )

        assert result.exit_code != 0
        assert "job not found" in result.output
        assert "Traceback" not in result.output

    def test_registered_on_root_cli(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0, result.output
        assert "enrich-paste" in result.output


# ---------------------------------------------------------------------------
# enrich-linkedin-guest
# ---------------------------------------------------------------------------


# Non-default knob values asserted to flow config -> service wiring.
_GUEST_PACING_S = 2.5
_GUEST_BATCH_LIMIT = 7
_GUEST_PROXY = "http://proxy.example.test:8080"
_GUEST_TIMEOUT_S = 20.0


def _guest_settings(**overrides: Any) -> Settings:
    """Settings with an enabled linkedin_guest source (plus overrides)."""
    config: dict[str, Any] = {
        "enabled": True,
        "search_urls": ["https://www.linkedin.com/jobs/search/?keywords=swe"],
        **overrides,
    }
    return Settings.model_validate({"sources": {"linkedin_guest": config}})


def _summary(**overrides: Any) -> EnrichSummary:
    counters: dict[str, Any] = {
        "enriched": 3,
        "closed": 1,
        "blocked": 2,
        "skipped": 4,
        "stopped_early": False,
    }
    counters.update(overrides)
    return EnrichSummary(**counters)


class _FakeClient:
    """Minimal async-context stand-in for the guest httpx client."""

    def __init__(self) -> None:
        self.closed = False

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self.closed = True


class TestEnrichLinkedInGuest:
    """Tests for ``enrich-linkedin-guest`` (service stubbed, no network)."""

    def _stub_wiring(
        self,
        monkeypatch: pytest.MonkeyPatch,
        summary: EnrichSummary,
        run_error: Exception | None = None,
    ) -> tuple[list[_FakeClient], list[tuple[Any, Any]], list[Any]]:
        """Stub client construction + EnrichService; record both."""
        clients: list[_FakeClient] = []
        client_args: list[tuple[Any, Any]] = []
        services: list[Any] = []

        def fake_create_client(proxies: str | None, timeout: float) -> _FakeClient:
            client_args.append((proxies, timeout))
            client = _FakeClient()
            clients.append(client)
            return client

        class FakeService:
            def __init__(self, **kwargs: Any) -> None:
                self.kwargs = kwargs
                self.run_calls: list[dict[str, Any]] = []
                services.append(self)

            async def run(self, *, platform: str, batch_limit: int) -> EnrichSummary:
                self.run_calls.append(
                    {"platform": platform, "batch_limit": batch_limit}
                )
                if run_error is not None:
                    raise run_error
                return summary

        monkeypatch.setattr(enrich_module, "create_client", fake_create_client)
        monkeypatch.setattr(enrich_module, "EnrichService", FakeService)
        return clients, client_args, services

    def test_runs_service_and_prints_summary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The command wires config -> enricher/service and prints counters."""
        clients, client_args, services = self._stub_wiring(monkeypatch, _summary())
        store = _make_store()
        settings = _guest_settings(
            pacing_s=_GUEST_PACING_S,
            enrich_batch_limit=_GUEST_BATCH_LIMIT,
            proxies=_GUEST_PROXY,
            timeout_s=_GUEST_TIMEOUT_S,
        )

        result = CliRunner().invoke(
            enrich_linkedin_guest, [], obj=_make_app(store, settings)
        )

        assert result.exit_code == 0, result.output
        (service,) = services
        assert service.run_calls == [
            {"platform": "linkedin_guest", "batch_limit": _GUEST_BATCH_LIMIT}
        ]
        assert isinstance(service.kwargs["enricher"], LinkedInGuestEnricher)
        assert service.kwargs["store"] is store
        assert service.kwargs["pacing"].min_interval_s == _GUEST_PACING_S
        assert client_args == [(_GUEST_PROXY, _GUEST_TIMEOUT_S)]
        assert clients[0].closed
        assert "Enriched 3" in result.output
        assert "closed 1" in result.output
        assert "blocked 2" in result.output
        assert "skipped 4" in result.output
        assert "stopped early" not in result.output

    def test_stopped_early_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A rate-limited early stop is called out in the summary line."""
        self._stub_wiring(monkeypatch, _summary(stopped_early=True))
        store = _make_store()

        result = CliRunner().invoke(
            enrich_linkedin_guest, [], obj=_make_app(store, _guest_settings())
        )

        assert result.exit_code == 0, result.output
        assert "stopped early" in result.output

    def test_service_error_still_closes_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A service failure exits nonzero and the httpx client is closed."""
        clients, _, _ = self._stub_wiring(
            monkeypatch, _summary(), run_error=RuntimeError("boom")
        )
        store = _make_store()

        result = CliRunner().invoke(
            enrich_linkedin_guest, [], obj=_make_app(store, _guest_settings())
        )

        assert result.exit_code == 1
        assert clients[0].closed
        assert "Error:" in result.output

    def test_disabled_config_is_click_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A disabled linkedin_guest config fails before touching the store."""
        _, _, services = self._stub_wiring(monkeypatch, _summary())
        store = _make_store()

        result = CliRunner().invoke(enrich_linkedin_guest, [], obj=_make_app(store))

        assert result.exit_code == 1
        assert "linkedin-guest source is disabled in config" in result.output
        assert services == []
        store.connect.assert_not_awaited()

    def test_registered_on_root_cli(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0, result.output
        assert "enrich-linkedin-guest" in result.output
