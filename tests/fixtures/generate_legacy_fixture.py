"""Generate a synthetic legacy v16 SQLite fixture for import testing.

This script creates an anonymized legacy v16 database with fake data that
exercises all import paths. It is synthetic-only and must not be treated as the
real legacy parity fixture described in the Phase 1 plan.

Usage:
    python tests/fixtures/generate_legacy_fixture.py \
        --out-db /tmp/legacy_v16.synthetic.db \
        --out-manifest /tmp/legacy_v16.synthetic_manifest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

PLATFORMS = ["linkedin", "indeed", "greenhouse", "lever", "workday"]
FAKE_COMPANIES = [
    "Acme Corp",
    "Globex Inc.",
    "Initech Technologies",
    "Umbrella LLC",
    "Stark Industries",
    "Wayne Enterprises",
    "Cyberdyne Systems",
    "Soylent Corp",
    "Tyrell Corp",
    "Weyland-Yutani",
]
FAKE_TITLES = [
    "Software Engineer",
    "Senior Software Engineer",
    "Staff Engineer",
    "Backend Developer",
    "Full Stack Developer",
    "ML Engineer",
    "Data Engineer",
    "Platform Engineer",
    "DevOps Engineer",
    "Site Reliability Engineer",
    "Frontend Developer",
    "Engineering Manager",
    "Principal Engineer",
    "Cloud Architect",
    "Security Engineer",
    "Systems Engineer",
    "QA Engineer",
    "Infrastructure Engineer",
    "Mobile Developer",
    "Research Scientist",
]
LOCATIONS = [
    "San Francisco, CA",
    "New York, NY",
    "Seattle, WA",
    "Austin, TX",
    "Remote",
    "Boston, MA",
    "Chicago, IL",
    "",
    "Denver, CO",
    "Los Angeles, CA",
]
QUALITY_BANDS = ["full", "good", "partial", "stub", "missing"]
STATUS_VALUES = [
    "new",
    "scored",
    "shortlisted",
    "archived",
    "ignored",
    "applied",
    "interviewing",
    "rejected",
    "offer",
    "ghosted",
]
VERDICTS = ["apply", "consider", "skip"]

JD_PLACEHOLDER = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
    "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
    "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris "
    "nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in "
    "reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla "
    "pariatur. Excepteur sint occaecat cupidatat non prosequent, sunt in "
    "culpa qui officia deserunt mollit anim id est laborum."
)

RESUME_PLACEHOLDER = (
    "John Doe\njohn.doe@example.com\n\nExperience:\n"
    "Software Engineer at Example Corp (2020-2024)\n"
    "Junior Developer at Sample Inc (2018-2020)\n\n"
    "Education:\nBS Computer Science, State University (2018)\n\n"
    "Skills: Python, SQL, AWS, Docker, Kubernetes"
)

COVER_LETTER_PLACEHOLDER = (
    "Dear Hiring Manager,\n\n"
    "I am writing to express my interest in the position.\n\n"
    "Best regards,\nJohn Doe"
)

# Seed for reproducible fixture generation
random.seed(42)


def _ts(days_ago: int, hour: int = 12) -> str:
    """Generate an ISO timestamp N days ago."""
    dt = datetime(2026, 5, 1, hour, 0, 0, tzinfo=UTC) - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(content: str) -> str:
    """Compute sha256 hex digest of content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _normalize(s: str) -> str:
    """Simple normalization matching legacy behavior."""
    import re

    result = re.sub(r"[^a-z0-9]+", "", s.lower())
    for suffix in [
        "inc",
        "llc",
        "corp",
        "ltd",
        "technologies",
        "enterprises",
        "systems",
    ]:
        if result.endswith(suffix):
            result = result[: -len(suffix)].rstrip()
    return result


def _make_fit_json(score: int) -> str:
    """Create a legacy block_c_fit_analysis JSON."""
    return json.dumps(
        {
            "score_0_100": score,
            "strong_match": [
                {"requirement": "Python proficiency", "evidence": "5 years exp"}
            ],
            "gaps": [
                {
                    "requirement": "Kubernetes",
                    "severity": "minor",
                    "mitigation": "Fast learner",
                }
            ],
        }
    )


