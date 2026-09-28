"""Reproducible stratified random JD sample; read-only, no model calls."""

# Report strings and SQL are intentionally kept on a single line.
# ruff: noqa: E501

import difflib
import json
import random
import sqlite3
from collections import defaultdict
from pathlib import Path

SEED = 20260922


def main():
    rng = random.Random(SEED)
    connection = sqlite3.connect("file:data/jobfeed.sqlite?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows = [
        dict(row)
        for row in connection.execute(
            "SELECT id, company, company_norm, title, title_norm, location, platform "
            "FROM jobs WHERE jd_quality='full' AND length(jd_text)>=1200 "
            "AND company_norm NOT IN ('','unknown','unknown company') ORDER BY id"
        )
    ]
    companies = defaultdict(lambda: defaultdict(list))
    for row in rows:
        companies[row["company_norm"]][row["title_norm"]].append(row)
    same = {}
    different = {}
    for company, titles in companies.items():
        groups = [group for group in titles.values() if len(group) > 1]
        if groups:
            same[company] = groups
        if len(titles) > 1:
            different[company] = list(titles.values())
    pairs = []
    for company in rng.sample(sorted(same), 12):
        group = rng.choice(same[company])
        a, b = rng.sample(group, 2)
        pairs.append(("same_normalized_title", a, b))
    for company in rng.sample(sorted(different), 8):
        ga, gb = rng.sample(different[company], 2)
        pairs.append(("different_normalized_title", rng.choice(ga), rng.choice(gb)))
    samples = []
    review = []
    for index, (stratum, a, b) in enumerate(pairs, 1):
        for row in (a, b):
            row.update(
                dict(
                    connection.execute(
                        "SELECT jd_text, url, canonical_id, external_identity FROM jobs WHERE id=?",
                        (row["id"],),
                    ).fetchone()
                )
            )
        left, right = a["jd_text"].split(), b["jd_text"].split()
        matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
        differences = [
            {"left": " ".join(left[i:j]), "right": " ".join(right[k:end])}
            for tag, i, j, k, end in matcher.get_opcodes()
            if tag != "equal"
        ]
        samples.append(
            {
                "case": index,
                "stratum": stratum,
                "left": a,
                "right": b,
                "raw_equal": a["jd_text"] == b["jd_text"],
                "word_sequence_ratio": matcher.ratio(),
                "differences": differences,
            }
        )
        review.extend(
            [
                f"\nCASE {index} {stratum}",
                f"A: {a['id']} {a['company']} | {a['title']} | {a['location']} | {a['platform']}",
                f"B: {b['id']} {b['company']} | {b['title']} | {b['location']} | {b['platform']}",
                f"raw_equal={a['jd_text'] == b['jd_text']} ratio={matcher.ratio():.4f}",
            ]
        )
        for difference in differences:
            review.extend(["- " + difference["left"], "+ " + difference["right"]])
    connection.close()
    output = Path("artifacts/reuse-hypothesis-random")
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "seed": SEED,
        "eligible_records": len(rows),
        "same_title_company_pool": len(same),
        "different_title_company_pool": len(different),
        "method": "12 companies uniform without replacement, then one same-title group and two records; independently 8 companies then two distinct title groups and one record each. All choices sorted before random sampling where applicable. No similarity threshold used for selection.",
        "samples": samples,
    }
    (output / "samples.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2)
    )
    (output / "differences.txt").write_text("\n".join(review))
    print({key: value for key, value in manifest.items() if key != "samples"})
    print("\n".join(line for line in review if not line.startswith(("- ", "+ "))))


if __name__ == "__main__":
    main()
