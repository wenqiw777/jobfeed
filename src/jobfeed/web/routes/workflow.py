"""Workflow routes: transitions, notes, follow-ups, JD paste, interviews.

Thin parse/format shell over ``WorkflowService`` and the store ops port.
Service errors map to the shared error shape via ``ApiError``: ValueError
from the transition graph -> 409 ``illegal_transition``; ValueError from
restore -> 409 ``not_restorable``; missing rows (KeyError, ``set_followup``
False, unknown interview round) -> 404.
"""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends

from jobfeed.domain.models_status import TransitionRequest
from jobfeed.ports.store import JobStore
from jobfeed.ports.store_ops import StoreOpsMixin
from jobfeed.ports.store_status import StoreStatusMixin
from jobfeed.services.workflow import WorkflowService
from jobfeed.web.deps import get_store, get_workflow_service
from jobfeed.web.errors import ApiError
from jobfeed.web.schemas import (
    BulkTransitionBody,
    BulkTransitionResponse,
    FollowupBody,
    InterviewAddBody,
    InterviewCompleteBody,
    InterviewsListResponse,
    JdPasteBody,
    JdPasteResponse,
    NoteBody,
    OkResponse,
    RestoreResponse,
    TransitionBody,
    TransitionResponse,
    bulk_transition_response,
    interview_round_response,
)
from jobfeed.web.schemas.jobs_detail import InterviewRoundDetail

_HTTP_NOT_FOUND = 404
_HTTP_CONFLICT = 409
_HTTP_VALIDATION_ERROR = 422

router = APIRouter()

_Workflow = Annotated[WorkflowService, Depends(get_workflow_service)]
_Store = Annotated[JobStore, Depends(get_store)]


def _not_found(message: str) -> ApiError:
    """Build the shared 404 error."""
    return ApiError(_HTTP_NOT_FOUND, "not_found", message)


def _key_error_message(exc: KeyError) -> str:
    """Unwrap a KeyError's message without its repr quotes."""
    return str(exc.args[0]) if exc.args else "not found"


# Registered before /jobs/{job_id}/transition so the literal segment wins.
@router.post("/jobs/bulk/transition")
async def bulk_transition(
    body: BulkTransitionBody, service: _Workflow
) -> BulkTransitionResponse:
    """Transition multiple jobs with twin-cluster cascade.

    Args:
        body: Requested (id, to) pairs and the force flag.
        service: Shared workflow service from the app state.

    Returns:
        Outcome summary: succeeded, skipped, failed items, cascaded count.

    Raises:
        ApiError: 422 when a job id is not numeric.
    """
    items: list[tuple[str, str]] = [(item.id, item.to) for item in body.items]
    try:
        result = await service.transition_bulk(items, force=body.force)
    except ValueError as exc:
        raise ApiError(_HTTP_VALIDATION_ERROR, "validation_error", str(exc)) from exc
    return bulk_transition_response(result)


@router.post("/jobs/{job_id}/transition")
async def transition_job(
    job_id: int, body: TransitionBody, service: _Workflow
) -> TransitionResponse:
    """Transition a single job, optionally appending a note.

    Args:
        job_id: Store-assigned job identity.
        body: Target status, optional note, force flag.
        service: Shared workflow service from the app state.

    Returns:
        The job's resulting status.

    Raises:
        ApiError: 404 when the job has no status row; 409 with code
            ``illegal_transition`` when the transition graph forbids it.
    """
    request = TransitionRequest(
        job_id=str(job_id), new_status=body.to, force=body.force
    )
    try:
        status = await service.transition(request, note=body.note)
    except KeyError as exc:
        raise _not_found(_key_error_message(exc)) from exc
    except ValueError as exc:
        raise ApiError(_HTTP_CONFLICT, "illegal_transition", str(exc)) from exc
    return TransitionResponse(job_id=str(job_id), status=status)


@router.post("/jobs/{job_id}/restore")
async def restore_job(
    job_id: int, service: _Workflow, store: _Store
) -> RestoreResponse:
    """Restore a ghosted/archived job to its last non-terminal status.

    The restore target comes from the job's status history (domain
    ``pick_restore_target``) — never recomputed client-side.

    Args:
        job_id: Store-assigned job identity.
        service: Shared workflow service from the app state.
        store: Shared job store from the app state.

    Returns:
        Job id and the status the job was restored to.

    Raises:
        ApiError: 404 when the job has no status row; 409 with code
            ``not_restorable`` when the job is not ghosted or archived.
    """
    status_info = await cast(StoreStatusMixin, store).get_status(str(job_id))
    if status_info is None:
        raise _not_found(f"no status row for job {job_id}")
    try:
        status = await service.restore(str(job_id))
    except KeyError as exc:  # status row vanished between check and write
        raise _not_found(_key_error_message(exc)) from exc
    except ValueError as exc:
        raise ApiError(_HTTP_CONFLICT, "not_restorable", str(exc)) from exc
    return RestoreResponse(job_id=str(job_id), status=status)


