"""Freeze the lightweight daily CI and explicit compatibility boundaries."""

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


def test_ci_excludes_postgres_and_docker_jobs() -> None:
    """Daily CI must not provision PostgreSQL or build Docker images."""
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "dorny/paths-filter@v3" in workflow
    assert "needs.changes.outputs.backend == 'true'" in workflow
    assert "needs.changes.outputs.browser == 'true'" in workflow
    assert "postgres-tests:" not in workflow
    assert "docker-build:" not in workflow
    assert "PGTEST_DSN" not in workflow
    assert "docker compose" not in workflow
    quality_job, browser_job = workflow.split("  browser-tests:", maxsplit=1)

    assert "make quality" in quality_job
    assert "services:" not in quality_job
    assert "postgres:" not in browser_job
