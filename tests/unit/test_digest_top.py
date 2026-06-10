"""Unit tests for digest top cap, KV cutoff, file output, and attention footer."""

from __future__ import annotations

import asyncio
import pathlib
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner

from jobfeed.cli import cli
from jobfeed.domain.digest import render_attention_footer, render_digest
from jobfeed.domain.models import (
    AttentionItem,
    AttentionReport,
    FitAnalysis,
    JobEvaluation,
    StageBResult,
    Verdict,
    WorkflowAttention,
    WorkflowAttentionItem,
)
from jobfeed.services.digest import LAST_RENDERED_KEY, DigestService
from tests.support.factories import make_job

APPLY_SCORE = 91

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_evaluation(
    canonical_id: str,
    verdict: Verdict,
    discovered_at: datetime | None = None,
) -> JobEvaluation:
    """Create a minimal evaluation with the given verdict."""
    return JobEvaluation(
        job=make_job(
            canonical_id,
            title=f"Intern {canonical_id}",
            company="Example",
            discovered_at=discovered_at or datetime.now(UTC),
        ),
        stage_a=None,
        stage_b=StageBResult(
            verdict=verdict,
            jd_summary=f"Summary for {canonical_id}",
            fit_analysis=FitAnalysis(score=APPLY_SCORE, strengths=[], gaps=[]),
            resume_hooks=[],
            model="mock/stage-b",
            prompt_hash="prompt",
            resume_hash="resume",
        ),
    )


def make_workflow_item(
    job_id: str = "12", reason: str = "follow-up due"
) -> WorkflowAttentionItem:
    """Create a workflow attention item for footer tests."""
    return WorkflowAttentionItem(
        job_id=job_id,
        title="Backend Intern",
        company="Example",
        url="https://example.com/12",
        status="applied",
        last_status_change_at=datetime(2026, 6, 1, tzinfo=UTC),
        next_followup_at=None,
        notes=None,
        reason=reason,
        days_since=3,
    )


def empty_attention() -> WorkflowAttention:
    """Create an empty workflow attention report."""
    return WorkflowAttention(
        follow_up_today=[],
        interview_prep=[],
        going_ghosted=[],
    )


# ---------------------------------------------------------------------------
# Domain: --top cap
# ---------------------------------------------------------------------------


class TestRenderDigestTop:
    """Tests for render_digest(top=...)."""

    def test_top_caps_apply_and_consider_groups(self) -> None:
        """top=2 should show at most 2 rows per group with (2/3) headers."""
        evaluations = [
            make_evaluation("a1", Verdict.APPLY),
            make_evaluation("a2", Verdict.APPLY),
            make_evaluation("a3", Verdict.APPLY),
            make_evaluation("c1", Verdict.CONSIDER),
            make_evaluation("c2", Verdict.CONSIDER),
            make_evaluation("c3", Verdict.CONSIDER),
        ]
        digest = render_digest(evaluations, {}, top=2)
        assert "## Apply (2/3)" in digest
        assert "## Consider (2/3)" in digest
        assert "https://example.com/a1" in digest
        assert "https://example.com/a2" in digest
        assert "https://example.com/a3" not in digest
        assert "Intern c1" in digest
        assert "Intern c2" in digest
        assert "Intern c3" not in digest

    def test_without_top_headers_unchanged(self) -> None:
        """Without top, group headers stay plain and all rows render."""
        evaluations = [
            make_evaluation("a1", Verdict.APPLY),
            make_evaluation("a2", Verdict.APPLY),
        ]
        digest = render_digest(evaluations, {})
        assert "## Apply\n" in digest
        assert "## Apply (" not in digest
        assert "https://example.com/a1" in digest
        assert "https://example.com/a2" in digest

    def test_top_larger_than_group_shows_actual_counts(self) -> None:
        """top above group size should show (total/total)."""
        digest = render_digest([make_evaluation("a1", Verdict.APPLY)], {}, top=5)
        assert "## Apply (1/5)" not in digest
        assert "## Apply (1/1)" in digest

    def test_top_equal_to_group_size_shows_full_group(self) -> None:
        """top == group size renders an (N/N) header with every row."""
        evaluations = [
            make_evaluation("a1", Verdict.APPLY),
            make_evaluation("a2", Verdict.APPLY),
        ]
        digest = render_digest(evaluations, {}, top=2)
        assert "## Apply (2/2)" in digest
        assert "https://example.com/a1" in digest
        assert "https://example.com/a2" in digest

    def test_top_caps_before_cutoff_split(self) -> None:
        """top should cap the apply group before the new/seen split."""
        cutoff = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
        new_time = datetime(2026, 5, 21, 13, 0, tzinfo=UTC)
        old_time = datetime(2026, 5, 21, 11, 0, tzinfo=UTC)
        evaluations = [
            make_evaluation("n1", Verdict.APPLY, new_time),
            make_evaluation("n2", Verdict.APPLY, new_time),
            make_evaluation("o1", Verdict.APPLY, old_time),
        ]
        digest = render_digest(evaluations, {}, cutoff_at=cutoff, top=2)
        assert "## Apply (2/3)" in digest
        assert "### New" in digest
        assert "https://example.com/n1" in digest
        assert "https://example.com/n2" in digest
        assert "https://example.com/o1" not in digest

    def test_top_rejects_non_positive(self) -> None:
        """top below 1 should raise ValueError."""
        with pytest.raises(ValueError, match="top must be >= 1"):
            render_digest([], {}, top=0)


