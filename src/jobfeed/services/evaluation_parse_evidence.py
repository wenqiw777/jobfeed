"""Durable evidence for malformed paid responses, independent of result status."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from jobfeed.domain.errors import ScoringParseError
from jobfeed.domain.models import LLMResponse
from jobfeed.ports.store_ops import StoreOpsMixin


async def save_parse_failure(  # noqa: PLR0913 - explicit evidence identity
    store: StoreOpsMixin,
    response: LLMResponse,
    error: ScoringParseError,
    *,
    run_id: str,
    job_id: str,
    stage: str,
    attempt: int,
    real_job_id: str | None = None,
    input_revision: int | None = None,
) -> str:
    """Save each attempt before retrying, without truncating the original text.

    Args:
        store: Durable state store shared with the evaluation database.
        response: Exact paid response and usage metadata.
        error: Parser failure, retained alongside the untouched response.
        run_id: Owning evaluation run.
        job_id: Source job identity used for billing attribution.
        stage: Scoring stage a or b.
        attempt: One-based attempt within this stage invocation.
        real_job_id: Canonical job identity, when available.
        input_revision: Claimed canonical input revision, when available.

    Returns:
        Unique state key for offline retrieval and re-parsing.
    """
    key = f"evaluation-parse-error:{run_id}:{stage}:{job_id}:{uuid4()}"
    record = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "source_job_id": job_id,
        "real_job_id": real_job_id,
        "input_revision": input_revision,
        "stage": stage,
        "attempt": attempt,
        "error_type": type(error).__name__,
        "error": str(error),
        "raw_response": response.content,
        "model": response.model,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "cost_usd": response.cost_usd,
        "cached": response.cached,
        "latency_ms": response.latency_ms,
    }
    await store.set_state(key, json.dumps(record, ensure_ascii=False))
    return key
