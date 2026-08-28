#!/usr/bin/env python3
"""Run a free local shadow evaluation of the seniority model."""

from __future__ import annotations

import argparse
import asyncio
import random
import sqlite3
from pathlib import Path

from jobfeed.adapters.ml.seniority_model import XGBoostSeniorityModel
from jobfeed.domain.seniority import SeniorityInput, classify_seniority_rule
from jobfeed.services.seniority_gate import HybridSeniorityGate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--threshold", type=float, required=True)
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260826)
    args = parser.parse_args()
    asyncio.run(_run(args))


async def _run(args: argparse.Namespace) -> None:
    jobs = _sample_unclear(args.database, args.count, args.seed)
    model = XGBoostSeniorityModel(
        model_dir=args.model_dir, model_version=args.model_version
    )
    gate = HybridSeniorityGate(
        model=model,
        out_of_scope_threshold=args.threshold,
        version=args.model_version,
    )
    decisions = await gate.predict_batch(jobs)
    out = [
        (job, decision)
        for job, decision in zip(jobs, decisions, strict=True)
        if decision.result == "out_of_scope"
    ]
    inside = len(decisions) - len(out)
    print(f"sample={len(decisions)} in_scope={inside} out_of_scope={len(out)}")
    print(f"blocked_pct={len(out) / max(1, len(decisions)):.4f}")
    conflicts = _sub_three_conflicts(
        args.database, [job.job_id for job, _decision in out]
    )
    print(f"stored_yoe_below_3_conflicts={len(conflicts)}")
    for job_id, title, yoe_min in conflicts[:20]:
        print(f"CONFLICT\t{job_id}\tyoe={yoe_min}\t{title}")
    print("highest_out_of_scope:")
    for job, decision in sorted(out, key=lambda item: item[1].confidence, reverse=True)[
        :20
    ]:
        print(f"{decision.confidence:.4f}\t{job.job_id}\t{job.title}")


def _sample_unclear(database: Path, count: int, seed: int) -> list[SeniorityInput]:
    connection = sqlite3.connect(f"file:{database}?immutable=1", uri=True)
    rows = connection.execute(
        "SELECT id,title,jd_text FROM jobs "
        "WHERE jd_text IS NOT NULL AND jd_text<>'' AND is_swe_role=1"
    ).fetchall()
    connection.close()
    jobs = [
        SeniorityInput(str(job_id), str(title), str(jd_text))
        for job_id, title, jd_text in rows
        if classify_seniority_rule(str(title), str(jd_text)).result == "unclear"
    ]
    random.Random(seed).shuffle(jobs)
    return jobs[:count]


def _sub_three_conflicts(
    database: Path, job_ids: list[str]
) -> list[tuple[str, str, int]]:
    if not job_ids:
        return []
    connection = sqlite3.connect(f"file:{database}?immutable=1", uri=True)
    placeholders = ",".join("?" for _ in job_ids)
    rows = connection.execute(
        f"SELECT id,title,yoe_min FROM jobs WHERE id IN ({placeholders}) "
        "AND yoe_min < 3",
        [int(job_id) for job_id in job_ids],
    ).fetchall()
    connection.close()
    return [(str(job_id), str(title), int(yoe_min)) for job_id, title, yoe_min in rows]


if __name__ == "__main__":
    main()
