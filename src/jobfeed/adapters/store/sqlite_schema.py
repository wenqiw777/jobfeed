"""Versioned, atomic SQLite schema creation for the runtime store adapter."""

from __future__ import annotations

import re
from typing import Final

import aiosqlite

from jobfeed.adapters.store._sqlite_schema_metadata import (
    SQLITE_METADATA,
    SQLITE_SCHEMA_VERSION,
    SQLITE_TABLE_NAMES,
    schema_ddl_statements,
)
from jobfeed.domain.external_identity import external_identity
from jobfeed.domain.ml_features import classify_role_type

_SEED_LEASE_SQL: Final = (
    "INSERT INTO run_leases(kind, generation) VALUES('scan', 0),('evaluate', 0)"
)
_DATA_REPAIR_STATE_KEY: Final = "sqlite_schema_data_repair_version"
_DATA_REPAIR_VERSION: Final = "2026-09-28-real-job-dates"
_CREATE_PREFIX = re.compile(r"^CREATE\s+(TABLE|INDEX|TRIGGER)\s+([^\s(]+)", re.I)
_ADDITIVE_TABLES = frozenset(
    {
        ("table", "job_priority_snapshot"),
        ("table", "real_jobs"),
        ("table", "real_job_identifiers"),
        ("table", "real_job_review_cases"),
        ("table", "real_job_status"),
        ("table", "real_job_status_history"),
        ("table", "real_job_interview_rounds"),
        ("table", "real_job_applications"),
        ("table", "real_job_evaluations"),
        ("table", "real_job_evaluation_history"),
    }
)
_ADDITIVE_INDEXES = frozenset(
    {
        ("index", "idx_eval_personal_ml"),
        ("index", "idx_jobs_personal_ml"),
        ("index", "idx_eval_verdict_job"),
        ("index", "idx_jobs_external_identity"),
        ("index", "idx_job_priority_snapshot_page"),
        ("index", "idx_jobs_real_job_id"),
        ("index", "idx_real_job_identifiers_parent"),
        ("index", "idx_real_job_status_status"),
        ("index", "idx_real_job_status_history_job"),
        ("index", "idx_real_job_interviews_job"),
        ("index", "idx_real_job_applications_parent"),
    }
)
_TOLERATED_EXTERNAL_OBJECTS = frozenset(
    {
        ("table", "application_queue"),
        ("index", "idx_application_queue_state"),
    }
)
_ADDITIVE_COLUMNS: Final[dict[tuple[str, str], str]] = {
    ("real_jobs", "official_closed_at"): "TEXT",
    ("real_jobs", "first_discovered_at"): "TEXT",
    ("real_jobs", "canonical_posted_at"): "TEXT",
    ("jobs", "real_job_id"): "INTEGER REFERENCES real_jobs(id)",
    ("jobs", "apply_url"): "TEXT",
    ("jobs", "is_repost"): "INTEGER",
    ("jobs", "repost_evidence"): "TEXT",
    ("jobs", "repost_observed_at"): "TEXT",
    ("jobs", "external_identity"): "TEXT",
    ("jobs", "enrich_attempted_at"): "TEXT",
    ("jobs", "enrich_error_code"): "TEXT",
    ("jobs", "enrich_retry_after"): "TEXT",
    (
        "pipeline_runs",
        "jobs_seniority_filtered",
    ): "INTEGER DEFAULT 0 NOT NULL",
    ("pipeline_runs", "failure_code"): "TEXT",
    ("pipeline_runs", "failure_message"): "TEXT",
    ("pipeline_runs", "failed_stage"): "TEXT",
    ("pipeline_runs", "failed_source"): "TEXT",
    ("pipeline_runs", "last_progress_at"): "TEXT",
    ("pipeline_runs", "restart_count"): "INTEGER DEFAULT 0 NOT NULL",
    ("pipeline_runs", "restarted_by_run_id"): "TEXT",
    ("pipeline_runs", "scan_stats_json"): "TEXT",
    ("pipeline_runs", "scan_progress_json"): "TEXT",
    ("pipeline_runs", "verdict_counts_json"): "TEXT",
    ("real_job_evaluation_history", "input_facts_json"): "TEXT",
}


