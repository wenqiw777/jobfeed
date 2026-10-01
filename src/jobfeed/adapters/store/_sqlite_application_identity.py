"""Commit application evidence and its resolution receipt together."""

from __future__ import annotations

from jobfeed.adapters.store._application_identity_verification import (
    verification_facts_match,
)
from jobfeed.adapters.store._sqlite_capability_support import (
    _fetch_row,
    _immediate_transaction,
)
from jobfeed.adapters.store._sqlite_real_job_evaluation import (
    sync_sqlite_real_job_input,
)
from jobfeed.adapters.store._sqlite_real_job_identity import resolve_sqlite_real_job
from jobfeed.adapters.store._sqlite_values import _job_from_row, _utc_now_text
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle


async def record_application_identity(  # noqa: PLR0913
    lifecycle: SqliteLifecycle,
    *,
    job_id: str,
    expected_apply_url: str | None,
    ats_url: str | None,
    state_key: str,
    state_value: str,
    run_id: str | None = None,
    owner_id: str | None = None,
    generation: int | None = None,
) -> bool:
    """Reject stale Apply evidence inside the same transaction as identity writes.

    Args:
        lifecycle: Open SQLite store lifecycle.
        job_id: Stored source identity.
        expected_apply_url: Application URL observed before browser resolution.
        ats_url: Verified ATS URL, or None for a negative outcome.
        state_key: Resolution receipt key.
        state_value: Serialized outcome and optional verification facts.
        run_id: Optional scan lease identity.
        owner_id: Optional scan lease owner.
        generation: Optional scan lease generation; all fence fields travel together.

    Returns:
        Whether the source exists and its application URL and facts remain current.

    Raises:
        ValueError: If only part of a scan fence is supplied.
        RuntimeError: If the supplied scan lease is lost.
    """
    fence = (run_id, owner_id, generation)
    if any(value is not None for value in fence) and any(
        value is None for value in fence
    ):
        raise ValueError("application evidence requires a complete scan fence")
    numeric_id = int(job_id)
    async with lifecycle.connection() as connection, _immediate_transaction(connection):
        if run_id is not None:
            lease = await _fetch_row(
                connection,
                "SELECT 1 FROM run_leases WHERE kind='scan' AND run_id=? "
                "AND owner_id=? AND generation=? AND expires_at>?",
                (run_id, owner_id, generation, _utc_now_text()),
            )
            if lease is None:
                raise RuntimeError("Scan write lease lost")
        row = await _fetch_row(
            connection, "SELECT * FROM jobs WHERE id=?", (numeric_id,)
        )
        if row is None or row["apply_url"] != expected_apply_url:
            return False
        if ats_url is not None:
            posting = _job_from_row(row)
            if not verification_facts_match(posting, state_value):
                return False
            posting.identity_evidence_url = ats_url
            await resolve_sqlite_real_job(
                connection, numeric_id, posting, include_content=False
            )
            await sync_sqlite_real_job_input(connection, numeric_id)
        await connection.execute(
            "INSERT INTO state(key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (state_key, state_value),
        )
    return True
