"""Build a source-aware 200-SDE cohort from the local Jobfeed database."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

LINKEDIN_PLATFORMS = {"linkedin", "linkedin_guest", "linkedin_jobspy"}
NATIVE_API_SOURCES = {"api-greenhouse", "api-lever", "api-ashby"}
OFFICIAL_UNSTRUCTURED_SOURCES = {
    "detail-page",
    "jobright_official_icims",
    "jobright_official_jsonld",
    "jobright_official_smartrecruiters",
    "jobright_official_workday",
    "manual-paste",
    "speedyapply-icims",
    "speedyapply-icims-notfound",
    "speedyapply-smartrecruiters",
    "speedyapply-workday",
    "speedyapply-workday-notfound",
}
SDE_TITLE_MARKERS = (
    "software engineer",
    "software developer",
    "software development engineer",
    "full stack",
    "full-stack",
    "backend engineer",
    "back-end engineer",
    "frontend engineer",
    "front-end engineer",
    "mobile engineer",
    "ios engineer",
    "android engineer",
    "embedded software",
    "firmware engineer",
    "platform engineer",
)
AGGREGATOR_HOSTS = {"indeed.com", "jobright.ai", "linkedin.com"}


def is_sde_title(title):
    normalized = title.casefold()
    return any(marker in normalized for marker in SDE_TITLE_MARKERS)


def _is_aggregator_url(url):
    host = urlparse(url).netloc.casefold().removeprefix("www.")
    return any(host == item or host.endswith(f".{item}") for item in AGGREGATOR_HOSTS)


def select_official_cases(rows, limit, max_per_source=35, max_per_company=1):
    selected = []
    company_counts = Counter()
    source_counts = Counter()
    seen_texts = set()
    for row in rows:
        source = row["enrich_source"]
        normalized_text = " ".join(row["jd_text"].casefold().split())
        if (
            not is_sde_title(row["title"])
            or source in NATIVE_API_SOURCES
            or source not in OFFICIAL_UNSTRUCTURED_SOURCES
            or _is_aggregator_url(row["url"])
            or company_counts[row["company_norm"]] >= max_per_company
            or source_counts[source] >= max_per_source
            or normalized_text in seen_texts
        ):
            continue
        selected.append(row)
        company_counts[row["company_norm"]] += 1
        source_counts[source] += 1
        seen_texts.add(normalized_text)
        if len(selected) == limit:
            break
    return selected


def select_linkedin_cases(  # noqa: PLR0913
    rows,
    official_keys,
    limit,
    *,
    excluded_companies=None,
    excluded_texts=None,
    platform_targets=None,
):
    selected = []
    companies = set(excluded_companies or ())
    seen_texts = set(excluded_texts or ())
    strict_targets = platform_targets is not None
    targets = platform_targets or {"all": limit}
    if sum(targets.values()) != limit:
        raise ValueError("platform targets must sum to the requested limit")
    for platform, target in targets.items():
        added = 0
        for row in rows:
            normalized_text = " ".join(row["jd_text"].casefold().split())
            job_key = (row["company_norm"], row["title_norm"])
            platform_matches = (
                row["platform"] in LINKEDIN_PLATFORMS
                if platform == "all"
                else row["platform"] == platform
            )
            if (
                not platform_matches
                or not is_sde_title(row["title"])
                or job_key in official_keys
                or row["company_norm"] in companies
                or normalized_text in seen_texts
            ):
                continue
            selected.append(row)
            companies.add(row["company_norm"])
            seen_texts.add(normalized_text)
            added += 1
            if added == target:
                break
        if strict_targets and added != target:
            raise ValueError(
                f"insufficient rows for platform {platform}: {added}/{target}"
            )
    return selected


def load_rows(database):
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        result = connection.execute(
            """
            SELECT id, platform, company, title, location, url, jd_text,
                   jd_quality, enrich_source, company_norm, title_norm,
                   location_norm, discovered_at
            FROM jobs
            WHERE is_swe_role = 1
              AND jd_quality = 'full'
              AND jd_text IS NOT NULL
              AND length(jd_text) >= 800
            ORDER BY discovered_at DESC, id DESC
            """
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in result]


def build_cohort(  # noqa: PLR0913
    rows,
    official_count=100,
    linkedin_count=100,
    *,
    max_official_per_company=1,
    max_per_source=35,
    linkedin_platform_targets=None,
):
    official_keys = {
        (row["company_norm"], row["title_norm"])
        for row in rows
        if row["platform"] not in LINKEDIN_PLATFORMS
    }
    official = select_official_cases(
        rows,
        official_count,
        max_per_source=max_per_source,
        max_per_company=max_official_per_company,
    )
    linkedin = select_linkedin_cases(
        rows,
        official_keys,
        linkedin_count,
        excluded_companies={row["company_norm"] for row in official},
        excluded_texts={
            " ".join(row["jd_text"].casefold().split()) for row in official
        },
        platform_targets=linkedin_platform_targets,
    )
    if len(official) != official_count or len(linkedin) != linkedin_count:
        raise ValueError(
            f"insufficient eligible rows: official={len(official)}, "
            f"linkedin={len(linkedin)}, required={official_count}/{linkedin_count}"
        )
    return [("official_unstructured", row) for row in official] + [
        ("linkedin_only_in_corpus", row) for row in linkedin
    ]


def write_cohort(output_dir, cohort):
    output_dir.mkdir(parents=True)
    manifest = []
    inputs = []
    for sample_id, (source_group, row) in enumerate(cohort, 1):
        manifest.append(
            {
                "sample_id": sample_id,
                "db_job_id": row["id"],
                "source_group": source_group,
                "platform": row["platform"],
                "enrich_source": row["enrich_source"],
                "company": row["company"],
                "title": row["title"],
                "location": row["location"],
                "url": row["url"],
                "discovered_at": row["discovered_at"],
                "jd_characters": len(row["jd_text"]),
            }
        )
        inputs.append(
            {
                "sample_id": sample_id,
                "source_group": source_group,
                "document": {
                    "job_id": row["id"],
                    "title": row["title"],
                    "text": row["jd_text"],
                    "completeness": "full",
                    "source_url": row["url"],
                },
            }
        )
    summary = {
        "samples": len(cohort),
        "source_groups": dict(Counter(group for group, _ in cohort)),
        "platforms": dict(Counter(row["platform"] for _, row in cohort)),
        "enrich_sources": dict(Counter(row["enrich_source"] for _, row in cohort)),
        "companies": len({row["company_norm"] for _, row in cohort}),
        "selection": {
            "role": "software engineering / SDE title",
            "jd_quality": "full",
            "minimum_characters": 800,
            "official_group": "no Greenhouse/Lever/Ashby native API result",
            "linkedin_group": "no exact company/title official copy in local corpus",
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps({"summary": summary, "samples": manifest}, indent=2),
        encoding="utf-8",
    )
    (output_dir / "inputs.json").write_text(
        json.dumps({"summary": summary, "samples": inputs}, indent=2),
        encoding="utf-8",
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/jobfeed.sqlite"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--per-group", type=int, default=100)
    parser.add_argument("--official-count", type=int)
    parser.add_argument("--linkedin-count", type=int)
    parser.add_argument("--max-official-per-company", type=int, default=1)
    parser.add_argument("--max-per-source", type=int, default=35)
    parser.add_argument(
        "--linkedin-platform-targets",
        help="comma-separated platform=count quotas",
    )
    args = parser.parse_args()
    if args.per_group < 1:
        parser.error("per-group must be positive")
    if args.out.exists():
        parser.error("output directory already exists")

    official_count = args.official_count or args.per_group
    linkedin_count = args.linkedin_count or args.per_group
    linkedin_platform_targets = None
    if args.linkedin_platform_targets:
        linkedin_platform_targets = {
            platform: int(count)
            for platform, count in (
                item.split("=", 1) for item in args.linkedin_platform_targets.split(",")
            )
        }
    cohort = build_cohort(
        load_rows(args.database),
        official_count,
        linkedin_count,
        max_official_per_company=args.max_official_per_company,
        max_per_source=args.max_per_source,
        linkedin_platform_targets=linkedin_platform_targets,
    )
    print(json.dumps(write_cohort(args.out, cohort), indent=2))


if __name__ == "__main__":
    main()
