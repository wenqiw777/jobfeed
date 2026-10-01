"""Backfill uses the production browser and excludes paid post-scan work."""

from unittest.mock import AsyncMock

from jobfeed.services.jobright_bridge import JobrightBridge
from jobfeed.web.app import build_web_app
from tests.web.test_app_skeleton import fake_context, open_client

HTTP_CONFLICT = 409
HTTP_OK = 200


async def test_backfill_requires_updated_connected_extension():
    context = fake_context()
    context["jobright_bridge"] = JobrightBridge()
    app = build_web_app(context)
    async with open_client(app) as client:
        response = await client.post("/api/runs/application-backfill", json={})
    assert response.status_code == HTTP_CONFLICT


async def test_backfill_registers_an_owned_run_with_selected_existing_ids():
    context = fake_context()
    bridge = JobrightBridge()
    bridge.connect(["application-resolution", "application-resolution-10"])
    context["jobright_bridge"] = bridge
    app = build_web_app(context)
    manager = app.state.run_manager
    manager.trigger_application_backfill = AsyncMock(return_value="backfill-1")
    async with open_client(app) as client:
        response = await client.post(
            "/api/runs/application-backfill", json={"job_ids": ["1", "2"]}
        )
    assert response.status_code == HTTP_OK
    assert response.json()["run_id"] == "backfill-1"
    manager.trigger_application_backfill.assert_awaited_once()


async def test_backfill_rejects_old_three_tab_extension_before_starting():
    context = fake_context()
    bridge = JobrightBridge()
    bridge.connect(["application-resolution"])
    context["jobright_bridge"] = bridge
    app = build_web_app(context)
    manager = app.state.run_manager
    manager.trigger_application_backfill = AsyncMock(return_value="should-not-start")
    async with open_client(app) as client:
        response = await client.post(
            "/api/runs/application-backfill", json={"days": 30}
        )
    assert response.status_code == HTTP_CONFLICT
    manager.trigger_application_backfill.assert_not_awaited()
