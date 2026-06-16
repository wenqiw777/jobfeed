"""Workflow routes tests (PG-backed): Task 4 acceptance criteria.

Pins the A4 workflow contract over real HTTP + PostgreSQL:

* Single transition: legal persists into detail history; illegal -> 409 with
  ``illegal_transition``; missing status row -> 404; unknown status -> 422
  (shared error shape).
* Bulk transition over a seeded twin cluster reports non-zero ``cascaded``
  and actually changes the twin's status; unknown status -> 422.
* Note + follow-up round-trip into the detail view; note or follow-up on a
  missing status row -> 404; naive follow-up datetime -> 422.
* JD paste removes the job from the pending_jd tab and returns the stored
  assessed quality; blank text -> 422.
* Interview rounds: add auto-transitions applied -> interviewing, list,
  complete with notes; add on an unknown job -> 404 (never an FK 500);
  unknown/completed index -> 404.
* Restore: ghosted -> 200 with the history-derived status, visible in
  detail; non-ghosted -> 409 ``not_restorable``; unknown job -> 404.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import StageAResult, TransitionRequest
from jobfeed.web.app import create_web_app
from tests.support.factories import make_job
from tests.web.test_app_skeleton import open_client, write_db_config

pytestmark = pytest.mark.postgres

HTTP_OK = 200
HTTP_NOT_FOUND = 404
HTTP_CONFLICT = 409
HTTP_VALIDATION_ERROR = 422
UUID4_HEX_LENGTH = 32

_FOLLOWUP_AT = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
# 600 chars sits in the GOOD band (500 < n <= 1000) of assess_quality.
_GOOD_JD_TEXT = "x" * 600
# Selected job + one cascaded twin.
_BULK_SUCCEEDED = 2
_UNKNOWN_JOB_ID = 999999
_UNKNOWN_ROUND_INDEX = 9


@asynccontextmanager
async def _seed_store(dsn: str) -> AsyncIterator[PostgresStore]:
    """Yield a connected store for seeding the migrated test database.

    Args:
        dsn: PostgreSQL DSN of a freshly migrated database.

    Yields:
        Connected PostgresStore.
    """
    store = PostgresStore(dsn)
    await store.connect()
    try:
        yield store
    finally:
        await store.close()


async def _insert(
    store: PostgresStore,
    canonical_id: str,
    *,
    status: str | None = None,
    **overrides: object,
) -> str:
    """Save a job fixture, optionally force-transitioning its status."""
    saved = await store.save_job(make_job(canonical_id, **overrides))
    if status is not None:
        await store.transition_status(
            TransitionRequest(job_id=saved.job_id, new_status=status, force=True)
        )
    return saved.job_id


# ---------------------------------------------------------------------------
# Single transition
# ---------------------------------------------------------------------------


async def test_illegal_transition_returns_409_with_code(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """new -> interviewing violates ALLOWED_TRANSITIONS -> 409 + error shape."""
    async with _seed_store(fresh_pg_dsn) as store:
        job_id = await _insert(store, "wf-illegal")
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.post(
            f"/api/jobs/{job_id}/transition", json={"to": "interviewing"}
        )

    assert response.status_code == HTTP_CONFLICT, response.text
    error = response.json()["error"]
    assert error["code"] == "illegal_transition"
    assert "not allowed" in error["message"]
    assert len(error["request_id"]) == UUID4_HEX_LENGTH


async def test_legal_transition_persists_and_shows_in_history(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """A legal transition with a note lands in the detail status section."""
    async with _seed_store(fresh_pg_dsn) as store:
        job_id = await _insert(store, "wf-legal")
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.post(
            f"/api/jobs/{job_id}/transition",
            json={"to": "scored", "note": "looks promising"},
        )
        detail = await client.get(f"/api/jobs/{job_id}")

    assert response.status_code == HTTP_OK, response.text
    assert response.json() == {"job_id": job_id, "status": "scored"}
    status = detail.json()["status"]
    assert status["status"] == "scored"
    assert status["history"][0] == "scored"
    assert "looks promising" in status["notes"]


async def test_force_transition_bypasses_graph(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """force=true allows a transition outside ALLOWED_TRANSITIONS."""
    async with _seed_store(fresh_pg_dsn) as store:
        job_id = await _insert(store, "wf-force")
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.post(
            f"/api/jobs/{job_id}/transition",
            json={"to": "applied", "force": True},
        )

    assert response.status_code == HTTP_OK, response.text
    assert response.json()["status"] == "applied"


async def test_evaluated_job_accepts_shortlist_transition(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """A Stage A-evaluated job is scored, so shortlisting needs no force."""
    async with _seed_store(fresh_pg_dsn) as store:
        job_id = await _insert(store, "wf-evaluated")
        await store.save_stage_a(
            job_id,
            StageAResult(
                score=80,
                one_line="fits",
                timing_eligible="yes",
                model="mock/stage-a",
                prompt_hash="prompt-a",
                resume_hash="resume-a",
            ),
        )
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.post(
            f"/api/jobs/{job_id}/transition", json={"to": "shortlisted"}
        )

    assert response.status_code == HTTP_OK, response.text
    assert response.json() == {"job_id": job_id, "status": "shortlisted"}


async def test_transition_missing_job_returns_404(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """A job without a status row maps the KeyError to 404 not_found."""
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.post(
            f"/api/jobs/{_UNKNOWN_JOB_ID}/transition", json={"to": "scored"}
        )

    assert response.status_code == HTTP_NOT_FOUND, response.text
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["message"]
    assert len(error["request_id"]) == UUID4_HEX_LENGTH


async def test_transition_unknown_status_returns_422(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """A status outside STATUS_VALUES fails validation, never reaching 409."""
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.post(
            f"/api/jobs/{_UNKNOWN_JOB_ID}/transition", json={"to": "bogus"}
        )

    assert response.status_code == HTTP_VALIDATION_ERROR, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["message"]
    assert len(error["request_id"]) == UUID4_HEX_LENGTH


# ---------------------------------------------------------------------------
# Bulk transition
# ---------------------------------------------------------------------------


async def test_bulk_transition_cascades_to_twins(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Bulk over a twin cluster reports cascaded > 0 and moves the twin."""
    async with _seed_store(fresh_pg_dsn) as store:
        selected = await _insert(
            store, "bulk-1", status="applied", company="TwinCo", title="Platform Eng"
        )
        twin = await _insert(
            store,
            "bulk-2",
            status="applied",
            platform="greenhouse",
            company="TwinCo",
            title="Platform Eng",
        )
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.post(
            "/api/jobs/bulk/transition",
            json={"items": [{"id": selected, "to": "interviewing"}]},
        )
        twin_detail = await client.get(f"/api/jobs/{twin}")

    assert response.status_code == HTTP_OK, response.text
    assert response.json() == {
        "succeeded": _BULK_SUCCEEDED,
        "skipped": 0,
        "failed": [],
        "cascaded": 1,
    }
    assert twin_detail.json()["status"]["status"] == "interviewing"


