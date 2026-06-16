"""Apply + applications routes tests (PG-backed): Task 4 acceptance criteria.

Pins the D8 multipart apply contract over real HTTP + PostgreSQL:

* Multipart apply (tailored + cover letter files, variant/method/notes form
  fields) persists snapshots + audit + the applied transition in one
  transaction, verified via the detail and applications endpoints plus a
  direct store read of the Stage B snapshot columns.
* Second apply returns the no-op parity result (applied=false, no notice).
* The reapply notice appears when a same-company active application exists.
* Unknown job -> 404; non-UTF-8 upload -> 422; terminal-status job -> 409
  with ``illegal_transition`` (shared error shape).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import (
    FitAnalysis,
    StageAResult,
    StageBResult,
    TransitionRequest,
    Verdict,
)
from jobfeed.web.app import create_web_app
from tests.support.factories import make_job
from tests.web.test_app_skeleton import open_client

pytestmark = pytest.mark.postgres

HTTP_OK = 200
HTTP_NOT_FOUND = 404
HTTP_CONFLICT = 409
HTTP_VALIDATION_ERROR = 422
_UNKNOWN_JOB_ID = 999999

_MASTER_RESUME = "Alice Engineer\nPython, Go, SQL\n"
_TAILORED_RESUME = b"tailored resume text"
_COVER_LETTER = b"cover letter text"
_STAGE_B_FIT_SCORE = 77

_VERDICT_BLOCK: dict[str, object] = {"recommendation": "apply", "confidence": "high"}
_FIT_BLOCK: dict[str, object] = {
    "score_0_100": _STAGE_B_FIT_SCORE,
    "strong_match": [],
    "gaps": [],
}
_HOOKS_BLOCK: dict[str, object] = {
    "lead_with": "Lead X",
    "supporting": ["Sup 1"],
    "avoid_mentioning": [],
}


def _write_config(tmp_path: Path, dsn: str) -> Path:
    """Write a TOML config with a database DSN and a real master resume.

    Args:
        tmp_path: Temporary directory owned by pytest.
        dsn: PostgreSQL DSN.

    Returns:
        Path to the written config file.
    """
    resume_path = tmp_path / "resume.md"
    resume_path.write_text(_MASTER_RESUME, encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[db]",
                f'url = "{dsn}"',
                "",
                "[observability]",
                'log_level = "warning"',
                'log_format = "human"',
                "",
                "[llm]",
                f'master_resume_path = "{resume_path}"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return config_path


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


async def _seed_evaluated_job(store: PostgresStore, canonical_id: str) -> str:
    """Seed one job with a completed Stage A + Stage B evaluation.

    The Stage B raw blocks carry explicit verdict/fit/hooks objects so the
    apply route's snapshot capture has deterministic content to persist.

    Args:
        store: Connected store.
        canonical_id: Source-specific natural identity.

    Returns:
        Store-assigned job id.
    """
    saved = await store.save_job(make_job(canonical_id, company="SnapCo"))
    await store.save_stage_a(
        saved.job_id,
        StageAResult(
            score=80,
            one_line="fits",
            timing_eligible="yes",
            model="mock/stage-a",
            prompt_hash="prompt-a",
            resume_hash="resume-a",
        ),
    )
    await store.save_stage_b(
        saved.job_id,
        StageBResult(
            verdict=Verdict.APPLY,
            jd_summary="summary",
            fit_analysis=FitAnalysis(score=_STAGE_B_FIT_SCORE, strengths=[], gaps=[]),
            resume_hooks=["Lead X", "Sup 1"],
            model="mock/stage-b",
            prompt_hash="prompt-b",
            resume_hash="resume-b",
            raw_blocks={
                "verdict": _VERDICT_BLOCK,
                "fit_analysis": _FIT_BLOCK,
                "resume_hooks": _HOOKS_BLOCK,
            },
        ),
    )
    return saved.job_id


def _sha256(content: str | bytes) -> str:
    """SHA-256 hex digest matching the application service's content hash."""
    data = content.encode() if isinstance(content, str) else content
    return hashlib.sha256(data).hexdigest()


_APPLY_FILES = {
    "tailored": ("tailored.md", _TAILORED_RESUME, "text/markdown"),
    "cover_letter": ("cover.md", _COVER_LETTER, "text/plain"),
}
_APPLY_FORM = {"variant": "ml-focused", "method": "referral", "notes": "via Sam"}