async def ensure_sqlite_schema(connection: aiosqlite.Connection) -> None:
    """Create or validate the exact SQLite schema version supported by Jobfeed.

    Args:
        connection: Open aiosqlite connection with no active transaction.

    Raises:
        RuntimeError: If called inside an existing transaction.
        ValueError: If the version is unknown or the live schema is inconsistent.
        aiosqlite.Error: If transactional schema creation fails.
    """
    if connection.in_transaction:
        raise RuntimeError("SQLite schema migration requires no active transaction")
    version = await _schema_version(connection)
    if version == 0:
        await _migrate_zero_to_current(connection)
        return
    if version == SQLITE_SCHEMA_VERSION:
        await _repair_current_data(connection)
        return
    raise ValueError(f"unsupported SQLite schema version: {version}")


async def migrate_real_jobs_schema(connection: aiosqlite.Connection) -> None:
    """Explicitly install and reconcile real-job ownership on a disposable DB.

    Existing v1 databases never invoke this from normal startup. Callers must
    arrange backup and activation outside the application process.

    Args:
        connection: Open SQLite connection to the backed-up migration target.

    Raises:
        RuntimeError: If the connection already has an active transaction.
    """
    if connection.in_transaction:
        raise RuntimeError("real-job migration requires no active transaction")
    await ensure_sqlite_schema(connection)
    await connection.execute("BEGIN IMMEDIATE")
    try:
        if not await _identity_schema_present(connection):
            statements = {
                (match.group(1).lower(), match.group(2).strip('"`[]')): statement
                for statement in schema_ddl_statements()
                if (match := _CREATE_PREFIX.match(statement.lstrip())) is not None
            }
            for name in (
                "real_jobs",
                "real_job_identifiers",
                "real_job_review_cases",
                "real_job_status",
                "real_job_status_history",
                "real_job_interview_rounds",
                "real_job_applications",
                "real_job_evaluations",
                "real_job_evaluation_history",
            ):
                await _execute_schema_statement(connection, statements[("table", name)])
            await _execute_schema_statement(
                connection,
                "ALTER TABLE jobs ADD COLUMN real_job_id "
                "INTEGER REFERENCES real_jobs(id)",
            )
            await _execute_schema_statement(
                connection, "ALTER TABLE jobs ADD COLUMN apply_url TEXT"
            )
            await _execute_schema_statement(
                connection, statements[("index", "idx_jobs_real_job_id")]
            )
            for name in (
                "idx_real_job_identifiers_parent",
                "idx_real_job_status_status",
                "idx_real_job_status_history_job",
                "idx_real_job_interviews_job",
                "idx_real_job_applications_parent",
            ):
                await _execute_schema_statement(connection, statements[("index", name)])
        if await _identity_schema_present(connection):
            cursor = await connection.execute("PRAGMA table_info(real_jobs)")
            parent_columns = {row[1] for row in await cursor.fetchall()}
            await cursor.close()
            if "official_closed_at" not in parent_columns:
                await connection.execute(
                    "ALTER TABLE real_jobs ADD COLUMN official_closed_at TEXT"
                )
            cursor = await connection.execute(
                "PRAGMA table_info(real_job_evaluation_history)"
            )
            columns = {row[1] for row in await cursor.fetchall()}
            await cursor.close()
            if "input_facts_json" not in columns:
                await connection.execute(
                    "ALTER TABLE real_job_evaluation_history "
                    "ADD COLUMN input_facts_json TEXT"
                )
        await _validate_v1(connection, identity_enabled=True)
        await _backfill_real_jobs(connection)
        await _backfill_real_job_dates(connection)
        await _backfill_real_job_workflow(connection)
        await connection.commit()
    except BaseException:
        await connection.rollback()
        raise


async def _identity_schema_present(connection: aiosqlite.Connection) -> bool:
    table = await _scalar(
        connection,
        "SELECT COUNT(*) FROM sqlite_schema WHERE type='table' AND name='real_jobs'",
    )
    column = await _scalar(
        connection,
        "SELECT COUNT(*) FROM pragma_table_info('jobs') WHERE name='real_job_id'",
    )
    if bool(table) != bool(column):
        raise ValueError("partial real-job schema on SQLite database")
    return bool(table)


