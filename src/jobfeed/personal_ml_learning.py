"""Pure lifecycle policy for a user's lightweight relevance correction layer."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Literal, Protocol, runtime_checkable

LearningState = Literal["collecting", "ranking", "shadow", "ready", "active", "paused"]

_COLLECT_LABELS = 100
_RANKING_END = 300
_SHADOW_REQUIRED = 200
_MIN_RECALL = 0.95
_MIN_REJECTION = 0.40
_MIN_CATEGORY_RECALL = 0.90
_PAUSE_RECALL = 0.90
_MIN_CATEGORY_POSITIVES = 10


@dataclass(frozen=True, kw_only=True)
class PersonalMLObservation:
    """One Quick teacher label with an optional earlier model prediction."""

    quick_pass: bool
    model_score: float | None = None
    baseline_pass: bool = True
    family: str = "other"


@dataclass(frozen=True, kw_only=True)
class PersonalMLStatus:
    """User-facing learning state and measured quality evidence."""

    state: LearningState
    label_count: int
    ranking_count: int
    shadow_count: int
    next_target: int | None
    model_threshold: float | None
    quick_pass_recall: float | None = None
    quick_fail_rejection: float | None = None
    category_recall: float | None = None
    baseline_rejection: float | None = None
    estimated_call_reduction: float | None = None
    rolling_recall: float | None = None


@runtime_checkable
class PersonalMLObservationStore(Protocol):
    """Persistence capability required by the learning policy."""

    async def list_personal_ml_observations(
        self, *, quick_pass_threshold: int
    ) -> list[PersonalMLObservation]:
        """Return chronological Quick labels with any earlier gate scores.
        Args: Inclusive Quick-pass threshold.
        Returns: Ordered personal learning observations.
        """
        ...


class PersonalMLLearningService:
    """Read persisted learning signals and expose their current lifecycle state."""

    def __init__(self, store: PersonalMLObservationStore) -> None:
        self._store = store

    async def status(
        self, *, quick_pass_threshold: int, enabled: bool
    ) -> PersonalMLStatus:
        """Return current progress without training or activating filtering.
        Args: Quick-pass threshold and current enabled state.
        Returns: Current learning lifecycle state and measured quality.
        """
        observations = await self._store.list_personal_ml_observations(
            quick_pass_threshold=quick_pass_threshold
        )
        return assess_personal_ml(observations, enabled=enabled)


def assess_personal_ml(
    observations: list[PersonalMLObservation],
    *,
    enabled: bool = False,
) -> PersonalMLStatus:
    """Assess learning progress without changing filtering behavior.
    Args: Chronological observations and current enabled state.
    Returns: Derived lifecycle state, threshold, and validation metrics.
    """
    label_count = len(observations)
    scored_after_seed = _scored(observations[_COLLECT_LABELS:])
    ranking_required = _RANKING_END - _COLLECT_LABELS
    ranking = scored_after_seed[:ranking_required]
    shadow = scored_after_seed[ranking_required:]
    retrain_cycles = max(
        0,
        (len(shadow) - _SHADOW_REQUIRED) // _COLLECT_LABELS,
    )
    training_count = ranking_required + retrain_cycles * _COLLECT_LABELS
    training = scored_after_seed[:training_count]
    validation = scored_after_seed[training_count : training_count + _SHADOW_REQUIRED]
    threshold = _fit_recall_threshold(training)
    metrics = _metrics(validation, threshold)
    rolling_recall = _recall(_scored(observations[-100:]), threshold)

    state, next_target = _state(
        progress=_Progress(
            label_count=label_count,
            ranking_count=len(ranking),
            shadow_count=len(shadow),
            next_retrain_target=(
                label_count
                + _COLLECT_LABELS
                - (max(0, len(shadow) - _SHADOW_REQUIRED) % _COLLECT_LABELS)
            ),
        ),
        threshold=threshold,
        metrics=metrics,
        enabled=enabled,
        rolling_recall=rolling_recall,
    )
    return PersonalMLStatus(
        state=state,
        label_count=label_count,
        ranking_count=len(ranking),
        shadow_count=len(shadow),
        next_target=next_target,
        model_threshold=threshold,
        quick_pass_recall=metrics.recall,
        quick_fail_rejection=metrics.rejection,
        category_recall=metrics.category_recall,
        baseline_rejection=metrics.baseline_rejection,
        estimated_call_reduction=metrics.call_reduction,
        rolling_recall=rolling_recall,
    )


@dataclass(frozen=True, kw_only=True)
class _Metrics:
    recall: float | None = None
    rejection: float | None = None
    category_recall: float | None = None
    baseline_rejection: float | None = None
    call_reduction: float | None = None

    @property
    def passes(self) -> bool:
        return (
            self.recall is not None
            and self.recall >= _MIN_RECALL
            and self.rejection is not None
            and self.rejection >= _MIN_REJECTION
            and self.category_recall is not None
            and self.category_recall >= _MIN_CATEGORY_RECALL
            and self.baseline_rejection is not None
            and self.rejection > self.baseline_rejection
        )


@dataclass(frozen=True, kw_only=True)
class _Progress:
    label_count: int
    ranking_count: int
    shadow_count: int
    next_retrain_target: int


def _state(
    *,
    progress: _Progress,
    threshold: float | None,
    metrics: _Metrics,
    enabled: bool,
    rolling_recall: float | None,
) -> tuple[LearningState, int | None]:
    if progress.label_count < _COLLECT_LABELS:
        return "collecting", _COLLECT_LABELS
    ranking_required = _RANKING_END - _COLLECT_LABELS
    if progress.ranking_count < ranking_required:
        return (
            "ranking",
            progress.label_count + ranking_required - progress.ranking_count,
        )
    if threshold is None or progress.shadow_count < _SHADOW_REQUIRED:
        return (
            "shadow",
            progress.label_count + _SHADOW_REQUIRED - progress.shadow_count,
        )
    if enabled and rolling_recall is not None and rolling_recall < _PAUSE_RECALL:
        return "paused", None
    if enabled:
        return "active", None
    if metrics.passes:
        return "ready", None
    return "shadow", progress.next_retrain_target


def _fit_recall_threshold(
    observations: list[PersonalMLObservation],
) -> float | None:
    positives = [item for item in observations if item.quick_pass]
    negatives = [item for item in observations if not item.quick_pass]
    if not positives or not negatives:
        return None
    positive_scores = sorted(
        (item.model_score for item in positives if item.model_score is not None),
        reverse=True,
    )
    required_kept = ceil(_MIN_RECALL * len(positives))
    if len(positive_scores) < required_kept:
        return None
    # Rejection is monotonic as the threshold rises, so the highest threshold
    # that still keeps the required positive count is exactly the former
    # exhaustive-search winner. Selecting the quantile avoids rescanning the
    # complete history once per distinct model score.
    return positive_scores[required_kept - 1]


def _metrics(
    observations: list[PersonalMLObservation], threshold: float | None
) -> _Metrics:
    if threshold is None or len(observations) < _SHADOW_REQUIRED:
        return _Metrics()
    rejection = _rejection(observations, threshold)
    rejected = sum(
        item.model_score is not None and item.model_score < threshold
        for item in observations
    )
    return _Metrics(
        recall=_recall(observations, threshold),
        rejection=rejection,
        category_recall=_category_recall(observations, threshold),
        baseline_rejection=_baseline_rejection(observations),
        call_reduction=rejected / len(observations),
    )


def _category_recall(
    observations: list[PersonalMLObservation], threshold: float
) -> float | None:
    families = {item.family for item in observations if item.quick_pass}
    recalls = [
        recall
        for family in families
        if len(
            positives := [
                item
                for item in observations
                if item.quick_pass and item.family == family
            ]
        )
        >= _MIN_CATEGORY_POSITIVES
        if (recall := _recall(positives, threshold)) is not None
    ]
    return min(recalls) if recalls else None


def _recall(
    observations: list[PersonalMLObservation], threshold: float | None
) -> float | None:
    if threshold is None:
        return None
    positives = [item for item in observations if item.quick_pass]
    if not positives:
        return None
    kept = sum(
        item.model_score is not None and item.model_score >= threshold
        for item in positives
    )
    return kept / len(positives)


def _rejection(
    observations: list[PersonalMLObservation], threshold: float
) -> float | None:
    negatives = [item for item in observations if not item.quick_pass]
    if not negatives:
        return None
    rejected = sum(
        item.model_score is not None and item.model_score < threshold
        for item in negatives
    )
    return rejected / len(negatives)


def _baseline_rejection(observations: list[PersonalMLObservation]) -> float | None:
    negatives = [item for item in observations if not item.quick_pass]
    if not negatives:
        return None
    return sum(not item.baseline_pass for item in negatives) / len(negatives)


def _scored(
    observations: list[PersonalMLObservation],
) -> list[PersonalMLObservation]:
    return [item for item in observations if item.model_score is not None]
