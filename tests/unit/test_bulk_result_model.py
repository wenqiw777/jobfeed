"""Unit tests for the BulkResult domain model (Phase 6).

Tests cover:
- BulkResult default construction
- BulkResult field types and defaults
- BulkResult mutation (accumulation patterns)
- BulkResult is exported from domain.models and domain.models_status
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from jobfeed.domain.models_status import BulkResult


class TestBulkResultDefaults:
    """Tests for BulkResult default construction."""

    def test_default_succeeded_is_zero(self) -> None:
        """BulkResult().succeeded should default to 0."""
        br = BulkResult()
        assert br.succeeded == 0

    def test_default_skipped_is_zero(self) -> None:
        """BulkResult().skipped should default to 0."""
        br = BulkResult()
        assert br.skipped == 0

    def test_default_failed_is_empty_list(self) -> None:
        """BulkResult().failed should default to an empty list."""
        br = BulkResult()
        assert br.failed == []
        assert isinstance(br.failed, list)

    def test_failed_list_is_independent_per_instance(self) -> None:
        """Each BulkResult should get its own failed list (not shared)."""
        br1 = BulkResult()
        br2 = BulkResult()
        br1.failed.append(("42", "some error"))
        assert br2.failed == []


class TestBulkResultConstruction:
    """Tests for explicit BulkResult construction."""

    def test_construct_with_all_fields(self) -> None:
        """BulkResult should accept all three fields."""
        br = BulkResult(
            succeeded=5,
            failed=[("1", "err A"), ("2", "err B")],
            skipped=3,
        )
        assert br.succeeded == 5
        assert br.skipped == 3
        assert len(br.failed) == 2

    def test_failed_tuple_structure(self) -> None:
        """failed should store (job_id, error_message) tuples."""
        br = BulkResult(failed=[("100", "transition blocked")])
        job_id, msg = br.failed[0]
        assert job_id == "100"
        assert "transition blocked" in msg

    def test_construct_partial_override(self) -> None:
        """BulkResult should accept partial overrides."""
        br = BulkResult(succeeded=10)
        assert br.succeeded == 10
        assert br.skipped == 0
        assert br.failed == []


class TestBulkResultMutation:
    """Tests for accumulation / mutation patterns used in transition_status_bulk."""

    def test_increment_succeeded(self) -> None:
        """succeeded field can be incremented."""
        br = BulkResult()
        br.succeeded += 3
        assert br.succeeded == 3

    def test_append_failed(self) -> None:
        """failed list can be appended to."""
        br = BulkResult()
        br.failed.append(("99", "timeout"))
        assert len(br.failed) == 1
        assert br.failed[0] == ("99", "timeout")

    def test_increment_skipped(self) -> None:
        """skipped field can be incremented."""
        br = BulkResult()
        br.skipped += 2
        assert br.skipped == 2

    def test_accumulate_typical_bulk_run(self) -> None:
        """A typical bulk run: 3 succeeded, 1 failed, 2 skipped."""
        br = BulkResult()
        br.succeeded += 3
        br.failed.append(("55", "already rejected"))
        br.skipped += 2
        assert br.succeeded == 3
        assert len(br.failed) == 1
        assert br.skipped == 2


class TestBulkResultExports:
    """Tests that BulkResult is correctly exported from domain modules."""

    def test_bulk_result_importable_from_models(self) -> None:
        """BulkResult should be importable from jobfeed.domain.models."""
        from jobfeed.domain.models import BulkResult as BRFromModels

        assert BRFromModels is BulkResult

    def test_bulk_result_in_models_status_all(self) -> None:
        """BulkResult should appear in models_status.__all__."""
        from jobfeed.domain import models_status

        assert "BulkResult" in models_status.__all__

    def test_bulk_result_in_models_all(self) -> None:
        """BulkResult should appear in domain.models.__all__."""
        from jobfeed.domain import models

        assert "BulkResult" in models.__all__


class TestBulkResultFieldShape:
    """Tests that pin the field names of BulkResult for schema contract."""

    def test_field_names_are_correct(self) -> None:
        """BulkResult should have exactly succeeded, failed, skipped fields."""
        field_names = {f.name for f in fields(BulkResult)}
        assert field_names == {"succeeded", "failed", "skipped"}