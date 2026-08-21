"""Wire models for gradual personal relevance learning."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from jobfeed.personal_ml_learning import LearningState, PersonalMLStatus


class PersonalMLStatusResponse(BaseModel):
    """User-visible progress and shadow-quality evidence."""

    model_config = ConfigDict(extra="forbid")

    state: LearningState
    label_count: int
    ranking_count: int
    shadow_count: int
    next_target: int | None
    model_threshold: float | None
    quick_pass_recall: float | None
    quick_fail_rejection: float | None
    category_recall: float | None
    baseline_rejection: float | None
    estimated_call_reduction: float | None
    rolling_recall: float | None

    @classmethod
    def from_domain(cls, status: PersonalMLStatus) -> PersonalMLStatusResponse:
        """Build the API response from the pure policy result.

        Args:
            status: Pure personal-learning lifecycle result.

        Returns:
            Validated wire response.
        """
        return cls.model_validate(status.__dict__)


__all__ = ["PersonalMLStatusResponse"]
