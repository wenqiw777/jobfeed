"""CLI source IDs resolve to one canonical workflow identity."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import aiosqlite
from click.testing import CliRunner

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.cli.apply import apply_cmd
from jobfeed.cli.status import mark
from jobfeed.cli.status_query import list_cmd, stats
from jobfeed.config import Settings
from jobfeed.domain.models import JobPosting


def test_cli_mark_list_apply_and_stats_use_real_job(tmp_path: Path) -> None:
    path = tmp_path / "cli-real-jobs.db"

    async def seed() -> tuple[str, str, str]:
        store = SQLiteStore(path)
        await store.connect()
        try:
            ids = []
            for platform in ("linkedin", "jobright"):
                saved = await store.save_job(
                    JobPosting(
                        platform=platform,
                        canonical_id=f"cli-{platform}",
                        url=f"https://example.test/{platform}",
                        title="Engineer",
                        company="Acme",
                        location="Remote",
                        discovered_at=datetime.now(UTC),
                    )
                )
                ids.append(saved.job_id)
            real_id = await store.resolve_real_job_id(ids[0])
            assert real_id is not None
            async with aiosqlite.connect(path) as db:
                await db.execute(
                    "UPDATE jobs SET real_job_id=? WHERE id=?",
                    (int(real_id), int(ids[1])),
                )
                await db.commit()
            return ids[0], ids[1], real_id
        finally:
            await store.close()

    first, second, real_id = asyncio.run(seed())
    resume = tmp_path / "resume.md"
    resume.write_text("Engineer resume", encoding="utf-8")
    settings = Settings()
    settings.llm.master_resume_path = str(resume)
    context = {"store": SQLiteStore(path), "settings": settings, "logger": MagicMock()}
    runner = CliRunner()

    marked = runner.invoke(mark, [second, "--status", "shortlisted"], obj=context)
    assert marked.exit_code == 0, marked.output
    listed = runner.invoke(list_cmd, ["--status", "shortlisted", "--json"], obj=context)
    assert listed.exit_code == 0, listed.output
    assert f'"id": "{real_id}"' in listed.output
    assert f'"id": "{second}"' not in listed.output or second == real_id
    applied = runner.invoke(
        apply_cmd,
        [second, "--apply-url", "https://ats.example.test/submit"],
        obj=context,
    )
    assert applied.exit_code == 0, applied.output
    aggregate = runner.invoke(stats, [], obj=context)
    assert aggregate.exit_code == 0, aggregate.output

    async def verify() -> None:
        store = SQLiteStore(path)
        await store.connect()
        try:
            assert (await store.get_real_job_status(real_id)).status == "applied"
            assert (await store.get_status(first)).status == "new"
            assert (await store.get_status(second)).status == "new"
            assert (await store.real_job_application_stats()).applied_count == 1
        finally:
            await store.close()

    asyncio.run(verify())
