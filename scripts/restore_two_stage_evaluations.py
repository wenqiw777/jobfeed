"""Archive Evidence Fit evaluations, then return only those rows to unassessed.

Use only after backing up the database and stopping the local service. This
maintenance command never invokes models or changes jobs/application records.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

TARGET = (
    "stage_b_status='completed' AND json_valid(stage_b_fit_json) "
    "AND json_type(stage_b_fit_json, '$.similar_work')='object'"
)


def restore(db: Path, archive: Path, expected: int) -> dict[str, int]:
    """Archive exact original rows and reset A/B fields in one transaction."""
    if not db.is_file() or archive.exists() or db.resolve() == archive.resolve():
        raise ValueError("Require an existing database and a new archive path")
    with sqlite3.connect(db) as connection:
        active = connection.execute(
            "SELECT count(*) FROM pipeline_runs WHERE status='running'"
        ).fetchone()[0]
        if active:
            raise ValueError("Stop active pipeline runs before restoring")
        count = connection.execute(
            f"SELECT count(*) FROM evaluations WHERE {TARGET}"
        ).fetchone()[0]
        if count != expected or count == 0:
            raise ValueError(f"Expected {expected} Evidence Fit rows; found {count}")
        connection.execute("ATTACH DATABASE ? AS archive", (str(archive),))
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "CREATE TABLE archive.evaluations AS "
            f"SELECT * FROM evaluations WHERE {TARGET}"
        )
        archived = connection.execute(
            "SELECT count(*) FROM archive.evaluations"
        ).fetchone()[0]
        if archived != expected:
            raise ValueError("Target rows changed before transaction")
        columns = [
            row[1] for row in connection.execute("PRAGMA table_info(evaluations)")
        ]
        assignments = [
            f'"{name}"=' + ("0" if name.endswith("_error_count") else "NULL")
            for name in columns
            if name.startswith(("stage_a_", "stage_b_"))
        ]
        assignments.append("updated_at=strftime('%Y-%m-%dT%H:%M:%f000Z','now')")
        updated = connection.execute(
            f"UPDATE evaluations SET {', '.join(assignments)} "
            "WHERE job_id IN (SELECT job_id FROM archive.evaluations)"
        ).rowcount
        pending = connection.execute(
            "SELECT count(*) FROM evaluations e "
            "JOIN archive.evaluations a USING(job_id) "
            "WHERE e.stage_a_status IS NULL AND e.stage_b_status IS NULL "
            "AND e.stage_a_score IS NULL AND e.stage_b_fit_json IS NULL"
        ).fetchone()[0]
        if updated != expected or pending != expected:
            raise ValueError("Unexpected reset count; transaction rolled back")
        connection.commit()
    return {"archived": archived, "reset": updated, "pending": pending}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--expected", required=True, type=int)
    args = parser.parse_args()
    print(json.dumps(restore(args.db, args.archive, args.expected)))
