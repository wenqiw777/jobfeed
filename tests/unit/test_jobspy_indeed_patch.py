"""Unit tests for the Indeed dateOnIndeed monkeypatch.

The patch swaps ``datePublished`` for ``dateOnIndeed`` in each Indeed job dict
BEFORE JobSpy's ``_process_job`` maps ``date_posted``, so ``posted_at`` reflects
"new on Indeed" rather than the employer's original publish date. Tests are
behavioral (a crafted ``_process_job``-style input) and assert idempotency, plus
a loud failure if jobspy renames the patched symbol.
"""

from __future__ import annotations

import builtins
import sys
import types
from typing import Any

import pytest

from jobfeed.adapters.sources import _jobspy_patches as patches
from jobfeed.adapters.sources._jobspy_patches import (
    JobSpyPatchError,
    _swap_date_field,
    apply_indeed_date_patch,
)

# Distinct epoch-millis values: datePublished is months older than dateOnIndeed.
_PUBLISHED_MS = 1_695_000_000_000  # ~2023-09
_ON_INDEED_MS = 1_748_000_000_000  # ~2025-05


# ---------------------------------------------------------------------------
# pure swap function
# ---------------------------------------------------------------------------


def test_swap_prefers_date_on_indeed() -> None:
    """datePublished is overwritten by dateOnIndeed when present."""
    job = {"datePublished": _PUBLISHED_MS, "dateOnIndeed": _ON_INDEED_MS, "key": "k1"}
    swapped = _swap_date_field(job)
    assert swapped["datePublished"] == _ON_INDEED_MS
    assert swapped["key"] == "k1"
    # original dict is not mutated (shallow copy)
    assert job["datePublished"] == _PUBLISHED_MS


def test_swap_noop_without_date_on_indeed() -> None:
    """A job with no dateOnIndeed is returned unchanged."""
    job = {"datePublished": _PUBLISHED_MS, "key": "k2"}
    assert _swap_date_field(job) is job


# ---------------------------------------------------------------------------
# behavioral: patch makes _process_job read dateOnIndeed
# ---------------------------------------------------------------------------


class _FakeIndeed:
    """Stand-in for jobspy.indeed.Indeed exposing only _process_job.

    ``_process_job`` records the ``datePublished`` it actually received, which is
    the value JobSpy would map into ``date_posted``.
    """

    def __init__(self) -> None:
        self.seen_date_published: int | None = None

    def _process_job(self, job: dict[str, Any]) -> str:
        self.seen_date_published = job["datePublished"]
        return f"processed:{job['key']}"


@pytest.fixture
def fake_indeed_module(monkeypatch: pytest.MonkeyPatch) -> type[_FakeIndeed]:
    """Make ``from jobspy.indeed import Indeed`` resolve to ``_FakeIndeed``.

    Installs a fake ``jobspy.indeed`` module whose ``Indeed`` is a fresh class so
    the patch can wrap its ``_process_job`` without touching the real jobspy.
    """
    fake_class = type("Indeed", (_FakeIndeed,), {})
    module = types.ModuleType("jobspy.indeed")
    module.Indeed = fake_class  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "jobspy.indeed", module)
    return fake_class


def test_patch_makes_posted_at_follow_date_on_indeed(
    fake_indeed_module: type[_FakeIndeed],
) -> None:
    """After patching, _process_job sees dateOnIndeed in the datePublished slot."""
    apply_indeed_date_patch()
    instance = fake_indeed_module()
    job = {"datePublished": _PUBLISHED_MS, "dateOnIndeed": _ON_INDEED_MS, "key": "amzn"}
    result = instance._process_job(job)
    # The mapping JobSpy applies (datePublished -> date_posted) now uses the
    # dateOnIndeed value, not the months-old datePublished.
    assert instance.seen_date_published == _ON_INDEED_MS
    assert result == "processed:amzn"


def test_patch_is_idempotent(fake_indeed_module: type[_FakeIndeed]) -> None:
    """Calling apply twice does not double-wrap _process_job."""
    apply_indeed_date_patch()
    first = fake_indeed_module._process_job
    apply_indeed_date_patch()
    second = fake_indeed_module._process_job
    assert first is second  # second call short-circuited on the marker
    assert getattr(second, "_jobfeed_patched", False) is True


def test_patch_still_swaps_after_double_apply(
    fake_indeed_module: type[_FakeIndeed],
) -> None:
    """The swap behavior survives a redundant second apply call."""
    apply_indeed_date_patch()
    apply_indeed_date_patch()
    instance = fake_indeed_module()
    instance._process_job(
        {"datePublished": _PUBLISHED_MS, "dateOnIndeed": _ON_INDEED_MS, "key": "k"}
    )
    assert instance.seen_date_published == _ON_INDEED_MS


# ---------------------------------------------------------------------------
# loud failure when jobspy renames the symbol
# ---------------------------------------------------------------------------


def test_patch_raises_when_process_job_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A renamed/removed _process_job raises JobSpyPatchError (fails loud)."""
    module = types.ModuleType("jobspy.indeed")
    # Indeed class with no _process_job method.
    module.Indeed = type("Indeed", (), {})  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "jobspy.indeed", module)
    with pytest.raises(JobSpyPatchError, match="_process_job"):
        apply_indeed_date_patch()


def test_patch_raises_when_indeed_unimportable(monkeypatch: pytest.MonkeyPatch) -> None:
    """If jobspy.indeed cannot be imported, the patch fails loud, not silent."""
    real_import = builtins.__import__

    def _blocking(name: str, *args: object, **kwargs: object):
        if name in {"jobspy.indeed", "jobspy"}:
            raise ImportError("jobspy.indeed renamed/removed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocking)
    with pytest.raises(JobSpyPatchError):
        apply_indeed_date_patch()


def test_module_exports() -> None:
    """Public surface is the patch fn + its error type."""
    assert set(patches.__all__) == {"JobSpyPatchError", "apply_indeed_date_patch"}
