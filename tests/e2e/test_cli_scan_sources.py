"""E2E CLI tests for the Phase 4a scan sources (SpeedyApply + JobSpy).

These run inside ``make quality`` (no PostgreSQL, no network): the store is
replaced with an in-memory fake via patching ``jobfeed.cli._create_store`` and
every source's network call is monkeypatched, so a ``CliRunner`` drives the real
``jobfeed scan`` dispatch + ``_run_scan`` AsyncExitStack wiring without touching
real HTTP, JobSpy, or Postgres.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from click.testing import CliRunner, Result

from jobfeed.adapters.sources import _jobspy
from jobfeed.adapters.sources.ats import ATSSource
from jobfeed.adapters.sources.speedyapply import SpeedyApplySource
from jobfeed.cli import _resolve_config_path, cli
from jobfeed.domain.models import JobPosting, SaveJobResult
from jobfeed.domain.quality import assess_quality
from jobfeed.ports.source import DiscoverResult, EnrichResult

# ``jobfeed.cli`` rebinds the ``scan`` attribute to the Click command, so the
# submodules are reached via ``sys.modules``: ``scan`` holds the command,
# ``_scan_sources`` holds the source builders (LinkedInSource, create_http_client).
scan_module = sys.modules["jobfeed.cli.scan"]
sources_module = sys.modules["jobfeed.cli._scan_sources"]

# ``--source all`` here disables ats + linkedin-jobspy + linkedin -> skips.
_EXPECTED_SKIPS = 3
# ats + speedyapply both own an httpx client -> two clients created.
_EXPECTED_CLIENTS = 2

# ---------------------------------------------------------------------------
# In-memory fakes (no Postgres, no network)
# ---------------------------------------------------------------------------


class FakeStore:
    """Minimal JobStore covering only the scan path (connect/save/record).

    ``StoreOpsMixin``'s company helpers back the ATS seeding step so the ATS
    builder can run without a real database.
    """

    def __init__(self) -> None:
        self.jobs: list[JobPosting] = []
        self.companies: dict[str, Any] = {}
        self.connected = False
        self.closed = False
        self.runs: list[Any] = []

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.closed = True

    async def save_job(self, job: JobPosting) -> SaveJobResult:
        self.jobs.append(job)
        return SaveJobResult(job_id=job.canonical_id, inserted=True, updated=False)

    async def record_pipeline_run(self, run: Any) -> None:
        self.runs.append(run)

    async def get_company(self, slug: str) -> Any:
        return self.companies.get(slug)

    async def upsert_company(self, company: Any) -> None:
        self.companies[company.slug] = company


class FakeLinkedInScanSession:
    """Session fake used by the Playwright LinkedIn CLI wiring tests."""

    def __init__(self, posting: JobPosting) -> None:
        self.posting = posting

    async def discover(self, _config: dict[str, object]) -> DiscoverResult:
        """Return one discovered LinkedIn posting."""
        return DiscoverResult(postings=[self.posting])

    async def enrich(self, posting: JobPosting) -> EnrichResult:
        """Return a deterministic enriched JD."""
        return EnrichResult(
            jd_text=posting.jd_text or "",
            quality=posting.jd_quality or assess_quality(posting.jd_text),
            enrich_source="linkedin_fake",
        )


class FakeLinkedInSource:
    """LinkedIn SessionSource fake replacing the Playwright adapter in E2E tests."""

    def __init__(self, **_kwargs: object) -> None:
        self.posting = _posting("linkedin", "1")

    def session(self) -> object:
        """Open a fake discover-and-enrich session."""
        posting = self.posting

        @asynccontextmanager
        async def manager() -> AsyncIterator[FakeLinkedInScanSession]:
            yield FakeLinkedInScanSession(posting)

        return manager()


def _posting(platform: str, suffix: str) -> JobPosting:
    """Build one fully-populated posting for a faked source fetch."""
    jd = (
        "We build local-first job tooling and need a strong engineer who is "
        "comfortable with async Python, structured data, and clean adapters "
        "between domain logic and external services. You will ship features "
        "used daily and keep observability simple."
    )
    return JobPosting(
        platform=platform,
        canonical_id=f"{platform}-{suffix}",
        url=f"https://example.com/{platform}/{suffix}",
        title="Backend Intern",
        company="Northstar Systems",
        location="Remote",
        discovered_at=datetime.now(UTC),
        jd_text=jd,
        jd_quality=assess_quality(jd),
        enrich_source=platform,
    )


# ---------------------------------------------------------------------------
# Config + invocation helpers
# ---------------------------------------------------------------------------


def _write_config(tmp_path: Path, enabled: dict[str, bool]) -> Path:
    """Write a config enabling the named sources (ats defaults off here)."""
    config_path = tmp_path / "config.toml"
    blocks = [
        "[db]",
        'url = "postgresql://x:y@localhost:5432/unused"',
        "",
        "[observability]",
        'log_level = "info"',
        'log_format = "json"',
        "",
        "[sources.ats]",
        f"enabled = {str(enabled.get('ats', False)).lower()}",
        'seed_companies = ["acme"]',
        "",
        "[sources.speedyapply]",
        f"enabled = {str(enabled.get('speedyapply', False)).lower()}",
        'search_urls = ["https://lists.example.test/speedyapply.md"]',
        "",
        "[sources.indeed]",
        f"enabled = {str(enabled.get('indeed', False)).lower()}",
        "",
        "[sources.linkedin_jobspy]",
        f"enabled = {str(enabled.get('linkedin_jobspy', False)).lower()}",
        "",
        "[sources.linkedin]",
        f"enabled = {str(enabled.get('linkedin', False)).lower()}",
        "",
    ]
    config_path.write_text("\n".join(blocks), encoding="utf-8")
    return config_path


def _invoke(runner: CliRunner, config_path: Path, *args: str) -> Result:
    return runner.invoke(cli, ["--config", str(config_path), *args])


@pytest.fixture
def fake_store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    """Replace the CLI store factory with one shared in-memory FakeStore."""
    store = FakeStore()
    monkeypatch.setattr("jobfeed.cli._create_store", lambda _settings: store)
    return store


def _fetch_returning(*postings: JobPosting) -> Any:
    """Build a fake async ``fetch_jobs(self, config)`` returning ``postings``.

    Absorbs the bound ``self`` and the protocol ``config`` arg so a single
    helper can stand in for any SimpleSource's ``fetch_jobs``.
    """

    async def _fetch(*_args: object, **_kwargs: object) -> list[JobPosting]:
        return list(postings)

    return _fetch


def _mock_jobspy_process(
    monkeypatch: pytest.MonkeyPatch, *postings: JobPosting
) -> None:
    """Mock the JobSpy process boundary without launching child processes."""

    def _fake(_request: object, _timeout_s: float) -> object:
        return _jobspy._ScrapeProcessOutcome(postings=list(postings))

    monkeypatch.setattr(_jobspy, "_run_scrape_process", _fake)


# ---------------------------------------------------------------------------
# Per-source scan tests (network mocked)
# ---------------------------------------------------------------------------


def test_scan_speedyapply_runs_source(
    tmp_path: Path, fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--source speedyapply`` runs SpeedyApply with its fetch mocked."""
    monkeypatch.setattr(
        SpeedyApplySource,
        "fetch_jobs",
        _fetch_returning(_posting("speedyapply", "1"), _posting("speedyapply", "2")),
    )
    config_path = _write_config(tmp_path, {"speedyapply": True})

    result = _invoke(CliRunner(), config_path, "scan", "--source", "speedyapply")

    assert result.exit_code == 0, result.output
    assert "Discovered 2 jobs, inserted 2, updated 0" in result.output
    assert [j.platform for j in fake_store.jobs] == ["speedyapply", "speedyapply"]


