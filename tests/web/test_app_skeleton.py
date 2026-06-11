"""Tests for the Phase 8 web skeleton: factory, health, errors, serve guard."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast
from unittest.mock import patch

import httpx
import pytest
import structlog
from click.testing import CliRunner
from fastapi import FastAPI

from jobfeed.cli import AppContext, cli
from jobfeed.domain.models import JobPosting
from jobfeed.web.app import build_web_app, create_web_app

UUID4_HEX_LENGTH = 32
HTTP_OK = 200
HTTP_NOT_FOUND = 404
HTTP_VALIDATION_ERROR = 422
HTTP_INTERNAL_ERROR = 500
HTTP_SERVICE_UNAVAILABLE = 503
DEFAULT_PORT = 7654
REQUEST_COUNT = 2


class FakeStore:
    """In-memory JobStore stand-in counting lifecycle and roundtrip calls."""

    def __init__(self, *, should_fail_roundtrip: bool = False) -> None:
        """Initialize call counters.

        Args:
            should_fail_roundtrip: Whether list_jobs should raise to simulate
                a broken database connection.
        """
        self.should_fail_roundtrip = should_fail_roundtrip
        self.connect_calls = 0
        self.close_calls = 0
        self.roundtrip_calls = 0
        self.last_limit: int | None = None

    async def connect(self) -> None:
        """Record one connect call."""
        self.connect_calls += 1

    async def close(self) -> None:
        """Record one close call."""
        self.close_calls += 1

    async def list_jobs(self, limit: int = 100) -> list[JobPosting]:
        """Record one roundtrip and return no jobs.

        Args:
            limit: Recorded so tests can assert the read stays cheap.

        Returns:
            Empty job list.

        Raises:
            RuntimeError: If configured with ``should_fail_roundtrip``.
        """
        self.roundtrip_calls += 1
        self.last_limit = limit
        if self.should_fail_roundtrip:
            raise RuntimeError("connection refused")
        return []


def fake_context(store: FakeStore | None = None) -> AppContext:
    """Build a minimal AppContext around a fake store.

    Args:
        store: Fake store to wire in; a fresh one when omitted.

    Returns:
        Context carrying only the keys the web skeleton touches.
    """
    return cast(AppContext, {"store": store or FakeStore()})


@asynccontextmanager
async def open_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Run the app lifespan and yield an ASGI-backed HTTP client.

    Args:
        app: FastAPI app under test.

    Yields:
        Client sending requests in-process through the ASGI transport.
    """
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield client


