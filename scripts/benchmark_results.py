"""Compare Results timings and exact ordering on a fixed local database."""

import argparse
import asyncio
import cProfile
import json
import pstats
import sqlite3
import time
from pathlib import Path

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.config import load_settings
from jobfeed.domain.models_views import JobsViewQuery
from jobfeed.services.jobs_view import JobsViewService


async def run(args):
    if args.snapshot:
        with (
            sqlite3.connect("file:data/jobfeed.sqlite?mode=ro", uri=True) as source,
            sqlite3.connect(args.database) as target,
        ):
            source.backup(target)
    store = SQLiteStore(Path(args.database))
    await store.connect()
    service = JobsViewService(
        store, load_settings(Path("config.toml")).hard_filters.to_domain()
    )
    results = {}
    try:
        sorts = (
            "triage_posted_desc",
            "triage_posted_asc",
            "triage_score_desc",
            "triage_score_asc",
        )
        for index, sort in enumerate(sorts * args.repeats):
            profiler = cProfile.Profile()
            start = time.perf_counter()
            if args.profile:
                profiler.enable()
            page = await service.list_jobs(
                JobsViewQuery(
                    tab="queue",
                    statuses=("new", "scored"),
                    require_verdict=True,
                    limit=10000,
                ),
                apply_hard_filters=True,
                dedupe=True,
                sort=sort,
            )
            profiler.disable()
            key = sort if index < len(sorts) else f"{sort}_repeat{index // len(sorts)}"
            results[key] = {
                "seconds": time.perf_counter() - start,
                "total": page.total,
                "ids": [r.job.id for r in page.rows],
            }
            print(key, results[key]["seconds"], page.total, flush=True)
            if args.profile:
                pstats.Stats(profiler).sort_stats("cumulative").print_stats(20)
    finally:
        await store.close()
    Path(args.output).write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--repeats", type=int, default=1)
    asyncio.run(run(parser.parse_args()))
