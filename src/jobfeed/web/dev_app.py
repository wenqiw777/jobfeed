"""Import-string application factory used by the local reload process."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from jobfeed.web.app import create_web_app


def create_dev_app() -> FastAPI:
    """Create one API app per reload worker using the selected config.

    Returns:
        A fresh FastAPI application for the current reload worker.
    """
    raw_path = os.environ.get("JOBFEED_DEV_CONFIG")
    return create_web_app(Path(raw_path) if raw_path else None)


__all__ = ["create_dev_app"]
