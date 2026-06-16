"""SPA static serving tests: index, fallback, assets, and JSON API 404s.

Built around a tmp dist fixture wired in through ``build_web_app``'s
``static_dir`` seam, with the skeleton's fake store context — no database
and no real ``web-ui/dist`` needed.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from jobfeed.web.app import _static_file_or_index, build_web_app
from tests.web.test_app_skeleton import fake_context, open_client

HTTP_OK = 200
HTTP_NOT_FOUND = 404

_INDEX_HTML = "<!doctype html><html><body>jobfeed spa</body></html>"
_APP_JS = "console.log('jobfeed');"
_VITE_SVG = "<svg xmlns='http://www.w3.org/2000/svg'></svg>"


def make_dist(tmp_path: Path) -> Path:
    """Build a minimal Vite-shaped dist fixture.

    Args:
        tmp_path: Temporary directory owned by pytest.

    Returns:
        Path to the dist directory (index.html + assets/app.js + vite.svg).
    """
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(_INDEX_HTML, encoding="utf-8")
    (dist / "assets" / "app.js").write_text(_APP_JS, encoding="utf-8")
    (dist / "vite.svg").write_text(_VITE_SVG, encoding="utf-8")
    return dist


async def test_root_serves_index_html(tmp_path: Path) -> None:
    """GET / should serve the SPA index page as HTML.

    Args:
        tmp_path: Temporary directory holding the dist fixture.
    """
    app = build_web_app(fake_context(), static_dir=make_dist(tmp_path))

    async with open_client(app) as client:
        response = await client.get("/")

    assert response.status_code == HTTP_OK
    assert response.text == _INDEX_HTML
    assert response.headers["content-type"].startswith("text/html")


async def test_spa_index_is_served_with_no_cache(tmp_path: Path) -> None:
    """The SPA index must carry Cache-Control: no-cache on / and fallbacks.

    Without the header, browsers heuristically cache the shell, so a stale
    copy keeps referencing hashed bundles deleted by the next deploy.

    Args:
        tmp_path: Temporary directory holding the dist fixture.
    """
    app = build_web_app(fake_context(), static_dir=make_dist(tmp_path))

    async with open_client(app) as client:
        root = await client.get("/")
        triage = await client.get("/triage")

    assert root.headers["cache-control"] == "no-cache"
    assert triage.headers["cache-control"] == "no-cache"


async def test_head_root_returns_html_headers_without_body(tmp_path: Path) -> None:
    """HEAD / should return 200 with the HTML content type and no body.

    Args:
        tmp_path: Temporary directory holding the dist fixture.
    """
    app = build_web_app(fake_context(), static_dir=make_dist(tmp_path))

    async with open_client(app) as client:
        response = await client.head("/")

    assert response.status_code == HTTP_OK
    assert response.headers["content-type"].startswith("text/html")
    assert response.text == ""


async def test_client_route_falls_back_to_index(tmp_path: Path) -> None:
    """Unknown non-API paths should serve index.html for the client router.

    Args:
        tmp_path: Temporary directory holding the dist fixture.
    """
    app = build_web_app(fake_context(), static_dir=make_dist(tmp_path))

    async with open_client(app) as client:
        triage = await client.get("/triage")
        deep_link = await client.get("/library/jobs/abc123")

    assert triage.status_code == HTTP_OK
    assert triage.text == _INDEX_HTML
    assert deep_link.status_code == HTTP_OK
    assert deep_link.text == _INDEX_HTML


async def test_vite_asset_serves_with_content_type(tmp_path: Path) -> None:
    """/assets/* files should serve their bytes with a real content type.

    Args:
        tmp_path: Temporary directory holding the dist fixture.
    """
    app = build_web_app(fake_context(), static_dir=make_dist(tmp_path))

    async with open_client(app) as client:
        response = await client.get("/assets/app.js")

    assert response.status_code == HTTP_OK
    assert response.text == _APP_JS
    # text/javascript vs application/javascript varies by platform mimetypes.
    assert "javascript" in response.headers["content-type"]


async def test_dist_root_file_served_via_fallback_route(tmp_path: Path) -> None:
    """Files at the dist root (e.g. vite.svg) should serve, not fall back.

    Args:
        tmp_path: Temporary directory holding the dist fixture.
    """
    app = build_web_app(fake_context(), static_dir=make_dist(tmp_path))

    async with open_client(app) as client:
        response = await client.get("/vite.svg")

    assert response.status_code == HTTP_OK
    assert response.text == _VITE_SVG
    assert "svg" in response.headers["content-type"]


async def test_unknown_api_path_stays_json_404_with_dist(tmp_path: Path) -> None:
    """Unknown /api paths must keep the JSON error shape, never HTML.

    Args:
        tmp_path: Temporary directory holding the dist fixture.
    """
    app = build_web_app(fake_context(), static_dir=make_dist(tmp_path))

    async with open_client(app) as client:
        response = await client.get("/api/nope")

    assert response.status_code == HTTP_NOT_FOUND
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["request_id"]


async def test_missing_asset_is_json_404_not_index_html(tmp_path: Path) -> None:
    """An asset miss should 404 in the JSON shape, not serve index.html.

    Serving HTML for a missing hashed bundle would surface as a JS parse
    error in the browser; a clean 404 is the debuggable failure.

    Args:
        tmp_path: Temporary directory holding the dist fixture.
    """
    app = build_web_app(fake_context(), static_dir=make_dist(tmp_path))

    async with open_client(app) as client:
        response = await client.get("/assets/missing.js")

    assert response.status_code == HTTP_NOT_FOUND
    assert response.json()["error"]["code"] == "not_found"


async def test_assets_miss_without_assets_dir_is_json_404(tmp_path: Path) -> None:
    """A dist without an assets/ dir must still 404 asset paths as JSON.

    No assets/ dir means no StaticFiles mount, so the catch-all sees the
    request; it must never hand index.html to a script tag.

    Args:
        tmp_path: Temporary directory holding an assets-less dist fixture.
    """
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(_INDEX_HTML, encoding="utf-8")
    app = build_web_app(fake_context(), static_dir=dist)

    async with open_client(app) as client:
        response = await client.get("/assets/foo.js")

    assert response.status_code == HTTP_NOT_FOUND
    assert response.json()["error"]["code"] == "not_found"


async def test_no_dist_keeps_json_404_everywhere(tmp_path: Path) -> None:
    """Without a built bundle, every unknown path keeps the JSON 404.

    Args:
        tmp_path: Temporary directory; the dist path inside it never exists.
    """
    app = build_web_app(fake_context(), static_dir=tmp_path / "missing-dist")

    async with open_client(app) as client:
        root = await client.get("/")
        triage = await client.get("/triage")

    assert root.status_code == HTTP_NOT_FOUND
    assert root.json()["error"]["code"] == "not_found"
    assert triage.status_code == HTTP_NOT_FOUND
    assert triage.json()["error"]["code"] == "not_found"


def test_present_dist_marks_spa_mounted_and_logs(tmp_path: Path) -> None:
    """A built dist should set is_spa_mounted and log web_ui_mounted.

    Args:
        tmp_path: Temporary directory holding the dist fixture.
    """
    dist = make_dist(tmp_path)

    with structlog.testing.capture_logs() as logs:
        app = build_web_app(fake_context(), static_dir=dist)

    assert app.state.is_spa_mounted is True
    mounted = [entry for entry in logs if entry["event"] == "web_ui_mounted"]
    assert len(mounted) == 1
    assert mounted[0]["dist_dir"] == str(dist)


def test_missing_dist_marks_api_only_and_logs_hint(tmp_path: Path) -> None:
    """A missing dist should clear is_spa_mounted and log web_ui_not_built.

    The hint must name `make web-build` so a bare `jobfeed serve` without
    a built bundle is explainable from the startup log alone.

    Args:
        tmp_path: Temporary directory; the dist path inside it never exists.
    """
    with structlog.testing.capture_logs() as logs:
        app = build_web_app(fake_context(), static_dir=tmp_path / "missing-dist")

    assert app.state.is_spa_mounted is False
    not_built = [entry for entry in logs if entry["event"] == "web_ui_not_built"]
    assert len(not_built) == 1
    assert "make web-build" in str(not_built[0]["hint"])


def test_path_escaping_dist_falls_back_to_index(tmp_path: Path) -> None:
    """A traversal path resolving outside dist must not serve that file.

    HTTP clients normalize ``..`` before sending, so this guards the
    resolver directly.

    Args:
        tmp_path: Temporary directory holding the dist fixture + a secret.
    """
    dist = make_dist(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("do not serve", encoding="utf-8")
    index_file = dist / "index.html"

    resolved = _static_file_or_index(dist, index_file, "../secret.txt")

    assert resolved == index_file
