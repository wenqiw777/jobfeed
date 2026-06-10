"""Unit tests for Phase 6 evaluate_types changes.

Tests cover:
- EvaluateRuntimeConfig new ghost_days and archive_ignored_days fields
- EvaluateDependencies new store_status field
- EvaluateService.run() integrates auto_decay sweep before evaluation
"""

from __future__ import annotations

import asyncio
from dataclasses import fields
from unittest.mock import AsyncMock, MagicMock

import pytest

from jobfeed.domain.models_status import AutoDecayResult
from jobfeed.services.evaluate_types import (
    EvaluateDependencies,
    EvaluateLLMConfig,
    EvaluateRuntimeConfig,
)


# ---------------------------------------------------------------------------
# EvaluateRuntimeConfig
# ---------------------------------------------------------------------------


class TestEvaluateRuntimeConfigPhase6:
    """Tests for new fields in EvaluateRuntimeConfig."""

    def test_ghost_days_default_is_30(self) -> None:
        """ghost_days should default to 30."""
        field_map = {f.name: f for f in fields(EvaluateRuntimeConfig)}
        assert field_map["ghost_days"].default == 30

    def test_archive_ignored_days_default_is_14(self) -> None:
        """archive_ignored_days should default to 14."""
        field_map = {f.name: f for f in fields(EvaluateRuntimeConfig)}
        assert field_map["archive_ignored_days"].default == 14

    def test_runtime_config_accepts_custom_ghost_days(self) -> None:
        """EvaluateRuntimeConfig should accept a custom ghost_days value."""
        llm = EvaluateLLMConfig(
            stage_a="mock/stage-a",
            stage_b="mock/stage-b",
            max_concurrent=2,
            max_daily_score_calls=100,
            max_daily_cost_usd=10.0,
        )
        config = EvaluateRuntimeConfig(
            llm=llm,
            stage_a_threshold=60,
            resume_text="test",
            ghost_days=45,
        )
        assert config.ghost_days == 45

    def test_runtime_config_accepts_custom_archive_days(self) -> None:
        """EvaluateRuntimeConfig should accept a custom archive_ignored_days value."""
        llm = EvaluateLLMConfig(
            stage_a="mock/stage-a",
            stage_b="mock/stage-b",
            max_concurrent=2,
            max_daily_score_calls=100,
            max_daily_cost_usd=10.0,
        )
        config = EvaluateRuntimeConfig(
            llm=llm,
            stage_a_threshold=60,
            resume_text="test",
            archive_ignored_days=7,
        )
        assert config.archive_ignored_days == 7

    def test_runtime_config_is_frozen(self) -> None:
        """EvaluateRuntimeConfig should be a frozen dataclass."""
        llm = EvaluateLLMConfig(
            stage_a="mock/stage-a",
            stage_b="mock/stage-b",
            max_concurrent=2,
            max_daily_score_calls=100,
            max_daily_cost_usd=10.0,
        )
        config = EvaluateRuntimeConfig(
            llm=llm,
            stage_a_threshold=60,
            resume_text="test",
        )
        with pytest.raises((AttributeError, TypeError)):
            config.ghost_days = 99  # type: ignore[misc]

    def test_runtime_config_has_both_new_fields(self) -> None:
        """EvaluateRuntimeConfig field set should include both new decay fields."""
        field_names = {f.name for f in fields(EvaluateRuntimeConfig)}
        assert "ghost_days" in field_names
        assert "archive_ignored_days" in field_names

    def test_runtime_config_defaults_match_domain_constants(self) -> None:
        """Default values should match the domain-layer constants."""
        from jobfeed.domain.status import (
            DEFAULT_ARCHIVE_IGNORED_DAYS,
            DEFAULT_GHOST_DAYS,
        )

        field_map = {f.name: f for f in fields(EvaluateRuntimeConfig)}
        assert field_map["ghost_days"].default == DEFAULT_GHOST_DAYS
        assert field_map["archive_ignored_days"].default == DEFAULT_ARCHIVE_IGNORED_DAYS


# ---------------------------------------------------------------------------
# EvaluateDependencies
# ---------------------------------------------------------------------------


class TestEvaluateDependenciesPhase6:
    """Tests for Phase 6 additions to EvaluateDependencies."""

    def test_dependencies_has_store_status_field(self) -> None:
        """EvaluateDependencies should have a store_status field."""
        field_names = {f.name for f in fields(EvaluateDependencies)}
        assert "store_status" in field_names

    def test_dependencies_store_status_has_no_default(self) -> None:
        """store_status should be required (no default value)."""
        import dataclasses

        field_map = {f.name: f for f in fields(EvaluateDependencies)}
        store_status_field = field_map["store_status"]
        # Required fields have MISSING as their default
        assert store_status_field.default is dataclasses.MISSING
        assert store_status_field.default_factory is dataclasses.MISSING

    def test_dependencies_is_frozen(self) -> None:
        """EvaluateDependencies should be a frozen dataclass."""
        deps = EvaluateDependencies(
            store=MagicMock(),
            store_ops=MagicMock(),
            store_status=MagicMock(),
            prompt_renderer=MagicMock(),
            llm_stage_a=MagicMock(),
            llm_stage_b=MagicMock(),
        )
        with pytest.raises((AttributeError, TypeError)):
            deps.store = MagicMock()  # type: ignore[misc]

    def test_dependencies_store_status_is_required(self) -> None:
        """store_status should be a named field that participates in construction."""
        store_status_mock = MagicMock()
        deps = EvaluateDependencies(
            store=MagicMock(),
            store_ops=MagicMock(),
            store_status=store_status_mock,
            prompt_renderer=MagicMock(),
            llm_stage_a=MagicMock(),
            llm_stage_b=MagicMock(),
        )
        assert deps.store_status is store_status_mock


