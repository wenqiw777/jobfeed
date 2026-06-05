"""Tests for mark_stale_jobs_closed store operation.

Verifies:
- Stale row (jd_quality='missing', discovered 40 days ago, closed_at IS NULL)
  gets closed_at set and enrich_error='backfill:stale-no-jd' after dry_run=False
- Fresh row (discovered 5 days ago) is untouched
- Row with jd_quality='full' (stale by age) is untouched
- Row already having closed_at set is untouched
- Row with jd_quality=NULL (stale by age) IS marked
- dry_run=True writes nothing, returns would-affect count
- dry_run=False writes and returns updated count
- Running dry_run=False twice: second call returns 0 (idempotent)
- CLI command is registered and prints the count
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.cli import cli
from jobfeed.domain.models import QualityBand
from tests.support.factories import make_job

pytestmark = pytest.mark.postgres

NOW = datetime.now(UTC)
STALE_BATCH_SIZE = 3


def _days_ago(n: int) -> datetime:
    return NOW - timedelta(days=n)


async def test_dry_run_returns_count_without_writing(store: PostgresStore) -> None:
    """dry_run=True returns the would-affect count and writes nothing."""
    stale = make_job(
        "stale-1",
        jd_text=None,
        jd_quality=QualityBand.MISSING,
        discovered_at=_days_ago(40),
    )
    result = await store.save_job(stale)

    count = await store.mark_stale_jobs_closed(older_than_days=30, dry_run=True)

    assert count == 1

    # Nothing written: closed_at is still NULL
    loaded = await store.get_job(result.job_id)
    assert loaded is not None
    assert loaded.closed_at is None
    assert loaded.enrich_error is None


async def test_dry_run_false_closes_stale_missing_quality_row(
    store: PostgresStore,
) -> None:
    """dry_run=False sets closed_at and enrich_error on stale missing-quality row."""
    stale = make_job(
        "stale-2",
        jd_text=None,
        jd_quality=QualityBand.MISSING,
        discovered_at=_days_ago(40),
    )
    result = await store.save_job(stale)

    count = await store.mark_stale_jobs_closed(older_than_days=30, dry_run=False)

    assert count == 1
    loaded = await store.get_job(result.job_id)
    assert loaded is not None
    assert loaded.closed_at is not None
    assert loaded.enrich_error == "backfill:stale-no-jd"


async def test_fresh_row_is_untouched(store: PostgresStore) -> None:
    """A row discovered only 5 days ago must not be closed."""
    fresh = make_job(
        "stale-fresh",
        jd_text=None,
        jd_quality=QualityBand.MISSING,
        discovered_at=_days_ago(5),
    )
    result = await store.save_job(fresh)

    await store.mark_stale_jobs_closed(older_than_days=30, dry_run=False)

    loaded = await store.get_job(result.job_id)
    assert loaded is not None
    assert loaded.closed_at is None


async def test_full_quality_stale_row_is_untouched(store: PostgresStore) -> None:
    """A stale row with jd_quality='full' must not be closed."""
    full = make_job(
        "stale-full",
        jd_text="This is a full job description.",
        jd_quality=QualityBand.FULL,
        discovered_at=_days_ago(40),
    )
    result = await store.save_job(full)

    await store.mark_stale_jobs_closed(older_than_days=30, dry_run=False)

    loaded = await store.get_job(result.job_id)
    assert loaded is not None
    assert loaded.closed_at is None


async def test_already_closed_row_is_untouched(store: PostgresStore) -> None:
    """A row that already has closed_at set must not be re-closed."""
    existing_closed_at = _days_ago(2)
    closed = make_job(
        "stale-already-closed",
        jd_text=None,
        jd_quality=QualityBand.MISSING,
        discovered_at=_days_ago(40),
        closed_at=existing_closed_at,
    )
    result = await store.save_job(closed)

    count = await store.mark_stale_jobs_closed(older_than_days=30, dry_run=False)

    assert count == 0
    loaded = await store.get_job(result.job_id)
    assert loaded is not None
    # Original closed_at unchanged (COALESCE semantics keep earliest)
    assert loaded.closed_at is not None


async def test_null_jd_quality_stale_row_is_marked(store: PostgresStore) -> None:
    """A row with jd_quality IS NULL (no JD) and stale age IS marked."""
    null_quality = make_job(
        "stale-null-quality",
        jd_text=None,
        jd_quality=None,
        discovered_at=_days_ago(40),
    )
    result = await store.save_job(null_quality)

    count = await store.mark_stale_jobs_closed(older_than_days=30, dry_run=False)

    assert count >= 1
    loaded = await store.get_job(result.job_id)
    assert loaded is not None
    assert loaded.closed_at is not None
    assert loaded.enrich_error == "backfill:stale-no-jd"


async def test_abandoned_quality_stale_row_is_marked(store: PostgresStore) -> None:
    """A row with jd_quality='abandoned' and stale age IS marked."""
    abandoned = make_job(
        "stale-abandoned",
        jd_text=None,
        jd_quality=QualityBand.ABANDONED,
        discovered_at=_days_ago(40),
    )
    result = await store.save_job(abandoned)

    count = await store.mark_stale_jobs_closed(older_than_days=30, dry_run=False)

    assert count >= 1
    loaded = await store.get_job(result.job_id)
    assert loaded is not None
    assert loaded.closed_at is not None
    assert loaded.enrich_error == "backfill:stale-no-jd"


async def test_idempotent_second_call_returns_zero(store: PostgresStore) -> None:
    """Running dry_run=False twice: second call returns 0 (rows already closed)."""
    stale = make_job(
        "stale-idem",
        jd_text=None,
        jd_quality=QualityBand.MISSING,
        discovered_at=_days_ago(40),
    )
    await store.save_job(stale)

    first = await store.mark_stale_jobs_closed(older_than_days=30, dry_run=False)
    second = await store.mark_stale_jobs_closed(older_than_days=30, dry_run=False)

    assert first == 1
    assert second == 0


async def test_dry_run_count_matches_write_count(store: PostgresStore) -> None:
    """The dry_run count must equal the count returned by the subsequent write."""
    for i in range(3):
        stale = make_job(
            f"stale-count-{i}",
            jd_text=None,
            jd_quality=QualityBand.MISSING,
            discovered_at=_days_ago(40),
        )
        await store.save_job(stale)

    dry = await store.mark_stale_jobs_closed(older_than_days=30, dry_run=True)
    written = await store.mark_stale_jobs_closed(older_than_days=30, dry_run=False)

    assert dry == written == STALE_BATCH_SIZE


async def test_zero_older_than_days_raises(store: PostgresStore) -> None:
    """older_than_days=0 is rejected — it would select fresh rows (footgun)."""
    with pytest.raises(ValueError, match="older_than_days"):
        await store.mark_stale_jobs_closed(older_than_days=0, dry_run=True)


async def test_negative_older_than_days_raises(store: PostgresStore) -> None:
    """A negative older_than_days is rejected."""
    with pytest.raises(ValueError, match="older_than_days"):
        await store.mark_stale_jobs_closed(older_than_days=-1, dry_run=False)


@pytest.mark.postgres
def test_cli_rejects_zero_older_than_days(fresh_pg_dsn: str, tmp_path: Path) -> None:
    """CLI rejects --older-than-days 0 with a non-zero exit (IntRange min=1)."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[db]\nurl = "{fresh_pg_dsn}"\n', encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--config", str(config_path), "mark-stale-closed", "--older-than-days", "0"],
    )

    assert result.exit_code != 0


@pytest.mark.postgres
def test_cli_command_registered_in_help() -> None:
    """mark-stale-closed must appear in jobfeed --help output."""
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "mark-stale-closed" in result.output


@pytest.mark.postgres
def test_cli_dry_run_prints_would_close(fresh_pg_dsn: str, tmp_path: Path) -> None:
    """CLI default (dry-run) prints 'Would close N stale jobs'."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[db]\nurl = "{fresh_pg_dsn}"\n', encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--config", str(config_path), "mark-stale-closed"],
    )

    assert result.exit_code == 0, result.output
    assert "Would close" in result.output
    assert "dry-run" in result.output


@pytest.mark.postgres
def test_cli_apply_prints_closed_count(fresh_pg_dsn: str, tmp_path: Path) -> None:
    """CLI --apply writes and prints 'Closed N stale jobs.'."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[db]\nurl = "{fresh_pg_dsn}"\n', encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--config", str(config_path), "mark-stale-closed", "--apply"],
    )

    assert result.exit_code == 0, result.output
    assert "Closed" in result.output
    assert "stale jobs" in result.output
