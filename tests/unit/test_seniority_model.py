"""Tests for the independent seniority XGBoost model adapter."""

import shutil
from pathlib import Path

import numpy as np
import pytest

from jobfeed.adapters.ml.seniority_model import XGBoostSeniorityModel
from jobfeed.domain.seniority import SeniorityInput
from jobfeed.seniority_training import choose_recall_threshold

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
EMBED_DIM = 384
EXPECTED_SCORE_COUNT = 2
MAX_IN_SCOPE_SCORE = 0.6


class _FakeEmbedder:
    def format_input(self, title: str, jd_text: str) -> str:
        return f"{title} | {jd_text}"

    def embed_batch(self, texts: list[str]) -> np.ndarray:
        return np.zeros((len(texts), EMBED_DIM), dtype=np.float32)


def _model_dir(tmp_path: Path) -> Path:
    shutil.copy(
        FIXTURES / "ml_gate_tiny_model.json",
        tmp_path / "v20260101T000000Z.json",
    )
    shutil.copy(
        FIXTURES / "ml_gate_tiny_model.meta.json",
        tmp_path / "v20260101T000000Z.meta.json",
    )
    return tmp_path


@pytest.mark.asyncio
async def test_seniority_model_returns_one_probability_per_input(
    tmp_path: Path,
) -> None:
    model = XGBoostSeniorityModel(
        model_dir=_model_dir(tmp_path), embedder=_FakeEmbedder()
    )
    jobs = [
        SeniorityInput("1", "Software Engineer II", "Build services."),
        SeniorityInput("2", "Senior Engineer", "Own production systems."),
    ]

    scores = await model.predict_out_of_scope(jobs)

    assert len(scores) == EXPECTED_SCORE_COUNT
    assert all(0.0 <= score <= 1.0 for score in scores)


def test_threshold_prioritizes_in_scope_recall() -> None:
    labels = [0, 0, 0, 1, 1]
    scores = [0.05, 0.1, 0.6, 0.7, 0.9]

    threshold = choose_recall_threshold(labels, scores, minimum_in_scope_recall=1.0)

    assert threshold > MAX_IN_SCOPE_SCORE
    predicted = [score >= threshold for score in scores]
    assert predicted[:3] == [False, False, False]
    assert any(predicted[3:])
