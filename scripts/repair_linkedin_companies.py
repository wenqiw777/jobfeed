"""Backfill only missing LinkedIn companies with exact-ID or public-header evidence."""

import argparse
import asyncio
import json
import sqlite3
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from jobfeed.domain.normalize import normalize_company


async def repair(db: Path, audit: Path, evidence: Path | None = None) -> None:  # noqa: C901
    """Keep a recoverable audit before updating just company and company_norm."""
    if audit.exists():
        raise ValueError("Audit path must be new")
    connection = sqlite3.connect(db, timeout=30)
    connection.row_factory = sqlite3.Row
    targets = connection.execute(
        "SELECT id,canonical_id,company,company_norm FROM jobs "
        "WHERE platform='linkedin' AND "
        "(lower(trim(company))='unknown' OR trim(company)='') "
        "ORDER BY discovered_at DESC"
    ).fetchall()
    records = []
    cached = {}
    if evidence is not None:
        with sqlite3.connect(f"file:{evidence}?mode=ro", uri=True) as prior:
            cached = {
                row[0]: row[1:]
                for row in prior.execute(
                    "SELECT job_id,new_company,evidence FROM changes"
                )
            }
    known = {}
    for row in connection.execute(
        "SELECT canonical_id,company FROM jobs WHERE platform IN "
        "('linkedin','linkedin_guest','linkedin_jobspy') "
        "AND trim(company)<>'' AND lower(trim(company))<>'unknown'"
    ):
        known.setdefault(row["canonical_id"], set()).add(row["company"])
    audit_db = sqlite3.connect(audit)
    audit_db.execute(
        "CREATE TABLE changes (job_id INTEGER PRIMARY KEY, old_company TEXT, "
        "old_company_norm TEXT, new_company TEXT, evidence TEXT)"
    )
    blocked = asyncio.Event()
    if evidence is not None:
        # Apply rehearsal evidence without repeating network calls.
        blocked.set()
    queue = asyncio.Queue()
    for row in targets:
        queue.put_nowait(row)
    processed = 0

    def save(row, company, evidence):
        """Commit original values before a guarded production update."""
        audit_db.execute(
            "INSERT INTO changes VALUES (?,?,?,?,?)",
            (row["id"], row["company"], row["company_norm"], company, evidence),
        )
        audit_db.commit()
        connection.execute(
            "UPDATE jobs SET company=?,company_norm=? WHERE id=? AND company=?",
            (company, normalize_company(company), row["id"], row["company"]),
        )
        connection.commit()
        records.append(row["id"])

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:

        async def worker():
            """Resolve four independent postings with one-second per-worker pacing."""
            nonlocal processed
            while not queue.empty():
                row = queue.get_nowait()
                names = known.get(row["canonical_id"], set())
                if row["id"] in cached:
                    save(row, *cached[row["id"]])
                elif len(names) == 1:
                    save(row, next(iter(names)), "same LinkedIn native job ID")
                elif not blocked.is_set() and row["canonical_id"].isdigit():
                    url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{row['canonical_id']}"
                    try:
                        response = await client.get(url)
                        if response.status_code in (401, 403, 429, 999):
                            blocked.set()
                            print(
                                json.dumps(
                                    {"public_lookup_stopped": response.status_code}
                                ),
                                flush=True,
                            )
                        elif response.status_code == httpx.codes.OK:
                            node = BeautifulSoup(
                                response.text, "html.parser"
                            ).select_one(".topcard__org-name-link")
                            name = node.get_text(" ", strip=True) if node else ""
                            if name and name.lower() != "unknown":
                                save(row, name, url)
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(1)
                processed += 1
                if processed % 50 == 0:
                    print(
                        json.dumps(
                            {
                                "processed": processed,
                                "total": len(targets),
                                "repaired": len(records),
                            }
                        ),
                        flush=True,
                    )

        await asyncio.gather(*(worker() for _ in range(4)))
    print(
        json.dumps(
            {
                "total": len(targets),
                "repaired": len(records),
                "remaining": len(targets) - len(records),
                "rate_limited": blocked.is_set(),
                "audit": str(audit),
            }
        ),
        flush=True,
    )
    connection.close()
    audit_db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    asyncio.run(repair(args.db, args.audit, args.evidence))
