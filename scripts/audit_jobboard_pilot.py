"""Compare saved, browser-observed Jobright descriptions with visible JD sections.

Offline pilot only: no requests, production writes, or LLM calls. Optionally
inspect an exported recommendation response without assuming its field schema.
"""

from __future__ import annotations

import argparse
import json
import re
from html import unescape
from pathlib import Path


def plain(value: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", value)).split())


def inspect_capture(path: Path) -> dict:
    row = json.loads(path.read_text())
    description = row["schema"]["description"]
    sections = row["sections"]
    visible = " ".join(plain(section["text"]) for section in sections)
    # Only compare job-specific list items; company overview and sponsorship
    # history are additional platform context, not requirements of this job.
    job_html = re.split(r"<p>Company Overview</p>", description, maxsplit=1)[0]
    items = [plain(item) for item in re.findall(r"<li[^>]*>(.*?)</li>", job_html, re.S)]
    return {
        "file": path.name,
        "title": row["title"],
        "url": row["url"],
        "elapsed_ms": row["elapsed_ms"],
        "description_html_characters": len(description),
        "job_list_items": len(items),
        "items_absent_from_visible_sections": [
            item for item in items if item not in visible
        ],
        "page_has_required_preferred_split": "Required" in visible
        and "Preferred" in visible,
        "description_has_required_preferred_split": "Required" in plain(description)
        and "Preferred" in plain(description),
        "limit": (
            "One-way text containment; does not prove all visible text "
            "or employer original is retained."
        ),
    }


def field_inventory(value: object, prefix: str = "") -> list[dict]:
    if isinstance(value, dict):
        return [
            entry
            for key, item in value.items()
            for entry in field_inventory(item, f"{prefix}.{key}".strip("."))
        ]
    if isinstance(value, list):
        return [
            entry for item in value for entry in field_inventory(item, prefix + "[]")
        ]
    if isinstance(value, str):
        return [{"path": prefix, "characters": len(value)}]
    return []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--raw-recommendations", type=Path)
    args = parser.parse_args()
    rows = [
        inspect_capture(path)
        for path in sorted(args.directory.glob("jobright-structured-*.json"))
    ]
    result = {
        "jobright_samples": rows,
        "sample_count": len(rows),
        "live_batch_api_verified": False,
    }
    if args.raw_recommendations:
        result["raw_field_inventory"] = field_inventory(
            json.loads(args.raw_recommendations.read_text())
        )
    destination = args.directory / "content-audit.json"
    destination.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "samples": len(rows),
                "unmatched_items": sum(
                    len(row["items_absent_from_visible_sections"]) for row in rows
                ),
                "report": str(destination),
            }
        )
    )


if __name__ == "__main__":
    main()