def _make_verdict_json(verdict: str) -> str:
    """Create a legacy block_a_verdict JSON."""
    return json.dumps({"verdict": verdict, "confidence": "high"})


def _make_summary_json() -> str:
    """Create a legacy block_b_jd_summary JSON."""
    return json.dumps(
        {
            "summary": "This role involves building scalable systems.",
            "key_requirements": ["Python", "AWS", "SQL"],
        }
    )


def _make_hooks_json() -> str:
    """Create a legacy block_e_resume_hooks JSON."""
    return json.dumps(
        {
            "hooks": [
                "Strong Python background",
                "Relevant cloud experience",
            ]
        }
    )


# ---- Legacy v16 Schema ----

LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    canonical_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT,
    jd_text TEXT,
    jd_text_quality TEXT,
    posted_at TEXT,
    scraped_at TEXT NOT NULL,
    discovered_at TEXT,
    enriched_at TEXT,
    enrich_source TEXT,
    company_norm TEXT,
    title_norm TEXT,
    location_norm TEXT,
    jd_lang TEXT,
    enrich_error TEXT,
    quality_rubric_version INTEGER,
    reapply_notice TEXT,
    hard_filter TEXT,
    seniority_level TEXT,
    degree_required TEXT,
    clearance_required INTEGER,
    school_restricted INTEGER,
    domain_tags TEXT,
    tech_required TEXT,
    role_type TEXT,
    yoe_min INTEGER,
    ml_gate_score REAL,
    ml_gate_result TEXT,
    ml_gate_fail_reason TEXT,
    ml_gate_at TEXT,
    ml_gate_version TEXT,
    is_swe_role INTEGER,
    UNIQUE(platform, canonical_id)
);

