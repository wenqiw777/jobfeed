"""Optional canonical persistence capabilities used after backend selection."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from jobfeed.domain.canonical_priority import CanonicalPriorityInput
from jobfeed.domain.interview import InterviewRound
from jobfeed.domain.models import MLGateResult, StageAResult, StageBResult
from jobfeed.domain.models_application import (
    ApplicationRecord,
    ApplicationStats,
    RealJobApplicationEvent,
    ResumeSnapshot,
)
from jobfeed.domain.models_status import (
    AutoDecayResult,
    BulkResult,
    BulkTransitionRequest,
    StatusFilter,
    StatusInfo,
    TransitionRequest,
    WorkflowAttention,
)
from jobfeed.domain.real_job_evaluation import RealJobEvaluationInput


class CanonicalEvaluationStore(Protocol):
    """Explicit capability contract for canonical persistence."""

    async def resolve_real_job_ids(self, source_ids: list[str]) -> list[str]:
        """Resolve a source scope once and reject orphaned source rows.

        Args:
            source_ids: Source posting IDs whose canonical parents are required.

        Returns:
            Distinct canonical IDs in first-source order.

        Raises:
            ValueError: If an ID is invalid or a requested source lacks a canonical
                parent.
        """
        ...

    async def list_real_job_ids_for_evaluation(
        self,
        *,
        limit: int,
        stage: str = "both",
        threshold: int = 0,
        before_id: int | None = None,
        stage_a_policy: dict[str, object] | None = None,
        stage_b_policy: dict[str, object] | None = None,
    ) -> list[str]:
        """Bound backlog selection before canonical claim filtering.

        Args:
            limit: Maximum number of records to select.
            stage: Evaluation stage: a, b, or both.
            threshold: Minimum Stage A score for Stage B eligibility.
            before_id: Exclusive canonical-ID bound for descending pagination.
            stage_a_policy: Configured Stage A policy used to verify stored scores.
            stage_b_policy: Configured Stage B policy used to verify stored scores.

        Returns:
            Bounded canonical IDs eligible for the requested evaluation stages.

        Raises:
            ValueError: If stage is not a, b, or both.
        """
        ...

    async def load_real_job_priority_inputs(
        self,
        real_job_ids: list[str],
        *,
        stage_a_policy: dict[str, object] | None = None,
        stage_b_policy: dict[str, object] | None = None,
    ) -> list[CanonicalPriorityInput]:
        """Return one canonical priority input per requested parent.

        Args:
            real_job_ids: Canonical parent IDs to load.
            stage_a_policy: Configured Stage A policy used to verify stored scores.
            stage_b_policy: Configured Stage B policy used to verify stored scores.

        Returns:
            Priority inputs for existing requested canonical jobs.
        """
        ...

    async def claim_real_job_stage_a_by_ids(
        self,
        real_job_ids: list[str],
        *,
        limit: int = 100,
        max_days: int | None = None,
        stage_a_policy: dict[str, object] | None = None,
        stage_b_policy: dict[str, object] | None = None,
    ) -> list[RealJobEvaluationInput]:
        """Atomically claim eligible parents for one Stage A evaluation each.

        Args:
            real_job_ids: Canonical parents to consider.
            limit: Maximum number of claims to return.
            max_days: Optional age limit for the selected posting.
            stage_a_policy: Explicit quick-score model and gate policy.
            stage_b_policy: Explicit detailed-score model policy.

        Returns:
            Selected inputs with revision and claim-generation fences.
        """
        ...

    async def save_real_job_stage_a(
        self,
        real_job_id: str,
        result: StageAResult,
        *,
        expected_revision: int,
        expected_generation: int,
    ) -> bool:
        """Save Stage A only while this worker owns the current claim.

        Args:
            real_job_id: Canonical parent to update.
            result: Completed quick evaluation.
            expected_revision: Claimed input revision.
            expected_generation: Claimed lease generation.

        Returns:
            Whether the fenced write succeeded.
        """
        ...

    async def save_real_job_ml_gate(
        self,
        real_job_id: str,
        result: MLGateResult,
        *,
        expected_revision: int,
        expected_generation: int,
    ) -> bool:
        """Save gate evidence only for the current Stage A owner.

        Args:
            real_job_id: Canonical parent to update.
            result: Gate decision and evidence.
            expected_revision: Claimed input revision.
            expected_generation: Claimed lease generation.

        Returns:
            Whether the fenced write succeeded.
        """
        ...

    async def claim_real_job_stage_b_by_ids(
        self,
        real_job_ids: list[str],
        *,
        stage_a_threshold: int,
        limit: int = 100,
        max_days: int | None = None,
        stage_a_policy: dict[str, object] | None = None,
        stage_b_policy: dict[str, object] | None = None,
    ) -> list[RealJobEvaluationInput]:
        """Claim eligible scored parents for detailed evaluation.

        Args:
            real_job_ids: Canonical parents to consider.
            stage_a_threshold: Minimum Stage A score.
            limit: Maximum number of claims to return.
            max_days: Optional age limit for the selected posting.
            stage_a_policy: Quick-score policy required by this claim.
            stage_b_policy: Detailed-score policy to apply.

        Returns:
            Selected inputs with current Stage A scores and claim fences.
        """
        ...

    async def save_real_job_stage_b(
        self,
        real_job_id: str,
        result: StageBResult,
        *,
        expected_revision: int,
        expected_generation: int,
    ) -> bool:
        """Save the detailed answer only for the current Stage B owner.

        Args:
            real_job_id: Canonical parent to update.
            result: Completed detailed evaluation.
            expected_revision: Claimed input revision.
            expected_generation: Claimed lease generation.

        Returns:
            Whether the fenced write succeeded.
        """
        ...

    async def refresh_real_job_stage_a_claim(
        self, real_job_id: str, *, expected_revision: int, expected_generation: int
    ) -> bool:
        """Extend a still-owned Stage A lease.

        Args:
            real_job_id: Claimed canonical parent.
            expected_revision: Claimed input revision.
            expected_generation: Claimed lease generation.

        Returns:
            Whether this worker still owns the claim.
        """
        ...

    async def refresh_real_job_stage_b_claim(
        self, real_job_id: str, *, expected_revision: int, expected_generation: int
    ) -> bool:
        """Extend a still-owned Stage B lease.

        Args:
            real_job_id: Claimed canonical parent.
            expected_revision: Claimed input revision.
            expected_generation: Claimed lease generation.

        Returns:
            Whether this worker still owns the claim.
        """
        ...

    async def save_real_job_stage_a_error(
        self,
        real_job_id: str,
        error: str,
        *,
        expected_revision: int,
        expected_generation: int,
    ) -> bool:
        """Record a Stage A failure only for the current claim owner.

        Args:
            real_job_id: Claimed canonical parent.
            error: Failure message to retain.
            expected_revision: Claimed input revision.
            expected_generation: Claimed lease generation.

        Returns:
            Whether the fenced update succeeded.
        """
        ...

    async def save_real_job_stage_b_error(
        self,
        real_job_id: str,
        error: str,
        *,
        expected_revision: int,
        expected_generation: int,
    ) -> bool:
        """Record a Stage B failure only for the current claim owner.

        Args:
            real_job_id: Claimed canonical parent.
            error: Failure message to retain.
            expected_revision: Claimed input revision.
            expected_generation: Claimed lease generation.

        Returns:
            Whether the fenced update succeeded.
        """
        ...

    async def release_real_job_stage_a_claim(
        self, real_job_id: str, *, expected_revision: int, expected_generation: int
    ) -> bool:
        """Release a still-owned Stage A claim without recording an error.

        Args:
            real_job_id: Claimed canonical parent.
            expected_revision: Claimed input revision.
            expected_generation: Claimed lease generation.

        Returns:
            Whether this worker released its claim.
        """
        ...

    async def release_real_job_stage_b_claim(
        self, real_job_id: str, *, expected_revision: int, expected_generation: int
    ) -> bool:
        """Release a still-owned Stage B claim without recording an error.

        Args:
            real_job_id: Claimed canonical parent.
            expected_revision: Claimed input revision.
            expected_generation: Claimed lease generation.

        Returns:
            Whether this worker released its claim.
        """
        ...


class CanonicalWorkflowStore(Protocol):
    """Explicit capability contract for canonical persistence."""

    async def record_real_job_application_with_snapshots(
        self,
        record: ApplicationRecord,
        *,
        real_job_id: str,
        source_job_id: str,
        apply_url: str | None,
        snapshots: list[ResumeSnapshot] | None = None,
        resume_variant: str | None = None,
    ) -> bool:
        """Record one submission and canonical decision in the same transaction.

        Args:
            record: Submitted application data to persist.
            real_job_id: Canonical parent ID, never a source posting ID.
            source_job_id: Source posting ID that supplied this event or lookup.
            apply_url: Observed application URL, if known.
            snapshots: Existing resume snapshots to retain for source audit.
            resume_variant: Optional resume variant saved on the canonical decision.

        Returns:
            True for a newly recorded submission; False for a duplicate source and URL.

        Raises:
            ValueError: The source is unrelated or the parent is terminal.
            KeyError: Canonical status is missing.
        """
        ...

    async def list_real_job_applications(
        self, *, limit: int = 100
    ) -> list[RealJobApplicationEvent]:
        """List submission events without exposing resume snapshot fields.

        Args:
            limit: Maximum number of rows to return.

        Returns:
            Canonical submission events ordered newest first.

        Raises:
            ValueError: The requested limit is negative.
        """
        ...

    async def auto_decay_real_jobs(
        self, *, ghost_days: int = 30, archive_ignored_days: int = 14
    ) -> AutoDecayResult:
        """Decay each canonical decision once, independent of source aliases.

        Args:
            ghost_days: Age after which an unanswered application becomes ghosted.
            archive_ignored_days: Age after which an ignored parent becomes archived.

        Returns:
            Counts of parents advanced to ghosted and archived.
        """
        ...

    async def real_job_workflow_attention(
        self, *, auto_ghost_days: int = 30, lookahead_days: int = 5
    ) -> WorkflowAttention:
        """Build workflow reminder buckets from canonical rows only.

        Args:
            auto_ghost_days: Ghosting age used to project the upcoming reminder window.
            lookahead_days: Days ahead included in interview and ghosting reminders.

        Returns:
            Canonical follow-up, interview, and ghosting reminder buckets.
        """
        ...

    async def real_job_application_stats(
        self, *, since_days_ago: int | None = 30, by_resume: bool = False
    ) -> ApplicationStats:
        """Count submitted real jobs and responses after their first event.

        Args:
            since_days_ago: Optional submission cohort age; None includes all events.
            by_resume: Whether to group outcomes by recorded resume variant.

        Returns:
            Submission-cohort counts, outcomes, response latency, and resume breakdown.
        """
        ...

    async def compute_real_job_reapply_notice(
        self, *, job_id: str, lookback_days: int = 60
    ) -> str | None:
        """Exclude all aliases of the submitted canonical job from the notice.

        Args:
            job_id: Source posting ID used only to resolve its canonical parent.
            lookback_days: How far back to search for active same-company applications.

        Returns:
            A same-company warning, or None when no other active application exists.

        Raises:
            ValueError: The source exists but has no canonical parent.
        """
        ...

    async def resolve_real_job_id(self, source_job_id: str) -> str | None:
        """Resolve a source posting ID to its canonical parent without ID guessing.

        Args:
            source_job_id: Source posting ID that supplied this event or lookup.

        Returns:
            Canonical parent ID, or None for an unknown source.

        Raises:
            ValueError: The source exists but its canonical parent is unresolved.
        """
        ...

    async def get_real_job_status(self, real_job_id: str) -> StatusInfo | None:
        """Read one canonical status with representative company and title.

        Args:
            real_job_id: Canonical parent ID, never a source posting ID.

        Returns:
            Canonical status, or None when the parent has no status row.
        """
        ...

    async def list_real_job_statuses(
        self, filters: StatusFilter | None = None
    ) -> list[StatusInfo]:
        """List one canonical workflow row per real job with CLI filters.

        Args:
            filters: Optional status or Results eligibility filters.

        Returns:
            Matching canonical statuses ordered by most recent change.

        Raises:
            ValueError: The requested limit is negative.
        """
        ...

    async def get_real_job_status_history(self, real_job_id: str) -> list[str]:
        """Read canonical destination statuses, most recent first.

        Args:
            real_job_id: Canonical parent ID, never a source posting ID.

        Returns:
            Destination status names ordered newest first.
        """
        ...

    async def transition_real_job_status(self, request: TransitionRequest) -> str:
        """Validate and persist one canonical decision with history.

        Args:
            request: Canonical transition request and its force/reason fields.

        Returns:
            Persisted destination status name.
        """
        ...

    async def transition_real_jobs_bulk(
        self, request: BulkTransitionRequest
    ) -> BulkResult:
        """Transition each distinct canonical ID independently.

        Args:
            request: Canonical IDs, destination status, and shared transition options.

        Returns:
            Per-item success and failure counts for the submitted IDs.
        """
        ...

    async def append_real_job_note(self, *, real_job_id: str, text: str) -> bool:
        """Append a timestamped note to one canonical status.

        Args:
            real_job_id: Canonical parent ID, never a source posting ID.
            text: Note text to append.

        Returns:
            True when the parent status was updated.
        """
        ...

    async def set_real_job_followup(self, *, real_job_id: str, at: datetime) -> bool:
        """Set the next follow-up time on one canonical status.

        Args:
            real_job_id: Canonical parent ID, never a source posting ID.
            at: Timezone-aware follow-up timestamp.

        Returns:
            True when the parent status was updated.
        """
        ...

    async def restore_real_job(self, real_job_id: str) -> str:
        """Restore an archived or ghosted parent to its previous active state.

        Args:
            real_job_id: Canonical parent ID, never a source posting ID.

        Returns:
            Restored destination status name.

        Raises:
            KeyError: The parent has no canonical status.
            ValueError: The current state cannot be restored.
        """
        ...

    async def add_real_job_interview(
        self, *, real_job_id: str, label: str, scheduled_at: datetime | None = None
    ) -> InterviewRound:
        """Create the next interview round and advance an applied parent.

        Args:
            real_job_id: Canonical parent ID, never a source posting ID.
            label: Interview round label.
            scheduled_at: Optional scheduled interview time.

        Returns:
            The created canonical interview round.

        Raises:
            KeyError: The parent has no canonical status.
        """
        ...

    async def list_real_job_interviews(self, real_job_id: str) -> list[InterviewRound]:
        """List interview rounds for one canonical parent in round order.

        Args:
            real_job_id: Canonical parent ID, never a source posting ID.

        Returns:
            Canonical interview rounds in ascending round order.
        """
        ...

    async def complete_real_job_interview(
        self,
        *,
        real_job_id: str,
        round_index: int | None = None,
        notes: str | None = None,
    ) -> InterviewRound:
        """Complete the requested open round or the latest open round.

        Args:
            real_job_id: Canonical parent ID, never a source posting ID.
            round_index: Optional exact round; otherwise choose the latest open round.
            notes: Optional completion notes.

        Returns:
            The completed canonical interview round.

        Raises:
            ValueError: No matching open interview round exists.
        """
        ...
