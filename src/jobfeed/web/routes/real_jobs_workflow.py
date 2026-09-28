"""Workflow endpoints whose path IDs always mean canonical real jobs."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Protocol, cast

from fastapi import APIRouter, Depends

from jobfeed.domain.interview import InterviewRound
from jobfeed.domain.models_status import (
    BulkResult,
    BulkTransitionRequest,
    StatusInfo,
    TransitionRequest,
)
from jobfeed.web.deps import get_store
from jobfeed.web.errors import ApiError
from jobfeed.web.schemas import (
    BulkTransitionBody,
    BulkTransitionResponse,
    FollowupBody,
    InterviewAddBody,
    InterviewCompleteBody,
    InterviewsListResponse,
    NoteBody,
    OkResponse,
    RestoreResponse,
    TransitionBody,
    TransitionResponse,
    bulk_transition_response,
    interview_round_response,
)
from jobfeed.web.schemas.jobs_detail import InterviewRoundDetail

router = APIRouter()


class RealJobWorkflowPort(Protocol):
    """Store capabilities required by canonical workflow HTTP routes."""

    async def get_real_job_status(self, real_job_id: str) -> StatusInfo | None:
        """Read one canonical status with representative company and title.

        Args:
            real_job_id: Canonical parent ID, never a source posting ID.

        Returns:
            Canonical status, or None when the parent has no status row.
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
            request: Canonical transition request and its force/reason fields.

        Returns:
            Per-item success and failure counts for the submitted IDs.
        """
        ...

    async def restore_real_job(self, real_job_id: str) -> str:
        """Restore an archived or ghosted parent to its previous active state.

        Args:
            real_job_id: Canonical parent ID, never a source posting ID.

        Returns:
            Restored destination status name.
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

    async def list_real_job_interviews(self, real_job_id: str) -> list[InterviewRound]:
        """List interview rounds for one canonical parent in round order.

        Args:
            real_job_id: Canonical parent ID, never a source posting ID.

        Returns:
            Canonical interview rounds in ascending round order.
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
        """
        ...

    async def resolve_real_job_id(self, source_job_id: str) -> str | None:
        """Resolve a source posting ID to its canonical parent without ID guessing.

        Args:
            source_job_id: Source posting ID that supplied this event or lookup.

        Returns:
            Canonical parent ID, or None for an unknown or unmigrated source.
        """
        ...


def _store(store: Annotated[object, Depends(get_store)]) -> RealJobWorkflowPort:
    return cast(RealJobWorkflowPort, store)


_Store = Annotated[RealJobWorkflowPort, Depends(_store)]


def _map_error(exc: Exception) -> ApiError:
    if isinstance(exc, KeyError):
        return ApiError(404, "not_found", str(exc.args[0] if exc.args else exc))
    return ApiError(409, "illegal_transition", str(exc))


@router.post("/real-jobs/bulk/transition")
async def bulk_transition(
    body: BulkTransitionBody, store: _Store
) -> BulkTransitionResponse:
    """Apply a bulk decision to distinct canonical IDs.

    Args:
        body: Validated HTTP request body.
        store: Canonical store resolved from the request context.

    Returns:
        HTTP response with canonical bulk transition outcomes.
    """
    result = await store.transition_real_jobs_bulk(
        BulkTransitionRequest(
            items=[(item.id, item.to) for item in body.items],
            reason_selected="bulk_selected",
            reason_cascade="bulk_cascade",
            force=body.force,
        )
    )
    return bulk_transition_response(result)


@router.post("/real-jobs/{real_job_id}/transition")
async def transition(
    real_job_id: int, body: TransitionBody, store: _Store
) -> TransitionResponse:
    """Update one canonical status and optionally append a note.

    Args:
        real_job_id: Canonical parent ID, never a source posting ID.
        body: Validated HTTP request body.
        store: Canonical store resolved from the request context.

    Returns:
        HTTP response with the canonical ID and saved status.

    Raises:
        ApiError: The parent is absent or transition is invalid.
    """
    request = TransitionRequest(
        job_id=str(real_job_id), new_status=body.to, force=body.force
    )
    try:
        result = await store.transition_real_job_status(request)
        if body.note:
            await store.append_real_job_note(
                real_job_id=str(real_job_id), text=body.note
            )
    except (KeyError, ValueError) as exc:
        raise _map_error(exc) from exc
    return TransitionResponse(job_id=str(real_job_id), status=result)


