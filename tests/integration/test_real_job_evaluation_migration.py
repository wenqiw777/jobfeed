"""Disposable PostgreSQL rehearsal for canonical evaluation DDL rollback."""

import asyncio
import os
import subprocess
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import JobPosting, QualityBand

pytestmark = pytest.mark.postgres


def test_0020_preserves_0019_history(blank_migrated_dsn: str) -> None:
    env = {**os.environ, "JOBFEED_DB_URL": blank_migrated_dsn}
    engine = create_engine(blank_migrated_dsn)
    try:
        async def seed_parent() -> tuple[int, int]:
            store = PostgresStore(blank_migrated_dsn)
            await store.connect()
            try:
                saved = await store.save_job(JobPosting(
                    platform="linkedin", canonical_id="migration-history",
                    url="https://example.test/migration-history", title="Engineer",
                    company="Acme", location="Remote",
                    discovered_at=datetime.now(UTC),
                    jd_text="Build software services. " * 12,
                    jd_quality=QualityBand.FULL,
                ))
                real_id = await store.resolve_real_job_id(saved.job_id)
                return int(real_id), int(saved.job_id)
            finally:
                await store.close()

        real_id, source_id = asyncio.run(seed_parent())
        with engine.connect() as db:
            assert (
                db.execute(text("SELECT version_num FROM alembic_version")).scalar()
                == "0021"
            )
            assert db.execute(
                text("SELECT to_regclass('public.real_job_evaluations')")
            ).scalar()
        subprocess.run(
            ["alembic", "-c", "migrations/alembic.ini", "downgrade", "0020"],
            env=env,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["alembic", "-c", "migrations/alembic.ini", "downgrade", "0019"],
            env=env,
            check=True,
            capture_output=True,
        )
        with engine.connect() as db:
            assert (
                db.execute(text("SELECT version_num FROM alembic_version")).scalar()
                == "0019"
            )
            assert db.execute(text(
                "SELECT to_regclass('public.real_job_evaluations')"
            )).scalar()
            assert db.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='real_job_evaluation_history' "
                "AND column_name='input_facts_json'"
            )).scalar() is None
        with engine.begin() as db:
            db.execute(text(
                "INSERT INTO real_job_evaluation_history("
                "real_job_id,source_job_id,input_revision,stage_a_status,"
                "stage_a_score,archived_at,reason) "
                "VALUES(:real_id,:source_id,1,'completed',88,now(),'old_policy')"
            ), {"real_id": real_id, "source_id": source_id})
        subprocess.run(
            ["alembic", "-c", "migrations/alembic.ini", "upgrade", "0020"],
            env=env,
            check=True,
            capture_output=True,
        )
        with engine.connect() as db:
            assert (
                db.execute(text("SELECT version_num FROM alembic_version")).scalar()
                == "0020"
            )
            assert (
                db.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='real_job_evaluations' "
                        "AND column_name='claim_generation'"
                    )
                ).scalar()
                == "claim_generation"
            )
            assert db.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='real_job_evaluation_history' "
                "AND column_name='input_facts_json'"
            )).scalar() == "input_facts_json"
            assert db.execute(text(
                "SELECT stage_a_score,reason,input_facts_json "
                "FROM real_job_evaluation_history WHERE real_job_id=:real_id"
            ), {"real_id": real_id}).one() == (88, "old_policy", None)
    finally:
        engine.dispose()


def test_0021_official_closure_upgrade_and_downgrade(
    blank_migrated_dsn: str,
) -> None:
    env = {**os.environ, "JOBFEED_DB_URL": blank_migrated_dsn}
    engine = create_engine(blank_migrated_dsn)
    try:
        with engine.begin() as db:
            parent_id = db.execute(
                text("INSERT INTO real_jobs DEFAULT VALUES RETURNING id")
            ).scalar_one()
            db.execute(
                text("UPDATE real_jobs SET official_closed_at=now() WHERE id=:id"),
                {"id": parent_id},
            )
        subprocess.run(
            ["alembic", "-c", "migrations/alembic.ini", "downgrade", "0020"],
            env=env, check=True, capture_output=True,
        )
        with engine.connect() as db:
            assert db.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='real_jobs' AND column_name='official_closed_at'"
            )).scalar() is None
            assert db.execute(text(
                "SELECT id FROM real_jobs WHERE id=:id"
            ), {"id": parent_id}).scalar() == parent_id
        subprocess.run(
            ["alembic", "-c", "migrations/alembic.ini", "upgrade", "0021"],
            env=env, check=True, capture_output=True,
        )
        with engine.connect() as db:
            assert db.execute(text(
                "SELECT official_closed_at FROM real_jobs WHERE id=:id"
            ), {"id": parent_id}).scalar() is None
    finally:
        engine.dispose()
