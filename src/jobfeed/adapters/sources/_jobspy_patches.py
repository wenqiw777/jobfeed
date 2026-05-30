"""Runtime monkeypatch for python-jobspy's Indeed date handling.

Why this file exists
--------------------
JobSpy's Indeed scraper FILTERS by ``dateOnIndeed`` (when Indeed indexed the
job) but populates the result-side ``date_posted`` from ``datePublished`` (the
employer's original publish date). For syndicated postings these diverge by
months: a role re-syndicated to Indeed today may carry ``datePublished``
months in the past, so JobSpy reports an ancient ``date_posted`` even though
the filter that returned it was "last 72h".

For this pipeline that is the wrong answer — we want "what is new on Indeed
today" (``dateOnIndeed``), not "when the employer first published this
somewhere" (``datePublished``). The cheapest containment is to swap
``datePublished`` for ``dateOnIndeed`` in each job dict BEFORE JobSpy's
``_process_job`` runs, so JobSpy's existing mapping produces the value we want.

This is the rewrite port of the legacy ``_jobspy_patches.apply_indeed_date_patch``.
jobspy / pandas are confined to this module and ``_jobspy.py`` only.

Failure signals to watch
------------------------
1. ``apply_indeed_date_patch()`` raises ``JobSpyPatchError`` -> JobSpy renamed
   ``jobspy.indeed.Indeed`` or its ``_process_job`` method. A jobspy upgrade
   fails LOUDLY here rather than silently no-op'ing. Re-target the patch.
2. Indeed ``posted_at`` values suddenly look ancient again -> the patch isn't
   running before ``scrape_jobs`` (import-order bug), or jobspy moved date
   handling out of ``_process_job``.
"""

from __future__ import annotations

from typing import Any

_PATCH_MARKER = "_jobfeed_patched"


class JobSpyPatchError(RuntimeError):
    """Raised when the Indeed date patch cannot find its target symbol.

    Signals that a jobspy upgrade renamed ``jobspy.indeed.Indeed`` or its
    ``_process_job`` method, so the patch would silently no-op. Fail loud.
    """


def _swap_date_field(job: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of ``job`` with ``datePublished`` <- ``dateOnIndeed``.

    Pure function (no jobspy needed) so the behavior is unit-testable in
    isolation. When ``dateOnIndeed`` is absent/None the job dict is returned
    unchanged.

    Args:
        job: A raw Indeed job dict as handed to ``_process_job``.

    Returns:
        The job dict, with ``datePublished`` overwritten when ``dateOnIndeed``
        is present.
    """
    date_on_indeed = job.get("dateOnIndeed")
    if date_on_indeed is None:
        return job
    return {**job, "datePublished": date_on_indeed}


def apply_indeed_date_patch() -> None:
    """Wrap ``jobspy.indeed.Indeed._process_job`` to swap the date source.

    Idempotent: a second call is a no-op (detected via the ``_jobfeed_patched``
    marker attribute), so it is safe to call once per ``fetch_jobs``.

    Raises:
        JobSpyPatchError: If ``jobspy.indeed.Indeed`` or its ``_process_job``
            method no longer exists (jobspy renamed the patched symbol).
    """
    try:
        from jobspy.indeed import Indeed  # noqa: PLC0415 — lazy: keep import-cheap
    except ImportError as exc:  # jobspy missing or restructured its package
        raise JobSpyPatchError(
            "jobspy.indeed.Indeed not importable; jobspy may have renamed it"
        ) from exc

    original = getattr(Indeed, "_process_job", None)
    if original is None:
        raise JobSpyPatchError(
            "jobspy.indeed.Indeed._process_job not found; re-target the date patch"
        )

    if getattr(original, _PATCH_MARKER, False):
        return

    def _patched(self: Any, job: dict[str, Any], _original: Any = original) -> Any:
        return _original(self, _swap_date_field(job))

    _patched._jobfeed_patched = True  # type: ignore[attr-defined]  # idempotency marker
    Indeed._process_job = _patched


__all__ = ["JobSpyPatchError", "apply_indeed_date_patch"]
