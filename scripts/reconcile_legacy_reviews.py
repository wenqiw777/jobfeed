#!/usr/bin/env python3
"""Preview/apply approved legacy review policy on an explicitly selected SQLite DB.

Stop writers before --apply. The command makes an online backup before any
changes; it does not initialize schema, run evaluation, or contact sources.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from jobfeed.adapters.store._sqlite_real_job_evaluation import (
    reconcile_legacy_review_page,
)


async def reconcile(database: Path, *, apply: bool, report: Path) -> None:
    """Emit per-ID policy decisions; mutation requires explicit apply."""
    database = database.resolve(strict=True)
    protected = {
        database,
        *(Path(str(database) + suffix) for suffix in ("-wal", "-shm", "-journal")),
    }
    if report.resolve() in protected or report.resolve().name.startswith(
        database.name + ".before-legacy-review"
    ):
        raise ValueError("report must not overwrite database, journal or backup")
    if apply:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = database.with_name(
            database.name + f".before-legacy-review-{timestamp}.sqlite"
        )
        if backup.exists():
            raise ValueError(
                f"backup already exists: {backup}; retain it and choose another copy"
            )
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as source:
            active = source.execute(
                "SELECT COUNT(*) FROM real_job_evaluations WHERE "
                "stage_a_status='in_progress' OR stage_b_status='in_progress'"
            ).fetchone()[0]
            if active:
                raise ValueError(
                    "active canonical claims exist; stop/release evaluation first"
                )
            descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            with sqlite3.connect(backup) as target:
                source.backup(target)
    mode = "rw" if apply else "ro"
    decisions = []
    async with aiosqlite.connect(
        database.as_uri() + f"?mode={mode}", uri=True, isolation_level=None
    ) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA busy_timeout=5000")
        after_id = 0
        while page := await reconcile_legacy_review_page(
            db, after_id=after_id, apply=apply
        ):
            decisions.extend(page)
            after_id = int(page[-1]["real_job_id"])
    result = {
        "database": str(database),
        "applied": apply,
        "counts": dict(Counter(str(row["action"]) for row in decisions)),
        "decisions": decisions,
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(report, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {"applied": apply, "counts": result["counts"], "report": str(report)}
        )
    )


def main() -> None:
    """Require explicit database and output paths; dry-run is the default."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(reconcile(args.db, apply=args.apply, report=args.report))


if __name__ == "__main__":
    main()