CREATE TABLE IF NOT EXISTS evaluations (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
    stage_a_score INTEGER,
    stage_a_one_line TEXT,
    timing_eligible TEXT,
    stage_a_status TEXT,
    stage_a_error TEXT,
    stage_a_model TEXT,
    stage_a_cost_usd REAL,
    stage_a_prompt_hash TEXT,
    resume_hash TEXT,
    stage_b_verdict TEXT,
    stage_b_jd_summary TEXT,
    block_a_verdict TEXT,
    block_b_jd_summary TEXT,
    block_c_fit_analysis TEXT,
    block_e_resume_hooks TEXT,
    stage_b_blocks_run TEXT,
    stage_b_status TEXT,
    stage_b_error TEXT,
    stage_b_model TEXT,
    stage_b_cost_usd REAL,
    stage_b_prompt_hash TEXT,
    stage_b_resume_hash TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_status (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
    status TEXT NOT NULL DEFAULT 'new',
    next_followup_at TEXT,
    resume_variant TEXT,
    notes TEXT,
    last_status_change_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    from_status TEXT,
    to_status TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    reason TEXT,
    resume_variant_at_change TEXT
);

CREATE TABLE IF NOT EXISTS applied (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
    applied_at TEXT NOT NULL,
    notes TEXT,
    master_resume_hash TEXT,
    tailored_resume_hash TEXT,
    cover_letter TEXT,
    application_method TEXT,
    block_a_snapshot TEXT,
    block_c_snapshot TEXT,
    block_e_snapshot TEXT
);

CREATE TABLE IF NOT EXISTS resume_snapshots (
    resume_hash TEXT PRIMARY KEY,
    captured_at TEXT NOT NULL,
    source TEXT NOT NULL,
    content TEXT NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS resume_variants (
    name TEXT PRIMARY KEY,
    description TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS companies (
    slug TEXT PRIMARY KEY,
    ats_vendor TEXT,
    ats_override INTEGER NOT NULL DEFAULT 0,
    last_verified_at TEXT,
    last_probe_attempt_at TEXT,
    job_count_last_scan INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    consecutive_discover_failures INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cost_ledger (
    day TEXT PRIMARY KEY,
    spent_usd REAL NOT NULL DEFAULT 0.0,
    calls INTEGER NOT NULL DEFAULT 0,
    last_updated TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def generate(db_path: Path) -> dict:
    """Generate the legacy v16 fixture database and return row counts."""
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    conn.executescript(LEGACY_SCHEMA)

    counts: dict[str, int] = {}

    # ---- Resume variants (no FK deps) ----
    variants = ["default", "ml-focused", "backend-focused"]
    for v in variants:
        conn.execute(
            "INSERT INTO resume_variants (name, description, created_at) VALUES (?, ?, ?)",
            (v, f"Resume variant: {v}", _ts(30)),
        )
    counts["resume_variants"] = len(variants)

    # ---- Resume snapshots ----
    resume_contents = []
    resume_hashes = []
    for i in range(3):
        content = f"{RESUME_PLACEHOLDER}\nVariant {i}: {variants[i % len(variants)]}"
        h = _sha256(content)
        resume_contents.append(content)
        resume_hashes.append(h)
        conn.execute(
            "INSERT INTO resume_snapshots (resume_hash, captured_at, source, content, notes) "
            "VALUES (?, ?, ?, ?, ?)",
            (h, _ts(25 - i), variants[i % len(variants)], content, None),
        )
    counts["resume_snapshots"] = 3

    # ---- Jobs ----
    num_jobs = 20
    job_ids = []
    for i in range(1, num_jobs + 1):
        platform = PLATFORMS[i % len(PLATFORMS)]
        company = FAKE_COMPANIES[i % len(FAKE_COMPANIES)]
        title = FAKE_TITLES[i % len(FAKE_TITLES)]
        loc = LOCATIONS[i % len(LOCATIONS)]
        # Some jobs have NULL location to test COALESCE
        if i == 8:
            loc = None
        quality = QUALITY_BANDS[i % len(QUALITY_BANDS)] if i <= 15 else None
        jd = JD_PLACEHOLDER if quality in ("full", "good", "partial") else None
        scraped_at = _ts(num_jobs - i)
        discovered_at = _ts(num_jobs - i - 1) if i % 3 == 0 else None
        posted_at = _ts(num_jobs - i + 5) if i % 2 == 0 else None
        enriched_at = _ts(num_jobs - i - 2) if jd else None
        enrich_source = "scraper" if jd else None

        company_norm = _normalize(company)
        title_norm = _normalize(title)
        location_norm = _normalize(loc) if loc else ""

        ml_gate_score = round(random.uniform(0.1, 0.99), 4) if i <= 12 else None
        ml_gate_result = (
            ("pass" if ml_gate_score and ml_gate_score > 0.5 else "fail")
            if ml_gate_score
            else None
        )
        ml_gate_at = _ts(num_jobs - i - 1) if ml_gate_score else None
        ml_gate_version = "v1.2" if ml_gate_score else None
        is_swe_role = 1 if i % 3 != 0 else 0

        conn.execute(
            """INSERT INTO jobs (
                id, platform, canonical_id, url, title, company, location,
                jd_text, jd_text_quality, posted_at, scraped_at, discovered_at,
                enriched_at, enrich_source, company_norm, title_norm, location_norm,
                jd_lang, enrich_error, quality_rubric_version, reapply_notice,
                hard_filter, seniority_level, degree_required, clearance_required,
                school_restricted, domain_tags, tech_required, role_type, yoe_min,
                ml_gate_score, ml_gate_result, ml_gate_fail_reason, ml_gate_at,
                ml_gate_version, is_swe_role
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?
            )""",
            (
                i,
                platform,
                f"job-{i:04d}",
                f"https://example.com/jobs/{i}",
                title,
                company,
                loc,
                jd,
                quality,
                posted_at,
                scraped_at,
                discovered_at,
                enriched_at,
                enrich_source,
                company_norm,
                title_norm,
                location_norm,
                "en" if jd else None,
                None,
                1 if quality else None,
                None,
                None,
                "senior" if "Senior" in title else None,
                "BS" if i % 4 == 0 else None,
                1 if i == 5 else 0,
                0,
                json.dumps(["backend", "infra"]) if i % 3 == 0 else None,
                json.dumps(["python", "sql"]) if i <= 10 else None,
                "ic" if i % 2 == 0 else "lead",
                random.randint(0, 10) if i <= 10 else None,
                ml_gate_score,
                ml_gate_result,
                "low_score" if ml_gate_result == "fail" else None,
                ml_gate_at,
                ml_gate_version,
                is_swe_role,
            ),
        )
        job_ids.append(i)
    counts["jobs"] = num_jobs

    # ---- Evaluations ----
    num_evals = 15
    for i in range(1, num_evals + 1):
        job_id = job_ids[i - 1]
        score = random.randint(10, 95)
        rh = resume_hashes[i % len(resume_hashes)]
        stage_b_done = i <= 10
        verdict = VERDICTS[i % len(VERDICTS)] if stage_b_done else None
        fit_score = random.randint(20, 90) if stage_b_done else None
        blocks_run = "a,b,c,e" if stage_b_done else None
        ts_created = _ts(num_jobs - i)
        ts_updated = _ts(num_jobs - i - 1) if stage_b_done else ts_created

        conn.execute(
            """INSERT INTO evaluations (
                job_id, stage_a_score, stage_a_one_line, timing_eligible,
                stage_a_status, stage_a_error, stage_a_model, stage_a_cost_usd,
                stage_a_prompt_hash, resume_hash,
                stage_b_verdict, stage_b_jd_summary,
                block_a_verdict, block_b_jd_summary,
                block_c_fit_analysis, block_e_resume_hooks,
                stage_b_blocks_run,
                stage_b_status, stage_b_error, stage_b_model, stage_b_cost_usd,
                stage_b_prompt_hash, stage_b_resume_hash,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?,
                ?, ?, ?, ?,
                ?, ?,
                ?, ?
            )""",
            (
                job_id,
                score,
                f"Good fit for role #{i}",
                "yes" if i % 2 == 0 else "no",
                "completed",
                None,
                "gpt-4o",
                round(random.uniform(0.001, 0.05), 4),
                _sha256(f"prompt-a-{i}"),
                rh,
                verdict,
                f"Summary for job #{job_id}" if stage_b_done else None,
                _make_verdict_json(verdict) if stage_b_done else None,
                _make_summary_json() if stage_b_done else None,
                _make_fit_json(fit_score) if stage_b_done else None,
                _make_hooks_json() if stage_b_done else None,
                blocks_run,
                "completed" if stage_b_done else None,
                None,
                "gpt-4o" if stage_b_done else None,
                round(random.uniform(0.01, 0.10), 4) if stage_b_done else None,
                _sha256(f"prompt-b-{i}") if stage_b_done else None,
                rh if stage_b_done else None,
                ts_created,
                ts_updated,
            ),
        )
    counts["evaluations"] = num_evals

    # ---- Job status ----
    num_statuses = 10
    for i in range(1, num_statuses + 1):
        job_id = job_ids[i - 1]
        status = STATUS_VALUES[i % len(STATUS_VALUES)]
        followup = _ts(-3) if status == "applied" else None
        variant = variants[0] if status in ("applied", "interviewing") else None
        conn.execute(
            "INSERT INTO job_status (job_id, status, next_followup_at, resume_variant, notes, last_status_change_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, status, followup, variant, None, _ts(num_jobs - i)),
        )
    counts["job_status"] = num_statuses

    # ---- Job status history ----
    history_id = 0
    num_history = 10
    for i in range(1, num_history + 1):
        job_id = job_ids[(i - 1) % num_statuses]
        history_id += 1
        conn.execute(
            "INSERT INTO job_status_history (id, job_id, from_status, to_status, changed_at, reason, resume_variant_at_change) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (history_id, job_id, None, "new", _ts(num_jobs - i), None, None),
        )
        # Some jobs get a second transition
        if i <= 5:
            history_id += 1
            target_status = STATUS_VALUES[i % len(STATUS_VALUES)]
            conn.execute(
                "INSERT INTO job_status_history (id, job_id, from_status, to_status, changed_at, reason, resume_variant_at_change) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    history_id,
                    job_id,
                    "new",
                    target_status,
                    _ts(num_jobs - i - 1),
                    f"Auto transition #{i}",
                    variants[0] if target_status in ("applied",) else None,
                ),
            )
    counts["job_status_history"] = history_id  # total rows inserted

    # ---- Applied ----
    num_applied = 5
    for i in range(1, num_applied + 1):
        job_id = job_ids[i - 1]
        conn.execute(
            """INSERT INTO applied (
                job_id, applied_at, notes, master_resume_hash,
                tailored_resume_hash, cover_letter, application_method,
                block_a_snapshot, block_c_snapshot, block_e_snapshot
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job_id,
                _ts(num_jobs - i - 2),
                None,
                resume_hashes[0],
                resume_hashes[1],
                COVER_LETTER_PLACEHOLDER,
                "direct",
                _make_verdict_json("apply"),
                _make_fit_json(80),
                _make_hooks_json(),
            ),
        )
    counts["applied"] = num_applied

    # ---- Companies ----
    company_slugs = ["acme-corp", "globex-inc", "initech-technologies"]
    for idx, slug in enumerate(company_slugs):
        conn.execute(
            """INSERT INTO companies (
                slug, ats_vendor, ats_override, last_verified_at,
                last_probe_attempt_at, job_count_last_scan, notes,
                consecutive_discover_failures
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                slug,
                "greenhouse" if idx == 0 else ("lever" if idx == 1 else None),
                0,
                _ts(5) if idx < 2 else None,
                _ts(3),
                random.randint(5, 50),
                None,
                0,
            ),
        )
    counts["companies"] = len(company_slugs)

    # ---- Cost ledger ----
    num_costs = 3
    for i in range(num_costs):
        day = (datetime(2026, 5, 1, tzinfo=UTC) - timedelta(days=i)).strftime(
            "%Y-%m-%d"
        )
        conn.execute(
            "INSERT INTO cost_ledger (day, spent_usd, calls, last_updated) VALUES (?, ?, ?, ?)",
            (day, round(random.uniform(0.10, 2.00), 4), random.randint(5, 30), _ts(i)),
        )
    counts["cost_ledger"] = num_costs

    # ---- State ----
    state_entries = [
        ("schema_version", "16"),
        ("last_resume_hash", resume_hashes[0]),
        ("last_run_id", "run-20260501-120000"),
        ("last_scan_at", _ts(0)),
        ("pipeline_version", "1.5.0"),
    ]
    for key, value in state_entries:
        conn.execute(
            "INSERT INTO state (key, value) VALUES (?, ?)",
            (key, value),
        )
    counts["state"] = len(state_entries)

    conn.commit()
    conn.close()

    return counts


def write_manifest(counts: dict[str, int], manifest_path: Path) -> None:
    """Write the manifest JSON with expected row counts."""
    manifest = {
        "schema_version": 16,
        "tables": {table: {"row_count": count} for table, count in counts.items()},
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    """Parse explicit synthetic fixture output paths."""
    parser = argparse.ArgumentParser(
        description="Generate a synthetic legacy v16 fixture for local testing."
    )
    parser.add_argument("--out-db", type=Path, required=True)
    parser.add_argument("--out-manifest", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    """Generate fixture and manifest."""
    args = _parse_args()
    args.out_db.parent.mkdir(parents=True, exist_ok=True)
    args.out_manifest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Generating synthetic legacy v16 fixture at {args.out_db} ...")
    counts = generate(args.out_db)
    write_manifest(counts, args.out_manifest)
    print("Row counts:")
    for table, count in sorted(counts.items()):
        print(f"  {table}: {count}")
    print(f"Manifest written to {args.out_manifest}")
    print("Done.")


if __name__ == "__main__":
    main()
