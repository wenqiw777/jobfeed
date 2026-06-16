#!/usr/bin/env python3
"""Dump the web API's OpenAPI schema to the committed snapshot.

Builds the FastAPI app via the production factory — no database roundtrip:
the store only connects inside the lifespan, which never runs here — and
writes the schema with sorted keys for a stable, review-friendly diff. The
snapshot is the type contract the frontend generates its TypeScript types
from; ``tests/contract/test_web_openapi.py`` fails whenever the live route
or DTO surface drifts from it.

Usage (see ``make web-schema``):
    python scripts/dump_openapi.py
"""

from __future__ import annotations

import json
from pathlib import Path

from jobfeed.web.app import create_web_app

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OUTPUT = _REPO_ROOT / "src/jobfeed/web/openapi.json"


def render_snapshot() -> str:
    """Render the factory's OpenAPI schema as canonical JSON text.

    The schema describes routes and DTOs only, so the output is independent
    of environment-specific settings (DSN, paths); sorted keys plus a fixed
    indent and trailing newline make regeneration byte-stable.

    Returns:
        Canonical JSON document, sorted keys, indent 2, trailing newline.
    """
    schema = create_web_app().openapi()
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main() -> int:
    """Write the snapshot to the committed location.

    Returns:
        Process exit code (0 on success).
    """
    _OUTPUT.write_text(render_snapshot(), encoding="utf-8")
    print(f"wrote OpenAPI snapshot to {_OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
