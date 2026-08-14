"""Performance route tests: correct JSON shapes via mocked service.

Each route is tested for its default-window happy path, returning the
expected Pydantic-serialized structure. The PerformanceService is stubbed
on ``app.state`` so no database is required.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jobfeed.domain.models_perf import (
    FunnelStats,
    LLMDailyStats,
    PerformanceOverview,
    StepTimingSeries,
)
from jobfeed.web.app import build_web_app
from tests.web.test_app_skeleton import fake_context, open_client

HTTP_OK = 200
HTTP_VALIDATION_ERROR = 422

_FIXED_DT = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)

# Canned fixture values used in both the stub and assertions.
_SCAN_MS = 1500.0
_EVAL_MS = 8000.0
_LLM_COST = 4.20
_ERROR_RATE = 0.1
_SCAN_DELTA = -0.05
_EVAL_DELTA = 0.12
_STEP_ELAPSED_MS = 1234.5
_P50 = 150.0
_P95 = 450.0
_AVG_IN = 800.0
_AVG_OUT = 200.0
_CALL_COUNT = 4
_FUNNEL_TOTAL = 100
_FUNNEL_FILTER = 80
_FUNNEL_GATE = 60
_FUNNEL_SCORED = 50


class FakePerformanceService:
    """Stub returning canned performance data for route tests."""

    async def get_overview(self, _window: int) -> PerformanceOverview:
        """Return a canned overview.

        Args:
            _window: Ignored.

        Returns:
            Fixed performance overview.
        """
        return PerformanceOverview(
            avg_scan_duration_ms=_SCAN_MS,
            avg_eval_duration_ms=_EVAL_MS,
            total_llm_cost_usd=_LLM_COST,
            error_rate=_ERROR_RATE,
            scan_duration_delta=_SCAN_DELTA,
            eval_duration_delta=_EVAL_DELTA,
            cost_delta=None,
            error_rate_delta=None,
        )

    async def get_step_timings(
        self, _window: int, _step_type: str | None = None
    ) -> list[StepTimingSeries]:
        """Return a canned step timing list.

        Args:
            _window: Ignored.
            _step_type: Ignored.

        Returns:
            One-element step timing list.
        """
        return [
            StepTimingSeries(
                step_type="scan",
                step_name="ats_greenhouse",
                run_id="run-1",
                elapsed_ms=_STEP_ELAPSED_MS,
                is_error=False,
                created_at=_FIXED_DT,
            )
        ]

    async def get_llm_stats(self, _window: int) -> list[LLMDailyStats]:
        """Return a canned LLM daily stats list.

        Args:
            _window: Ignored.

        Returns:
            One-element LLM stats list.
        """
        return [
            LLMDailyStats(
                day="2026-06-16",
                model="gpt-mini",
                stage="a",
                p50_latency_ms=_P50,
                p95_latency_ms=_P95,
                call_count=_CALL_COUNT,
                avg_input_tokens=_AVG_IN,
                avg_output_tokens=_AVG_OUT,
            )
        ]

    async def get_funnel_stats(self, _window: int) -> list[FunnelStats]:
        """Return a canned funnel stats list.

        Args:
            _window: Ignored.

        Returns:
            One-element funnel list.
        """
        return [
            FunnelStats(
                run_id="run-1",
                total_candidates=_FUNNEL_TOTAL,
                after_filter=_FUNNEL_FILTER,
                after_gate=_FUNNEL_GATE,
                scored=_FUNNEL_SCORED,
            )
        ]


@pytest.fixture
def _app():
    """Build a web app with a fake performance service on state."""
    app = build_web_app(fake_context())
    app.state.performance_service = FakePerformanceService()
    return app


# ------------------------------------------------------------------
# Overview
# ------------------------------------------------------------------


async def test_overview_returns_correct_shape(_app) -> None:
    """GET /api/performance/overview returns the expected fields."""
    async with open_client(_app) as client:
        resp = await client.get("/api/performance/overview")

    assert resp.status_code == HTTP_OK
    body = resp.json()
    assert body["avg_scan_duration_ms"] == _SCAN_MS
    assert body["avg_eval_duration_ms"] == _EVAL_MS
    assert body["total_llm_cost_usd"] == pytest.approx(_LLM_COST)
    assert body["error_rate"] == pytest.approx(_ERROR_RATE)
    assert body["scan_duration_delta"] == pytest.approx(_SCAN_DELTA)
    assert body["eval_duration_delta"] == pytest.approx(_EVAL_DELTA)
    assert body["cost_delta"] is None
    assert body["error_rate_delta"] is None


async def test_overview_window_validation(_app) -> None:
    """Out-of-range window returns 422."""
    async with open_client(_app) as client:
        too_low = await client.get("/api/performance/overview", params={"window": 0})
        too_high = await client.get("/api/performance/overview", params={"window": 366})

    assert too_low.status_code == HTTP_VALIDATION_ERROR
    assert too_high.status_code == HTTP_VALIDATION_ERROR


# ------------------------------------------------------------------
# Step timings
# ------------------------------------------------------------------


async def test_step_timings_returns_list(_app) -> None:
    """GET /api/performance/step-timings returns a timings array."""
    async with open_client(_app) as client:
        resp = await client.get("/api/performance/step-timings")

    assert resp.status_code == HTTP_OK
    body = resp.json()
    assert len(body["timings"]) == 1
    row = body["timings"][0]
    assert row["step_type"] == "scan"
    assert row["step_name"] == "ats_greenhouse"
    assert row["run_id"] == "run-1"
    assert row["elapsed_ms"] == pytest.approx(_STEP_ELAPSED_MS)
    assert row["is_error"] is False
    assert "created_at" in row


# ------------------------------------------------------------------
# LLM stats
# ------------------------------------------------------------------


async def test_llm_stats_returns_list(_app) -> None:
    """GET /api/performance/llm-stats returns a stats array."""
    async with open_client(_app) as client:
        resp = await client.get("/api/performance/llm-stats")

    assert resp.status_code == HTTP_OK
    body = resp.json()
    assert len(body["stats"]) == 1
    row = body["stats"][0]
    assert row["day"] == "2026-06-16"
    assert row["model"] == "gpt-mini"
    assert row["stage"] == "a"
    assert row["p50_latency_ms"] == pytest.approx(_P50)
    assert row["p95_latency_ms"] == pytest.approx(_P95)
    assert row["call_count"] == _CALL_COUNT
    assert row["avg_input_tokens"] == pytest.approx(_AVG_IN)
    assert row["avg_output_tokens"] == pytest.approx(_AVG_OUT)


# ------------------------------------------------------------------
# Funnel
# ------------------------------------------------------------------


async def test_funnel_returns_list(_app) -> None:
    """GET /api/performance/funnel returns a funnel array."""
    async with open_client(_app) as client:
        resp = await client.get("/api/performance/funnel")

    assert resp.status_code == HTTP_OK
    body = resp.json()
    assert len(body["funnel"]) == 1
    row = body["funnel"][0]
    assert row["run_id"] == "run-1"
    assert row["total_candidates"] == _FUNNEL_TOTAL
    assert row["after_filter"] == _FUNNEL_FILTER
    assert row["after_gate"] == _FUNNEL_GATE
    assert row["scored"] == _FUNNEL_SCORED
