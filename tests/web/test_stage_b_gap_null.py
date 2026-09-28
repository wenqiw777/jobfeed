"""Historical detailed scores may have no mitigation text."""

from jobfeed.web.schemas._jobs_detail_stage_b import StageBDetail

HISTORICAL_FIT_SCORE = 78


def test_historical_gap_with_null_mitigation_still_displays_score() -> None:
    detail = StageBDetail.model_validate(
        {
            "verdict": "apply",
            "fit_score": HISTORICAL_FIT_SCORE,
            "gaps": [
                {"requirement": "Experience", "severity": "minor", "mitigation": None}
            ],
        }
    )
    assert detail.fit_score == HISTORICAL_FIT_SCORE
    assert detail.gaps[0].mitigation == ""