@router.post("/jobs/{job_id}/note")
async def add_note(job_id: int, body: NoteBody, service: _Workflow) -> OkResponse:
    """Append a note to a job (resets its ghost clock).

    Args:
        job_id: Store-assigned job identity.
        body: Note text (non-blank).
        service: Shared workflow service from the app state.

    Returns:
        Acknowledgement.

    Raises:
        ApiError: 404 when the job has no status row.
    """
    was_appended = await service.note(str(job_id), body.text)
    if not was_appended:
        raise _not_found(f"no status row for job {job_id}")
    return OkResponse()


@router.post("/jobs/{job_id}/followup")
async def set_followup(
    job_id: int, body: FollowupBody, service: _Workflow
) -> OkResponse:
    """Set the next follow-up time for a job.

    Args:
        job_id: Store-assigned job identity.
        body: ISO datetime of the next follow-up.
        service: Shared workflow service from the app state.

    Returns:
        Acknowledgement.

    Raises:
        ApiError: 404 when the job has no status row.
    """
    was_set = await service.set_followup(job_id=str(job_id), at=body.at)
    if not was_set:
        raise _not_found(f"no status row for job {job_id}")
    return OkResponse()


@router.post("/jobs/{job_id}/jd")
async def paste_jd(job_id: int, body: JdPasteBody, store: _Store) -> JdPasteResponse:
    """Store manually pasted JD text and report the assessed quality.

    Resolves the job's platform + canonical_id, delegates to the store's
    ``enrich_paste``, then re-reads the job for its stored quality band.

    Args:
        job_id: Store-assigned job identity.
        body: Pasted JD text (non-blank).
        store: Shared job store from the app state.

    Returns:
        Job id and its stored assessed JD quality.

    Raises:
        ApiError: 404 when the job is unknown.
    """
    job = await store.get_job(str(job_id))
    if job is None:
        raise _not_found(f"job {job_id} not found")
    await cast(StoreOpsMixin, store).enrich_paste(
        platform=job.platform, canonical_id=job.canonical_id, jd_text=body.text
    )
    refreshed = await store.get_job(str(job_id))
    quality = refreshed.jd_quality if refreshed is not None else None
    return JdPasteResponse(
        job_id=str(job_id),
        jd_quality=quality.value if quality is not None else None,
    )


@router.get("/jobs/{job_id}/interviews")
async def list_interviews(job_id: int, service: _Workflow) -> InterviewsListResponse:
    """List a job's interview rounds, ascending by round index.

    Args:
        job_id: Store-assigned job identity.
        service: Shared workflow service from the app state.

    Returns:
        Interview rounds of the job.
    """
    rounds = await service.list_rounds(str(job_id))
    return InterviewsListResponse(
        interviews=[interview_round_response(round_) for round_ in rounds]
    )


@router.post("/jobs/{job_id}/interviews")
async def add_interview(
    job_id: int, body: InterviewAddBody, service: _Workflow, store: _Store
) -> InterviewRoundDetail:
    """Add an interview round (auto-transitions applied -> interviewing).

    Args:
        job_id: Store-assigned job identity.
        body: Round label and optional scheduled time.
        service: Shared workflow service from the app state.
        store: Shared job store from the app state.

    Returns:
        The newly created round.

    Raises:
        ApiError: 404 when the job has no status row.
    """
    status_info = await cast(StoreStatusMixin, store).get_status(str(job_id))
    if status_info is None:
        raise _not_found(f"no status row for job {job_id}")
    round_ = await service.add_round(
        str(job_id), body.label, scheduled_at=body.scheduled_at
    )
    return interview_round_response(round_)


@router.patch("/jobs/{job_id}/interviews/{round_index}")
async def complete_interview(
    job_id: int,
    round_index: int,
    body: InterviewCompleteBody,
    service: _Workflow,
) -> InterviewRoundDetail:
    """Complete the indexed interview round, attaching optional notes.

    Args:
        job_id: Store-assigned job identity.
        round_index: Round index to complete.
        body: Optional notes.
        service: Shared workflow service from the app state.

    Returns:
        The completed round.

    Raises:
        ApiError: 404 when no open round exists at that index.
    """
    try:
        round_ = await service.complete_round(
            str(job_id), round_index=round_index, notes=body.notes
        )
    except ValueError as exc:
        raise _not_found(str(exc)) from exc
    return interview_round_response(round_)


__all__ = ["router"]
