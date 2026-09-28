"""Per-posting gate state and conversion to persisted model decisions."""

from jobfeed.domain.ml_features import (
    MLGateFeatures,
    clearly_nonsoftware_title,
    hard_fail_reason,
)
from jobfeed.domain.models import MLGateResult
from jobfeed.ports.ml_gate import GateInput

FAIL_SCORE = 0.0


class _RowState:
    """Mutable per-input scratch: features, hard-fail verdict, model verdict."""

    def __init__(
        self,
        features: MLGateFeatures,
        job: GateInput,
        *,
        apply_legacy_hard_fail: bool = True,
    ) -> None:
        self.features = features
        self.job = job
        self.hard_fail = hard_fail_reason(features) if apply_legacy_hard_fail else None
        if clearly_nonsoftware_title(job.title):
            self.hard_fail = "not software engineering role"
        self.result = "fail"
        self.fail_reason: str | None = self.hard_fail
        self.score = FAIL_SCORE

    def apply_model_score(
        self, score: float, threshold: float, is_sde_classifier: bool
    ) -> None:
        """Record the model verdict for a non-hard-failed row."""
        self.score = score
        if score >= threshold:
            self.result = "pass"
            self.fail_reason = None
        else:
            self.result = "fail"
            self.fail_reason = (
                "not software engineering role" if is_sde_classifier else None
            )

    def to_result(self, version: str, is_sde_classifier: bool) -> MLGateResult:
        """Build the ordered ``MLGateResult``, coercing int columns to bool."""
        features = self.features
        return MLGateResult(
            score=self.score,
            result=self.result,
            fail_reason=self.fail_reason,
            version=version,
            is_swe_role=(self.result == "pass")
            if is_sde_classifier
            else features.is_swe_role,
            seniority_level=features.seniority_level,
            degree_required=features.degree_required,
            clearance_required=bool(features.clearance_required),
            school_restricted=bool(features.school_restricted),
            yoe_min=features.yoe_min,
            domain_tags=features.domain_tags,
            tech_required=features.tech_required,
            role_type=features.role_type,
        )
