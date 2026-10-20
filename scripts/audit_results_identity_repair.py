"""Group observed destination IDs in an exported snapshot; never write to a DB.

Usage: python scripts/audit_results_identity_repair.py snapshot.json report.json
The output is a review list, not an executable merge plan. It intentionally keeps
status conflicts, retained separations, and distinct official requisitions visible.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from itertools import combinations
from pathlib import Path

from jobfeed.domain.external_identity import ObservedIdentifier
from jobfeed.domain.models import JobPosting
from jobfeed.domain.real_job_identity import (
    ATS_REQUISITION_PROVIDERS,
    compatible_role_facts,
    source_identifiers,
)
from jobfeed.domain.requisition_alias import PREFIX, canonical_identifier


def audit(snapshot: dict) -> dict:  # noqa: C901 - explicit read-only audit guards
    """Return evidence groups and blockers from a single read-only snapshot."""
    aliases = {}
    separations = set()
    for row in snapshot["state"]:
        if row["key"].startswith(PREFIX):
            scope, _, native_id = row["key"][len(PREFIX) :].rpartition(":")
            aliases[scope, native_id] = row["value"]
        if row["key"].startswith("identity-separate:"):
            separations.add(tuple(map(int, row["key"].split(":")[1:])))
    groups = defaultdict(dict)
    official = defaultdict(lambda: defaultdict(set))
    statuses = defaultdict(set)
    parent_by_source = {r["id"]: r["real_job_id"] for r in snapshot["jobs"]}
    for row in snapshot["real_job_status"]:
        statuses[row["real_job_id"]].add(row["status"])
    for row in snapshot["job_status"]:
        statuses[parent_by_source[row["job_id"]]].add(row["status"])
    for row in snapshot["real_job_identifiers"]:
        item = canonical_identifier(
            ObservedIdentifier(
                row["provider"],
                row["scope"],
                row["native_id"],
                row["observed_url"] or "",
            ),
            aliases,
        )
        if item.provider in ATS_REQUISITION_PROVIDERS:
            official[row["real_job_id"]][item.provider, item.scope].add(item.native_id)
    for row in snapshot["jobs"]:
        parent = row["real_job_id"]
        job = JobPosting(
            **{
                k: row[k]
                for k in (
                    "platform",
                    "canonical_id",
                    "url",
                    "apply_url",
                    "company",
                    "title",
                    "location",
                )
            },
            discovered_at=datetime.fromisoformat(row["discovered_at"]),
        )
        for item in source_identifiers(job, aliases):
            if item.provider in ATS_REQUISITION_PROVIDERS:
                official[parent][item.provider, item.scope].add(item.native_id)
            if (
                item.provider in ATS_REQUISITION_PROVIDERS
                or item.provider == "linkedin"
            ):
                groups[item.provider, item.scope, item.native_id][parent] = (row, job)
    output = []
    for identity, members in sorted(groups.items()):
        if len(members) <= 1:
            continue
        blockers = set()
        explicit = set().union(*(statuses[p] - {"new", "scored"} for p in members))
        if len(explicit) > 1:
            blockers.add("explicit_status_conflict")
        scoped = defaultdict(set)
        for parent in members:
            for scope, ids in official[parent].items():
                scoped[scope].update(ids)
        if any(len(ids) > 1 for ids in scoped.values()):
            blockers.add("conflicting_requisition_ids")
        for left, right in combinations(sorted(members), 2):
            if (left, right) in separations:
                blockers.add("user_kept_separate")
            if not compatible_role_facts(members[left][1], members[right][1], aliases):
                blockers.add("conflicting_role_facts")
        output.append(
            {
                "identity": list(identity),
                "blockers": sorted(blockers),
                "members": [
                    {**row, "statuses": sorted(statuses[parent])}
                    for parent, (row, _) in sorted(members.items())
                ],
            }
        )
    return {
        "captured_at": snapshot["captured_at"],
        "scope": "Exported subset; review candidates, not authorization to merge",
        "source_count": len(snapshot["jobs"]),
        "group_count": len(output),
        "groups_without_detected_blockers": sum(not g["blockers"] for g in output),
        "groups": output,
    }


def main() -> None:
    """Read an exported snapshot and write only a JSON review artifact."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    report = audit(json.loads(args.snapshot.read_text()))
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "groups"}))


if __name__ == "__main__":
    main()
