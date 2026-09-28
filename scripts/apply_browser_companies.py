"""Apply exact-posting company evidence read through the Chrome extension."""

import argparse
import json
import sqlite3

from jobfeed.domain.normalize import normalize_company

LINKEDIN_TITLE_PARTS = 3


def apply(db, audit, rows):
    connection = sqlite3.connect(db, timeout=30)
    connection.execute("ATTACH DATABASE ? AS evidence", (str(audit),))
    connection.execute(
        "CREATE TABLE IF NOT EXISTS evidence.changes "
        "(job_id INTEGER PRIMARY KEY, old_company TEXT, old_company_norm TEXT, "
        "new_company TEXT, evidence TEXT)"
    )
    changed = 0
    with connection:
        for row in rows:
            identifier = str(row["id"])
            expected_url = f"https://www.linkedin.com/jobs/view/{identifier}/"
            if not identifier.isdigit() or row["url"] != expected_url:
                continue
            parts = row["title"].rsplit(" | ", 2)
            if len(parts) != LINKEDIN_TITLE_PARTS or parts[-1] != "LinkedIn":
                continue
            company = parts[-2].strip()
            if not company or company.lower() == "unknown":
                continue
            job = connection.execute(
                "SELECT id,company,company_norm FROM jobs "
                "WHERE platform='linkedin' AND canonical_id=? "
                "AND (lower(trim(company))='unknown' OR trim(company)='')",
                (identifier,),
            ).fetchone()
            if not job:
                continue
            connection.execute(
                "INSERT INTO evidence.changes VALUES (?,?,?,?,?)",
                (*job, company, json.dumps(row, ensure_ascii=False)),
            )
            changed += connection.execute(
                "UPDATE jobs SET company=?,company_norm=? WHERE id=? AND company=?",
                (company, normalize_company(company), job[0], job[1]),
            ).rowcount
    connection.close()
    return changed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--audit", required=True)
    parser.add_argument("--rows", required=True)
    args = parser.parse_args()
    print(json.dumps({"updated": apply(args.db, args.audit, json.loads(args.rows))}))