# ---------------------------------------------------------------------------
# Helpers for EvaluateService tests
# ---------------------------------------------------------------------------


def _make_store() -> AsyncMock:
    """Build a minimal mock store for EvaluateService tests."""
    store = AsyncMock()
    # auto-mock attribute access: all calls return AsyncMock by default
    return store


def _make_deps(store_status: AsyncMock) -> EvaluateDependencies:
    """Build EvaluateDependencies with the given store_status mock."""
    return EvaluateDependencies(
        store=_make_store(),
        store_ops=AsyncMock(),
        store_status=store_status,
        prompt_renderer=MagicMock(),
        llm_stage_a=MagicMock(),
        llm_stage_b=MagicMock(),
    )


def _make_config(*, ghost_days: int = 30, archive_ignored_days: int = 14) -> EvaluateRuntimeConfig:
    """Build an EvaluateRuntimeConfig with the given decay settings."""
    llm_cfg = EvaluateLLMConfig(
        stage_a="mock/stage-a",
        stage_b="mock/stage-b",
        max_concurrent=1,
        max_daily_score_calls=0,
        max_daily_cost_usd=0.0,
    )
    return EvaluateRuntimeConfig(
        llm=llm_cfg,
        stage_a_threshold=60,
        resume_text="test resume",
        ghost_days=ghost_days,
        archive_ignored_days=archive_ignored_days,
    )


# ---------------------------------------------------------------------------
# EvaluateService auto_decay integration
# ---------------------------------------------------------------------------


class TestEvaluateServiceAutoDecay:
    """Tests that EvaluateService.run() calls auto_decay before scoring."""

    def test_run_calls_auto_decay_before_scoring(self) -> None:
        """run() should call store_status.auto_decay on non-dry-run."""
        from jobfeed.services.evaluate import EvaluateService

        store_status = AsyncMock()
        store_status.auto_decay.return_value = AutoDecayResult(ghosted=0, archived=0)
        deps = _make_deps(store_status)
        config = _make_config(ghost_days=25, archive_ignored_days=10)
        svc = EvaluateService(deps=deps, config=config, logger=MagicMock())

        asyncio.run(svc.run(stage="both", dry_run=False))

        store_status.auto_decay.assert_awaited_once_with(
            ghost_days=25,
            archive_ignored_days=10,
        )

    def test_run_skips_auto_decay_on_dry_run(self) -> None:
        """run(dry_run=True) should NOT call auto_decay."""
        from jobfeed.services.evaluate import EvaluateService

        store_status = AsyncMock()
        store_status.auto_decay.return_value = AutoDecayResult(ghosted=0, archived=0)
        deps = _make_deps(store_status)
        config = _make_config()
        svc = EvaluateService(deps=deps, config=config, logger=MagicMock())

        asyncio.run(svc.run(stage="both", dry_run=True))

        store_status.auto_decay.assert_not_awaited()

    def test_run_forwards_ghost_days_from_config(self) -> None:
        """run() should forward ghost_days from config to auto_decay."""
        from jobfeed.services.evaluate import EvaluateService

        store_status = AsyncMock()
        store_status.auto_decay.return_value = AutoDecayResult(ghosted=0, archived=0)
        deps = _make_deps(store_status)
        config = _make_config(ghost_days=7, archive_ignored_days=3)
        svc = EvaluateService(deps=deps, config=config, logger=MagicMock())

        asyncio.run(svc.run(stage="a"))

        call_kwargs = store_status.auto_decay.call_args.kwargs
        assert call_kwargs["ghost_days"] == 7
        assert call_kwargs["archive_ignored_days"] == 3

    def test_run_auto_decay_with_ghost_result_still_proceeds(self) -> None:
        """run() should still proceed after auto_decay reports ghosts/archives."""
        from jobfeed.services.evaluate import EvaluateService

        store_status = AsyncMock()
        store_status.auto_decay.return_value = AutoDecayResult(ghosted=5, archived=3)
        deps = _make_deps(store_status)
        config = _make_config()
        svc = EvaluateService(deps=deps, config=config, logger=MagicMock())

        # Should not raise - the method proceeds past auto_decay
        asyncio.run(svc.run(stage="both"))

        store_status.auto_decay.assert_awaited_once()