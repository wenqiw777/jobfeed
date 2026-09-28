"""Repeated page reads reuse content work without hiding changed inputs."""

# ruff: noqa: PLR2004

from dataclasses import replace

from jobfeed.domain.models_views import JobsViewRow
from jobfeed.services import _jobs_view_fold as module
from tests.unit.test_display_content_dedupe import posting


def test_fold_cache_reuses_work_and_invalidates_changed_content(monkeypatch):
    cache = module.DisplayFoldCache()
    rows = [
        JobsViewRow(
            job=posting(str(i)),
            company_norm="amazon",
            title_norm="engineer",
            status="new",
            verdict=None,
            stage_a_score=None,
            stage_b_fit_score=None,
            stage_b_status=None,
        )
        for i in range(2)
    ]
    original = module._fold_to_display_representatives
    calls = []

    def counted(values):
        calls.append(len(values))
        return original(values)

    monkeypatch.setattr(module, "_fold_to_display_representatives", counted)
    assert len(cache.fold(rows)) == 1
    fresh_rows = [replace(row, job=replace(row.job), stage_a_score=80) for row in rows]
    assert cache.fold(fresh_rows)[0].stage_a_score == 80
    assert len(calls) == 1
    rows[1].job.jd_text = "A completely different job."
    assert len(cache.fold(rows)) == 2
    rows[0].status = "applied"
    cache.fold(rows)
    assert len(calls) == 3
    cache.fold(list(reversed(rows)))
    assert len(calls) == 4
    cache.fold([*rows, replace(rows[0], job=posting("3"))])
    assert len(calls) == 5
    assert len(cache._entries) == 4
    cache.fold(fresh_rows)
    assert len(calls) == 6  # The original input was evicted, not retained forever.
