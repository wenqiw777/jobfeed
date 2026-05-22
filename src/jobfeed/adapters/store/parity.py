"""Parity assertion harness for legacy import verification.

Verifies that all data from a legacy v16 SQLite database was imported
correctly into the new-schema target store with semantic equivalence.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

# ---- ParityReadPort Protocol ----


class ParityReadPort(Protocol):
    """Raw table read access for migration verification only."""

    async def read_all_rows(self, table: str) -> list[dict[str, Any]]:
        """Read all rows from a table as dicts.

        Args:
            table: Table name to read.

        Returns:
            List of row dicts.
        """
        ...

    async def count_rows(self, table: str) -> int:
        """Count rows in a table.

        Args:
            table: Table name to count.

        Returns:
            Row count.
        """
        ...


# ---- Report dataclasses ----


@dataclass
class ParityCheck:
    """Result of a single parity check."""

    name: str
    passed: bool
    details: str


@dataclass
class ParityReport:
    """Aggregate result of all parity checks."""

    passed: bool = True
    checks: list[ParityCheck] = field(default_factory=list)

    def add(self, check: ParityCheck) -> None:
        """Add a check result and update overall pass status.

        Args:
            check: Parity check result to record.
        """
        self.checks.append(check)
        if not check.passed:
            self.passed = False


# ---- Constants ----

_MAX_SCORE = 100

# ---- Status values ----

STATUS_VALUES = frozenset(
    {
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
        "oa",
        "hr_call",
        "second_round",
        "final_round",
    }
)

# ---- Canonicalization helpers ----

# Columns excluded from checksum comparison
_EXCLUDED_EVAL_COLS = frozenset(
    {
        "id",
        "updated_at",
        "stage_a_error_count",
        "stage_b_error_count",
        "stage_a_at",
        "stage_b_at",
    }
)

_JSON_COLUMNS = frozenset(
    {
        "stage_b_verdict_json",
        "stage_b_summary_json",
        "stage_b_fit_json",
        "stage_b_hooks_json",
        "verdict_snapshot",
        "fit_snapshot",
        "hooks_snapshot",
        "domain_tags",
        "tech_required",
    }
)

_TIMESTAMP_COLUMNS = frozenset(
    {
        "posted_at",
        "discovered_at",
        "enriched_at",
        "scraped_at",
        "created_at",
        "updated_at",
        "applied_at",
        "captured_at",
        "changed_at",
        "last_status_change_at",
        "next_followup_at",
        "last_verified_at",
        "last_probe_attempt_at",
        "last_updated",
        "ml_gate_at",
        "stage_a_at",
        "stage_b_at",
    }
)


def _canonicalize_value(key: str, value: object) -> str:
    """Canonicalize a value for checksum computation."""
    if value is None:
        return "<NULL>"

    if key in _JSON_COLUMNS:
        try:
            parsed = json.loads(str(value))
            return json.dumps(parsed, sort_keys=True)
        except (json.JSONDecodeError, TypeError):
            return str(value)

    if key in _TIMESTAMP_COLUMNS:
        try:
            # Try multiple formats
            for fmt in (
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S.%f+00",
                "%Y-%m-%d %H:%M:%S+00",
            ):
                try:
                    dt = datetime.strptime(str(value), fmt)
                    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                except ValueError:
                    continue
            # Fallback: return as-is
            return str(value)
        except Exception:
            return str(value)

    if isinstance(value, bool):
        return str(int(value))

    return str(value)


def _row_hash(row: dict[str, Any], exclude: frozenset[str] | None = None) -> str:
    """Compute a canonical hash of a row dict."""
    exclude = exclude or frozenset()
    parts = []
    for key in sorted(row.keys()):
        if key in exclude:
            continue
        parts.append(f"{key}={_canonicalize_value(key, row[key])}")
    content = "|".join(parts)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ---- Legacy column mapping for checksum comparison ----

# Maps legacy column names to new column names for comparison
_LEGACY_JOB_RENAMES = {
    "scraped_at": "discovered_at",
    "jd_text_quality": "jd_quality",
}
_LEGACY_JOB_DROP = {"discovered_at"}  # Legacy's own discovered_at is dropped

_LEGACY_EVAL_RENAMES = {
    "timing_eligible": "stage_a_timing_eligible",
    "resume_hash": "stage_a_resume_hash",
    "block_a_verdict": "stage_b_verdict_json",
    "block_b_jd_summary": "stage_b_summary_json",
    "block_c_fit_analysis": "stage_b_fit_json",
    "block_e_resume_hooks": "stage_b_hooks_json",
}
_LEGACY_EVAL_DROP = {"stage_b_blocks_run"}

_LEGACY_APPLIED_RENAMES = {
    "block_a_snapshot": "verdict_snapshot",
    "block_c_snapshot": "fit_snapshot",
    "block_e_snapshot": "hooks_snapshot",
}


def _remap_columns(
    row: dict[str, Any],
    renames: dict[str, str],
    drops: set[str] | None = None,
) -> dict:  # type: ignore[type-arg] #type: ignore[type-arg]
    """Apply column renames and drops to a row dict."""
    mapped = {}
    for k, v in row.items():
        if drops and k in drops:
            continue
        mapped[renames.get(k, k)] = v
    return mapped


def _map_legacy_row_for_comparison(
    row: dict[str, Any],
    table: str,
) -> dict:  # type: ignore[type-arg]
    """Remap legacy column names to new names for checksum comparison."""
    if table == "jobs":
        mapped = _remap_columns(row, _LEGACY_JOB_RENAMES, _LEGACY_JOB_DROP)
        if mapped.get("location") is None:
            mapped["location"] = ""
        return mapped

    if table == "evaluations":
        mapped = _remap_columns(row, _LEGACY_EVAL_RENAMES, _LEGACY_EVAL_DROP)
        stage_b_done = mapped.get("stage_b_status") == "completed"
        if stage_b_done and "stage_a_resume_hash" in mapped:
            mapped["stage_b_resume_hash"] = mapped["stage_a_resume_hash"]
        return mapped

    if table == "applied":
        return _remap_columns(row, _LEGACY_APPLIED_RENAMES)

    if table == "state":
        mapped = dict(row)
        if mapped.get("key") == "schema_version":
            mapped["key"] = "legacy_schema_version"
        return mapped

    return dict(row)


# ---- Parity check functions ----


def _read_legacy_table(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    """Read all rows from a legacy table as dicts."""
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(f"SELECT * FROM {table}")
    return [dict(r) for r in cursor.fetchall()]


async def _check_row_counts(
    _legacy_conn: sqlite3.Connection,
    target: ParityReadPort,
    manifest: dict[str, Any],
    report: ParityReport,
) -> None:
    """Check 1: Row count match per table."""
    tables = [
        "jobs",
        "evaluations",
        "job_status",
        "job_status_history",
        "applied",
        "resume_snapshots",
        "resume_variants",
        "companies",
        "cost_ledger",
        "state",
    ]
    mismatches = []
    for table in tables:
        expected = manifest["tables"].get(table, {}).get("row_count", 0)
        actual = await target.count_rows(table)
        if actual != expected:
            mismatches.append(f"{table}: expected={expected}, actual={actual}")

    report.add(
        ParityCheck(
            name="row_count_match",
            passed=len(mismatches) == 0,
            details=(
                "All table row counts match manifest"
                if not mismatches
                else f"Mismatches: {'; '.join(mismatches)}"
            ),
        )
    )


async def _check_fk_integrity(
    target: ParityReadPort,
    report: ParityReport,
) -> None:
    """Check 2: FK integrity -- referenced job_ids exist in jobs.

    Time complexity: O(T * R) where T is FK tables (4) and R is max rows per table.
    """
    jobs = await target.read_all_rows("jobs")
    job_ids = {row["id"] for row in jobs}

    fk_tables = ["evaluations", "job_status", "job_status_history", "applied"]
    violations = []
    for table in fk_tables:
        rows = await target.read_all_rows(table)
        for row in rows:
            if row["job_id"] not in job_ids:
                violations.append(f"{table}.job_id={row['job_id']}")

    report.add(
        ParityCheck(
            name="fk_integrity",
            passed=len(violations) == 0,
            details=(
                "All FK references resolve"
                if not violations
                else f"Broken FKs: {'; '.join(violations[:10])}"
            ),
        )
    )


async def _check_resume_hashes(
    target: ParityReadPort,
    report: ParityReport,
) -> None:
    """Check 3: Resume snapshot hash verification."""
    snapshots = await target.read_all_rows("resume_snapshots")
    mismatches = []
    for snap in snapshots:
        content = snap["content"]
        expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        actual_hash = snap["resume_hash"]
        if actual_hash != expected_hash:
            mismatches.append(
                f"hash={actual_hash[:16]}... expected={expected_hash[:16]}..."
            )

    report.add(
        ParityCheck(
            name="resume_hash_verification",
            passed=len(mismatches) == 0,
            details=(
                f"All {len(snapshots)} resume hashes verified"
                if not mismatches
                else f"Hash mismatches: {'; '.join(mismatches)}"
            ),
        )
    )


async def _check_status_enums(
    target: ParityReadPort,
    report: ParityReport,
) -> None:
    """Check 4: Status enum validation."""
    statuses = await target.read_all_rows("job_status")
    invalid = []
    for row in statuses:
        if row["status"] not in STATUS_VALUES:
            invalid.append(f"job_id={row['job_id']}, status={row['status']}")

    report.add(
        ParityCheck(
            name="status_enum_validation",
            passed=len(invalid) == 0,
            details=(
                f"All {len(statuses)} status values valid"
                if not invalid
                else f"Invalid statuses: {'; '.join(invalid)}"
            ),
        )
    )


async def _check_stage_b_json(
    target: ParityReadPort,
    report: ParityReport,
) -> None:
    """Check 5: Stage B JSON parseability."""
    evals = await target.read_all_rows("evaluations")
    errors = []
    for row in evals:
        fit_json = row.get("stage_b_fit_json")
        if fit_json is not None:
            try:
                parsed = json.loads(fit_json)
                required_keys = {"score_0_100", "strong_match", "gaps"}
                if not required_keys.issubset(parsed.keys()):
                    missing = required_keys - parsed.keys()
                    errors.append(f"job_id={row['job_id']}: missing keys {missing}")
            except json.JSONDecodeError as e:
                errors.append(f"job_id={row['job_id']}: invalid JSON: {e}")

    report.add(
        ParityCheck(
            name="stage_b_json_parseability",
            passed=len(errors) == 0,
            details=(
                "All Stage B fit JSON is parseable with expected keys"
                if not errors
                else f"JSON errors: {'; '.join(errors[:5])}"
            ),
        )
    )


async def _check_normalization(
    target: ParityReadPort,
    report: ParityReport,
) -> None:
    """Check 6: Normalization presence -- every job has company_norm and title_norm."""
    jobs = await target.read_all_rows("jobs")
    missing = []
    for row in jobs:
        if not row.get("company_norm"):
            missing.append(f"job_id={row['id']}: missing company_norm")
        if not row.get("title_norm"):
            missing.append(f"job_id={row['id']}: missing title_norm")

    report.add(
        ParityCheck(
            name="normalization_presence",
            passed=len(missing) == 0,
            details=(
                f"All {len(jobs)} jobs have company_norm and title_norm"
                if not missing
                else f"Missing normalization: {'; '.join(missing[:5])}"
            ),
        )
    )


async def _check_id_preservation(
    legacy_conn: sqlite3.Connection,
    target: ParityReadPort,
    report: ParityReport,
) -> None:
    """Check 7: ID preservation -- sample jobs exist with same ID."""
    legacy_jobs = _read_legacy_table(legacy_conn, "jobs")
    target_jobs = await target.read_all_rows("jobs")
    target_ids = {row["id"] for row in target_jobs}

    # Check all legacy job IDs are preserved
    missing = []
    for row in legacy_jobs:
        if row["id"] not in target_ids:
            missing.append(str(row["id"]))

    report.add(
        ParityCheck(
            name="id_preservation",
            passed=len(missing) == 0,
            details=(
                f"All {len(legacy_jobs)} legacy job IDs preserved"
                if not missing
                else f"Missing IDs: {', '.join(missing[:10])}"
            ),
        )
    )


async def _check_score_ranges(
    target: ParityReadPort,
    report: ParityReport,
) -> None:
    """Check 8: Evaluation score ranges -- all stage_a_score in 0-100."""
    evals = await target.read_all_rows("evaluations")
    out_of_range = []
    for row in evals:
        score = row.get("stage_a_score")
        if score is not None and (score < 0 or score > _MAX_SCORE):
            out_of_range.append(f"job_id={row['job_id']}: score={score}")

    report.add(
        ParityCheck(
            name="evaluation_score_ranges",
            passed=len(out_of_range) == 0,
            details=(
                f"All {len(evals)} evaluation scores in 0-100 range"
                if not out_of_range
                else f"Out of range: {'; '.join(out_of_range)}"
            ),
        )
    )


async def _check_canonical_checksums(
    legacy_conn: sqlite3.Connection,
    target: ParityReadPort,
    report: ParityReport,
) -> None:
    """Check 9: Canonical row checksum comparison.

    Time complexity: O(T * R * C) where T is tables (10), R is rows per
    table, and C is columns per row. Linear in total data volume.
    """
    tables_to_check = [
        ("jobs", None, "id"),
        ("evaluations", _EXCLUDED_EVAL_COLS, "job_id"),
        ("job_status", None, "job_id"),
        ("job_status_history", None, "id"),
        ("applied", None, "job_id"),
        ("resume_snapshots", None, "resume_hash"),
        ("resume_variants", None, "name"),
        ("companies", None, "slug"),
        ("cost_ledger", None, "day"),
        ("state", None, "key"),
    ]

    mismatches = []
    for table, exclude, pk_col in tables_to_check:
        legacy_rows = _read_legacy_table(legacy_conn, table)
        target_rows = await target.read_all_rows(table)

        # Map legacy rows to new column names for comparison
        legacy_mapped_rows = {}
        for r in legacy_rows:
            r_mapped = _map_legacy_row_for_comparison(r, table)
            if pk_col in r_mapped:
                legacy_mapped_rows[r_mapped[pk_col]] = r_mapped

        target_row_map = {}
        for row in target_rows:
            if pk_col in row:
                target_row_map[row[pk_col]] = row

        for pk_val, legacy_row in legacy_mapped_rows.items():
            target_row = target_row_map.get(pk_val)
            if target_row is None:
                mismatches.append(f"{table}[{pk_col}={pk_val}]: missing in target")
                continue
            # Only compare columns present in BOTH rows (minus exclusions)
            excl = exclude or frozenset()
            shared = frozenset(legacy_row) & frozenset(target_row)
            common_keys = shared - excl
            legacy_hash = _row_hash(
                {k: legacy_row[k] for k in common_keys}, frozenset()
            )
            target_hash = _row_hash(
                {k: target_row[k] for k in common_keys}, frozenset()
            )
            if legacy_hash != target_hash:
                mismatches.append(f"{table}[{pk_col}={pk_val}]: hash differs")

    report.add(
        ParityCheck(
            name="canonical_row_checksum",
            passed=len(mismatches) == 0,
            details=(
                "All canonical row checksums match across all tables"
                if not mismatches
                else (
                    f"Checksum mismatches ({len(mismatches)}): "
                    f"{'; '.join(mismatches[:10])}"
                )
            ),
        )
    )


# ---- Main entry point ----


async def verify_import_parity(
    legacy_path: Path,
    target: ParityReadPort,
    manifest: dict[str, Any],
) -> ParityReport:
    """Run all parity checks between a legacy v16 DB and the imported target.

    Args:
        legacy_path: Path to the legacy v16 .db file.
        target: Store implementing ParityReadPort.
        manifest: Parsed legacy_v16_manifest.json dict.

    Returns:
        ParityReport with all check results.
    """
    report = ParityReport()

    legacy_conn = sqlite3.connect(f"file:{legacy_path}?mode=ro", uri=True)
    legacy_conn.row_factory = sqlite3.Row

    try:
        await _check_row_counts(legacy_conn, target, manifest, report)
        await _check_fk_integrity(target, report)
        await _check_resume_hashes(target, report)
        await _check_status_enums(target, report)
        await _check_stage_b_json(target, report)
        await _check_normalization(target, report)
        await _check_id_preservation(legacy_conn, target, report)
        await _check_score_ranges(target, report)
        await _check_canonical_checksums(legacy_conn, target, report)
    finally:
        legacy_conn.close()

    return report
