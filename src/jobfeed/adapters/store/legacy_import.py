"""Legacy SQLite v16 import orchestrator.

Reads a legacy v16 SQLite database, maps column names to the new schema,
and bulk-inserts into a target store via the BulkImportPort protocol.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypedDict

# ---- Row TypedDicts (wire format for migration) ----


class JobRow(TypedDict, total=False):
    """New-schema job row for bulk import."""

    id: int
    platform: str
    canonical_id: str
    url: str
    title: str
    company: str
    location: str
    jd_text: str | None
    jd_quality: str | None
    posted_at: str | None
    discovered_at: str
    enriched_at: str | None
    enrich_source: str | None
    company_norm: str | None
    title_norm: str | None
    location_norm: str | None
    jd_lang: str | None
    enrich_error: str | None
    quality_rubric_version: int | None
    reapply_notice: str | None
    hard_filter: str | None
    seniority_level: str | None
    degree_required: str | None
    clearance_required: int | None
    school_restricted: int | None
    domain_tags: str | None
    tech_required: str | None
    role_type: str | None
    yoe_min: int | None
    ml_gate_score: float | None
    ml_gate_result: str | None
    ml_gate_fail_reason: str | None
    ml_gate_at: str | None
    ml_gate_version: str | None
    is_swe_role: int | None


class EvaluationRow(TypedDict, total=False):
    """New-schema evaluation row for bulk import."""

    job_id: int
    stage_a_score: int | None
    stage_a_one_line: str | None
    stage_a_timing_eligible: str | None
    stage_a_status: str | None
    stage_a_error: str | None
    stage_a_model: str | None
    stage_a_cost_usd: float | None
    stage_a_prompt_hash: str | None
    stage_a_resume_hash: str | None
    stage_b_verdict: str | None
    stage_b_jd_summary: str | None
    stage_b_verdict_json: str | None
    stage_b_summary_json: str | None
    stage_b_fit_json: str | None
    stage_b_hooks_json: str | None
    stage_b_status: str | None
    stage_b_error: str | None
    stage_b_model: str | None
    stage_b_cost_usd: float | None
    stage_b_prompt_hash: str | None
    stage_b_resume_hash: str | None
    created_at: str
    updated_at: str


class JobStatusRow(TypedDict, total=False):
    """New-schema job_status row for bulk import."""

    job_id: int
    status: str
    next_followup_at: str | None
    resume_variant: str | None
    notes: str | None
    last_status_change_at: str


class StatusHistoryRow(TypedDict, total=False):
    """New-schema job_status_history row for bulk import."""

    id: int
    job_id: int
    from_status: str | None
    to_status: str
    changed_at: str
    reason: str | None
    resume_variant_at_change: str | None


class AppliedRow(TypedDict, total=False):
    """New-schema applied row for bulk import."""

    job_id: int
    applied_at: str
    notes: str | None
    master_resume_hash: str | None
    tailored_resume_hash: str | None
    cover_letter: str | None
    application_method: str | None
    verdict_snapshot: str | None
    fit_snapshot: str | None
    hooks_snapshot: str | None


class ResumeSnapshotRow(TypedDict, total=False):
    """New-schema resume_snapshots row for bulk import."""

    resume_hash: str
    captured_at: str
    source: str
    content: str
    notes: str | None


class ResumeVariantRow(TypedDict, total=False):
    """New-schema resume_variants row for bulk import."""

    name: str
    description: str | None
    created_at: str


class CompanyRow(TypedDict, total=False):
    """New-schema companies row for bulk import."""

    slug: str
    ats_vendor: str | None
    ats_override: int
    last_verified_at: str | None
    last_probe_attempt_at: str | None
    job_count_last_scan: int
    notes: str | None
    consecutive_discover_failures: int


class CostLedgerRow(TypedDict, total=False):
    """New-schema cost_ledger row for bulk import."""

    day: str
    spent_usd: float
    calls: int
    last_updated: str


class StateRow(TypedDict, total=False):
    """New-schema state row for bulk import."""

    key: str
    value: str


# ---- BulkImportPort Protocol ----


class BulkImportPort(Protocol):
    """Adapter-specific bulk load operations for legacy migration.

    Not part of JobStore -- these bypass business rules (triggers,
    auto-seed, quality ladder) intentionally for data migration.
    """

    async def bulk_insert_jobs(self, rows: list[JobRow]) -> int:
        """Insert job rows in bulk.

        Args:
            rows: Job rows to insert.

        Returns:
            Count of rows inserted.
        """
        ...

    async def bulk_insert_evaluations(self, rows: list[EvaluationRow]) -> int:
        """Insert evaluation rows in bulk.

        Args:
            rows: Evaluation rows to insert.

        Returns:
            Count of rows inserted.
        """
        ...

    async def bulk_insert_job_status(self, rows: list[JobStatusRow]) -> int:
        """Insert job_status rows in bulk.

        Args:
            rows: Job status rows to insert.

        Returns:
            Count of rows inserted.
        """
        ...

    async def bulk_insert_job_status_history(self, rows: list[StatusHistoryRow]) -> int:
        """Insert job_status_history rows in bulk.

        Args:
            rows: Status history rows to insert.

        Returns:
            Count of rows inserted.
        """
        ...

    async def bulk_insert_applied(self, rows: list[AppliedRow]) -> int:
        """Insert applied rows in bulk.

        Args:
            rows: Applied rows to insert.

        Returns:
            Count of rows inserted.
        """
        ...

    async def bulk_insert_resume_snapshots(self, rows: list[ResumeSnapshotRow]) -> int:
        """Insert resume_snapshots rows in bulk.

        Args:
            rows: Resume snapshot rows to insert.

        Returns:
            Count of rows inserted.
        """
        ...

    async def bulk_insert_resume_variants(self, rows: list[ResumeVariantRow]) -> int:
        """Insert resume_variants rows in bulk.

        Args:
            rows: Resume variant rows to insert.

        Returns:
            Count of rows inserted.
        """
        ...

    async def bulk_insert_companies(self, rows: list[CompanyRow]) -> int:
        """Insert companies rows in bulk.

        Args:
            rows: Company rows to insert.

        Returns:
            Count of rows inserted.
        """
        ...

    async def bulk_insert_cost_ledger(self, rows: list[CostLedgerRow]) -> int:
        """Insert cost_ledger rows in bulk.

        Args:
            rows: Cost ledger rows to insert.

        Returns:
            Count of rows inserted.
        """
        ...

    async def bulk_insert_state(self, rows: list[StateRow]) -> int:
        """Insert state rows in bulk.

        Args:
            rows: State rows to insert.

        Returns:
            Count of rows inserted.
        """
        ...

    async def reset_sequences(self) -> None:
        """Reset serial/sequence columns to MAX(id)+1. No-op for SQLite."""
        ...

    async def disable_triggers(self) -> None:
        """Disable auto-seed trigger during import."""
        ...

    async def enable_triggers(self) -> None:
        """Re-enable auto-seed trigger after import."""
        ...

    async def begin_import_transaction(self) -> None:
        """Begin an import transaction."""
        ...

    async def commit_import_transaction(self) -> None:
        """Commit the import transaction."""
        ...

    async def rollback_import_transaction(self) -> None:
        """Roll back the import transaction."""
        ...


# ---- ImportReport ----


@dataclass
class ImportReport:
    """Result of a legacy import operation."""

    tables_imported: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_s: float = 0.0


# ---- Column mapping functions ----


def _map_job_row(row: dict[str, Any]) -> JobRow:
    """Map a legacy v16 job row to new schema column names."""
    return JobRow(
        id=row["id"],
        platform=row["platform"],
        canonical_id=row["canonical_id"],
        url=row["url"],
        title=row["title"],
        company=row["company"],
        # COALESCE(location, '') -- legacy allows NULL, new schema NOT NULL
        location=row["location"] if row["location"] is not None else "",
        jd_text=row["jd_text"],
        # jd_text_quality -> jd_quality
        jd_quality=row["jd_text_quality"],
        posted_at=row["posted_at"],
        # scraped_at -> discovered_at (authoritative timestamp)
        discovered_at=row["scraped_at"],
        enriched_at=row["enriched_at"],
        enrich_source=row["enrich_source"],
        company_norm=row["company_norm"],
        title_norm=row["title_norm"],
        location_norm=row["location_norm"],
        jd_lang=row["jd_lang"],
        enrich_error=row["enrich_error"],
        quality_rubric_version=row["quality_rubric_version"],
        reapply_notice=row["reapply_notice"],
        hard_filter=row["hard_filter"],
        seniority_level=row["seniority_level"],
        degree_required=row["degree_required"],
        clearance_required=row["clearance_required"],
        school_restricted=row["school_restricted"],
        domain_tags=row["domain_tags"],
        tech_required=row["tech_required"],
        role_type=row["role_type"],
        yoe_min=row["yoe_min"],
        ml_gate_score=row["ml_gate_score"],
        ml_gate_result=row["ml_gate_result"],
        ml_gate_fail_reason=row["ml_gate_fail_reason"],
        ml_gate_at=row["ml_gate_at"],
        ml_gate_version=row["ml_gate_version"],
        is_swe_role=row["is_swe_role"],
    )


def _map_evaluation_row(row: dict[str, Any]) -> EvaluationRow:
    """Map a legacy v16 evaluation row to new schema column names.

    Legacy has job_id as PK (no surrogate id). The new schema's
    evaluations.id is auto-generated.

    Column renames:
    - timing_eligible -> stage_a_timing_eligible
    - resume_hash -> stage_a_resume_hash (+ stage_b_resume_hash if stage_b completed)
    - block_a_verdict -> stage_b_verdict_json
    - block_b_jd_summary -> stage_b_summary_json
    - block_c_fit_analysis -> stage_b_fit_json
    - block_e_resume_hooks -> stage_b_hooks_json
    - stage_b_blocks_run -> dropped
    """
    stage_b_done = row.get("stage_b_status") == "completed"

    return EvaluationRow(
        job_id=row["job_id"],
        stage_a_score=row["stage_a_score"],
        stage_a_one_line=row["stage_a_one_line"],
        stage_a_timing_eligible=row["timing_eligible"],
        stage_a_status=row["stage_a_status"],
        stage_a_error=row["stage_a_error"],
        stage_a_model=row["stage_a_model"],
        stage_a_cost_usd=row["stage_a_cost_usd"],
        stage_a_prompt_hash=row["stage_a_prompt_hash"],
        # resume_hash -> stage_a_resume_hash
        stage_a_resume_hash=row["resume_hash"],
        stage_b_verdict=row["stage_b_verdict"],
        stage_b_jd_summary=row["stage_b_jd_summary"],
        # block_a_verdict -> stage_b_verdict_json
        stage_b_verdict_json=row["block_a_verdict"],
        # block_b_jd_summary -> stage_b_summary_json
        stage_b_summary_json=row["block_b_jd_summary"],
        # block_c_fit_analysis -> stage_b_fit_json
        stage_b_fit_json=row["block_c_fit_analysis"],
        # block_e_resume_hooks -> stage_b_hooks_json
        stage_b_hooks_json=row["block_e_resume_hooks"],
        # stage_b_blocks_run is DROPPED
        stage_b_status=row["stage_b_status"],
        stage_b_error=row["stage_b_error"],
        stage_b_model=row["stage_b_model"],
        stage_b_cost_usd=row["stage_b_cost_usd"],
        stage_b_prompt_hash=row["stage_b_prompt_hash"],
        # stage_b_resume_hash: copy from resume_hash if stage_b done
        stage_b_resume_hash=(
            row["resume_hash"] if stage_b_done else row.get("stage_b_resume_hash")
        ),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _map_job_status_row(row: dict[str, Any]) -> JobStatusRow:
    """Map a legacy v16 job_status row (no renames needed)."""
    return JobStatusRow(
        job_id=row["job_id"],
        status=row["status"],
        next_followup_at=row["next_followup_at"],
        resume_variant=row["resume_variant"],
        notes=row["notes"],
        last_status_change_at=row["last_status_change_at"],
    )


def _map_status_history_row(row: dict[str, Any]) -> StatusHistoryRow:
    """Map a legacy v16 job_status_history row (no renames needed)."""
    return StatusHistoryRow(
        id=row["id"],
        job_id=row["job_id"],
        from_status=row["from_status"],
        to_status=row["to_status"],
        changed_at=row["changed_at"],
        reason=row["reason"],
        resume_variant_at_change=row["resume_variant_at_change"],
    )


def _map_applied_row(row: dict[str, Any]) -> AppliedRow:
    """Map a legacy v16 applied row to new schema column names.

    Column renames:
    - block_a_snapshot -> verdict_snapshot
    - block_c_snapshot -> fit_snapshot
    - block_e_snapshot -> hooks_snapshot
    """
    return AppliedRow(
        job_id=row["job_id"],
        applied_at=row["applied_at"],
        notes=row["notes"],
        master_resume_hash=row["master_resume_hash"],
        tailored_resume_hash=row["tailored_resume_hash"],
        cover_letter=row["cover_letter"],
        application_method=row["application_method"],
        verdict_snapshot=row["block_a_snapshot"],
        fit_snapshot=row["block_c_snapshot"],
        hooks_snapshot=row["block_e_snapshot"],
    )


def _map_resume_snapshot_row(row: dict[str, Any]) -> ResumeSnapshotRow:
    """Map a legacy v16 resume_snapshots row (no renames needed)."""
    return ResumeSnapshotRow(
        resume_hash=row["resume_hash"],
        captured_at=row["captured_at"],
        source=row["source"],
        content=row["content"],
        notes=row["notes"],
    )


def _map_resume_variant_row(row: dict[str, Any]) -> ResumeVariantRow:
    """Map a legacy v16 resume_variants row (no renames needed)."""
    return ResumeVariantRow(
        name=row["name"],
        description=row["description"],
        created_at=row["created_at"],
    )


def _map_company_row(row: dict[str, Any]) -> CompanyRow:
    """Map a legacy v16 companies row (no renames needed)."""
    return CompanyRow(
        slug=row["slug"],
        ats_vendor=row["ats_vendor"],
        ats_override=row["ats_override"],
        last_verified_at=row["last_verified_at"],
        last_probe_attempt_at=row["last_probe_attempt_at"],
        job_count_last_scan=row["job_count_last_scan"],
        notes=row["notes"],
        consecutive_discover_failures=row["consecutive_discover_failures"],
    )


def _map_cost_ledger_row(row: dict[str, Any]) -> CostLedgerRow:
    """Map a legacy v16 cost_ledger row (no renames needed)."""
    return CostLedgerRow(
        day=row["day"],
        spent_usd=row["spent_usd"],
        calls=row["calls"],
        last_updated=row["last_updated"],
    )


def _map_state_row(row: dict[str, Any]) -> StateRow:
    """Map a legacy v16 state row.

    Special handling: schema_version=16 -> legacy_schema_version=16.
    The new DB's own schema_version is managed separately.
    """
    key = row["key"]
    value = row["value"]
    if key == "schema_version":
        key = "legacy_schema_version"
    return StateRow(key=key, value=value)


def _read_legacy_table(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    """Read all rows from a legacy table as dicts."""
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(f"SELECT * FROM {table}")
    rows = cursor.fetchall()
    return [dict(row) for row in rows]


# ---- Import orchestrator ----


async def import_legacy_sqlite(
    legacy_path: Path, target: BulkImportPort
) -> ImportReport:
    """Import a legacy v16 SQLite database into a new-schema target store.

    Args:
        legacy_path: Path to the legacy v16 .db file.
        target: Store implementing BulkImportPort.

    Returns:
        ImportReport with per-table counts and any warnings/errors.

    Raises:
        FileNotFoundError: If legacy_path does not exist.
        ValueError: If legacy DB is not schema version 16.
        Exception: On import failure (target is rolled back).
    """
    if not legacy_path.exists():
        raise FileNotFoundError(f"Legacy database not found: {legacy_path}")

    report = ImportReport()
    start = time.monotonic()

    # Open legacy DB read-only
    legacy_conn = sqlite3.connect(f"file:{legacy_path}?mode=ro", uri=True)
    legacy_conn.row_factory = sqlite3.Row

    try:
        # Validate schema version
        cursor = legacy_conn.execute(
            "SELECT value FROM state WHERE key = 'schema_version'"
        )
        row = cursor.fetchone()
        if row is None or str(dict(row)["value"]) != "16":
            version = dict(row)["value"] if row else "unknown"
            raise ValueError(f"Expected legacy schema_version=16, got {version}")

        # Read and map all tables
        legacy_jobs = [_map_job_row(r) for r in _read_legacy_table(legacy_conn, "jobs")]
        legacy_evals = [
            _map_evaluation_row(r)
            for r in _read_legacy_table(legacy_conn, "evaluations")
        ]
        legacy_job_status = [
            _map_job_status_row(r)
            for r in _read_legacy_table(legacy_conn, "job_status")
        ]
        legacy_history = [
            _map_status_history_row(r)
            for r in _read_legacy_table(legacy_conn, "job_status_history")
        ]
        legacy_applied = [
            _map_applied_row(r) for r in _read_legacy_table(legacy_conn, "applied")
        ]
        legacy_snapshots = [
            _map_resume_snapshot_row(r)
            for r in _read_legacy_table(legacy_conn, "resume_snapshots")
        ]
        legacy_variants = [
            _map_resume_variant_row(r)
            for r in _read_legacy_table(legacy_conn, "resume_variants")
        ]
        legacy_companies = [
            _map_company_row(r) for r in _read_legacy_table(legacy_conn, "companies")
        ]
        legacy_costs = [
            _map_cost_ledger_row(r)
            for r in _read_legacy_table(legacy_conn, "cost_ledger")
        ]
        legacy_state = [
            _map_state_row(r) for r in _read_legacy_table(legacy_conn, "state")
        ]
    finally:
        legacy_conn.close()

    # Import into target with triggers disabled, in one transaction
    await target.disable_triggers()
    try:
        await target.begin_import_transaction()
        try:
            # Insert in FK order: resume_variants first (no FK deps)
            report.tables_imported[
                "resume_variants"
            ] = await target.bulk_insert_resume_variants(legacy_variants)
            report.tables_imported["jobs"] = await target.bulk_insert_jobs(legacy_jobs)
            report.tables_imported["job_status"] = await target.bulk_insert_job_status(
                legacy_job_status
            )
            report.tables_imported[
                "job_status_history"
            ] = await target.bulk_insert_job_status_history(legacy_history)
            report.tables_imported[
                "evaluations"
            ] = await target.bulk_insert_evaluations(legacy_evals)
            report.tables_imported["applied"] = await target.bulk_insert_applied(
                legacy_applied
            )
            report.tables_imported[
                "resume_snapshots"
            ] = await target.bulk_insert_resume_snapshots(legacy_snapshots)
            report.tables_imported["companies"] = await target.bulk_insert_companies(
                legacy_companies
            )
            report.tables_imported[
                "cost_ledger"
            ] = await target.bulk_insert_cost_ledger(legacy_costs)
            report.tables_imported["state"] = await target.bulk_insert_state(
                legacy_state
            )

            await target.reset_sequences()
            await target.commit_import_transaction()
        except Exception:
            await target.rollback_import_transaction()
            raise
    finally:
        await target.enable_triggers()

    report.duration_s = time.monotonic() - start
    return report
