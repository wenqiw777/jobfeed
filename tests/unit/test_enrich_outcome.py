"""Unit tests for the EnrichOutcome exactly-one-signal construction contract."""

from __future__ import annotations

import pytest

from jobfeed.domain.models import QualityBand
from jobfeed.ports.enrich import EnrichOutcome
from jobfeed.ports.source import EnrichResult

_RESULT = EnrichResult(
    jd_text="Design, build, and operate distributed ingestion pipelines.",
    quality=QualityBand.FULL,
    enrich_source="linkedin_guest",
)


def test_zero_signals_raises() -> None:
    """An all-defaults outcome carries no signal and must not construct."""
    with pytest.raises(ValueError, match="exactly one signal"):
        EnrichOutcome()


def test_two_signals_raises() -> None:
    """A success result combined with a block flag must not construct."""
    with pytest.raises(ValueError, match="exactly one signal"):
        EnrichOutcome(result=_RESULT, is_blocked=True)


def test_single_signal_result_constructs() -> None:
    """A result-only outcome is valid."""
    outcome = EnrichOutcome(result=_RESULT)

    assert outcome.result is _RESULT


def test_single_signal_blocked_constructs() -> None:
    """A blocked-only outcome is valid."""
    outcome = EnrichOutcome(is_blocked=True)

    assert outcome.is_blocked is True


def test_single_signal_gone_constructs() -> None:
    """A gone-only outcome is valid."""
    outcome = EnrichOutcome(is_gone=True)

    assert outcome.is_gone is True


def test_single_signal_error_constructs() -> None:
    """An error-only outcome is valid."""
    outcome = EnrichOutcome(error="http_status:503")

    assert outcome.error == "http_status:503"
