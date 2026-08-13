"""Freeze the default SQLite and explicit PostgreSQL test-lane boundary."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_default_lane_is_sqlite_and_postgres_lane_is_explicit() -> None:
    """Default quality must exclude PG while the named PG lane remains runnable."""
    makefile = (ROOT / "Makefile").read_text()
    pytest_config = (ROOT / "pyproject.toml").read_text()
    contract_suite = (ROOT / "tests/contract/test_store_contract.py").read_text()

    assert "test-postgres:" in makefile
    assert "tests/contract tests/integration tests/store" in makefile
    assert "tests/e2e/test_legacy_import.py" in makefile
    assert "-m 'not postgres" in pytest_config
    assert "pytestmark = pytest.mark.postgres" not in contract_suite


def test_ci_keeps_default_and_postgres_lanes_separate() -> None:
    """CI quality runs without a PG service and PG behavior uses its named lane."""
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    quality_job, postgres_and_later = workflow.split("  postgres-tests:", maxsplit=1)
    postgres_job, browser_and_later = postgres_and_later.split(
        "  browser-tests:", maxsplit=1
    )
    browser_job, _later = browser_and_later.split("  docker-build:", maxsplit=1)

    assert "make quality" in quality_job
    assert "services:" not in quality_job
    assert "make test-postgres" in postgres_job
    assert "postgres:" not in browser_job
    assert "PGTEST_DSN" not in browser_job


def test_docker_smoke_uses_sqlite_container_runtime_only() -> None:
    """Optional container smoke must not initialize or require PostgreSQL."""
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    docker_job = workflow.split("  docker-build:", maxsplit=1)[1]

    assert "alembic -c migrations/alembic.ini upgrade head" not in docker_job
    assert "./bin/jobfeed" not in docker_job
    for command in ("--help", "scan --source mock", "evaluate --limit 3", "digest"):
        assert "docker compose run --rm jobfeed-cli jobfeed" in docker_job
        assert command in docker_job
    assert "docker-real-backend-missing.toml" in docker_job
