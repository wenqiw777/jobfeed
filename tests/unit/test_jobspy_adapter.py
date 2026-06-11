"""Unit tests for the JobSpy isolation boundary + Indeed source.

``jobspy.scrape_jobs`` or the process runner is always monkeypatched — NO real
network. Tests build real ``pandas.DataFrame``s and assert the boundary converts
them to ``JobPosting``s, parses search URLs site-aware, contains scrape
failures, runs off the event loop, and imports lazily.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import threading
import time
from datetime import UTC, date, datetime, timedelta
from queue import Empty

import jobspy
import pandas as pd
import pytest

from jobfeed.adapters.sources import _jobspy, _jobspy_process
from jobfeed.adapters.sources.indeed_jobspy import IndeedSource
from jobfeed.adapters.sources.linkedin_jobspy import LinkedInJobSpySource
from jobfeed.config import SourcesIndeedConfig, SourcesLinkedInJobSpyConfig
from jobfeed.domain.models import JobPosting, QualityBand
from jobfeed.observability import get_logger
from jobfeed.ports.source import SimpleSource

_DISCOVERED_AT = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)
# Long enough that assess_quality returns a non-MISSING band.
_LONG_JD = "Engineering internship. " * 30

# Expected JobSpy kwargs parsed from the crafted Indeed/LinkedIn URLs below.
_EXPECTED_INDEED_HOURS = 72  # fromage=3 days * 24
_EXPECTED_INDEED_RADIUS = 25
_EXPECTED_MAX_JOBS = 42
_EXPECTED_OVERRIDE_HOURS = 12
_EXPECTED_LI_DISTANCE = 10
_EXPECTED_LI_HOURS = 24  # f_TPR=r86400 seconds // 3600
_EXPECTED_LI_HOURS_NO_PREFIX = 2  # f_TPR=7200 seconds // 3600
_EXPECTED_LI_MAX_JOBS = 7
_DEFAULT_JOBSPY_TIMEOUT_S = 60.0
_DEFAULT_JOBSPY_MAX_CONCURRENT = 2
_JOBSPY_TIMEOUT_S = 0.01
_JOBSPY_MAX_CONCURRENT = 2
_CUSTOM_JOBSPY_TIMEOUT_S = 12.5
_CUSTOM_JOBSPY_MAX_CONCURRENT = 3
_CUSTOM_INDEED_COUNTRY = "canada"
_CONCURRENCY_SLEEP_S = 0.02
_REPEAT_COUNT = 3
_CUSTOM_INDEED_REPEAT = 4


def _frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    """Build a JobSpy-shaped DataFrame from row dicts."""
    return pd.DataFrame(rows)


def _good_row(**overrides: object) -> dict[str, object]:
    """A complete JobSpy row with all required fields populated."""
    row: dict[str, object] = {
        "id": "in-abc123",
        "title": "Software Engineer Intern",
        "company": "Acme",
        "location": "San Francisco, CA",
        "job_url": "https://www.indeed.com/viewjob?jk=abc123",
        "description": _LONG_JD,
        "date_posted": date(2026, 5, 28),
    }
    row.update(overrides)
    return row


def _posting_for_url(search_url: str, *, platform: str) -> JobPosting:
    """Build a real posting whose id reflects the URL suffix."""
    posting = _jobspy._row_to_posting(
        _good_row(id=f"in-{search_url[-1]}"),
        platform=platform,
        discovered_at=_DISCOVERED_AT,
    )
    assert posting is not None
    return posting


def _posting_with_id(canonical_id: str, *, platform: str) -> JobPosting:
    """Build a real posting carrying an explicit canonical_id."""
    posting = _jobspy._row_to_posting(
        _good_row(id=canonical_id),
        platform=platform,
        discovered_at=_DISCOVERED_AT,
    )
    assert posting is not None
    return posting


def _scrape_outcome(
    postings: list[JobPosting] | None = None,
    *,
    error: str | None = None,
    is_timed_out: bool = False,
) -> object:
    """Build a private JobSpy process outcome for fan-out tests."""
    return _jobspy_process._ScrapeProcessOutcome(
        postings=postings or [],
        error=error,
        is_timed_out=is_timed_out,
    )


class _HungQueue:
    def __init__(self) -> None:
        self.closed = False
        self.joined = False
        self.get_timeout: float | None = None

    def get(self, *, timeout: float) -> object:
        self.get_timeout = timeout
        raise Empty

    def close(self) -> None:
        self.closed = True

    def join_thread(self) -> None:
        self.joined = True


class _HungProcess:
    def __init__(self) -> None:
        self.started = False
        self.terminated = False
        self.killed = False
        self.joins: list[float | None] = []

    def start(self) -> None:
        self.started = True

    def join(self, timeout: float | None = None) -> None:
        self.joins.append(timeout)

    def is_alive(self) -> bool:
        return not self.killed

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


class _HungContext:
    def __init__(self) -> None:
        self.queue = _HungQueue()
        self.process = _HungProcess()
        self.method = ""

    def Queue(self, *, maxsize: int) -> _HungQueue:  # noqa: ARG002
        return self.queue

    def Process(
        self,
        *,
        target: object,
        args: tuple[object, ...],
        daemon: bool,
    ) -> _HungProcess:
        assert target is _jobspy_process._scrape_child_main
        assert args[-1] is self.queue
        assert daemon is True
        return self.process


@pytest.fixture
def patched_scrape_jobs(monkeypatch: pytest.MonkeyPatch):
    """Install a fake ``jobspy.scrape_jobs`` and capture its kwargs.

    Returns a small holder whose ``.frame`` is returned by the fake and whose
    ``.calls`` records each kwargs dict.
    """

    class _Holder:
        def __init__(self) -> None:
            self.frame: pd.DataFrame | None = _frame([_good_row()])
            self.calls: list[dict[str, object]] = []
            self.raise_exc: Exception | None = None

    holder = _Holder()

    def _fake(**kwargs: object) -> pd.DataFrame | None:
        holder.calls.append(kwargs)
        if holder.raise_exc is not None:
            raise holder.raise_exc
        return holder.frame

    monkeypatch.setattr(jobspy, "scrape_jobs", _fake)
    return holder


# ---------------------------------------------------------------------------
# DataFrame -> JobPosting conversion
# ---------------------------------------------------------------------------


def test_scrape_converts_dataframe_to_postings(patched_scrape_jobs) -> None:
    """A crafted DataFrame becomes JobPostings with inline JD + aware-UTC date."""
    patched_scrape_jobs.frame = _frame([_good_row()])
    postings = _jobspy.scrape(
        site_name="indeed",
        platform="indeed",
        search_url="https://www.indeed.com/jobs?q=swe",
        config=_jobspy.ScrapeConfig(max_jobs=50, hours_old=None),
        discovered_at=_DISCOVERED_AT,
    )
    assert len(postings) == 1
    posting = postings[0]
    assert posting.platform == "indeed"
    assert posting.canonical_id == "in-abc123"
    assert posting.url == "https://www.indeed.com/viewjob?jk=abc123"
    assert posting.enrich_source == "jobspy_inline"
    assert posting.jd_text is not None and "Engineering internship" in posting.jd_text
    assert posting.jd_quality == QualityBand.GOOD  # 720-char JD -> GOOD band
    assert posting.discovered_at == _DISCOVERED_AT
    assert posting.enriched_at == _DISCOVERED_AT
    # date_posted (a date) -> aware-UTC datetime
    assert posting.posted_at == datetime(2026, 5, 28, tzinfo=UTC)
    assert posting.posted_at is not None
    assert posting.posted_at.utcoffset() == timedelta(0)  # aware, zero offset = UTC


def test_scrape_coerces_nan_and_nat_to_none(patched_scrape_jobs) -> None:
    """A row with NaN company / NaT date yields None/'' fields, not 'nan'."""
    patched_scrape_jobs.frame = _frame(
        [
            _good_row(
                id="in-nan1",
                company=pd.NA,
                location=pd.NA,
                description=pd.NA,
                date_posted=pd.NaT,
            )
        ]
    )
    postings = _jobspy.scrape(
        site_name="indeed",
        platform="indeed",
        search_url="https://www.indeed.com/jobs?q=swe",
        config=_jobspy.ScrapeConfig(max_jobs=50, hours_old=None),
        discovered_at=_DISCOVERED_AT,
    )
    assert len(postings) == 1
    posting = postings[0]
    assert posting.company == ""
    assert posting.location == ""
    assert posting.jd_text is None
    assert posting.jd_quality == QualityBand.MISSING
    assert posting.posted_at is None


def test_scrape_skips_rows_missing_required_fields(patched_scrape_jobs) -> None:
    """Rows missing id/url/title are dropped; complete rows survive."""
    patched_scrape_jobs.frame = _frame(
        [
            _good_row(id="in-ok"),
            _good_row(id=pd.NA),  # missing id -> dropped
            _good_row(title=pd.NA),  # missing title -> dropped
        ]
    )
    postings = _jobspy.scrape(
        site_name="indeed",
        platform="indeed",
        search_url="https://www.indeed.com/jobs?q=swe",
        config=_jobspy.ScrapeConfig(max_jobs=50, hours_old=None),
    )
    assert [p.canonical_id for p in postings] == ["in-ok"]


def test_scrape_empty_and_none_frame_return_empty(patched_scrape_jobs) -> None:
    """A None or empty DataFrame yields no postings (no crash)."""
    patched_scrape_jobs.frame = None
    assert (
        _jobspy.scrape(
            site_name="indeed",
            platform="indeed",
            search_url="https://www.indeed.com/jobs?q=swe",
            config=_jobspy.ScrapeConfig(max_jobs=10, hours_old=None),
        )
        == []
    )
    patched_scrape_jobs.frame = _frame([])
    assert (
        _jobspy.scrape(
            site_name="indeed",
            platform="indeed",
            search_url="https://www.indeed.com/jobs?q=swe",
            config=_jobspy.ScrapeConfig(max_jobs=10, hours_old=None),
        )
        == []
    )


def test_scrape_tags_platform_distinct_from_site(patched_scrape_jobs) -> None:
    """``platform`` is stamped from the arg, independent of ``site_name``."""
    patched_scrape_jobs.frame = _frame([_good_row()])
    postings = _jobspy.scrape(
        site_name="linkedin",
        platform="linkedin_jobspy",
        search_url="https://www.linkedin.com/jobs/search/?keywords=swe",
        config=_jobspy.ScrapeConfig(max_jobs=10, hours_old=None),
    )
    assert postings[0].platform == "linkedin_jobspy"


def test_linkedin_scrape_requests_descriptions(patched_scrape_jobs) -> None:
    """LinkedIn scrapes set linkedin_fetch_description=True.

    JobSpy omits the LinkedIn `description` column by default, so without this
    flag every LinkedIn row persists with jd_text=None despite the inline-JD
    contract. Regression guard for the Codex P1 review finding.
    """
    patched_scrape_jobs.frame = _frame([_good_row()])
    _jobspy.scrape(
        site_name="linkedin",
        platform="linkedin_jobspy",
        search_url="https://www.linkedin.com/jobs/search/?keywords=swe",
        config=_jobspy.ScrapeConfig(max_jobs=10, hours_old=None),
    )
    assert patched_scrape_jobs.calls[-1]["linkedin_fetch_description"] is True


def test_indeed_scrape_omits_linkedin_description_flag(patched_scrape_jobs) -> None:
    """The linkedin_fetch_description flag is LinkedIn-only (not sent for Indeed)."""
    patched_scrape_jobs.frame = _frame([_good_row()])
    _jobspy.scrape(
        site_name="indeed",
        platform="indeed",
        search_url="https://www.indeed.com/jobs?q=swe",
        config=_jobspy.ScrapeConfig(max_jobs=10, hours_old=None),
    )
    assert "linkedin_fetch_description" not in patched_scrape_jobs.calls[-1]


def test_scrape_row_without_description_is_unenriched(patched_scrape_jobs) -> None:
    """A row with no description stays unenriched: no jobspy_inline, no enriched_at.

    Regression guard for the Codex P2 finding — a missing JD must not be tagged
    as an inline-enriched posting (consistent with SpeedyApply's no-JD rows).
    """
    patched_scrape_jobs.frame = _frame([{**_good_row(), "description": None}])
    postings = _jobspy.scrape(
        site_name="indeed",
        platform="indeed",
        search_url="https://www.indeed.com/jobs?q=swe",
        config=_jobspy.ScrapeConfig(max_jobs=10, hours_old=None),
    )
    assert postings[0].jd_text is None
    assert postings[0].enrich_source is None
    assert postings[0].enriched_at is None


# ---------------------------------------------------------------------------
# Indeed URL parsing
# ---------------------------------------------------------------------------


def test_indeed_url_parse_maps_all_params(patched_scrape_jobs) -> None:
    """q/l/fromage/radius -> search_term/location/hours_old(=N*24)/distance."""
    url = (
        "https://www.indeed.com/jobs?q=software+engineer&l=Seattle&fromage=3&radius=25"
    )
    _jobspy.scrape(
        site_name="indeed",
        platform="indeed",
        search_url=url,
        config=_jobspy.ScrapeConfig(max_jobs=42, hours_old=None),
    )
    kwargs = patched_scrape_jobs.calls[-1]
    assert kwargs["search_term"] == "software engineer"
    assert kwargs["location"] == "Seattle"
    assert kwargs["hours_old"] == _EXPECTED_INDEED_HOURS
    assert kwargs["distance"] == _EXPECTED_INDEED_RADIUS
    assert kwargs["site_name"] == "indeed"
    assert kwargs["results_wanted"] == _EXPECTED_MAX_JOBS
    assert kwargs["country_indeed"] == "usa"


def test_explicit_hours_old_overrides_url_fromage(patched_scrape_jobs) -> None:
    """The hours_old arg wins over the URL's fromage (legacy indeed.py:80-81)."""
    url = "https://www.indeed.com/jobs?q=swe&fromage=7"
    _jobspy.scrape(
        site_name="indeed",
        platform="indeed",
        search_url=url,
        config=_jobspy.ScrapeConfig(max_jobs=10, hours_old=_EXPECTED_OVERRIDE_HOURS),
    )
    kwargs = patched_scrape_jobs.calls[-1]
    assert kwargs["hours_old"] == _EXPECTED_OVERRIDE_HOURS  # arg override, not 7*24


def test_indeed_url_parse_drops_blank_and_bad_params(patched_scrape_jobs) -> None:
    """Blank q / non-numeric fromage are dropped, not passed as garbage."""
    url = "https://www.indeed.com/jobs?q=&fromage=soon&radius=abc"
    _jobspy.scrape(
        site_name="indeed",
        platform="indeed",
        search_url=url,
        config=_jobspy.ScrapeConfig(max_jobs=10, hours_old=None),
    )
    kwargs = patched_scrape_jobs.calls[-1]
    assert "search_term" not in kwargs
    assert "hours_old" not in kwargs
    assert "distance" not in kwargs


# ---------------------------------------------------------------------------
# LinkedIn URL parsing (branches on site_name; different keys)
# ---------------------------------------------------------------------------


def test_linkedin_url_parse_uses_linkedin_keys(patched_scrape_jobs) -> None:
    """keywords/location/distance/f_TPR=r<seconds> -> JobSpy kwargs."""
    url = (
        "https://www.linkedin.com/jobs/search/?keywords=backend+intern"
        "&location=New+York&distance=10&f_TPR=r86400"
    )
    _jobspy.scrape(
        site_name="linkedin",
        platform="linkedin_jobspy",
        search_url=url,
        config=_jobspy.ScrapeConfig(max_jobs=20, hours_old=None),
    )
    kwargs = patched_scrape_jobs.calls[-1]
    assert kwargs["search_term"] == "backend intern"
    assert kwargs["location"] == "New York"
    assert kwargs["distance"] == _EXPECTED_LI_DISTANCE
    assert kwargs["hours_old"] == _EXPECTED_LI_HOURS
    assert kwargs["site_name"] == "linkedin"


def test_linkedin_ignores_indeed_keys(patched_scrape_jobs) -> None:
    """Indeed's q/l/fromage are NOT honored for a LinkedIn URL (site-aware)."""
    url = "https://www.linkedin.com/jobs/search/?q=swe&l=SF&fromage=3"
    _jobspy.scrape(
        site_name="linkedin",
        platform="linkedin_jobspy",
        search_url=url,
        config=_jobspy.ScrapeConfig(max_jobs=20, hours_old=None),
    )
    kwargs = patched_scrape_jobs.calls[-1]
    assert "search_term" not in kwargs
    assert "location" not in kwargs
    assert "hours_old" not in kwargs


def test_linkedin_f_tpr_without_r_prefix(patched_scrape_jobs) -> None:
    """f_TPR seconds without the leading 'r' still convert to hours."""
    url = "https://www.linkedin.com/jobs/search/?keywords=swe&f_TPR=7200"
    _jobspy.scrape(
        site_name="linkedin",
        platform="linkedin_jobspy",
        search_url=url,
        config=_jobspy.ScrapeConfig(max_jobs=20, hours_old=None),
    )
    assert patched_scrape_jobs.calls[-1]["hours_old"] == _EXPECTED_LI_HOURS_NO_PREFIX


# ---------------------------------------------------------------------------
# Lazy import: module imports without jobspy present
# ---------------------------------------------------------------------------


def test_module_imports_without_jobspy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing the JobSpy modules succeeds even when jobspy is absent.

    jobspy/pandas are imported INSIDE ``scrape``, not at module level, so both
    the pandas-confined core (``_jobspy``) and the orchestration layer
    (``_jobspy_process``, which imports the core) are importable on a box without
    jobspy. We hide jobspy from the import system and re-import them fresh.
    """
    monkeypatch.delitem(sys.modules, "jobspy", raising=False)
    monkeypatch.delitem(sys.modules, "jobfeed.adapters.sources._jobspy", raising=False)
    monkeypatch.delitem(
        sys.modules, "jobfeed.adapters.sources._jobspy_process", raising=False
    )

    real_import = __import__

    def _blocking_import(name: str, *args: object, **kwargs: object):
        if name == "jobspy" or name.startswith("jobspy."):
            raise ImportError("jobspy hidden for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _blocking_import)
    core = importlib.import_module("jobfeed.adapters.sources._jobspy")
    process = importlib.import_module("jobfeed.adapters.sources._jobspy_process")
    assert hasattr(core, "scrape")
    assert hasattr(process, "scrape_urls")


# ---------------------------------------------------------------------------
# scrape_urls: per-URL error containment
# ---------------------------------------------------------------------------


async def test_scrape_urls_contains_one_failing_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One URL raising JobSpyError is logged and skipped; others still return."""

    def _fake_run_scrape_process(request: object, timeout_s: float) -> object:
        assert timeout_s == _DEFAULT_JOBSPY_TIMEOUT_S
        if "bad" in request.search_url:
            return _scrape_outcome(error="Cloudflare challenge")
        return _scrape_outcome(
            [_posting_for_url(request.search_url, platform=request.platform)]
        )

    monkeypatch.setattr(
        _jobspy_process, "_run_scrape_process", _fake_run_scrape_process
    )
    postings = await _jobspy_process.scrape_urls(
        site_name="indeed",
        platform="indeed",
        search_urls=[
            "https://indeed.com/jobs?q=1",
            "https://indeed.com/jobs?q=bad",
            "https://indeed.com/jobs?q=3",
        ],
        max_jobs=10,
        hours_old=None,
        timeout_s=_DEFAULT_JOBSPY_TIMEOUT_S,
        max_concurrent=2,
        logger=get_logger(),
        discovered_at=_DISCOVERED_AT,
    )
    # The two good URLs contributed; the bad one was contained.
    assert {p.canonical_id for p in postings} == {"in-1", "in-3"}


def test_run_scrape_process_terminates_and_kills_timed_out_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timed-out child process is stopped instead of being left running."""
    context = _HungContext()

    def _fake_get_context(method: str) -> _HungContext:
        context.method = method
        return context

    monkeypatch.setattr(_jobspy_process.mp, "get_context", _fake_get_context)

    outcome = _jobspy_process._run_scrape_process(
        _jobspy_process._ScrapeRequest(
            site_name="indeed",
            platform="indeed",
            search_url="https://indeed.com/jobs?q=slow",
            max_jobs=10,
            hours_old=None,
            country_indeed=None,
            discovered_at=_DISCOVERED_AT,
        ),
        timeout_s=_JOBSPY_TIMEOUT_S,
    )

    assert context.method == "spawn"
    assert outcome.is_timed_out is True
    assert outcome.postings == []
    assert context.process.started is True
    assert context.process.terminated is True
    assert context.process.killed is True
    # The result wait now happens on the queue (drain-before-join), so the only
    # process.join calls are the two kill-grace waits inside _stop_process.
    assert context.queue.get_timeout == _JOBSPY_TIMEOUT_S
    assert context.process.joins == [
        _jobspy_process._PROCESS_KILL_GRACE_S,
        _jobspy_process._PROCESS_KILL_GRACE_S,
    ]
    assert context.queue.closed is True
    assert context.queue.joined is True


class _DeliveringQueue:
    """A queue whose ``get`` returns a real outcome (the child delivered)."""

    def __init__(self, outcome: object) -> None:
        self._outcome = outcome
        self.get_timeout: float | None = None
        self.closed = False
        self.joined = False

    def get(self, *, timeout: float) -> object:
        self.get_timeout = timeout
        return self._outcome

    def close(self) -> None:
        self.closed = True

    def join_thread(self) -> None:
        self.joined = True


class _AliveDeliveringProcess(_HungProcess):
    """A child that delivered a result but is still flagged alive (feeder-blocked).

    Reproduces bug #2: a large successful payload keeps the child alive until the
    parent drains the queue. Join-before-read would mistake this for a timeout.
    """

    def is_alive(self) -> bool:
        return not self.killed


class _DeliveringContext:
    def __init__(self, outcome: object) -> None:
        self.queue = _DeliveringQueue(outcome)
        self.process = _AliveDeliveringProcess()
        self.method = ""

    def Queue(self, *, maxsize: int) -> _DeliveringQueue:  # noqa: ARG002
        return self.queue

    def Process(
        self, *, target: object, args: tuple[object, ...], daemon: bool
    ) -> _AliveDeliveringProcess:
        assert target is _jobspy_process._scrape_child_main
        assert args[-1] is self.queue
        assert daemon is True
        return self.process


def test_run_scrape_process_drains_queue_before_joining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A delivered result is returned even while the child is still alive.

    Regression for the join-before-drain deadlock: with the old code the child
    would be reported as timed out and its postings dropped.
    """
    delivered = _scrape_outcome(
        [_posting_for_url("https://indeed.com/jobs", platform="indeed")]
    )
    context = _DeliveringContext(delivered)
    monkeypatch.setattr(_jobspy_process.mp, "get_context", lambda _method: context)

    outcome = _jobspy_process._run_scrape_process(
        _jobspy_process._ScrapeRequest(
            site_name="indeed",
            platform="indeed",
            search_url="https://indeed.com/jobs?q=swe",
            max_jobs=10,
            hours_old=None,
            country_indeed=None,
            discovered_at=_DISCOVERED_AT,
        ),
        timeout_s=_JOBSPY_TIMEOUT_S,
    )

    assert outcome.is_timed_out is False
    assert outcome.postings == delivered.postings
    assert context.queue.get_timeout == _JOBSPY_TIMEOUT_S
    # The result was read before the process was joined.
    assert context.process.started is True
    assert context.queue.closed is True
    assert context.queue.joined is True


@pytest.mark.usefixtures("patched_scrape_jobs")
def test_scrape_applies_indeed_date_patch_in_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scrape() applies the dateOnIndeed patch for indeed (it runs in the child).

    The patch must run wherever jobspy actually runs — the spawn child — not only
    in IndeedSource.fetch_jobs() (the parent), which the child does not inherit.
    """
    patches: list[str] = []
    monkeypatch.setattr(
        _jobspy, "apply_indeed_date_patch", lambda: patches.append("indeed")
    )

    _jobspy.scrape(
        site_name="indeed",
        platform="indeed",
        search_url="https://www.indeed.com/jobs?q=swe",
        config=_jobspy.ScrapeConfig(max_jobs=10, hours_old=None),
        discovered_at=_DISCOVERED_AT,
    )
    assert patches == ["indeed"]


@pytest.mark.usefixtures("patched_scrape_jobs")
def test_scrape_skips_indeed_patch_for_linkedin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scrape() does NOT apply the indeed-only date patch for LinkedIn searches."""
    patches: list[str] = []
    monkeypatch.setattr(
        _jobspy, "apply_indeed_date_patch", lambda: patches.append("indeed")
    )

    _jobspy.scrape(
        site_name="linkedin",
        platform="linkedin_jobspy",
        search_url="https://www.linkedin.com/jobs/search?keywords=swe",
        config=_jobspy.ScrapeConfig(max_jobs=10, hours_old=None),
        discovered_at=_DISCOVERED_AT,
    )
    assert patches == []


async def test_scrape_urls_times_out_one_hanging_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hanging JobSpy call is timed out and contained per URL."""

    calls: list[tuple[str, float]] = []

    def _timeout_run_scrape_process(request: object, timeout_s: float) -> object:
        calls.append((request.search_url, timeout_s))
        return _scrape_outcome(is_timed_out=True)

    monkeypatch.setattr(
        _jobspy_process, "_run_scrape_process", _timeout_run_scrape_process
    )
    postings = await _jobspy_process.scrape_urls(
        site_name="indeed",
        platform="indeed",
        search_urls=["https://indeed.com/jobs?q=slow"],
        max_jobs=10,
        hours_old=None,
        timeout_s=_JOBSPY_TIMEOUT_S,
        max_concurrent=1,
        logger=get_logger(),
        discovered_at=_DISCOVERED_AT,
    )

    assert calls == [("https://indeed.com/jobs?q=slow", _JOBSPY_TIMEOUT_S)]
    assert postings == []


async def test_scrape_urls_honors_max_concurrent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JobSpy URL fan-out is bounded by the configured max_concurrent."""
    active = 0
    max_seen = 0
    lock = threading.Lock()

    def _tracked_run_scrape_process(request: object, timeout_s: float) -> object:
        nonlocal active, max_seen
        assert timeout_s == _DEFAULT_JOBSPY_TIMEOUT_S
        with lock:
            active += 1
            max_seen = max(max_seen, active)
        time.sleep(_CONCURRENCY_SLEEP_S)
        with lock:
            active -= 1
        return _scrape_outcome(
            [_posting_for_url(request.search_url, platform=request.platform)]
        )

    monkeypatch.setattr(
        _jobspy_process, "_run_scrape_process", _tracked_run_scrape_process
    )

    postings = await _jobspy_process.scrape_urls(
        site_name="indeed",
        platform="indeed",
        search_urls=[
            "https://indeed.com/jobs?q=1",
            "https://indeed.com/jobs?q=2",
            "https://indeed.com/jobs?q=3",
        ],
        max_jobs=10,
        hours_old=None,
        timeout_s=_DEFAULT_JOBSPY_TIMEOUT_S,
        max_concurrent=_JOBSPY_MAX_CONCURRENT,
        logger=get_logger(),
        discovered_at=_DISCOVERED_AT,
    )

    assert max_seen == _JOBSPY_MAX_CONCURRENT
    assert {p.canonical_id for p in postings} == {"in-1", "in-2", "in-3"}


async def test_scrape_urls_repeats_each_url_and_unions_by_canonical_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """repeat=N scrapes each URL N times and unions postings by canonical_id.

    Indeed's GraphQL backend returns a non-deterministic subset per call, so one
    pass misses postings. ``repeat`` issues multiple draws per URL; a canonical_id
    that recurs across draws collapses to a single posting (the union).
    """
    # Three draws of the one URL: in-a1 appears twice (proves dedup), in-a2 once.
    draws = [["in-a1"], ["in-a2"], ["in-a1"]]
    counter = {"n": 0}

    def _fake_run_scrape_process(request: object, timeout_s: float) -> object:
        assert timeout_s == _DEFAULT_JOBSPY_TIMEOUT_S
        ids = draws[counter["n"]]
        counter["n"] += 1
        return _scrape_outcome(
            [_posting_with_id(cid, platform=request.platform) for cid in ids]
        )

    monkeypatch.setattr(
        _jobspy_process, "_run_scrape_process", _fake_run_scrape_process
    )
    postings = await _jobspy_process.scrape_urls(
        site_name="indeed",
        platform="indeed",
        search_urls=["https://indeed.com/jobs?q=a"],
        max_jobs=10,
        hours_old=None,
        timeout_s=_DEFAULT_JOBSPY_TIMEOUT_S,
        max_concurrent=1,
        logger=get_logger(),
        discovered_at=_DISCOVERED_AT,
        repeat=_REPEAT_COUNT,
    )

    assert counter["n"] == _REPEAT_COUNT  # the URL was scraped repeat times
    # Union by canonical_id: in-a1 (drawn twice) collapses to one posting.
    assert sorted(p.canonical_id for p in postings) == ["in-a1", "in-a2"]


async def test_single_url_scrape_raises_on_challenge(
    patched_scrape_jobs,
) -> None:
    """A challenge inside scrape_jobs surfaces as JobSpyError from scrape()."""
    patched_scrape_jobs.raise_exc = RuntimeError("Cloudflare: verify you are human")
    with pytest.raises(_jobspy.JobSpyError) as excinfo:
        _jobspy.scrape(
            site_name="indeed",
            platform="indeed",
            search_url="https://indeed.com/jobs?q=swe",
            config=_jobspy.ScrapeConfig(max_jobs=10, hours_old=None),
        )
    assert excinfo.value.site_name == "indeed"
    assert "indeed.com" in excinfo.value.search_url


# ---------------------------------------------------------------------------
# IndeedSource: SimpleSource protocol, process-runner non-blocking, delegation
# ---------------------------------------------------------------------------


def _indeed_source(**overrides: object) -> IndeedSource:
    cfg = SourcesIndeedConfig(
        enabled=True,
        search_urls=["https://www.indeed.com/jobs?q=swe"],
        **overrides,  # type: ignore[arg-type]
    )
    return IndeedSource(config=cfg, logger=get_logger())


def test_indeed_source_satisfies_simple_source_protocol() -> None:
    """IndeedSource is a runtime SimpleSource."""
    assert isinstance(_indeed_source(), SimpleSource)


async def test_indeed_fetch_jobs_returns_inline_postings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fetch_jobs applies the date patch and returns indeed-tagged postings."""
    patches: list[str] = []
    captured: list[object] = []

    def _fake_run_scrape_process(request: object, timeout_s: float) -> object:
        assert timeout_s == _DEFAULT_JOBSPY_TIMEOUT_S
        captured.append(request)
        return _scrape_outcome(
            [_posting_for_url(request.search_url, platform=request.platform)]
        )

    monkeypatch.setattr(
        "jobfeed.adapters.sources.indeed_jobspy.apply_indeed_date_patch",
        lambda: patches.append("patched"),
    )
    monkeypatch.setattr(
        _jobspy_process, "_run_scrape_process", _fake_run_scrape_process
    )
    postings = await _indeed_source().fetch_jobs({})
    assert patches == ["patched"]
    assert len(postings) == 1
    assert postings[0].platform == "indeed"
    assert postings[0].enrich_source == "jobspy_inline"
    assert captured[-1].country_indeed == "usa"


async def test_indeed_fetch_jobs_forwards_runtime_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IndeedSource forwards timeout/concurrency/country config to JobSpy loop."""
    captured: dict[str, object] = {}

    async def _spy_scrape_urls(**kwargs: object) -> list[JobPosting]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        "jobfeed.adapters.sources.indeed_jobspy.apply_indeed_date_patch",
        lambda: None,
    )
    monkeypatch.setattr(_jobspy_process, "scrape_urls", _spy_scrape_urls)
    source = _indeed_source(
        timeout_s=_CUSTOM_JOBSPY_TIMEOUT_S,
        max_concurrent=_CUSTOM_JOBSPY_MAX_CONCURRENT,
        country_indeed=_CUSTOM_INDEED_COUNTRY,
    )

    assert await source.fetch_jobs({}) == []

    assert captured["timeout_s"] == _CUSTOM_JOBSPY_TIMEOUT_S
    assert captured["max_concurrent"] == _CUSTOM_JOBSPY_MAX_CONCURRENT
    assert captured["country_indeed"] == _CUSTOM_INDEED_COUNTRY


