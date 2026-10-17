"""Review routes validate decisions and preserve pending cases on invalid writes."""

from http import HTTPStatus
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.filtering import HardFilters
from jobfeed.web.deps import get_context, get_store
from jobfeed.web.routes.identity_review import router
from tests.integration.test_identity_review import seed


async def test_review_api_merge_validation_and_history(tmp_path):
    store = SQLiteStore(tmp_path / "web.db")
    await store.connect()
    try:
        ids = await seed(store)
        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.dependency_overrides[get_store] = lambda: store
        app.dependency_overrides[get_context] = lambda: {
            "settings": SimpleNamespace(
                hard_filters=SimpleNamespace(to_domain=HardFilters)
            )
        }
        url = f"/api/identity-review/{ids[0][0]}/{ids[1][0]}/decision"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/api/identity-review")).json()["total"] == 1
            assert (
                await client.post(url, json={"decision": "merge", "target_id": 999})
            ).status_code == HTTPStatus.CONFLICT
            assert (
                await client.post(url, json={"decision": "invalid"})
            ).status_code == HTTPStatus.UNPROCESSABLE_ENTITY
            assert (
                await client.post(url, json={"decision": "separate"})
            ).status_code == HTTPStatus.OK
            assert (await client.get("/api/identity-review")).json()["total"] == 0
            assert (await client.get("/api/identity-review?status=reviewed")).json()[
                "total"
            ] == 1
            assert (
                await client.post(
                    url, json={"decision": "merge", "target_id": ids[0][0]}
                )
            ).status_code == HTTPStatus.CONFLICT
    finally:
        await store.close()
