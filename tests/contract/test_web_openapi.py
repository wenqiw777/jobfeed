"""Contract test pinning the committed OpenAPI snapshot (Phase 8, D7).

The frontend generates its TypeScript types from the committed
``src/jobfeed/web/openapi.json``, so any drift in the live route or DTO
surface must show up as a reviewed snapshot diff. No database is needed:
the web factory only connects to the store inside the lifespan, which the
schema dump never runs.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from fastapi import FastAPI

from jobfeed.web.app import build_web_app
from tests.web.test_app_skeleton import fake_context

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts/dump_openapi.py"
_SNAPSHOT = _REPO_ROOT / "src/jobfeed/web/openapi.json"

_spec = importlib.util.spec_from_file_location("dump_openapi", _SCRIPT)
assert _spec is not None and _spec.loader is not None
dump_openapi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dump_openapi)

_REGENERATE_HINT = (
    "OpenAPI snapshot is stale: the live schema differs from the committed "
    "src/jobfeed/web/openapi.json. Run `make web-schema` and review the diff."
)


def _canonical_schema_of(app: FastAPI) -> str:
    """Render an app's OpenAPI schema with the dump script's exact recipe.

    Args:
        app: FastAPI app to render.

    Returns:
        Canonical JSON document, sorted keys, indent 2, trailing newline.
    """
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def test_live_schema_matches_committed_snapshot() -> None:
    """The factory's schema must equal the committed snapshot byte-for-byte."""
    live = dump_openapi.render_snapshot()
    committed = _SNAPSHOT.read_text(encoding="utf-8")
    assert live == committed, _REGENERATE_HINT


def test_snapshot_render_is_deterministic() -> None:
    """Two renders in one process must produce identical bytes."""
    assert dump_openapi.render_snapshot() == dump_openapi.render_snapshot()


def test_spa_mounting_does_not_change_the_schema(tmp_path: Path) -> None:
    """A present dist bundle must not leak the SPA catch-all into the schema.

    The snapshot is regenerated on machines with and without a built
    ``web-ui/dist``; both states must produce the committed document.

    Args:
        tmp_path: Temporary directory holding a minimal dist fixture.
    """
    (tmp_path / "index.html").write_text("<!doctype html>", encoding="utf-8")
    app_with_spa = build_web_app(fake_context(), static_dir=tmp_path)
    app_without_spa = build_web_app(fake_context(), static_dir=tmp_path / "missing")

    committed = _SNAPSHOT.read_text(encoding="utf-8")
    assert _canonical_schema_of(app_with_spa) == committed, _REGENERATE_HINT
    assert _canonical_schema_of(app_without_spa) == committed, _REGENERATE_HINT
