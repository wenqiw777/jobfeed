"""Human duplicate decisions, separate from job scoring."""

from __future__ import annotations

from typing import Annotated, Literal, Protocol, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from jobfeed.web.deps import get_store

router = APIRouter()


class _ReviewStore(Protocol):
    async def identity_review(
        self, *, status: str, limit: int, offset: int
    ) -> dict[str, object]: ...
    async def identity_decide(
        self,
        left_id: str,
        right_id: str,
        *,
        decision: str,
        target_id: str | None = None,
    ) -> None: ...


class IdentityDecision(BaseModel):
    """Explicit pair decision and selected surviving identity."""

    decision: Literal["merge", "separate"]
    target_id: int | None = Field(default=None, gt=0)


@router.get("/identity-review")
async def review(
    store: Annotated[object, Depends(get_store)],
    status: Literal["pending", "reviewed"] = "pending",
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    """Read pending comparisons or previous decisions.

    Args:
        store: Configured store.
        status: Active tab.
        limit: Page size.
        offset: Page offset.

    Returns:
        Review cards and matching count.
    """
    return await cast(_ReviewStore, store).identity_review(
        status=status, limit=limit, offset=offset
    )


@router.post("/identity-review/{left_id}/{right_id}/decision")
async def decide(
    left_id: int,
    right_id: int,
    body: IdentityDecision,
    store: Annotated[object, Depends(get_store)],
) -> dict[str, bool]:
    """Save one user decision against a still-pending pair.

    Args:
        left_id: First canonical ID.
        right_id: Second canonical ID.
        body: Chosen decision and target.
        store: Configured store.

    Returns:
        Success acknowledgement.

    Raises:
        HTTPException: Stale or unsafe merge; refresh before retrying.
    """
    try:
        await cast(_ReviewStore, store).identity_decide(
            str(left_id),
            str(right_id),
            decision=body.decision,
            target_id=str(body.target_id) if body.target_id else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}
