#!/usr/bin/env python3
"""Build free high-confidence seniority training labels from local JDs."""

from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
from pathlib import Path

from jobfeed.domain.seniority import classify_seniority_rule

_SWE_TITLE = re.compile(
    r"\b(?:software|developer|engineer|swe|sde|backend|front.?end|full.?stack"
    r"|machine learning|ml|ai|data|devops|sre|platform|infrastructure)\b",
    re.IGNORECASE,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--per-class", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=20260826)
    args = parser.parse_args()

    connection = sqlite3.connect(f"file:{args.database}?immutable=1", uri=True)
    rows = connection.execute(
        "SELECT id,title,jd_text FROM jobs WHERE jd_text IS NOT NULL AND jd_text<>''"
    )
    by_label: dict[str, list[dict[str, str]]] = {
        "in_scope": [],
        "out_of_scope": [],
    }
    for job_id, title, jd_text in rows:
        title_text = str(title)
        if not _SWE_TITLE.search(title_text):
            continue
        decision = classify_seniority_rule(title_text, str(jd_text))
        if decision.result == "unclear":
            continue
        by_label[decision.result].append(
            {
                "job_id": str(job_id),
                "title": title_text,
                "jd_text": str(jd_text),
                "label": decision.result,
                "reason": decision.reason,
                "teacher_model": "deterministic-rule-v1",
            }
        )
    connection.close()

    rng = random.Random(args.seed)
    selected: list[dict[str, str]] = []
    for label, candidates in by_label.items():
        rng.shuffle(candidates)
        chosen = candidates[: args.per_class]
        selected.extend(chosen)
        print(f"{label}={len(chosen)} available={len(candidates)}")
    rng.shuffle(selected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as stream:
        for row in selected:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote={len(selected)} path={args.output}")


if __name__ == "__main__":
    main()
