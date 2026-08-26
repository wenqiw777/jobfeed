"""Personal relevance-learning policy tests."""

# ruff: noqa: PLR2004

from time import perf_counter

from jobfeed.personal_ml_learning import (
    PersonalMLObservation,
    assess_personal_ml,
)


def _observation(
    *,
    label: bool,
    score: float | None = None,
    baseline_pass: bool = True,
    family: str = "backend",
) -> PersonalMLObservation:
    return PersonalMLObservation(
        quick_pass=label,
        model_score=score,
        baseline_pass=baseline_pass,
        family=family,
    )


def test_large_history_assessment_stays_below_interactive_budget() -> None:
    """Status reads must not refit the threshold with a quadratic scan."""
    seed = [_observation(label=True)] * 100
    history = [
        _observation(
            label=index % 3 != 0,
            score=(index % 997) / 1000,
            family="backend" if index % 2 else "frontend",
        )
        for index in range(9_900)
    ]

    started = perf_counter()
    status = assess_personal_ml(seed + history, enabled=True)
    elapsed = perf_counter() - started

    assert status.label_count == 10_000
    assert elapsed < 0.25


def test_collects_first_100_quick_labels_before_scoring() -> None:
    status = assess_personal_ml([_observation(label=True)] * 37)

    assert status.state == "collecting"
    assert status.label_count == 37
    assert status.next_target == 100
    assert status.model_threshold is None


def test_ranking_window_fits_user_threshold_without_filtering() -> None:
    seed = [_observation(label=True)] * 100
    ranking = [
        *[_observation(label=True, score=0.8) for _ in range(100)],
        *[_observation(label=False, score=0.2) for _ in range(100)],
    ]

    status = assess_personal_ml(seed + ranking)

    assert status.state == "shadow"
    assert status.ranking_count == 200
    assert status.model_threshold == 0.8
    assert status.next_target == 500


def test_batch_overshoot_does_not_leave_learning_stuck_before_shadow() -> None:
    seed = [_observation(label=True)] * 100
    overshoot = [_observation(label=True)] * 40
    ranking = [
        *[_observation(label=True, score=0.8) for _ in range(100)],
        *[_observation(label=False, score=0.2) for _ in range(100)],
    ]

    status = assess_personal_ml(seed + overshoot + ranking)

    assert status.state == "shadow"
    assert status.ranking_count == 200


def test_shadow_becomes_ready_only_after_future_quality_gate() -> None:
    seed = [_observation(label=True)] * 100
    ranking = [
        *[_observation(label=True, score=0.8) for _ in range(100)],
        *[_observation(label=False, score=0.2) for _ in range(100)],
    ]
    shadow = [
        *[_observation(label=True, score=0.9, family="backend") for _ in range(100)],
        *[
            _observation(
                label=False,
                score=0.1,
                baseline_pass=True,
                family="backend",
            )
            for _ in range(100)
        ],
    ]

    status = assess_personal_ml(seed + ranking + shadow)

    assert status.state == "ready"
    assert status.shadow_count == 200
    assert status.quick_pass_recall == 1.0
    assert status.quick_fail_rejection == 1.0
    assert status.estimated_call_reduction == 0.5
    assert status.next_target is None


def test_low_category_recall_keeps_model_in_shadow() -> None:
    seed = [_observation(label=True)] * 100
    ranking = [
        *[_observation(label=True, score=0.8) for _ in range(100)],
        *[_observation(label=False, score=0.2) for _ in range(100)],
    ]
    shadow = [
        *[_observation(label=True, score=0.9, family="backend") for _ in range(90)],
        *[_observation(label=True, score=0.1, family="intern") for _ in range(10)],
        *[_observation(label=False, score=0.1) for _ in range(100)],
    ]

    status = assess_personal_ml(seed + ranking + shadow)

    assert status.state == "shadow"
    assert status.category_recall == 0.0


def test_failed_shadow_retrains_threshold_after_100_more_labels() -> None:
    seed = [_observation(label=True)] * 100
    ranking = [
        *[_observation(label=True, score=0.8) for _ in range(100)],
        *[_observation(label=False, score=0.7) for _ in range(100)],
    ]
    first_shadow = [
        *[_observation(label=True, score=0.6) for _ in range(100)],
        *[_observation(label=False, score=0.5) for _ in range(100)],
    ]
    next_labels = [
        *[_observation(label=True, score=0.65) for _ in range(50)],
        *[_observation(label=False, score=0.4) for _ in range(50)],
    ]

    before = assess_personal_ml(seed + ranking + first_shadow)
    after = assess_personal_ml(seed + ranking + first_shadow + next_labels)

    assert before.state == "shadow"
    assert after.state == "ready"
    assert after.model_threshold == 0.6


def test_active_model_pauses_when_recent_recall_falls_below_90_percent() -> None:
    seed = [_observation(label=True)] * 100
    ranking = [
        *[_observation(label=True, score=0.8) for _ in range(100)],
        *[_observation(label=False, score=0.2) for _ in range(100)],
    ]
    healthy_shadow = [
        *[_observation(label=True, score=0.9) for _ in range(100)],
        *[_observation(label=False, score=0.1) for _ in range(100)],
    ]
    drift = [
        *[_observation(label=True, score=0.1) for _ in range(11)],
        *[_observation(label=True, score=0.9) for _ in range(89)],
    ]

    status = assess_personal_ml(seed + ranking + healthy_shadow + drift, enabled=True)

    assert status.state == "paused"
    assert status.rolling_recall == 0.89
