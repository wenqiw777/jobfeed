#!/usr/bin/env python3
"""Resolve current Results review holds with an explicit scoring source."""

from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.request
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from jobfeed.adapters.store._sqlite_values import _job_from_row, _utc_text
from jobfeed.domain.models import JobPosting, QualityBand
from jobfeed.domain.real_job_evaluation import (
    RealJobEvaluationInput,
    input_facts_json,
    official_closed_at,
    select_real_job_input,
)
from jobfeed.domain.real_job_identity import normalized_jd_body

_REVIEW_SOURCE_KEY = "real-job-evaluation-source:"
_AGGREGATOR_HOSTS = ("jobright.ai", "linkedin.com")
_COMPLETE = {QualityBand.FULL, QualityBand.GOOD}


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("data/jobfeed.sqlite"))
    parser.add_argument("--api-base", default="http://127.0.0.1:7654")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _results_review_ids(api_base: str) -> list[int]:
    url = (
        f"{api_base.rstrip('/')}/api/real-jobs"
        "?decision=results&require_verdict=true&limit=10000"
    )
    with urllib.request.urlopen(url) as response:
        payload = json.load(response)
    return [
        int(row["id"])
        for row in payload["jobs"]
        if row["identity_review_state"] != "clear"
    ]


def _source_rank(job: JobPosting) -> tuple[int, int, int, int]:
    host = (urlparse(job.url).hostname or "").casefold()
    aggregator = any(
        host == name or host.endswith(f".{name}") for name in _AGGREGATOR_HOSTS
    )
    quality = 0 if job.jd_quality is QualityBand.FULL else 1
    return (
        int(aggregator),
        quality,
        -len(normalized_jd_body(job.jd_text)),
        int(job.id or 0),
    )


def _choose_source(real_id: int, jobs: list[JobPosting]) -> JobPosting:
    selected = select_real_job_input(str(real_id), jobs, now=datetime.now(UTC))
    if selected is not None:
        return selected.job
    complete = [
        job
        for job in jobs
        if job.id is not None and job.jd_text and job.jd_quality in _COMPLETE
    ]
    if not complete:
        raise RuntimeError(f"real job {real_id} has no complete evaluation source")
    return min(complete, key=_source_rank)


def _canonical_input(
    real_id: int, chosen: JobPosting, jobs: list[JobPosting]
) -> RealJobEvaluationInput:
    strict = select_real_job_input(str(real_id), jobs, now=datetime.now(UTC))
    if strict is not None:
        return strict
    first_discovery = min(job.discovered_at for job in jobs)
    original_dates = [
        job.posted_at
        for job in jobs
        if job.posted_at is not None and not job.is_repost
    ]
    canonical = replace(
        chosen,
        discovered_at=first_discovery,
        posted_at=min(original_dates) if original_dates else chosen.posted_at,
        closed_at=official_closed_at(jobs),
        is_repost=all(job.is_repost is True for job in jobs),
    )
    return RealJobEvaluationInput(str(real_id), chosen.id, canonical)


def main() -> None:
    args = _arguments()
    ids = _results_review_ids(args.api_base)
    connection = sqlite3.connect(args.db, timeout=30)
    connection.row_factory = sqlite3.Row
    resolved = 0
    overrides = 0
    try:
        connection.execute("BEGIN IMMEDIATE")
        for real_id in ids:
            state = connection.execute(
                "SELECT identity_review_state FROM real_jobs WHERE id=?", (real_id,)
            ).fetchone()
            if state is None or state["identity_review_state"] == "clear":
                continue
            rows = connection.execute(
                "SELECT * FROM jobs WHERE real_job_id=? ORDER BY id", (real_id,)
            ).fetchall()
            jobs = [_job_from_row(row) for row in rows]
            chosen = _choose_source(real_id, jobs)
            selected = _canonical_input(real_id, chosen, jobs)
            if select_real_job_input(str(real_id), jobs, now=datetime.now(UTC)) is None:
                connection.execute(
                    "INSERT INTO state(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (f"{_REVIEW_SOURCE_KEY}{real_id}", str(chosen.id)),
                )
                overrides += 1
            connection.execute(
                "DELETE FROM real_job_review_cases WHERE left_real_job_id=? "
                "AND right_real_job_id=? AND reason='requirements_conflict'",
                (real_id, real_id),
            )
            connection.execute(
                "UPDATE real_jobs SET representative_job_id=?,"
                "identity_review_state='clear' WHERE id=?",
                (int(chosen.id), real_id),
            )
            connection.execute(
                """INSERT OR IGNORE INTO real_job_evaluations(
                       real_job_id,source_job_id,input_jd_text,input_facts_json,
                       input_revision,claim_generation,updated_at)
                   VALUES(?,?,?,?,1,0,?)""",
                (
                    real_id,
                    int(chosen.id),
                    selected.job.jd_text,
                    input_facts_json(selected),
                    _utc_text(datetime.now(UTC)),
                ),
            )
            resolved += 1
        if args.dry_run:
            connection.rollback()
        else:
            connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    print(
        json.dumps(
            {
                "review_rows": len(ids),
                "resolved": resolved,
                "overrides": overrides,
                "dry_run": args.dry_run,
            }
        )
    )


if __name__ == "__main__":
    main()