def write_db_config(tmp_path: Path, dsn: str = "") -> Path:
    """Write a minimal TOML config, optionally pointing at a database.

    Args:
        tmp_path: Temporary directory owned by pytest.
        dsn: PostgreSQL DSN; empty keeps the built-in default.

    Returns:
        Path to the written config file.
    """
    config_path = tmp_path / "config.toml"
    url_line = f'url = "{dsn}"' if dsn else ""
    config_path.write_text(
        "\n".join(
            [
                "[db]",
                url_line,
                "",
                "[observability]",
                'log_level = "warning"',
                'log_format = "human"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


@pytest.mark.postgres
async def test_health_returns_ok_with_db_roundtrip(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """GET /api/health should report status ok and db ok against real PG.

    Args:
        tmp_path: Temporary root used for the config file.
        fresh_pg_dsn: DSN of a freshly migrated, empty Postgres database.
    """
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.get("/api/health")

    assert response.status_code == HTTP_OK, response.text
    assert response.json() == {"status": "ok", "db": "ok"}


async def test_unknown_api_path_returns_error_shape_and_logs_request_id() -> None:
    """An unmatched /api path should yield the JSON error shape + logged id."""
    app = build_web_app(fake_context())

    with structlog.testing.capture_logs() as logs:
        async with open_client(app) as client:
            response = await client.get("/api/definitely-missing")

    assert response.status_code == HTTP_NOT_FOUND
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["message"]
    assert len(error["request_id"]) == UUID4_HEX_LENGTH
    logged_ids = [
        entry["request_id"] for entry in logs if entry["event"] == "http_request"
    ]
    assert error["request_id"] in logged_ids


async def test_validation_error_uses_error_shape() -> None:
    """A 422 from request validation should use the JSON error shape."""
    app = build_web_app(fake_context())

    @app.get("/api/echo")
    async def echo(n: int) -> dict[str, int]:
        return {"n": n}

    async with open_client(app) as client:
        response = await client.get("/api/echo", params={"n": "not-a-number"})

    assert response.status_code == HTTP_VALIDATION_ERROR
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert len(error["request_id"]) == UUID4_HEX_LENGTH


async def test_unhandled_exception_returns_json_500_without_traceback() -> None:
    """Unexpected route errors should become JSON 500s with no traceback."""
    app = build_web_app(fake_context())

    @app.get("/api/boom")
    async def boom() -> dict[str, str]:
        raise RuntimeError("secret internal detail")

    async with open_client(app) as client:
        response = await client.get("/api/boom")

    assert response.status_code == HTTP_INTERNAL_ERROR
    error = response.json()["error"]
    assert error["code"] == "internal_error"
    assert "secret internal detail" not in response.text
    assert "Traceback" not in response.text
    assert len(error["request_id"]) == UUID4_HEX_LENGTH


async def test_health_reports_db_error_as_503() -> None:
    """A failing store roundtrip should degrade health, not crash it."""
    app = build_web_app(fake_context(FakeStore(should_fail_roundtrip=True)))

    async with open_client(app) as client:
        response = await client.get("/api/health")

    assert response.status_code == HTTP_SERVICE_UNAVAILABLE
    assert response.json() == {"status": "degraded", "db": "error"}


async def test_store_connected_once_for_many_requests() -> None:
    """The store must be built and connected per process, not per request."""
    store = FakeStore()
    app = build_web_app(fake_context(store))

    with structlog.testing.capture_logs() as logs:
        async with open_client(app) as client:
            first = await client.get("/api/health")
            second = await client.get("/api/health")

    assert first.status_code == HTTP_OK
    assert second.status_code == HTTP_OK
    assert store.connect_calls == 1
    assert store.roundtrip_calls == REQUEST_COUNT
    assert store.last_limit == 1
    assert store.close_calls == 1
    logged_ids = [
        entry["request_id"] for entry in logs if entry["event"] == "http_request"
    ]
    assert len(logged_ids) == REQUEST_COUNT
    assert len(set(logged_ids)) == REQUEST_COUNT, "request ids must differ per request"


def test_serve_rejects_non_loopback_host(tmp_path: Path) -> None:
    """serve --host 0.0.0.0 should exit nonzero naming the loopback policy.

    Args:
        tmp_path: Temporary root used for the config file.
    """
    config_path = write_db_config(tmp_path)

    with patch("uvicorn.run") as run_mock:
        result = CliRunner().invoke(
            cli,
            ["--config", str(config_path), "serve", "--host", "0.0.0.0"],
        )

    assert result.exit_code != 0
    assert "loopback" in result.output
    assert "0.0.0.0" in result.output
    assert "Traceback" not in result.output
    assert run_mock.call_count == 0


def test_serve_default_binds_loopback_host_and_port(tmp_path: Path) -> None:
    """serve without options should bind 127.0.0.1:7654.

    Args:
        tmp_path: Temporary root used for the config file.
    """
    config_path = write_db_config(tmp_path)

    with patch("uvicorn.run") as run_mock:
        result = CliRunner().invoke(cli, ["--config", str(config_path), "serve"])

    assert result.exit_code == 0, result.output
    assert run_mock.call_count == 1
    (app,) = run_mock.call_args.args
    assert isinstance(app, FastAPI)
    assert run_mock.call_args.kwargs["host"] == "127.0.0.1"
    assert run_mock.call_args.kwargs["port"] == DEFAULT_PORT


def test_serve_accepts_localhost_alias(tmp_path: Path) -> None:
    """serve --host localhost should pass the loopback guard.

    Args:
        tmp_path: Temporary root used for the config file.
    """
    config_path = write_db_config(tmp_path)

    with patch("uvicorn.run") as run_mock:
        result = CliRunner().invoke(
            cli,
            ["--config", str(config_path), "serve", "--host", "localhost"],
        )

    assert result.exit_code == 0, result.output
    assert run_mock.call_args.kwargs["host"] == "localhost"