async def test_bulk_non_numeric_id_returns_422(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """A non-numeric job id fails validation before any transition runs."""
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.post(
            "/api/jobs/bulk/transition",
            json={"items": [{"id": "not-a-number", "to": "archived"}]},
        )

    assert response.status_code == HTTP_VALIDATION_ERROR, response.text
    assert response.json()["error"]["code"] == "validation_error"


async def test_bulk_transition_unknown_status_returns_422(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """A status outside STATUS_VALUES fails bulk validation, never reaching 409."""
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.post(
            "/api/jobs/bulk/transition",
            json={"items": [{"id": str(_UNKNOWN_JOB_ID), "to": "bogus"}]},
        )

    assert response.status_code == HTTP_VALIDATION_ERROR, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["message"]
    assert len(error["request_id"]) == UUID4_HEX_LENGTH


# ---------------------------------------------------------------------------
# Note + follow-up
# ---------------------------------------------------------------------------


async def test_note_and_followup_roundtrip(tmp_path: Path, fresh_pg_dsn: str) -> None:
    """Posted note and follow-up time show up in the detail status section."""
    async with _seed_store(fresh_pg_dsn) as store:
        job_id = await _insert(store, "wf-note", status="applied")
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        note = await client.post(
            f"/api/jobs/{job_id}/note", json={"text": "pinged recruiter"}
        )
        followup = await client.post(
            f"/api/jobs/{job_id}/followup", json={"at": _FOLLOWUP_AT.isoformat()}
        )
        detail = await client.get(f"/api/jobs/{job_id}")

    assert note.status_code == HTTP_OK, note.text
    assert followup.status_code == HTTP_OK, followup.text
    status = detail.json()["status"]
    assert "pinged recruiter" in status["notes"]
    assert datetime.fromisoformat(status["next_followup_at"]) == _FOLLOWUP_AT


async def test_followup_missing_status_row_returns_404(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """set_followup returning False maps to a 404 error response."""
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.post(
            f"/api/jobs/{_UNKNOWN_JOB_ID}/followup",
            json={"at": _FOLLOWUP_AT.isoformat()},
        )

    assert response.status_code == HTTP_NOT_FOUND, response.text
    assert response.json()["error"]["code"] == "not_found"


async def test_followup_naive_datetime_returns_422(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """A naive follow-up datetime is rejected; the aware form is accepted."""
    async with _seed_store(fresh_pg_dsn) as store:
        job_id = await _insert(store, "wf-naive", status="applied")
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        naive = await client.post(
            f"/api/jobs/{job_id}/followup",
            json={"at": _FOLLOWUP_AT.replace(tzinfo=None).isoformat()},
        )
        aware = await client.post(
            f"/api/jobs/{job_id}/followup", json={"at": _FOLLOWUP_AT.isoformat()}
        )

    assert naive.status_code == HTTP_VALIDATION_ERROR, naive.text
    assert naive.json()["error"]["code"] == "validation_error"
    assert aware.status_code == HTTP_OK, aware.text


async def test_note_missing_status_row_returns_404(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """append_note returning False maps to a 404 error response."""
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.post(
            f"/api/jobs/{_UNKNOWN_JOB_ID}/note", json={"text": "pinged recruiter"}
        )

    assert response.status_code == HTTP_NOT_FOUND, response.text
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["message"]
    assert len(error["request_id"]) == UUID4_HEX_LENGTH


# ---------------------------------------------------------------------------
# JD paste
# ---------------------------------------------------------------------------


async def test_jd_paste_clears_pending_jd_and_returns_quality(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Pasting a JD removes the row from pending_jd and reports its band."""
    async with _seed_store(fresh_pg_dsn) as store:
        job_id = await _insert(store, "wf-paste")
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        before = await client.get("/api/jobs", params={"tab": "pending_jd"})
        response = await client.post(
            f"/api/jobs/{job_id}/jd", json={"text": _GOOD_JD_TEXT}
        )
        after = await client.get("/api/jobs", params={"tab": "pending_jd"})

    assert [job["id"] for job in before.json()["jobs"]] == [job_id]
    assert response.status_code == HTTP_OK, response.text
    assert response.json() == {"job_id": job_id, "jd_quality": "good"}
    assert after.json()["jobs"] == []


async def test_jd_paste_blank_text_returns_422(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Whitespace-only JD text fails request validation with a 422."""
    async with _seed_store(fresh_pg_dsn) as store:
        job_id = await _insert(store, "wf-blank")
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.post(f"/api/jobs/{job_id}/jd", json={"text": "   "})

    assert response.status_code == HTTP_VALIDATION_ERROR, response.text
    assert response.json()["error"]["code"] == "validation_error"


async def test_jd_paste_missing_job_returns_404(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """JD paste on an unknown job id yields the shared 404 error shape."""
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.post(
            f"/api/jobs/{_UNKNOWN_JOB_ID}/jd", json={"text": _GOOD_JD_TEXT}
        )

    assert response.status_code == HTTP_NOT_FOUND, response.text
    assert response.json()["error"]["code"] == "not_found"


# ---------------------------------------------------------------------------
# Interview rounds
# ---------------------------------------------------------------------------


async def test_interview_add_auto_transitions_and_lists(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Adding a round to an applied job auto-transitions to interviewing."""
    async with _seed_store(fresh_pg_dsn) as store:
        job_id = await _insert(store, "wf-iv", status="applied")
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        added = await client.post(
            f"/api/jobs/{job_id}/interviews", json={"label": "Phone Screen"}
        )
        listed = await client.get(f"/api/jobs/{job_id}/interviews")
        detail = await client.get(f"/api/jobs/{job_id}")

    assert added.status_code == HTTP_OK, added.text
    round_ = added.json()
    assert round_["round_index"] == 1
    assert round_["label"] == "Phone Screen"
    assert round_["completed_at"] is None
    interviews = listed.json()["interviews"]
    assert len(interviews) == 1
    assert interviews[0]["label"] == "Phone Screen"
    assert detail.json()["status"]["status"] == "interviewing"


async def test_interview_complete_with_notes(tmp_path: Path, fresh_pg_dsn: str) -> None:
    """PATCH completes the indexed round and attaches the notes."""
    async with _seed_store(fresh_pg_dsn) as store:
        job_id = await _insert(store, "wf-iv-done", status="interviewing")
        await store.add_interview_round(job_id=job_id, label="Technical")
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.patch(
            f"/api/jobs/{job_id}/interviews/1", json={"notes": "went well"}
        )

    assert response.status_code == HTTP_OK, response.text
    round_ = response.json()
    assert round_["round_index"] == 1
    assert round_["completed_at"] is not None
    assert round_["notes"] == "went well"


async def test_interview_add_missing_job_returns_404(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Adding a round to an unknown job yields a 404, not an FK-violation 500."""
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.post(
            f"/api/jobs/{_UNKNOWN_JOB_ID}/interviews", json={"label": "Phone Screen"}
        )

    assert response.status_code == HTTP_NOT_FOUND, response.text
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["message"]
    assert len(error["request_id"]) == UUID4_HEX_LENGTH


async def test_interview_complete_unknown_index_returns_404(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Completing a round index with no open round yields a 404."""
    async with _seed_store(fresh_pg_dsn) as store:
        job_id = await _insert(store, "wf-iv-miss", status="interviewing")
        await store.add_interview_round(job_id=job_id, label="Technical")
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.patch(
            f"/api/jobs/{job_id}/interviews/{_UNKNOWN_ROUND_INDEX}",
            json={},
        )

    assert response.status_code == HTTP_NOT_FOUND, response.text
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert "no open interview round" in error["message"]


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


async def test_restore_ghosted_returns_previous_status(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Restoring a ghosted job lands on its last non-terminal status."""
    async with _seed_store(fresh_pg_dsn) as store:
        job_id = await _insert(store, "wf-restore", status="applied")
        await store.transition_status(
            TransitionRequest(job_id=job_id, new_status="ghosted")
        )
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.post(f"/api/jobs/{job_id}/restore")
        detail = await client.get(f"/api/jobs/{job_id}")

    assert response.status_code == HTTP_OK, response.text
    assert response.json() == {"job_id": job_id, "status": "applied"}
    assert detail.json()["status"]["status"] == "applied"


async def test_restore_non_ghosted_returns_409(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Restore on a job that is not ghosted/archived -> 409 not_restorable."""
    async with _seed_store(fresh_pg_dsn) as store:
        job_id = await _insert(store, "wf-restore-409", status="applied")
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.post(f"/api/jobs/{job_id}/restore")

    assert response.status_code == HTTP_CONFLICT, response.text
    error = response.json()["error"]
    assert error["code"] == "not_restorable"
    assert "ghosted or archived" in error["message"]
    assert len(error["request_id"]) == UUID4_HEX_LENGTH


async def test_restore_missing_job_returns_404(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Restore on an unknown job id yields the shared 404 error shape."""
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.post(f"/api/jobs/{_UNKNOWN_JOB_ID}/restore")

    assert response.status_code == HTTP_NOT_FOUND, response.text
    assert response.json()["error"]["code"] == "not_found"
