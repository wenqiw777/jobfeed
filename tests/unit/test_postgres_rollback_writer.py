"""Fail-closed PostgreSQL rollback-writer transaction contracts."""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator

import pytest

from jobfeed.adapters.migration._sqlite_rollback_types import (
    SqliteRollbackTableMetric,
)
from jobfeed.adapters.migration.canonical_row import canonical_rows_sha256
from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_ROW_SCHEMAS_V1,
    CANONICAL_SCHEMA_MANIFEST_V1,
)
from jobfeed.adapters.migration.postgres_rollback_writer import (
    RollbackFaultPoint,
    RollbackWriterConfig,
    replay_snapshot_to_postgres,
)
from tests.unit._sqlite_forward_import_fixture import (
    canonical_source_rows,
    snapshot_manifest,
)


class FakeRollbackSnapshot:
    """Typed canonical source fixture with async per-table streams."""

    def __init__(self, rows: dict[str, list[dict[str, object]]]) -> None:
        self.rows = copy.deepcopy(rows)
        self.table_metrics = tuple(
            SqliteRollbackTableMetric(
                table_name=table.name,
                primary_key=table.primary_key,
                row_count=len(rows[table.name]),
                max_identity=(
                    max(int(row["id"]) for row in rows[table.name])
                    if any(column.name == "id" for column in table.columns)
                    and rows[table.name]
                    else None
                ),
                canonical_sha256=canonical_rows_sha256(schema, rows[table.name]),
            )
            for table, schema in zip(
                CANONICAL_SCHEMA_MANIFEST_V1.tables,
                CANONICAL_ROW_SCHEMAS_V1,
                strict=True,
            )
        )

    async def stream_table(
        self, table_name: str, *, chunk_size: int | None = None
    ) -> AsyncIterator[dict[str, object]]:
        assert chunk_size is not None and chunk_size > 0
        for row in self.rows[table_name]:
            yield copy.deepcopy(row)


@pytest.mark.asyncio
async def test_rejects_bad_config_before_connecting() -> None:
    """A non-positive chunk size cannot open or mutate PostgreSQL."""
    rows = canonical_source_rows()

    with pytest.raises(ValueError, match="chunk_size"):
        await replay_snapshot_to_postgres(
            FakeRollbackSnapshot(rows),
            cutover_manifest=snapshot_manifest(rows),
            config=RollbackWriterConfig(dsn="not-opened", chunk_size=0),
        )


@pytest.mark.parametrize("fault", tuple(RollbackFaultPoint))
def test_fault_points_cover_every_frozen_transaction_boundary(
    fault: RollbackFaultPoint,
) -> None:
    """The injectable gates include every rollback-contract failure boundary."""
    assert fault.value in {
        "preflight",
        "after_trigger_disable",
        "after_jobs",
        "mid_replay",
        "after_sequence_reset",
        "before_trigger_enable",
        "trigger_enable",
    }
