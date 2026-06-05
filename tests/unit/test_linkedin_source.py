"""Unit tests for the LinkedIn Playwright SessionSource facade."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

import jobfeed.adapters.sources.linkedin as linkedin_module
from jobfeed.adapters.sources.linkedin import LinkedInSource
from jobfeed.config import SourcesLinkedInConfig
from jobfeed.ports.source import SessionSource


class RecordingLogger:
    """Small logger double for source construction tests."""

    def info(self, _event: str, **_kwargs: object) -> None:
        """Accept info logs."""

    def error(self, _event: str, **_kwargs: object) -> None:
        """Accept error logs."""


async def no_sleep(_seconds: float) -> None:
    """Test sleeper that does not wait."""


class _FakePage:
    """Page stand-in for the persistent context."""


class _FakeContext:
    async def new_page(self) -> _FakePage:
        return _FakePage()


def _source(tmp_path: Path) -> LinkedInSource:
    config = SourcesLinkedInConfig(
        profile_dir=str(tmp_path / "profile"),
        lock_path=str(tmp_path / "enrich.lock"),
    )
    return LinkedInSource(config=config, logger=RecordingLogger(), sleeper=no_sleep)


def test_linkedin_source_satisfies_session_source_protocol(tmp_path: Path) -> None:
    """LinkedInSource should be a SessionSource without importing Playwright."""
    source = _source(tmp_path)

    assert isinstance(source, SessionSource)
    assert source.profile_dir == tmp_path / "profile"
    assert source.lock_path == tmp_path / "enrich.lock"


@pytest.mark.asyncio
async def test_session_holds_lock_across_discover_and_enrich(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The enrich lock is held for the WHOLE session (covering discover)."""
    source = _source(tmp_path)

    @asynccontextmanager
    async def fake_ctx(**_kwargs: object) -> AsyncIterator[_FakeContext]:
        yield _FakeContext()

    monkeypatch.setattr(linkedin_module, "_persistent_context", fake_ctx)

    assert not source.lock_path.exists()
    async with source.session() as session:
        # Lock present while the session (which runs discover then enrich) is open.
        assert source.lock_path.exists()
        assert hasattr(session, "discover")
        assert hasattr(session, "enrich")
    assert not source.lock_path.exists()  # released on exit


@pytest.mark.asyncio
async def test_persistent_context_stops_playwright_when_launch_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A launch failure must still stop Playwright (no leaked driver subprocess)."""
    stopped: list[bool] = []

    class FakeChromium:
        async def launch_persistent_context(self, *_a: object, **_k: object) -> Any:
            raise RuntimeError("chromium not installed")

    class FakePlaywright:
        chromium = FakeChromium()

        async def stop(self) -> None:
            stopped.append(True)

    async def fake_start() -> FakePlaywright:
        return FakePlaywright()

    monkeypatch.setattr(linkedin_module, "_start_playwright", fake_start)

    with pytest.raises(RuntimeError, match="chromium not installed"):
        async with linkedin_module._persistent_context(
            profile_dir=tmp_path / "profile",
            headless=True,
        ):
            pass

    assert stopped == [True]