async def test_indeed_fetch_jobs_forwards_repeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IndeedSource forwards its configured repeat count to the JobSpy loop.

    Indeed's backend is non-deterministic, so the source re-runs each URL
    ``repeat`` times and unions the draws; the knob must reach ``scrape_urls``.
    """
    captured: dict[str, object] = {}

    async def _spy_scrape_urls(**kwargs: object) -> list[JobPosting]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        "jobfeed.adapters.sources.indeed_jobspy.apply_indeed_date_patch",
        lambda: None,
    )
    monkeypatch.setattr(_jobspy_process, "scrape_urls", _spy_scrape_urls)
    source = _indeed_source(repeat=_CUSTOM_INDEED_REPEAT)

    assert await source.fetch_jobs({}) == []
    assert captured["repeat"] == _CUSTOM_INDEED_REPEAT


async def test_indeed_fetch_jobs_runs_scrape_off_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The blocking process runner runs via asyncio.to_thread, freeing the loop.

    A concurrent coroutine flips a flag during ``asyncio.sleep(0)`` WHILE the
    synchronous process runner is busy-waiting inside the worker thread. If
    ``fetch_jobs`` ran the runner inline it would block the loop and the flag
    would still be False when the runner returns.
    """
    monkeypatch.setattr(
        "jobfeed.adapters.sources.indeed_jobspy.apply_indeed_date_patch",
        lambda: None,
    )
    flag = {"flipped": False}
    scrape_started = threading.Event()

    def _blocking_run_scrape_process(request: object, timeout_s: float) -> object:
        assert request.site_name == "indeed"
        assert timeout_s == _DEFAULT_JOBSPY_TIMEOUT_S
        scrape_started.set()
        # Busy-wait (no asyncio): if this ran on the loop thread the concurrent
        # coroutine below could never run. It only runs because we're in a
        # worker thread via to_thread.
        deadline = time.perf_counter() + 1.0
        while not flag["flipped"] and time.perf_counter() < deadline:
            pass
        return _scrape_outcome()

    monkeypatch.setattr(
        _jobspy_process, "_run_scrape_process", _blocking_run_scrape_process
    )

    async def _flip_flag() -> None:
        # Wait until the worker thread has actually started the process runner.
        while not scrape_started.is_set():
            await asyncio.sleep(0)
        flag["flipped"] = True

    source = _indeed_source()
    _, postings = await asyncio.gather(_flip_flag(), source.fetch_jobs({}))
    assert flag["flipped"] is True
    assert postings == []