async def test_apply_persists_snapshots_audit_and_transition(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Multipart apply lands status + snapshot refs + history in one shot."""
    async with _seed_store(fresh_pg_dsn) as store:
        job_id = await _seed_evaluated_job(store, "ap-main")
    app = create_web_app(_write_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.post(
            f"/api/jobs/{job_id}/apply", files=_APPLY_FILES, data=_APPLY_FORM
        )
        detail = await client.get(f"/api/jobs/{job_id}")
        history = await client.get("/api/applications")

    assert response.status_code == HTTP_OK, response.text
    assert response.json() == {"applied": True, "reapply_notice": None}

    payload = detail.json()
    assert payload["status"]["status"] == "applied"
    application = payload["application"]
    assert application["master_resume_hash"] == _sha256(_MASTER_RESUME)
    assert application["tailored_resume_hash"] == _sha256(_TAILORED_RESUME)
    assert application["application_method"] == "referral"

    rows = history.json()["applications"]
    assert len(rows) == 1
    assert rows[0]["job_id"] == job_id
    assert rows[0]["application_method"] == "referral"
    assert rows[0]["notes"] == "via Sam"
    assert rows[0]["master_resume_hash"] == _sha256(_MASTER_RESUME)

    async with _seed_store(fresh_pg_dsn) as store:
        record = await store.get_application(job_id)
    assert record is not None
    assert record.verdict_snapshot == json.dumps(_VERDICT_BLOCK, sort_keys=True)
    assert record.fit_snapshot == json.dumps(_FIT_BLOCK, sort_keys=True)
    assert record.hooks_snapshot == json.dumps(_HOOKS_BLOCK, sort_keys=True)


async def test_second_apply_returns_noop_parity(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Re-applying an applied job is a no-op: applied=false, single record."""
    async with _seed_store(fresh_pg_dsn) as store:
        job_id = await _seed_evaluated_job(store, "ap-twice")
    app = create_web_app(_write_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        first = await client.post(
            f"/api/jobs/{job_id}/apply", files=_APPLY_FILES, data=_APPLY_FORM
        )
        second = await client.post(
            f"/api/jobs/{job_id}/apply", files=_APPLY_FILES, data=_APPLY_FORM
        )
        history = await client.get("/api/applications")

    assert first.json()["applied"] is True
    assert second.status_code == HTTP_OK, second.text
    assert second.json() == {"applied": False, "reapply_notice": None}
    assert len(history.json()["applications"]) == 1


async def test_reapply_notice_for_same_company_active_application(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """An active same-company application surfaces the reapply notice."""
    async with _seed_store(fresh_pg_dsn) as store:
        job_id = await _seed_evaluated_job(store, "ap-notice")
        sibling = await store.save_job(
            make_job("ap-sibling", company="SnapCo", title="SRE")
        )
        await store.transition_status(
            TransitionRequest(job_id=sibling.job_id, new_status="applied", force=True)
        )
    app = create_web_app(_write_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.post(f"/api/jobs/{job_id}/apply", data=_APPLY_FORM)

    assert response.status_code == HTTP_OK, response.text
    payload = response.json()
    assert payload["applied"] is True
    assert payload["reapply_notice"] is not None
    assert "Active application" in payload["reapply_notice"]


async def test_apply_missing_job_returns_404(tmp_path: Path, fresh_pg_dsn: str) -> None:
    """Applying to an unknown job id yields the shared 404 error shape."""
    app = create_web_app(_write_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.post(
            f"/api/jobs/{_UNKNOWN_JOB_ID}/apply", data=_APPLY_FORM
        )

    assert response.status_code == HTTP_NOT_FOUND, response.text
    assert response.json()["error"]["code"] == "not_found"


async def test_apply_terminal_status_returns_409(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Applying to a rejected job maps the terminal-status guard to 409."""
    async with _seed_store(fresh_pg_dsn) as store:
        job_id = await _seed_evaluated_job(store, "ap-terminal")
        await store.transition_status(
            TransitionRequest(job_id=job_id, new_status="rejected", force=True)
        )
    app = create_web_app(_write_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.post(f"/api/jobs/{job_id}/apply", data=_APPLY_FORM)

    assert response.status_code == HTTP_CONFLICT, response.text
    error = response.json()["error"]
    assert error["code"] == "illegal_transition"
    assert "terminal" in error["message"]


async def test_apply_non_utf8_upload_returns_422(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """A non-UTF-8 tailored upload is rejected with a validation error."""
    async with _seed_store(fresh_pg_dsn) as store:
        job_id = await _seed_evaluated_job(store, "ap-binary")
    app = create_web_app(_write_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.post(
            f"/api/jobs/{job_id}/apply",
            files={"tailored": ("t.bin", b"\xff\xfe\xfa", "application/octet-stream")},
        )

    assert response.status_code == HTTP_VALIDATION_ERROR, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert "UTF-8" in error["message"]