async def _migrate_zero_to_current(connection: aiosqlite.Connection) -> None:
    await connection.execute("BEGIN IMMEDIATE")
    try:
        version = await _schema_version(connection)
        if version == SQLITE_SCHEMA_VERSION:
            await _validate_v1(connection)
            await connection.commit()
            return
        if version != 0:
            raise ValueError(f"unsupported SQLite schema version: {version}")
        if await _user_objects(connection):
            raise ValueError("SQLite version 0 database is not empty")
        for statement in schema_ddl_statements():
            await _execute_schema_statement(connection, statement)
        await connection.execute(_SEED_LEASE_SQL)
        await _backfill_missing_role_types(connection)
        await _mark_data_repair_complete(connection)
        await connection.execute(f"PRAGMA user_version={SQLITE_SCHEMA_VERSION}")
        await _validate_v1(connection)
        await connection.commit()
    except BaseException:
        await connection.rollback()
        raise


async def _repair_current_data(connection: aiosqlite.Connection) -> None:
    """Idempotently repair invariants that predate the current schema contract."""
    await connection.execute("BEGIN IMMEDIATE")
    try:
        version = await _schema_version(connection)
        if version != SQLITE_SCHEMA_VERSION:
            raise ValueError(f"unsupported SQLite schema version: {version}")
        identity_enabled = await _identity_schema_present(connection)
        await _install_additive_columns(connection, identity_enabled=identity_enabled)
        await _install_additive_tables(connection, identity_enabled=identity_enabled)
        await _install_additive_indexes(connection, identity_enabled=identity_enabled)
        await _validate_v1(connection, identity_enabled=identity_enabled)
        if not await _data_repair_is_current(connection):
            await _repair_legacy_data(connection, identity_enabled=identity_enabled)
            await _mark_data_repair_complete(connection)
        await _validate_v1(connection, identity_enabled=identity_enabled)
        await connection.commit()
    except BaseException:
        await connection.rollback()
        raise


async def _repair_legacy_data(
    connection: aiosqlite.Connection, *, identity_enabled: bool
) -> None:
    """Run the bounded, versioned data repair once for an existing database."""
    await _backfill_external_identities(connection)
    if identity_enabled:
        await _backfill_real_job_dates(connection)
    await _execute_data_migration_statement(
        connection,
        """UPDATE evaluations
               SET stage_a_at=created_at
               WHERE stage_a_status='completed' AND stage_a_at IS NULL""",
    )
    await _execute_data_migration_statement(
        connection,
        """UPDATE evaluations
               SET stage_b_at=updated_at
               WHERE stage_b_status='completed' AND stage_b_at IS NULL""",
    )
    await _execute_data_migration_statement(
        connection,
        """INSERT INTO job_status_history(
                   job_id, from_status, to_status, changed_at, reason
               )
               SELECT s.job_id, 'new', 'scored',
                      COALESCE(e.stage_a_at, e.created_at), 'schema_data_repair'
               FROM job_status s JOIN evaluations e ON e.job_id=s.job_id
               WHERE s.status='new' AND e.stage_a_status='completed'""",
    )
    await _execute_data_migration_statement(
        connection,
        """UPDATE job_status
               SET status='scored',
                   last_status_change_at=(
                     SELECT COALESCE(e.stage_a_at, e.created_at)
                     FROM evaluations e WHERE e.job_id=job_status.job_id
                   )
               WHERE status='new' AND EXISTS (
                 SELECT 1 FROM evaluations e
                 WHERE e.job_id=job_status.job_id
                   AND e.stage_a_status='completed'
               )""",
    )
    await _execute_data_migration_statement(
        connection,
        """INSERT INTO job_status_history(
                   job_id, from_status, to_status, changed_at, reason
               )
               SELECT s.job_id, 'scored', 'new', s.last_status_change_at,
                      'schema_data_repair'
               FROM job_status s LEFT JOIN evaluations e ON e.job_id=s.job_id
               WHERE s.status='scored' AND e.job_id IS NULL""",
    )
    await _execute_data_migration_statement(
        connection,
        """UPDATE job_status
               SET status='new'
               WHERE status='scored' AND NOT EXISTS (
                 SELECT 1 FROM evaluations e WHERE e.job_id=job_status.job_id
               )""",
    )
    await _backfill_missing_role_types(connection)


async def _data_repair_is_current(connection: aiosqlite.Connection) -> bool:
    cursor = await connection.execute(
        "SELECT value FROM state WHERE key=?", (_DATA_REPAIR_STATE_KEY,)
    )
    row = await cursor.fetchone()
    await cursor.close()
    return row is not None and row[0] == _DATA_REPAIR_VERSION