# ---------------------------------------------------------------------------
# LinkedInJobSpySource: thin shell over the SHARED scrape_urls (no date patch)
# ---------------------------------------------------------------------------


def _li_jobspy_source(**overrides: object) -> LinkedInJobSpySource:
    fields: dict[str, object] = {
        "enabled": True,
        "search_urls": ["https://www.linkedin.com/jobs/search/?keywords=swe"],
    }
    fields.update(overrides)
    cfg = SourcesLinkedInJobSpyConfig(**fields)  # type: ignore[arg-type]
    return LinkedInJobSpySource(config=cfg, logger=get_logger())


def test_linkedin_jobspy_source_satisfies_simple_source_protocol() -> None:
    """LinkedInJobSpySource is a runtime SimpleSource."""
    assert isinstance(_li_jobspy_source(), SimpleSource)


async def test_linkedin_jobspy_fetch_jobs_returns_inline_postings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fetch_jobs returns postings tagged linkedin_jobspy with inline JD."""
    captured: list[object] = []

    def _fake_run_scrape_process(request: object, timeout_s: float) -> object:
        assert timeout_s == _DEFAULT_JOBSPY_TIMEOUT_S
        captured.append(request)
        return _scrape_outcome(
            [_posting_for_url(request.search_url, platform=request.platform)]
        )

    monkeypatch.setattr(
        _jobspy_process, "_run_scrape_process", _fake_run_scrape_process
    )
    postings = await _li_jobspy_source().fetch_jobs({})
    assert len(postings) == 1
    assert postings[0].platform == "linkedin_jobspy"
    assert postings[0].enrich_source == "jobspy_inline"
    assert postings[0].jd_text is not None
    # The shared boundary scraped the linkedin site (not indeed).
    assert captured[-1].site_name == "linkedin"


async def test_linkedin_jobspy_fetch_jobs_delegates_to_shared_scrape_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fetch_jobs reuses _jobspy_process.scrape_urls with the LinkedIn site/platform.

    Spying on the shared loop proves the source duplicates NO DataFrame /
    process-runner / containment logic — it forwards every config field verbatim.
    """
    captured: dict[str, object] = {}

    async def _spy_scrape_urls(**kwargs: object) -> list[JobPosting]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(_jobspy_process, "scrape_urls", _spy_scrape_urls)
    source = _li_jobspy_source(search_urls=["https://li/a", "https://li/b"], max_jobs=7)
    assert await source.fetch_jobs({}) == []
    assert captured["site_name"] == "linkedin"
    assert captured["platform"] == "linkedin_jobspy"
    assert captured["search_urls"] == ["https://li/a", "https://li/b"]
    assert captured["max_jobs"] == _EXPECTED_LI_MAX_JOBS
    assert captured["timeout_s"] == _DEFAULT_JOBSPY_TIMEOUT_S
    assert captured["max_concurrent"] == _DEFAULT_JOBSPY_MAX_CONCURRENT
    assert isinstance(captured["discovered_at"], datetime)


