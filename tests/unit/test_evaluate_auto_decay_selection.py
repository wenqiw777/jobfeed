"""Evaluate must sweep the workflow belonging to its selected data model."""

from types import SimpleNamespace
from typing import cast

import pytest

from jobfeed.domain.models import AutoDecayResult
from jobfeed.services._evaluate_helpers import run_auto_decay
from jobfeed.services.evaluate_types import EvaluateDependencies, EvaluateRuntimeConfig


class _DualStatusStore:
    def __init__(self) -> None:
        self.called: list[str] = []

    async def auto_decay(self, **_kwargs: int) -> AutoDecayResult:
        self.called.append("source")
        return AutoDecayResult(ghosted=0, archived=0)

    async def auto_decay_real_jobs(self, **_kwargs: int) -> AutoDecayResult:
        self.called.append("canonical")
        return AutoDecayResult(ghosted=0, archived=0)


@pytest.mark.parametrize(
    ("canonical", "expected"), [(False, "source"), (True, "canonical")]
)
async def test_auto_decay_uses_selected_evaluation_model(
    canonical: bool, expected: str
) -> None:
    store = _DualStatusStore()
    deps = cast(EvaluateDependencies, SimpleNamespace(store_status=store))
    config = cast(
        EvaluateRuntimeConfig, SimpleNamespace(ghost_days=30, archive_ignored_days=14)
    )
    logger = SimpleNamespace(info=lambda *_args, **_kwargs: None)

    await run_auto_decay(deps, config, logger, canonical=canonical)

    assert store.called == [expected]