async def _mark_data_repair_complete(connection: aiosqlite.Connection) -> None:
    await connection.execute(
        "INSERT INTO state(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (_DATA_REPAIR_STATE_KEY, _DATA_REPAIR_VERSION),
    )


async def _install_additive_indexes(
    connection: aiosqlite.Connection, *, identity_enabled: bool
) -> None:
    """Install known index-only additions when the prior schema is otherwise exact."""
    expected = _expected_schema_objects(identity_enabled=identity_enabled)
    live = await _live_schema_objects(connection, identity_enabled=identity_enabled)
    missing = set(expected) - set(live)
    unexpected = set(live) - set(expected) - _TOLERATED_EXTERNAL_OBJECTS
    allowed = _ADDITIVE_INDEXES
    if not identity_enabled:
        allowed = allowed - {
            ("index", "idx_jobs_real_job_id"),
            ("index", "idx_real_job_identifiers_parent"),
            ("index", "idx_real_job_status_status"),
            ("index", "idx_real_job_status_history_job"),
            ("index", "idx_real_job_interviews_job"),
            ("index", "idx_real_job_applications_parent"),
        }
    if not missing.issubset(allowed) or unexpected:
        return
    if any(live[key] != expected[key] for key in set(live) & set(expected)):
        return
    statements = {
        (match.group(1).lower(), match.group(2).strip('"`[]')): statement
        for statement in schema_ddl_statements()
        if (match := _CREATE_PREFIX.match(statement.lstrip())) is not None
    }
    for key in sorted(allowed):
        if key in missing:
            await _execute_schema_statement(connection, statements[key])


async def _install_additive_tables(
    connection: aiosqlite.Connection, *, identity_enabled: bool
) -> None:
    """Install the disposable projection table on an otherwise current v1 DB."""
    expected = _expected_schema_objects(identity_enabled=identity_enabled)
    live = await _live_schema_objects(connection, identity_enabled=identity_enabled)
    missing = set(expected) - set(live)
    allowed_tables = _ADDITIVE_TABLES
    if not identity_enabled:
        allowed_tables = frozenset({("table", "job_priority_snapshot")})
    allowed_missing = allowed_tables | _ADDITIVE_INDEXES
    unexpected = set(live) - set(expected) - _TOLERATED_EXTERNAL_OBJECTS
    if not missing.issubset(allowed_missing) or unexpected:
        return
    statements = {
        (match.group(1).lower(), match.group(2).strip('"`[]')): statement
        for statement in schema_ddl_statements()
        if (match := _CREATE_PREFIX.match(statement.lstrip())) is not None
    }
    for key in sorted(allowed_tables):
        if key in missing:
            await _execute_schema_statement(connection, statements[key])


async def _install_additive_columns(
    connection: aiosqlite.Connection, *, identity_enabled: bool
) -> None:
    """Add known backward-compatible columns before exact schema validation."""
    for (table, column), definition in _ADDITIVE_COLUMNS.items():
        if not identity_enabled and (
            table == "real_jobs"
            or (table, column)
            in {
                ("jobs", "real_job_id"),
                ("jobs", "apply_url"),
            }
        ):
            continue
        cursor = await connection.execute(f'PRAGMA table_info("{table}")')
        rows = list(await cursor.fetchall())
        await cursor.close()
        if not rows:
            continue
        if any(str(row[1]) == column for row in rows):
            continue
        await _execute_schema_statement(
            connection,
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}",
        )


async def _schema_version(connection: aiosqlite.Connection) -> int:
    version = await _scalar(connection, "PRAGMA user_version")
    if type(version) is not int:
        raise ValueError("SQLite user_version is not an integer")
    return version


async def _execute_schema_statement(connection: aiosqlite.Connection, sql: str) -> None:
    await connection.execute(sql)


async def _execute_data_migration_statement(
    connection: aiosqlite.Connection, sql: str
) -> None:
    await connection.execute(sql)


async def _backfill_missing_role_types(connection: aiosqlite.Connection) -> None:
    """Repair legacy NULL classifications that depended on the old ML gate."""
    cursor = await connection.execute(
        "SELECT id,title,COALESCE(jd_text,'') FROM jobs WHERE role_type IS NULL"
    )
    rows = list(await cursor.fetchall())
    await cursor.close()
    if not rows:
        return
    await connection.executemany(
        "UPDATE jobs SET role_type=? WHERE id=?",
        [
            (classify_role_type(str(title), str(jd_text)), int(job_id))
            for job_id, title, jd_text in rows
        ],
    )


