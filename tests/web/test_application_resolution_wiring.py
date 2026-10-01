"""Web scans must retain the live browser reader for hydrated Apply links."""

from unittest.mock import patch

from jobfeed.services.jobright_bridge import JobrightBridge
from jobfeed.web.app import build_web_app
from tests.web.test_app_skeleton import fake_context


def test_web_scan_factory_passes_the_connected_extension_bridge():
    context = fake_context()
    bridge = JobrightBridge()
    context["jobright_bridge"] = bridge
    with patch("jobfeed.web.app.build_scan_service") as factory:
        app = build_web_app(context)
        app.state.run_manager._scan_factory()
    assert factory.call_args.kwargs.get("bridge") is bridge
