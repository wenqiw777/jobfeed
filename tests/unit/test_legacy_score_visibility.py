"""Missing historic policy records do not hide existing scores."""

import json

import pytest

from jobfeed.domain.real_job_evaluation import policy_visibility


@pytest.mark.parametrize(
    "facts",
    [
        None,
        "{}",
        '{"stage_a_policy":null}',
        '{"stage_a_policy":{"model":"quick"}}',
        '{"stage_b_policy":{"model":"detail"}}',
    ],
)
def test_missing_policy_keeps_scores_visible(facts: str | None) -> None:
    assert policy_visibility(
        facts,
        stage_a_policy={"model": "quick"},
        stage_b_policy={"model": "detail"},
    ) == (True, True, None)


def test_recorded_policy_change_remains_distinct_from_missing_policy() -> None:
    assert policy_visibility(
        json.dumps({"stage_b_policy": {"model": "old-detail"}}),
        stage_a_policy={"model": "quick"},
        stage_b_policy={"model": "detail"},
    ) == (True, False, "stage_b_policy_changed")