def test_scan_indeed_runs_source(
    tmp_path: Path, fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--source indeed`` runs Indeed JobSpy with the scrape boundary mocked."""
    monkeypatch.setattr(
        "jobfeed.adapters.sources.indeed_jobspy.apply_indeed_date_patch",
        lambda: None,
    )
    _mock_jobspy_process(monkeypatch, _posting("indeed", "1"))
    config_path = _write_config(tmp_path, {"indeed": True})
    _enable_indeed_url(config_path)

    result = _invoke(CliRunner(), config_path, "scan", "--source", "indeed")

    assert result.exit_code == 0, result.output
    assert "Discovered 1 jobs, inserted 1, updated 0" in result.output
    assert fake_store.jobs[0].platform == "indeed"


def test_scan_linkedin_jobspy_runs_source(
    tmp_path: Path, fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--source linkedin-jobspy`` runs LinkedIn JobSpy with process mocked."""
    _mock_jobspy_process(monkeypatch, _posting("linkedin_jobspy", "1"))
    config_path = _write_config(tmp_path, {"linkedin_jobspy": True})
    _enable_linkedin_url(config_path)

    result = _invoke(CliRunner(), config_path, "scan", "--source", "linkedin-jobspy")

    assert result.exit_code == 0, result.output
    assert "Discovered 1 jobs, inserted 1, updated 0" in result.output
    assert fake_store.jobs[0].platform == "linkedin_jobspy"


def test_scan_linkedin_runs_session_source(
    tmp_path: Path, fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--source linkedin`` runs the Playwright SessionSource path."""
    monkeypatch.setattr(sources_module, "LinkedInSource", FakeLinkedInSource)
    config_path = _write_config(tmp_path, {"linkedin": True})
    _enable_linkedin_playwright_url(config_path)

    result = _invoke(CliRunner(), config_path, "scan", "--source", "linkedin")

    assert result.exit_code == 0, result.output
    assert "Discovered 1 jobs, inserted 1, updated 0" in result.output
    assert fake_store.jobs[0].platform == "linkedin"


def test_scan_mock_unchanged(tmp_path: Path, fake_store: FakeStore) -> None:
    """``--source mock`` still scans the seeded mock source (no network)."""
    config_path = _write_config(tmp_path, {})

    result = _invoke(CliRunner(), config_path, "scan", "--source", "mock")

    assert result.exit_code == 0, result.output
    assert "Discovered 3 jobs, inserted 3, updated 0" in result.output
    assert all(j.platform == "mock" for j in fake_store.jobs)


def test_scan_ats_unchanged(
    tmp_path: Path, fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--source ats`` still builds + runs ATSSource (fetch mocked)."""
    monkeypatch.setattr(
        ATSSource, "fetch_jobs", _fetch_returning(_posting("greenhouse", "1"))
    )
    config_path = _write_config(tmp_path, {"ats": True})

    result = _invoke(CliRunner(), config_path, "scan", "--source", "ats")

    assert result.exit_code == 0, result.output
    assert "Discovered 1 jobs, inserted 1, updated 0" in result.output
    # ATS seeding ran against the fake store.
    assert "acme" in fake_store.companies


# ---------------------------------------------------------------------------
# --source all: enabled subset + skip logging
# ---------------------------------------------------------------------------


def test_scan_all_runs_enabled_and_logs_skips(
    tmp_path: Path, fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--source all`` runs the enabled REAL sources (not mock), logs skips."""
    monkeypatch.setattr(
        SpeedyApplySource, "fetch_jobs", _fetch_returning(_posting("speedyapply", "1"))
    )
    monkeypatch.setattr(
        "jobfeed.adapters.sources.indeed_jobspy.apply_indeed_date_patch",
        lambda: None,
    )
    _mock_jobspy_process(monkeypatch, _posting("indeed", "1"))
    config_path = _write_config(tmp_path, {"speedyapply": True, "indeed": True})
    _enable_indeed_url(config_path)

    result = _invoke(CliRunner(), config_path, "scan", "--source", "all")

    assert result.exit_code == 0, result.output
    platforms = {j.platform for j in fake_store.jobs}
    # --source all runs the ENABLED real sources only; the mock seed is NOT
    # folded in (it is explicit-only via --source mock).
    assert {"speedyapply", "indeed"} <= platforms
    assert "mock" not in platforms
    # ats + linkedin_jobspy were disabled -> structured skip events, no run.
    assert "linkedin_jobspy" not in platforms
    assert "linkedin" not in platforms
    assert '"source": "ats"' in result.output
    assert '"source": "linkedin-jobspy"' in result.output
    assert '"source": "linkedin"' in result.output
    assert result.output.count("scan_source_skipped") == _EXPECTED_SKIPS


# ---------------------------------------------------------------------------
# Disabled explicit source -> ClickException (mirrors ATS)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("fake_store")
def test_scan_speedyapply_disabled_is_click_error(tmp_path: Path) -> None:
    """Explicit ``--source speedyapply`` while disabled fails like ATS does."""
    config_path = _write_config(tmp_path, {"speedyapply": False})

    result = _invoke(CliRunner(), config_path, "scan", "--source", "speedyapply")

    assert result.exit_code == 1
    assert "speedyapply source is disabled in config" in result.output
    assert "Traceback" not in result.output


@pytest.mark.usefixtures("fake_store")
def test_scan_ats_disabled_is_click_error(tmp_path: Path) -> None:
    """Explicit ``--source ats`` while disabled fails (unchanged behavior)."""
    config_path = _write_config(tmp_path, {"ats": False})

    result = _invoke(CliRunner(), config_path, "scan", "--source", "ats")

    assert result.exit_code == 1
    assert "ats source is disabled in config" in result.output


@pytest.mark.usefixtures("fake_store")
def test_scan_linkedin_disabled_is_click_error(tmp_path: Path) -> None:
    """Explicit ``--source linkedin`` while disabled fails like other sources."""
    config_path = _write_config(tmp_path, {"linkedin": False})

    result = _invoke(CliRunner(), config_path, "scan", "--source", "linkedin")

    assert result.exit_code == 1
    assert "linkedin source is disabled in config" in result.output


# ---------------------------------------------------------------------------
# AsyncExitStack leak guard: every created httpx client is closed
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("fake_store")
def test_scan_all_closes_every_http_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--source all`` with ATS + SpeedyApply closes BOTH httpx clients.

    Regression guard for the old single-``client`` variable that leaked one of
    two clients when multiple client-owning sources ran together. A spy wraps
    ``create_http_client`` and tracks each returned client's ``aclose``; the
    test asserts every created client was closed exactly once.
    """
    created: list[httpx.AsyncClient] = []
    closed: list[int] = []

    def spy_create(_timeout: float = 30.0) -> httpx.AsyncClient:
        client = httpx.AsyncClient()
        original_aclose = client.aclose

        async def tracked_aclose() -> None:
            closed.append(id(client))
            await original_aclose()

        client.aclose = tracked_aclose  # type: ignore[method-assign]
        created.append(client)
        return client

    # _scan_sources looks up create_http_client in its own module namespace.
    monkeypatch.setattr(sources_module, "create_http_client", spy_create)
    monkeypatch.setattr(
        ATSSource, "fetch_jobs", _fetch_returning(_posting("greenhouse", "1"))
    )
    monkeypatch.setattr(
        SpeedyApplySource, "fetch_jobs", _fetch_returning(_posting("speedyapply", "1"))
    )
    config_path = _write_config(tmp_path, {"ats": True, "speedyapply": True})

    result = _invoke(CliRunner(), config_path, "scan", "--source", "all")

    assert result.exit_code == 0, result.output
    # Two client-owning sources -> two clients created.
    assert len(created) == _EXPECTED_CLIENTS
    # EVERY created client was closed (no leak, no overwrite).
    assert {id(c) for c in created} == set(closed)
    assert len(closed) == _EXPECTED_CLIENTS


# ---------------------------------------------------------------------------
# --help lists the new choices
# ---------------------------------------------------------------------------


def test_scan_help_lists_new_choices() -> None:
    """``scan --help`` advertises every Phase 4a source token."""
    result = CliRunner().invoke(cli, ["scan", "--help"])

    assert result.exit_code == 0
    for token in (
        "mock",
        "ats",
        "speedyapply",
        "indeed",
        "linkedin-jobspy",
        "linkedin",
        "all",
    ):
        assert token in result.output


# ---------------------------------------------------------------------------
# Config patch helpers (JobSpy sources need a search URL to scrape)
# ---------------------------------------------------------------------------


def _enable_indeed_url(config_path: Path) -> None:
    """Add one Indeed search URL so the scrape loop has a URL to process."""
    text = config_path.read_text(encoding="utf-8")
    text = text.replace(
        "[sources.indeed]\nenabled = true",
        '[sources.indeed]\nenabled = true\nsearch_urls = ["https://indeed.com/jobs?q=swe"]',
    )
    config_path.write_text(text, encoding="utf-8")


def _enable_linkedin_url(config_path: Path) -> None:
    """Add one LinkedIn search URL so the scrape loop has a URL to process."""
    text = config_path.read_text(encoding="utf-8")
    text = text.replace(
        "[sources.linkedin_jobspy]\nenabled = true",
        '[sources.linkedin_jobspy]\nenabled = true\nsearch_urls = ["https://linkedin.com/jobs/search?keywords=swe"]',
    )
    config_path.write_text(text, encoding="utf-8")


def _enable_linkedin_playwright_url(config_path: Path) -> None:
    """Add one LinkedIn Playwright search URL for the SessionSource."""
    text = config_path.read_text(encoding="utf-8")
    text = text.replace(
        "[sources.linkedin]\nenabled = true",
        '[sources.linkedin]\nenabled = true\nsearch_urls = ["https://linkedin.com/jobs/search?keywords=swe"]',
    )
    config_path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Default source = all  +  ./config.toml auto-discovery
# ---------------------------------------------------------------------------


def test_scan_source_default_is_all() -> None:
    """`jobfeed scan` defaults to --source all (not mock)."""
    source_opt = next(p for p in scan_module.scan.params if p.name == "source_name")
    assert source_opt.default == "all"


def test_resolve_config_path_prefers_explicit(tmp_path: Path) -> None:
    """An explicit --config path always wins over a cwd config.toml."""
    explicit = tmp_path / "custom.toml"
    explicit.write_text("", encoding="utf-8")
    assert _resolve_config_path(explicit) == explicit


def test_resolve_config_path_falls_back_to_cwd_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With --config omitted, ./config.toml in the cwd is picked up."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    assert _resolve_config_path(None) == Path("config.toml")


def test_resolve_config_path_none_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No --config and no ./config.toml -> None (built-in defaults apply)."""
    monkeypatch.chdir(tmp_path)
    assert _resolve_config_path(None) is None