async def _backfill_external_identities(connection: aiosqlite.Connection) -> None:
    """Make existing URL aliases reusable before the first incremental scan."""
    cursor = await connection.execute(
        "SELECT id,url FROM jobs WHERE external_identity IS NULL"
    )
    rows = list(await cursor.fetchall())
    await cursor.close()
    updates = [
        (identity, row[0]) for row in rows if (identity := external_identity(row[1]))
    ]
    if updates:
        await connection.executemany(
            "UPDATE jobs SET external_identity=? WHERE id=?", updates
        )
    for platform in ("jobright", "handshake"):
        await connection.execute(
            "UPDATE jobs AS alias SET external_identity="
            "(SELECT official.external_identity "
            "FROM jobs official WHERE official.platform=? "
            "AND official.canonical_id=substr(alias.external_identity,?) "
            "AND official.external_identity NOT LIKE ? LIMIT 1) "
            "WHERE alias.external_identity LIKE ? "
            "AND EXISTS (SELECT 1 FROM jobs official WHERE official.platform=? "
            "AND official.canonical_id=substr(alias.external_identity,?) "
            "AND official.external_identity NOT LIKE ?)",
            (
                platform,
                len(platform) + 2,
                platform + ":%",
                platform + ":%",
                platform,
                len(platform) + 2,
                platform + ":%",
            ),
        )


async def _backfill_real_jobs(connection: aiosqlite.Connection) -> None:
    """Assign every preexisting source a singleton parent without merging evidence."""
    await connection.execute(
        "UPDATE jobs SET real_job_id=(SELECT id FROM real_jobs "
        "WHERE representative_job_id=jobs.id) WHERE real_job_id IS NULL "
        "AND EXISTS(SELECT 1 FROM real_jobs WHERE representative_job_id=jobs.id)"
    )
    await connection.execute(
        "INSERT INTO real_jobs(representative_job_id) "
        "SELECT id FROM jobs WHERE real_job_id IS NULL"
    )
    await connection.execute(
        "UPDATE jobs SET real_job_id=(SELECT id FROM real_jobs "
        "WHERE representative_job_id=jobs.id) WHERE real_job_id IS NULL"
    )
    await connection.execute(
        "INSERT OR IGNORE INTO real_job_identifiers("
        "real_job_id,provider,scope,native_id,evidence_job_id,observed_url) "
        "SELECT real_job_id,platform,'',canonical_id,id,url FROM jobs "
        "WHERE real_job_id IS NOT NULL"
    )


async def _backfill_real_job_dates(connection: aiosqlite.Connection) -> None:
    """Materialize source-date aggregates once for canonical list reads."""
    await connection.execute(
        "WITH dates AS ("
        "SELECT real_job_id,MIN(discovered_at) AS first_discovered_at,"
        "MIN(CASE WHEN posted_at IS NOT NULL AND COALESCE(is_repost,0)=0 "
        "THEN posted_at END) AS original_posted_at FROM jobs "
        "WHERE real_job_id IS NOT NULL GROUP BY real_job_id) "
        "UPDATE real_jobs SET first_discovered_at=("
        "SELECT first_discovered_at FROM dates WHERE dates.real_job_id=real_jobs.id),"
        "canonical_posted_at=COALESCE(CASE WHEN ("
        "SELECT original_posted_at FROM dates WHERE dates.real_job_id=real_jobs.id)"
        "<=(SELECT first_discovered_at FROM dates "
        "WHERE dates.real_job_id=real_jobs.id) THEN ("
        "SELECT original_posted_at FROM dates WHERE dates.real_job_id=real_jobs.id) "
        "END,(SELECT first_discovered_at FROM dates "
        "WHERE dates.real_job_id=real_jobs.id))"
    )


