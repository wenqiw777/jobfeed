"""Loopback-only evidence receiver for the user's Chrome extension session.

Does not contact LinkedIn. The browser supplies observed titles and exact URLs.
Each accepted batch is audited before returning the next missing native IDs.
"""

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from apply_browser_companies import apply

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/jobfeed.sqlite"
AUDIT = ROOT / "artifacts/company-browser-backfill-20260919.sqlite"
TOKEN = str(uuid4())
seen = set()


class Receiver(BaseHTTPRequestHandler):
    def do_GET(self):
        request = urlparse(self.path)
        if request.path != "/" + TOKEN:
            self.send_error(404)
            return
        params = parse_qs(request.query)
        rows = json.loads(params.get("rows", ["[]"])[0])
        if len(rows) > 40:
            self.send_error(400)
            return
        updated = apply(DB, AUDIT, rows)
        seen.update(str(row["id"]) for row in rows)
        with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as conn:
            missing = conn.execute(
                "SELECT canonical_id FROM jobs WHERE platform='linkedin' "
                "AND (lower(trim(company))='unknown' OR trim(company)='') "
                "ORDER BY posted_at DESC"
            ).fetchall()
        next_ids = [row[0] for row in missing if row[0] not in seen][:20]
        result = json.dumps({"updated": updated, "remaining": len(missing), "ids": next_ids})
        body = ("<!doctype html><title>" + result + "</title><pre>" + result + "</pre>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # Evidence stays in SQLite, not in URL access logs.


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 0), Receiver)
    print(f"http://127.0.0.1:{server.server_port}/{TOKEN}", flush=True)
    server.serve_forever()
