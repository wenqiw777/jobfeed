"""Application routes: multipart apply (plan D8) and applications history.

The route resolves boundary inputs only — master resume content from
settings, uploaded files decoded as UTF-8 — and delegates recording to
``ApplicationService`` (snapshots, hashing, atomic persistence), mirroring
``cli/apply.py`` exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile

from jobfeed.config import Settings
from jobfeed.ports.store import JobStore
from jobfeed.services.application import ApplicationService, ApplyRequest
from jobfeed.web.deps import get_application_service, get_context, get_store
from jobfeed.web.errors import ApiError
from jobfeed.web.schemas import (
    ApplicationsListResponse,
    ApplyResponse,
    applications_list_response,
)

_HTTP_NOT_FOUND = 404
_HTTP_CONFLICT = 409
_HTTP_VALIDATION_ERROR = 422
_HTTP_INTERNAL_ERROR = 500
_DEFAULT_HISTORY_LIMIT = 50
_MAX_HISTORY_LIMIT = 1000

router = APIRouter()

_Applications = Annotated[ApplicationService, Depends(get_application_service)]
_Store = Annotated[JobStore, Depends(get_store)]


@dataclass(kw_only=True, frozen=True)
class ApplyForm:
    """Parsed multipart fields of the apply request."""

    tailored: UploadFile | None
    cover_letter: UploadFile | None
    variant: str | None
    method: str | None
    notes: str | None


async def _apply_form(
    tailored: Annotated[UploadFile | None, File()] = None,
    cover_letter: Annotated[UploadFile | None, File()] = None,
    variant: Annotated[str | None, Form()] = None,
    method: Annotated[str | None, Form()] = None,
    notes: Annotated[str | None, Form()] = None,
) -> ApplyForm:
    """Collect the optional multipart upload + form fields (plan D8).

    Args:
        tailored: Optional tailored resume file.
        cover_letter: Optional cover letter file.
        variant: Optional resume variant name.
        method: Optional application method.
        notes: Optional free-form note.

    Returns:
        Parsed form bundle.
    """
    return ApplyForm(
        tailored=tailored,
        cover_letter=cover_letter,
        variant=variant,
        method=method,
        notes=notes,
    )


@router.post("/jobs/{job_id}/apply")
async def apply_to_job(
    job_id: int,
    request: Request,
    form: Annotated[ApplyForm, Depends(_apply_form)],
    service: _Applications,
    store: _Store,
) -> ApplyResponse:
    """Record an application with resume snapshots and audit fields.

    Mirrors the CLI apply command: master resume read from settings,
    Stage B snapshot fields captured via the shared service helper, and the
    no-op parity result when the job was already applied.

    Args:
        job_id: Store-assigned job identity.
        request: Current request (carries the app settings).
        form: Parsed multipart upload + form fields.
        service: Shared application service from the app state.
        store: Shared job store (existence check only).

    Returns:
        ``applied`` flag plus the reapply notice when one applies.

    Raises:
        ApiError: 404 unknown job; 422 non-UTF-8 upload; 409 with code
            ``illegal_transition`` when the job is in a terminal status.
    """
    if await store.get_job(str(job_id)) is None:
        raise ApiError(_HTTP_NOT_FOUND, "not_found", f"job {job_id} not found")
    settings: Settings = get_context(request)["settings"]
    snapshots = await service.stage_b_snapshots(str(job_id))
    req = ApplyRequest(
        job_id=str(job_id),
        master_resume=_read_master_resume(settings),
        tailored_resume=await _decode_upload(form.tailored, "tailored"),
        cover_letter=await _decode_upload(form.cover_letter, "cover_letter"),
        variant=form.variant,
        application_method=form.method,
        notes=form.notes,
        verdict_snapshot=snapshots[0],
        fit_snapshot=snapshots[1],
        hooks_snapshot=snapshots[2],
    )
    try:
        is_new = await service.apply(req)
    except ValueError as exc:
        raise ApiError(_HTTP_CONFLICT, "illegal_transition", str(exc)) from exc
    notice = await service.reapply_notice(str(job_id)) if is_new else None
    return ApplyResponse(applied=is_new, reapply_notice=notice)


@router.get("/applications")
async def list_applications(
    service: _Applications,
    limit: Annotated[int, Query(ge=1, le=_MAX_HISTORY_LIMIT)] = _DEFAULT_HISTORY_LIMIT,
) -> ApplicationsListResponse:
    """List recent application records, newest first.

    Args:
        service: Shared application service from the app state.
        limit: Maximum number of records to return.

    Returns:
        Application history rows (audit metadata + snapshot hashes only).
    """
    records = await service.apply_history(limit=limit)
    return applications_list_response(records)


def _read_master_resume(settings: Settings) -> str:
    """Read the configured master resume at the web boundary.

    Args:
        settings: App settings carrying ``llm.master_resume_path``.

    Returns:
        Master resume content.

    Raises:
        ApiError: 500 when the configured file cannot be read.
    """
    path = Path(settings.llm.master_resume_path)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ApiError(
            _HTTP_INTERNAL_ERROR,
            "internal_error",
            f"cannot read master resume: {exc}",
        ) from exc


async def _decode_upload(upload: UploadFile | None, field: str) -> str | None:
    """Decode an optional upload as UTF-8 text.

    Args:
        upload: Uploaded file, or None when the field was omitted.
        field: Field name used in the error message.

    Returns:
        Decoded content, or None when no file was uploaded.

    Raises:
        ApiError: 422 when the upload is not valid UTF-8.
    """
    if upload is None:
        return None
    data = await upload.read()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ApiError(
            _HTTP_VALIDATION_ERROR,
            "validation_error",
            f"{field} upload must be UTF-8 text",
        ) from exc


__all__ = ["router"]