async def _backfill_real_job_workflow(connection: aiosqlite.Connection) -> None:
    """Copy one unambiguous source decision per parent; never alter source audit."""
    cursor = await connection.execute(
        "SELECT r.id FROM real_jobs r LEFT JOIN real_job_status s "
        "ON s.real_job_id=r.id WHERE s.real_job_id IS NULL "
        "OR s.status IN ('new','scored') ORDER BY r.id"
    )
    parents = await cursor.fetchall()
    await cursor.close()
    neutral = {"new", "scored"}
    for (real_job_id,) in parents:
        cursor = await connection.execute(
            "SELECT j.id,s.status,s.next_followup_at,s.resume_variant,s.notes,"
            "s.last_status_change_at FROM jobs j JOIN job_status s ON s.job_id=j.id "
            "WHERE j.real_job_id=? ORDER BY s.last_status_change_at DESC,j.id DESC",
            (real_job_id,),
        )
        rows = list(await cursor.fetchall())
        await cursor.close()
        explicit = {str(row[1]) for row in rows if str(row[1]) not in neutral}
        if len(explicit) > 1:
            await connection.execute(
                "UPDATE real_jobs SET identity_review_state='status_conflict' "
                "WHERE id=?",
                (real_job_id,),
            )
            continue
        if not rows:
            continue
        chosen = next((row for row in rows if row[1] in explicit), rows[0])
        source_id = int(chosen[0])
        await connection.execute(
            "INSERT OR IGNORE INTO real_job_status("
            "real_job_id,status,next_followup_at,resume_variant,notes,"
            "last_status_change_at) "
            "VALUES(?,?,?,?,?,?)",
            (real_job_id, *chosen[1:]),
        )
        if explicit:
            await connection.execute(
                "UPDATE real_job_status SET status=?,next_followup_at=?,"
                "resume_variant=?,notes=?,last_status_change_at=? "
                "WHERE real_job_id=? AND status IN ('new','scored')",
                (*chosen[1:], real_job_id),
            )
        await connection.execute(
            "INSERT OR IGNORE INTO real_job_status_history("
            "real_job_id,source_history_id,from_status,to_status,changed_at,reason,"
            "resume_variant_at_change) SELECT ?,id,from_status,to_status,changed_at,"
            "reason,resume_variant_at_change FROM job_status_history WHERE job_id=? "
            "ORDER BY id",
            (real_job_id, source_id),
        )
        await connection.execute(
            "INSERT OR IGNORE INTO real_job_interview_rounds("
            "real_job_id,source_round_id,round_index,label,scheduled_at,"
            "completed_at,notes,created_at) SELECT ?,id,round_index,label,"
            "scheduled_at,completed_at,notes,created_at FROM interview_rounds "
            "WHERE job_id=? ORDER BY round_index",
            (real_job_id, source_id),
        )
    await connection.execute(
        "INSERT OR IGNORE INTO real_job_applications("
        "real_job_id,source_job_id,source_applied_job_id,apply_url,applied_at,"
        "notes,cover_letter,"
        "application_method,verdict_snapshot,fit_snapshot,hooks_snapshot) "
        "SELECT j.real_job_id,a.job_id,a.job_id,NULL,a.applied_at,a.notes,"
        "a.cover_letter,"
        "a.application_method,a.verdict_snapshot,a.fit_snapshot,a.hooks_snapshot "
        "FROM applied a JOIN jobs j ON j.id=a.job_id "
        "WHERE j.real_job_id IS NOT NULL"
    )


async def _validate_v1(
    connection: aiosqlite.Connection, *, identity_enabled: bool = True
) -> None:
    expected = _expected_schema_objects(identity_enabled=identity_enabled)
    live = await _live_schema_objects(connection, identity_enabled=identity_enabled)
    unexpected = set(live) - set(expected) - _TOLERATED_EXTERNAL_OBJECTS
    if (
        set(expected) - set(live)
        or unexpected
        or any(live[name] != expected[name] for name in set(live) & set(expected))
    ):
        missing = sorted(set(expected) - set(live))
        extra = sorted(unexpected)
        changed = sorted(
            name for name in set(live) & set(expected) if live[name] != expected[name]
        )
        raise ValueError(
            "SQLite schema v1 is inconsistent: "
            f"missing={missing}, extra={extra}, changed={changed}"
        )
    cursor = await connection.execute(
        "SELECT kind, generation, owner_id, run_id, heartbeat_at, expires_at "
        "FROM run_leases ORDER BY kind"
    )
    leases = await cursor.fetchall()
    await cursor.close()
    if [row[0] for row in leases] != ["evaluate", "scan"]:
        raise ValueError("SQLite schema v1 is inconsistent: run lease rows differ")


