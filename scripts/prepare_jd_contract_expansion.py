"""Select a diverse expansion cohort without reusing the original demo jobs.

Sampling buckets are inherited selection metadata, not correctness labels.
Reads only public JD records and writes new, inspectable experiment inputs.
"""

import argparse
import json
from collections import Counter
from pathlib import Path

from scripts.demo_jd_contract import load_documents

BUCKETS = ("graduation", "yoe", "degree", "compensation", "no_signal")


def select_samples(entries, excluded_ids, per_bucket=10):
    selected, companies, jobs = [], set(), set()
    for bucket in BUCKETS:
        count = 0
        for entry in sorted(entries, key=lambda item: item["sample_id"]):
            company = entry["company"].strip().casefold()
            job = entry["db_job_id"]
            if (
                entry["sampling_bucket"] != bucket
                or job in excluded_ids
                or job in jobs
                or company in companies
            ):
                continue
            selected.append(
                {
                    key: entry[key]
                    for key in ("sample_id", "db_job_id", "company", "sampling_bucket")
                }
            )
            companies.add(company)
            jobs.add(job)
            count += 1
            if count == per_bucket:
                break
        if count != per_bucket:
            raise ValueError(f"not enough distinct companies for {bucket}")
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("choose a new output directory")
    manifest = Path("data/jd-semantic-double-blind-500-manifest.json")
    original = json.loads(Path("data/jd-semantic-demo-50.json").read_text())
    excluded = {entry["db_job_id"] for entry in original["samples"]}
    selected = select_samples(json.loads(manifest.read_text())["samples"], excluded)
    documents = load_documents(
        manifest,
        Path("data/jobfeed.sqlite"),
        [entry["sample_id"] for entry in selected],
    )
    args.output.mkdir(parents=True)
    (args.output / "cohort.json").write_text(
        json.dumps(
            {
                "samples": selected,
                "sample_count": len(selected),
                "companies": len({entry["company"].casefold() for entry in selected}),
                "sampling_counts": dict(
                    Counter(entry["sampling_bucket"] for entry in selected)
                ),
                "excluded_original_job_count": len(excluded),
                "reference_status": "old_two_model_annotations_not_adjudicated_gold",
                "review_protocol": (
                    "primary_agent_full_text_posthoc_audit_not_blind_human_gold"
                ),
            },
            indent=2,
        )
    )
    (args.output / "inputs.json").write_text(
        json.dumps(
            [
                {"sample_id": sid, "document": doc.model_dump()}
                for sid, doc in documents
            ],
            ensure_ascii=False,
            indent=2,
        )
    )
    print(json.dumps({"samples": len(selected), "output": str(args.output)}))


if __name__ == "__main__":
    main()