# ---------------------------------------------------------------------------
# Domain: attention footer
# ---------------------------------------------------------------------------


class TestRenderAttentionFooter:
    """Tests for render_attention_footer."""

    def test_all_empty_returns_empty_string(self) -> None:
        """Empty buckets in both reports should render nothing."""
        assert render_attention_footer(empty_attention(), AttentionReport()) == ""

    def test_follow_up_bucket_renders_and_empty_buckets_omitted(self) -> None:
        """A follow-up-due item should render; empty buckets stay omitted."""
        attention = WorkflowAttention(
            follow_up_today=[make_workflow_item()],
            interview_prep=[],
            going_ghosted=[],
        )
        footer = render_attention_footer(attention, AttentionReport())
        assert "## Attention" in footer
        assert "### Follow-up due (1)" in footer
        assert "[12] Backend Intern @ Example: follow-up due" in footer
        assert "Interview prep" not in footer
        assert "Going ghosted" not in footer
        assert "Enrich errors" not in footer

    def test_report_bucket_renders_detail(self) -> None:
        """needs_attention buckets should render item detail lines."""
        report = AttentionReport(
            enrich_errors=[
                AttentionItem(
                    job_id="7",
                    title="Data Intern",
                    company="Acme",
                    category="enrich_errors",
                    detail="timeout fetching JD",
                )
            ]
        )
        footer = render_attention_footer(empty_attention(), report)
        assert "### Enrich errors (1)" in footer
        assert "[7] Data Intern @ Acme: timeout fetching JD" in footer
        assert "Follow-up due" not in footer


# ---------------------------------------------------------------------------
# Service: KV cutoff, file output, footer wiring
# ---------------------------------------------------------------------------


def _make_store(**overrides: Any) -> AsyncMock:
    """Build a mock digest store with empty defaults."""
    store = AsyncMock()
    store.list_evaluated_jobs.return_value = overrides.get("list_evaluated_jobs", [])
    store.get_state.return_value = overrides.get("get_state")
    store.set_state.return_value = None
    store.workflow_attention.return_value = overrides.get(
        "workflow_attention", empty_attention()
    )
    store.needs_attention.return_value = overrides.get(
        "needs_attention", AttentionReport()
    )
    return store


def _svc(store: AsyncMock) -> DigestService:
    return DigestService(store, MagicMock())