@router.post("/real-jobs/{real_job_id}/restore")
async def restore(real_job_id: int, store: _Store) -> RestoreResponse:
    """Restore one archived or ghosted canonical job.

    Args:
        real_job_id: Canonical parent ID, never a source posting ID.
        store: Canonical store resolved from the request context.

    Returns:
        HTTP response with the canonical ID and restored status.

    Raises:
        ApiError: The parent is absent or cannot be restored.
    """
    try:
        status = await store.restore_real_job(str(real_job_id))
    except (KeyError, ValueError) as exc:
        raise _map_error(exc) from exc
    return RestoreResponse(job_id=str(real_job_id), status=status)


@router.post("/real-jobs/{real_job_id}/note")
async def note(real_job_id: int, body: NoteBody, store: _Store) -> OkResponse:
    """Append a note to one canonical job.

    Args:
        real_job_id: Canonical parent ID, never a source posting ID.
        body: Validated HTTP request body.
        store: Canonical store resolved from the request context.

    Returns:
        Success response after the note is saved.

    Raises:
        ApiError: The canonical parent is absent.
    """
    if not await store.append_real_job_note(
        real_job_id=str(real_job_id), text=body.text
    ):
        raise ApiError(404, "not_found", f"real job {real_job_id} not found")
    return OkResponse()


@router.post("/real-jobs/{real_job_id}/followup")
async def followup(real_job_id: int, body: FollowupBody, store: _Store) -> OkResponse:
    """Schedule the next follow-up for one canonical job.

    Args:
        real_job_id: Canonical parent ID, never a source posting ID.
        body: Validated HTTP request body.
        store: Canonical store resolved from the request context.

    Returns:
        Success response after the schedule is saved.

    Raises:
        ApiError: The canonical parent is absent.
    """
    if not await store.set_real_job_followup(real_job_id=str(real_job_id), at=body.at):
        raise ApiError(404, "not_found", f"real job {real_job_id} not found")
    return OkResponse()


@router.get("/real-jobs/{real_job_id}/interviews")
async def list_interviews(real_job_id: int, store: _Store) -> InterviewsListResponse:
    """List interview rounds for one canonical job.

    Args:
        real_job_id: Canonical parent ID, never a source posting ID.
        store: Canonical store resolved from the request context.

    Returns:
        HTTP response containing canonical interview rounds.

    Raises:
        ApiError: The canonical parent is absent.
    """
    if await store.get_real_job_status(str(real_job_id)) is None:
        raise ApiError(404, "not_found", f"real job {real_job_id} not found")
    rounds = await store.list_real_job_interviews(str(real_job_id))
    return InterviewsListResponse(
        interviews=[interview_round_response(item) for item in rounds]
    )


@router.post("/real-jobs/{real_job_id}/interviews")
async def add_interview(
    real_job_id: int, body: InterviewAddBody, store: _Store
) -> InterviewRoundDetail:
    """Add an interview round for one canonical job.

    Args:
        real_job_id: Canonical parent ID, never a source posting ID.
        body: Validated HTTP request body.
        store: Canonical store resolved from the request context.

    Returns:
        The newly created interview round.

    Raises:
        ApiError: The parent is absent or the round is invalid.
    """
    try:
        round_ = await store.add_real_job_interview(
            real_job_id=str(real_job_id),
            label=body.label,
            scheduled_at=body.scheduled_at,
        )
    except (KeyError, ValueError) as exc:
        raise _map_error(exc) from exc
    return interview_round_response(round_)


@router.patch("/real-jobs/{real_job_id}/interviews/{round_index}")
async def complete_interview(
    real_job_id: int, round_index: int, body: InterviewCompleteBody, store: _Store
) -> InterviewRoundDetail:
    """Mark one canonical interview round complete.

    Args:
        real_job_id: Canonical parent ID, never a source posting ID.
        round_index: Optional exact round; otherwise choose the latest open round.
        body: Validated HTTP request body.
        store: Canonical store resolved from the request context.

    Returns:
        The completed interview round.

    Raises:
        ApiError: No matching open round exists.
    """
    try:
        round_ = await store.complete_real_job_interview(
            real_job_id=str(real_job_id),
            round_index=round_index,
            notes=body.notes,
        )
    except (KeyError, ValueError) as exc:
        raise _map_error(exc) from exc
    return interview_round_response(round_)
