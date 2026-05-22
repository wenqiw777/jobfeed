"""Shared pytest fixtures for Jobfeed tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import structlog


@pytest.fixture(autouse=True)
def reset_structlog_state() -> Iterator[None]:
    """Reset global structlog configuration and context around each test."""
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()
    yield
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()