async def test_linkedin_jobspy_fetch_jobs_forwards_repeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LinkedInJobSpySource forwards its configured repeat to the shared loop.

    LinkedIn's JobSpy backend is non-deterministic too, so the shared ``repeat``
    knob must reach ``scrape_urls`` rather than being silently ignored.
    """
    captured: dict[str, object] = {}

    async def _spy_scrape_urls(**kwargs: object) -> list[JobPosting]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(_jobspy_process, "scrape_urls", _spy_scrape_urls)
    source = _li_jobspy_source(repeat=_CUSTOM_INDEED_REPEAT)

    assert await source.fetch_jobs({}) == []
    assert captured["repeat"] == _CUSTOM_INDEED_REPEAT


async def test_linkedin_jobspy_does_not_apply_indeed_date_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Indeed-only date patch is NEVER invoked by the LinkedIn source.

    Spy on ``apply_indeed_date_patch`` at its definition module; if the LinkedIn
    source (incorrectly) imported and called it, the spy would record a call.
    """
    patches: list[str] = []
    monkeypatch.setattr(
        "jobfeed.adapters.sources._jobspy_patches.apply_indeed_date_patch",
        lambda: patches.append("patched"),
    )
    monkeypatch.setattr(
        _jobspy_process,
        "_run_scrape_process",
        lambda request, _timeout_s: _scrape_outcome(
            [_posting_for_url(request.search_url, platform=request.platform)]
        ),
    )
    await _li_jobspy_source().fetch_jobs({})
    assert patches == []  # LinkedIn must not touch the Indeed date patch
