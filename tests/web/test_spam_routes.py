"""Review HTTP actions preserve uncertainty and apply reversible explicit choices."""

from http import HTTPStatus

import httpx
from fastapi import FastAPI

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.web.deps import get_store
from jobfeed.web.routes.spam import router
from tests.integration.test_posting_spam import publisher


async def test_review_decide_reset_and_validation(tmp_path):
    store = SQLiteStore(tmp_path / "api.sqlite")
    await store.connect()
    try:
        saved = await store.save_job(publisher(apply_url=None))
        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.dependency_overrides[get_store] = lambda: store
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/spam-review")
            assert response.status_code == HTTPStatus.OK
            assert response.json()["total"] == 1
            response = await client.post(
                f"/api/spam-review/{saved.job_id}/decision",
                json={"scope": "job", "decision": "block"},
            )
            assert response.status_code == HTTPStatus.OK
            assert (await store.query_real_jobs_view(decision="results"))["total"] == 0
            response = await client.post(
                f"/api/spam-review/{saved.job_id}/decision",
                json={"scope": "company", "decision": "allow"},
            )
            assert response.status_code == HTTPStatus.OK
            # Explicit job block still wins over company allow.
            assert (await store.query_real_jobs_view(decision="results"))["total"] == 0
            await client.post(
                f"/api/spam-review/{saved.job_id}/decision",
                json={"scope": "job", "decision": "reset"},
            )
            assert (await store.query_real_jobs_view(decision="results"))["total"] == 1
            assert (
                await client.get("/api/spam-review?limit=10000")
            ).status_code == HTTPStatus.UNPROCESSABLE_ENTITY
            assert (
                await client.post(
                    "/api/spam-review/99999/decision",
                    json={"scope": "job", "decision": "allow"},
                )
            ).status_code == HTTPStatus.NOT_FOUND
    finally:
        await store.close()