def _expected_schema_objects(
    *, identity_enabled: bool = True
) -> dict[tuple[str, str], str]:
    objects: dict[tuple[str, str], str] = {}
    for statement in schema_ddl_statements():
        match = _CREATE_PREFIX.match(statement.lstrip())
        if match is None:
            raise RuntimeError("generated SQLite DDL has an unknown object")
        kind, raw_name = match.groups()
        name = raw_name.strip('"`[]')
        if not identity_enabled and name in {
            "real_jobs",
            "real_job_identifiers",
            "real_job_review_cases",
            "real_job_status",
            "real_job_status_history",
            "real_job_interview_rounds",
            "real_job_applications",
            "real_job_evaluations",
            "real_job_evaluation_history",
            "idx_jobs_real_job_id",
            "idx_real_job_identifiers_parent",
            "idx_real_job_status_status",
            "idx_real_job_status_history_job",
            "idx_real_job_interviews_job",
            "idx_real_job_applications_parent",
        }:
            continue
        objects[(kind.lower(), name)] = _normalize_sql(
            statement, identity_enabled=identity_enabled
        )
    return objects


async def _live_schema_objects(
    connection: aiosqlite.Connection,
    *,
    identity_enabled: bool = True,
) -> dict[tuple[str, str], str]:
    cursor = await connection.execute(
        "SELECT type, name, sql FROM sqlite_schema "
        "WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL"
    )
    rows = list(await cursor.fetchall())
    await cursor.close()
    return {
        (str(kind), str(name)): _normalize_sql(
            str(sql), identity_enabled=identity_enabled
        )
        for kind, name, sql in rows
    }


async def _user_objects(connection: aiosqlite.Connection) -> tuple[str, ...]:
    cursor = await connection.execute(
        "SELECT name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
    )
    rows = list(await cursor.fetchall())
    await cursor.close()
    return tuple(str(row[0]) for row in rows)


async def _scalar(connection: aiosqlite.Connection, sql: str) -> object:
    cursor = await connection.execute(sql)
    row = await cursor.fetchone()
    await cursor.close()
    if row is None:
        raise ValueError("SQLite schema query returned no row")
    return row[0]


def _normalize_sql(sql: str, *, identity_enabled: bool = True) -> str:
    normalized = " ".join(sql.split()).rstrip(";")
    if not identity_enabled and normalized.startswith("CREATE TABLE jobs ("):
        normalized = re.sub(
            r", FOREIGN KEY\(real_job_id\) REFERENCES real_jobs \(id\)",
            "",
            normalized,
        )
        normalized = re.sub(
            r", real_job_id INTEGER(?: REFERENCES real_jobs\(id\))?"
            r"(?=\s*[,\)])",
            "",
            normalized,
        )
        normalized = re.sub(r", apply_url TEXT(?=\s*[,\)])", "", normalized)
    if normalized.startswith(
        (
            "CREATE TABLE jobs (",
            "CREATE TABLE pipeline_runs (",
            "CREATE TABLE real_jobs (",
        )
    ):
        # ALTER TABLE appends nullable metadata after whichever columns were
        # present at that deployment. Compare known additive definitions in a
        # stable order while retaining strict types, constraints and base DDL.
        additive = []
        for (table, column), definition in sorted(_ADDITIVE_COLUMNS.items()):
            if not identity_enabled and (table, column) in {
                ("jobs", "real_job_id"),
                ("jobs", "apply_url"),
            }:
                continue
            if not normalized.startswith(f"CREATE TABLE {table} ("):
                continue
            if (table, column) == ("jobs", "real_job_id"):
                normalized = re.sub(
                    r", FOREIGN KEY\(real_job_id\) REFERENCES real_jobs \(id\)",
                    "",
                    normalized,
                )
                pattern = (
                    r", real_job_id INTEGER(?: REFERENCES real_jobs\(id\))?"
                    r"(?=\s*[,\)])"
                )
            else:
                pattern = rf", {re.escape(column)} {re.escape(definition)}(?=\s*[,\)])"
            normalized, count = re.subn(pattern, "", normalized)
            additive.extend([f"{column} {definition}"] * count)
        if additive:
            normalized += " ADDITIVE " + ", ".join(additive)
    return normalized


__all__ = [
    "SQLITE_METADATA",
    "SQLITE_SCHEMA_VERSION",
    "SQLITE_TABLE_NAMES",
    "ensure_sqlite_schema",
    "migrate_real_jobs_schema",
]
