"""Single-transaction PostgreSQL-0008 rollback writer."""

from __future__ import annotations

from collections.abc import Mapping

import asyncpg  # type: ignore[import-untyped]

from jobfeed.adapters.migration._baseline_evidence import _validate_manifest
from jobfeed.adapters.migration._baseline_evidence_shape import mapping
from jobfeed.adapters.migration._pg_rollback_metrics import _read_table_metrics
from jobfeed.adapters.migration._pg_rollback_replay import _replay_all
from jobfeed.adapters.migration._pg_rollback_schema import (
    _disable_trigger,
    _enable_trigger,
    _lock_target_tables,
    _require_quiescent_target,
    _reset_sequences,
    _trigger_is_enabled,
    _validate_target_schema,
)
from jobfeed.adapters.migration._pg_rollback_types import (
    CanonicalRollbackSource,
    CanonicalRollbackTableMetric,
    PostgresRollbackError,
    PostgresRollbackReport,
    RollbackFaultPoint,
    RollbackWriterConfig,
)
from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_SCHEMA_MANIFEST_V1,
)


async def replay_snapshot_to_postgres(
    source: CanonicalRollbackSource,
    *,
    cutover_manifest: object,
    config: RollbackWriterConfig,
) -> PostgresRollbackReport:
    """Reconcile a canonical snapshot into one quiescent PostgreSQL-0008 target.

    Args:
        source: Open consistent final canonical snapshot.
        cutover_manifest: Exact manifest captured before PostgreSQL cutover.
        config: Target DSN, bounded chunk size, and optional test fault.

    Returns:
        Committed table metrics, replay counts, and trigger-state proof.

    Raises:
        ValueError: Input manifest or chunk size is invalid.
        PostgresRollbackError: Target preflight or final parity fails.
        Exception: PostgreSQL and source failures propagate after rollback.

    Complexity:
        O(cutover target rows + final source rows).
    """
    if type(config.chunk_size) is not int or config.chunk_size <= 0:
        raise ValueError("rollback writer chunk_size must be a positive integer")
    manifest = mapping(cutover_manifest, "cutover manifest")
    _validate_manifest(manifest)
    expected_cutover = _metrics(manifest)
    connection = await asyncpg.connect(
        config.dsn, server_settings={"application_name": "jobfeed-rollback-writer"}
    )
    rollback_verification_box: list[bool] = []
    try:
        return await _run_transaction(
            connection,
            source,
            expected_cutover=expected_cutover,
            config=config,
            rollback_verification_box=rollback_verification_box,
        )
    except BaseException as error:
        if not rollback_verification_box:
            raise
        if not await _trigger_enabled_after_transaction(connection):
            raise PostgresRollbackError(
                "rollback target jobs seed trigger was not restored"
            ) from error
        raise error
    finally:
        await connection.close()


async def _run_transaction(
    connection: asyncpg.Connection,
    source: CanonicalRollbackSource,
    *,
    expected_cutover: dict[str, Mapping[str, object]],
    config: RollbackWriterConfig,
    rollback_verification_box: list[bool],
) -> PostgresRollbackReport:
    async with connection.transaction(isolation="serializable"):
        revision = await _validate_target_schema(connection)
        await _require_quiescent_target(connection)
        await _lock_target_tables(connection)
        await _require_quiescent_target(connection)
        trigger_was_enabled = await _trigger_is_enabled(connection)
        if not trigger_was_enabled:
            raise PostgresRollbackError(
                "rollback target jobs seed trigger must initially be enabled"
            )
        rollback_verification_box.append(True)
        actual_cutover = await _read_table_metrics(
            connection, chunk_size=config.chunk_size
        )
        target_was_empty = all(
            metric["row_count"] == 0 for metric in actual_cutover.values()
        )
        if not target_was_empty and actual_cutover != expected_cutover:
            raise PostgresRollbackError("rollback target cutover snapshot divergence")
        _inject(config, RollbackFaultPoint.PREFLIGHT)
        await _disable_trigger(connection)
        _inject(config, RollbackFaultPoint.AFTER_TRIGGER_DISABLE)

        async def inject(name: str) -> None:
            _inject(config, RollbackFaultPoint(name))

        replayed, deleted = await _replay_all(
            connection,
            source,
            chunk_size=config.chunk_size,
            fault_hook=inject,
        )
        await _reset_sequences(connection)
        _inject(config, RollbackFaultPoint.AFTER_SEQUENCE_RESET)
        final_metrics = await _read_table_metrics(
            connection, chunk_size=config.chunk_size
        )
        if final_metrics != _source_metrics(source.table_metrics):
            raise PostgresRollbackError("rollback final source parity mismatch")
        _inject(config, RollbackFaultPoint.BEFORE_TRIGGER_ENABLE)
        if config.fault_point is RollbackFaultPoint.TRIGGER_ENABLE:
            raise RuntimeError("injected rollback fault: trigger_enable")
        await _enable_trigger(connection)
        return PostgresRollbackReport(
            revision=revision,
            target_was_empty=target_was_empty,
            trigger_name="trg_jobs_seed_status",
            trigger_was_enabled=trigger_was_enabled,
            trigger_is_enabled=await _trigger_is_enabled(connection),
            pre_import_table_metrics=actual_cutover,
            replayed_rows=replayed,
            deleted_rows=deleted,
            final_table_metrics=final_metrics,
        )


async def _trigger_enabled_after_transaction(connection: asyncpg.Connection) -> bool:
    try:
        return await _trigger_is_enabled(connection)
    except BaseException:
        return False


def _inject(config: RollbackWriterConfig, point: RollbackFaultPoint) -> None:
    if config.fault_point is point:
        raise RuntimeError(f"injected rollback fault: {point.value}")


def _metrics(manifest: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    raw = mapping(manifest["tables"], "cutover manifest tables")
    return {name: mapping(value, f"cutover.{name}") for name, value in raw.items()}


def _source_metrics(
    metrics: tuple[CanonicalRollbackTableMetric, ...],
) -> dict[str, Mapping[str, object]]:
    tables = {table.name: table for table in CANONICAL_SCHEMA_MANIFEST_V1.tables}
    expected_names = tuple(tables)
    if tuple(metric.table_name for metric in metrics) != expected_names:
        raise PostgresRollbackError("rollback source table metric coverage mismatch")
    values: dict[str, Mapping[str, object]] = {}
    for metric in metrics:
        primary_key = tuple(tables[metric.table_name].primary_key)
        if metric.primary_key != primary_key:
            raise PostgresRollbackError(
                f"rollback source primary key mismatch: {metric.table_name}"
            )
        values[metric.table_name] = {
            "row_count": metric.row_count,
            "primary_key": list(primary_key),
            "max_identity": metric.max_identity,
            "canonical_sha256": metric.canonical_sha256,
        }
    return values


__all__ = [
    "CanonicalRollbackSource",
    "PostgresRollbackError",
    "PostgresRollbackReport",
    "RollbackFaultPoint",
    "RollbackWriterConfig",
    "replay_snapshot_to_postgres",
]
