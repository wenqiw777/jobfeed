"""Human review of automatic posting findings; no job/history deletion."""

from __future__ import annotations

from typing import Annotated, Literal, Protocol, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from jobfeed.web.deps import get_store

router = APIRouter()


class _SpamStore(Protocol):
    async def spam_review(
        self, *, status: str, limit: int, offset: int
    ) -> dict[str, object]: ...
    async def spam_decide(self, job_id: str, *, scope: str, decision: str) -> None: ...


class SpamDecision(BaseModel):
    """Validated reversible action chosen by the user."""

    scope: Literal["job", "company"]
    decision: Literal["allow", "block", "reset"]


@router.get("/spam-review")
async def review(
    store: Annotated[object, Depends(get_store)],
    status: Literal["pending", "blocked", "reviewed", "all"] = "pending",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    """Return a bounded review page.

    Args:
        store: Application store.
        status: Review tab filter.
        limit: Page size.
        offset: Page offset.

    Returns:
        Findings and matching total.
    """
    return await cast(_SpamStore, store).spam_review(
        status=status, limit=limit, offset=offset
    )


@router.post("/spam-review/{job_id}/decision")
async def decide(
    job_id: int,
    body: SpamDecision,
    store: Annotated[object, Depends(get_store)],
) -> dict[str, bool]:
    """Record a user-requested job or company decision.

    Args:
        job_id: Source posting anchoring the decision.
        body: Validated scope and action.
        store: Application store.

    Returns:
        Success acknowledgement.

    Raises:
        HTTPException: Missing source or invalid company scope.
    """
    try:
        await cast(_SpamStore, store).spam_decide(
            str(job_id), scope=body.scope, decision=body.decision
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=404 if str(exc) == "Job not found" else 422, detail=str(exc)
        ) from exc
    return {"ok": True}