class TestDigestServiceCutoff:
    """KV auto-cutoff behavior."""

    def test_first_run_empty_kv_no_split_and_writes_key(self) -> None:
        """Empty KV means no cutoff split; key is written after render."""
        store = _make_store(
            list_evaluated_jobs=[make_evaluation("a1", Verdict.APPLY)],
        )
        digest = asyncio.run(_svc(store).run())
        assert "### New" not in digest
        store.set_state.assert_awaited_once()
        key, value = store.set_state.await_args.args
        assert key == LAST_RENDERED_KEY
        written = datetime.fromisoformat(value)
        assert written.tzinfo is not None

    def test_second_run_splits_on_stored_timestamp(self) -> None:
        """A stored KV timestamp should act as the cutoff."""
        cutoff = datetime(2026, 6, 1, tzinfo=UTC)
        store = _make_store(
            get_state=cutoff.isoformat(),
            list_evaluated_jobs=[
                make_evaluation("a1", Verdict.APPLY, datetime(2026, 6, 2, tzinfo=UTC))
            ],
        )
        digest = asyncio.run(_svc(store).run())
        assert "### New" in digest
        assert "https://example.com/a1" in digest

    def test_malformed_kv_treated_as_first_run(self) -> None:
        """Garbage KV value should not crash and means no cutoff."""
        store = _make_store(
            get_state="not-a-timestamp",
            list_evaluated_jobs=[make_evaluation("a1", Verdict.APPLY)],
        )
        digest = asyncio.run(_svc(store).run())
        assert "### New" not in digest
        store.set_state.assert_awaited_once()

    def test_naive_kv_treated_as_first_run(self) -> None:
        """A timezone-naive KV value should be ignored, not crash."""
        store = _make_store(
            get_state="2026-06-01T00:00:00",
            list_evaluated_jobs=[make_evaluation("a1", Verdict.APPLY)],
        )
        digest = asyncio.run(_svc(store).run())
        assert "### New" not in digest

    def test_explicit_cutoff_overrides_kv_and_updates_key(self) -> None:
        """Explicit cutoff_at skips the KV read but still updates the key."""
        cutoff = datetime(2026, 6, 1, tzinfo=UTC)
        store = _make_store(
            list_evaluated_jobs=[
                make_evaluation("a1", Verdict.APPLY, datetime(2026, 6, 2, tzinfo=UTC))
            ],
        )
        digest = asyncio.run(_svc(store).run(cutoff))
        assert "### New" in digest
        store.get_state.assert_not_awaited()
        store.set_state.assert_awaited_once()

    def test_render_failure_does_not_write_key(self) -> None:
        """A failing render must not advance the stored cutoff."""
        store = _make_store(
            list_evaluated_jobs=[make_evaluation("a1", Verdict.APPLY)],
        )
        with pytest.raises(ValueError, match="timezone-aware"):
            asyncio.run(_svc(store).run(datetime(2026, 6, 1)))
        store.set_state.assert_not_awaited()


class TestDigestServiceFiles:
    """File output behavior."""

    def test_output_dir_writes_both_files(self, tmp_path: pathlib.Path) -> None:
        """today.md and the dated file should both contain the digest."""
        store = _make_store()
        target = tmp_path / "digests"
        digest = asyncio.run(_svc(store).run(output_dir=target))
        today = datetime.now(UTC).date().isoformat()
        assert (target / "today.md").read_text(encoding="utf-8") == digest
        assert (target / f"{today}.md").read_text(encoding="utf-8") == digest
        store.set_state.assert_awaited_once()

    def test_no_output_dir_writes_nothing(self, tmp_path: pathlib.Path) -> None:
        """Without output_dir no files appear."""
        store = _make_store()
        asyncio.run(_svc(store).run())
        assert list(tmp_path.iterdir()) == []


class TestDigestServiceFooter:
    """Attention footer wiring."""

    def test_footer_appended_when_attention_nonempty(self) -> None:
        """A follow-up-due job should surface in the digest footer."""
        store = _make_store(
            workflow_attention=WorkflowAttention(
                follow_up_today=[make_workflow_item()],
                interview_prep=[],
                going_ghosted=[],
            ),
        )
        digest = asyncio.run(_svc(store).run())
        assert "## Attention" in digest
        assert "### Follow-up due (1)" in digest
        assert "Interview prep" not in digest

    def test_no_footer_when_everything_empty(self) -> None:
        """Empty attention reports should leave the digest unchanged."""
        digest = asyncio.run(_svc(_make_store()).run())
        assert "## Attention" not in digest


# ---------------------------------------------------------------------------
# CLI flag behavior
# ---------------------------------------------------------------------------


class TestDigestCli:
    """CLI flag validation (no store connection needed)."""

    def test_quiet_without_output_dir_is_usage_error(self) -> None:
        """--quiet without configured digest.output_dir should fail fast."""
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["digest", "--quiet"])
        assert result.exit_code != 0
        assert "output_dir" in result.output

    def test_top_rejects_zero(self) -> None:
        """--top must be >= 1 via click IntRange."""
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["digest", "--top", "0"])
        assert result.exit_code != 0
        assert "--top" in result.output
