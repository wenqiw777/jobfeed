"""The loaded extension advertises its ten-reader pool over the real socket."""

from fastapi.testclient import TestClient

from jobfeed.services.jobright_bridge import JobrightBridge
from jobfeed.web.app import build_web_app
from tests.web.test_app_skeleton import fake_context


def test_ten_reader_capability_survives_websocket_handshake():
    context = fake_context()
    context["jobright_bridge"] = JobrightBridge()
    app = build_web_app(context)
    capabilities = ["application-resolution", "application-resolution-10"]
    with TestClient(app) as client:
        with client.websocket_connect("/api/sources/jobright/bridge") as socket:
            socket.send_json({"type": "hello", "protocol": 1, "sources": capabilities})
            assert socket.receive_json() == {"type": "ready", "protocol": 1}
            status = client.get("/api/sources/jobright/status").json()
            assert status == {"connected": True, "supported_sources": capabilities}
