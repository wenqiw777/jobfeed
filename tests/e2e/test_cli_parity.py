"""E2E parity suite for the Phase 7 CLI surface (PostgreSQL backend).

One golden-path test per Phase 7 command/flag-set against an ephemeral,
freshly migrated Postgres database, plus explicit assertions for the seven
pinned parity behaviors:

a. ILIKE case-insensitive ``list --notes-contain``
b. LIST_DEFAULT_STATUSES hides ``new`` unless ``--status new``
c. Empty ``list`` exits 1; ``--allow-empty`` exits 0
d. ``list`` plain columns + ``--json`` company/title fields
e. ``digest --top`` capping, KV cutoff key, output_dir file writes
f. ``apply --method/--notes`` persistence + ``apply-history --resume``
g. Snapshot prefix resolution, distinct errors, global list usage counts

All tests are offline: the mock source/LLM drive the pipeline and the
bootstrap fetch is monkeypatched to fixture markdown.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypeVar
from unittest.mock import patch

import pytest
from click.testing import CliRunner, Result

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.cli import cli
from jobfeed.services.digest import LAST_RENDERED_KEY
from tests.support.factories import make_job

T = TypeVar("T")

MASTER_RESUME_TEXT = "Master resume body for e2e parity."
EXPECTED_HISTORY_ROWS = 2
HASH_DISPLAY_LEN = 12

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_config(
    tmp_path: Path,
    name: str,
    dsn: str,
    *,
    resume_text: str = MASTER_RESUME_TEXT,
    digest_output_dir: Path | None = None,
) -> Path:
    """Write an isolated CLI config pointing at the test Postgres database.

    Args:
        tmp_path: Temporary root owned by pytest.
        name: Unique config namespace for one test flow.
        dsn: PostgreSQL DSN the store should connect to.
        resume_text: Content of the configured master resume file.
        digest_output_dir: Optional ``[digest] output_dir`` value.

    Returns:
        Path to the written TOML config file.
    """
    config_dir = tmp_path / name
    config_dir.mkdir(parents=True, exist_ok=True)
    resume_path = config_dir / "resume.md"
    resume_path.write_text(resume_text, encoding="utf-8")

    lines = [
        "[db]",
        f'url = "{dsn}"',
        "",
        "[llm]",
        'stage_a = "mock/stage-a"',
        'stage_b = "mock/stage-b"',
        f'master_resume_path = "{resume_path}"',
        "",
        "[observability]",
        'log_level = "warning"',
        'log_format = "human"',
        "",
    ]
    if digest_output_dir is not None:
        lines += ["[digest]", f'output_dir = "{digest_output_dir}"', ""]

    config_path = config_dir / "config.toml"
    config_path.write_text("\n".join(lines), encoding="utf-8")
    return config_path


def invoke(runner: CliRunner, config_path: Path, *args: str) -> Result:
    """Invoke the CLI without asserting on the exit code.

    Args:
        runner: Click test runner.
        config_path: Config file to pass through the top-level option.
        args: Command and command-specific arguments.

    Returns:
        Click invocation result.
    """
    return runner.invoke(cli, ["--config", str(config_path), *args])


def invoke_ok(runner: CliRunner, config_path: Path, *args: str) -> Result:
    """Invoke the CLI and assert a zero exit code.

    Args:
        runner: Click test runner.
        config_path: Config file to pass through the top-level option.
        args: Command and command-specific arguments.

    Returns:
        Successful Click invocation result.

    Raises:
        AssertionError: If the command exits non-zero.
    """
    result = invoke(runner, config_path, *args)
    assert result.exit_code == 0, result.output
    return result


def run_store(dsn: str, fn: Callable[[PostgresStore], Awaitable[T]]) -> T:
    """Run an async action against a connected store (sync wrapper).

    Args:
        dsn: PostgreSQL DSN.
        fn: Async action receiving the connected store.

    Returns:
        Whatever the action returns.
    """

    async def _run() -> T:
        store = PostgresStore(dsn)
        await store.connect()
        try:
            return await fn(store)
        finally:
            await store.close()

    return asyncio.run(_run())


def seed_job(dsn: str, canonical_id: str, **overrides: object) -> str:
    """Insert one job fixture and return its store-assigned id.

    The initial-status DB trigger gives the job status ``new``.

    Args:
        dsn: PostgreSQL DSN.
        canonical_id: Source-specific natural identity.
        **overrides: Optional JobPosting field overrides.

    Returns:
        Store-assigned job id.
    """

    async def _save(store: PostgresStore) -> str:
        result = await store.save_job(make_job(canonical_id, **overrides))
        return result.job_id

    return run_store(dsn, _save)


def first_tokens(output: str) -> list[str]:
    """Return the first whitespace-separated token of each non-blank line."""
    return [line.split()[0] for line in output.splitlines() if line.strip()]


def sha256_hex(text: str) -> str:
    """SHA-256 hex digest of text content (mirrors ApplicationService)."""
    return hashlib.sha256(text.encode()).hexdigest()


def tailored_text_sharing_first_hex(master_hash: str) -> str:
    """Find tailored content whose hash shares the master hash's first char.

    Used to manufacture an ambiguous one-character prefix deterministically.

    Args:
        master_hash: Hex digest to collide with on the first character.

    Returns:
        Tailored resume content string.

    Raises:
        AssertionError: If no candidate collides (practically impossible).
    """
    for i in range(4096):
        candidate = f"Tailored resume body for e2e parity v{i}."
        if sha256_hex(candidate)[0] == master_hash[0]:
            return candidate
    raise AssertionError("no first-hex-char collision found")


# ---------------------------------------------------------------------------
# list parity (pins a-d)
# ---------------------------------------------------------------------------


@pytest.mark.postgres
def test_list_notes_contain_is_case_insensitive(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Pin a: a lowercase note matches an UPPERCASE --notes-contain query."""
    config = write_config(tmp_path, "ilike", fresh_pg_dsn)
    runner = CliRunner()
    job_id = seed_job(fresh_pg_dsn, "ilike-1")
    invoke_ok(runner, config, "mark", job_id, "--status", "scored")
    invoke_ok(runner, config, "note", job_id, "circled back with recruiter")

    result = invoke_ok(runner, config, "list", "--notes-contain", "CIRCLED BACK")

    assert job_id in first_tokens(result.output)


@pytest.mark.postgres
def test_list_hides_new_by_default_but_status_new_shows_it(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Pin b: a ``new`` job is hidden by default list, shown by --status new."""
    config = write_config(tmp_path, "default-statuses", fresh_pg_dsn)
    runner = CliRunner()
    job_id = seed_job(fresh_pg_dsn, "new-1")

    hidden = invoke(runner, config, "list")
    shown = invoke_ok(runner, config, "list", "--status", "new")

    assert hidden.exit_code == 1, hidden.output
    assert "No matching jobs." in hidden.output
    assert job_id in first_tokens(shown.output)


@pytest.mark.postgres
def test_list_empty_exit_codes(tmp_path: Path, fresh_pg_dsn: str) -> None:
    """Pin c: empty list exits 1; --allow-empty exits 0."""
    config = write_config(tmp_path, "empty-exit", fresh_pg_dsn)
    runner = CliRunner()

    empty = invoke(runner, config, "list")
    allowed = invoke(runner, config, "list", "--allow-empty")

    assert empty.exit_code == 1, empty.output
    assert allowed.exit_code == 0, allowed.output


@pytest.mark.postgres
def test_list_output_columns_plain_and_json(tmp_path: Path, fresh_pg_dsn: str) -> None:
    """Pin d: plain rows carry id/status/company/title/last_change/followup;
    --json rows carry company/title."""
    config = write_config(tmp_path, "columns", fresh_pg_dsn)
    runner = CliRunner()
    job_id = seed_job(fresh_pg_dsn, "cols-1")
    # Captured before the apply, so that day's midnight always precedes the
    # apply timestamp even if the run straddles UTC midnight.
    apply_day = datetime.now(UTC).date()
    # apply transitions new -> applied and schedules a follow-up (+7d).
    invoke_ok(runner, config, "apply", job_id)

    plain = invoke_ok(runner, config, "list")
    as_json = invoke_ok(runner, config, "list", "--json")

    # Accept either adjacent UTC day in case the run straddled midnight.
    now = datetime.now(UTC)
    candidate_days = {now.date(), (now - timedelta(days=1)).date()}
    row = next(
        line for line in plain.output.splitlines() if line.split()[:1] == [job_id]
    )
    assert "applied" in row
    assert "Example" in row
    assert "Backend Intern" in row
    assert any(day.isoformat() in row for day in candidate_days)  # last_change
    assert any(  # followup (+7d from the apply-day candidate)
        (day + timedelta(days=7)).isoformat() in row for day in candidate_days
    )
    rows = json.loads(as_json.output)
    assert [r["id"] for r in rows] == [job_id]
    assert rows[0]["company"] == "Example"
    assert rows[0]["title"] == "Backend Intern"

    # --days <YYYY-MM-DD> means "since that date's midnight UTC", so the job
    # applied today must appear.
    since_today = invoke_ok(runner, config, "list", "--days", apply_day.isoformat())
    assert job_id in first_tokens(since_today.output)


# ---------------------------------------------------------------------------
# digest parity (pin e)
# ---------------------------------------------------------------------------


@pytest.mark.postgres
def test_digest_top_cap_kv_cutoff_and_file_writes(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Pin e: --top caps a 2-row group with a (1/2) header, the KV cutoff key
    is written, and output_dir gets today.md plus a dated file."""
    out_dir = tmp_path / "digest-out"
    config = write_config(tmp_path, "digest", fresh_pg_dsn, digest_output_dir=out_dir)
    runner = CliRunner()
    invoke_ok(runner, config, "scan", "--source", "mock")
    invoke_ok(runner, config, "evaluate", "--limit", "2")

    result = invoke_ok(runner, config, "digest", "--top", "1")

    assert "## Consider (1/2)" in result.output
    raw = run_store(fresh_pg_dsn, lambda store: store.get_state(LAST_RENDERED_KEY))
    assert raw is not None
    assert datetime.fromisoformat(raw).tzinfo is not None
    today_file = out_dir / "today.md"
    # Accept either adjacent UTC day's dated file in case the run straddled
    # midnight; exactly one of the two candidates must exist.
    now = datetime.now(UTC)
    candidate_days = {now.date(), (now - timedelta(days=1)).date()}
    dated_files = [
        path
        for path in (out_dir / f"{day.isoformat()}.md" for day in candidate_days)
        if path.is_file()
    ]
    assert today_file.is_file()
    assert len(dated_files) == 1
    assert "# Daily Digest" in today_file.read_text(encoding="utf-8")
    assert today_file.read_text(encoding="utf-8") == dated_files[0].read_text(
        encoding="utf-8"
    )

    # --quiet happy path: with output_dir configured it exits 0, suppresses
    # the stdout body, and still (re)writes the digest files.
    today_file.unlink()
    quiet = invoke_ok(runner, config, "digest", "--quiet")
    assert "Daily Digest" not in quiet.output
    assert "## Consider" not in quiet.output
    assert today_file.is_file()
    assert "# Daily Digest" in today_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# apply parity (pin f)
# ---------------------------------------------------------------------------


@pytest.mark.postgres
def test_apply_method_notes_and_history_resume_filter(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Pin f: --method/--notes show up in apply-history; --resume filters."""
    config = write_config(tmp_path, "apply", fresh_pg_dsn)
    runner = CliRunner()
    job_a = seed_job(fresh_pg_dsn, "apply-a")
    job_b = seed_job(fresh_pg_dsn, "apply-b")
    tailored_text = "Tailored resume for the resume-filter pin."
    tailored_path = tmp_path / "tailored.md"
    tailored_path.write_text(tailored_text, encoding="utf-8")

    # Notes deliberately avoid the word "referral" so the method assertion
    # below can only be satisfied by the method column.
    first = invoke_ok(
        runner,
        config,
        "apply",
        job_a,
        "--method",
        "referral",
        "--notes",
        "intro via alum",
    )
    second = invoke_ok(runner, config, "apply", job_b, "--tailored", str(tailored_path))

    # Both seeded jobs share company "Example": the second apply must print
    # the reapply notice pointing at the first, still-active application.
    assert "Active application at same company:" not in first.output
    assert "Active application at same company:" in second.output
    assert f"(job {job_a}, status=applied)" in second.output

    history = invoke_ok(runner, config, "apply-history")
    history_ids = first_tokens(history.output)
    assert job_a in history_ids
    assert job_b in history_ids
    assert len(history_ids) == EXPECTED_HISTORY_ROWS
    row_a = next(
        line for line in history.output.splitlines() if line.startswith(f"{job_a} ")
    )
    assert "referral" in row_a  # method column (notes contain no "referral")
    assert "intro via alum" in row_a

    tailored_prefix = sha256_hex(tailored_text)[:10]
    filtered = invoke_ok(runner, config, "apply-history", "--resume", tailored_prefix)
    filtered_ids = first_tokens(filtered.output)
    assert filtered_ids == [job_b]


# ---------------------------------------------------------------------------
# snapshots parity (pin g)
# ---------------------------------------------------------------------------


@pytest.mark.postgres
def test_snapshots_show_and_diff_resolve_prefixes_with_distinct_errors(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Pin g: unique prefixes resolve; unknown vs ambiguous errors differ."""
    config = write_config(tmp_path, "snap-prefix", fresh_pg_dsn)
    runner = CliRunner()
    job_id = seed_job(fresh_pg_dsn, "snap-1")
    master_hash = sha256_hex(MASTER_RESUME_TEXT)
    tailored_text = tailored_text_sharing_first_hex(master_hash)
    tailored_hash = sha256_hex(tailored_text)
    tailored_path = tmp_path / "tailored.md"
    tailored_path.write_text(tailored_text, encoding="utf-8")
    invoke_ok(runner, config, "apply", job_id, "--tailored", str(tailored_path))

    shown = invoke_ok(
        runner, config, "snapshots", "show", master_hash[:HASH_DISPLAY_LEN]
    )
    assert MASTER_RESUME_TEXT in shown.output

    # 'z' is outside the hex alphabet, so this prefix can never match.
    unknown = invoke(runner, config, "snapshots", "show", "zzzz")
    assert unknown.exit_code != 0
    assert "no resume snapshot matches prefix 'zzzz'" in unknown.output

    # Both stored hashes share their first hex character by construction.
    ambiguous = invoke(runner, config, "snapshots", "show", master_hash[0])
    assert ambiguous.exit_code != 0
    assert "matches multiple snapshots" in ambiguous.output
    assert "use more characters" in ambiguous.output
    assert "no resume snapshot matches" not in ambiguous.output

    diff = invoke_ok(
        runner,
        config,
        "snapshots",
        "diff",
        master_hash[:HASH_DISPLAY_LEN],
        tailored_hash[:HASH_DISPLAY_LEN],
    )
    assert f"--- {master_hash}" in diff.output
    assert f"+++ {tailored_hash}" in diff.output
    assert f"-{MASTER_RESUME_TEXT}" in diff.output
    assert f"+{tailored_text}" in diff.output

    bad_diff = invoke(
        runner, config, "snapshots", "diff", "zzzz", master_hash[:HASH_DISPLAY_LEN]
    )
    assert bad_diff.exit_code != 0
    assert "no resume snapshot matches prefix 'zzzz'" in bad_diff.output


@pytest.mark.postgres
def test_snapshots_global_list_usage_counts_and_per_job_mode(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Pin g: global list shows usage counts, --source filters, per-job works."""
    config = write_config(tmp_path, "snap-list", fresh_pg_dsn)
    runner = CliRunner()
    job_a = seed_job(fresh_pg_dsn, "snap-list-a")
    job_b = seed_job(fresh_pg_dsn, "snap-list-b")
    master_hash = sha256_hex(MASTER_RESUME_TEXT)
    tailored_text = "Tailored resume used once for the usage-count pin."
    tailored_hash = sha256_hex(tailored_text)
    tailored_path = tmp_path / "tailored.md"
    tailored_path.write_text(tailored_text, encoding="utf-8")
    # Master used by both applications, tailored only by the first.
    invoke_ok(runner, config, "apply", job_a, "--tailored", str(tailored_path))
    invoke_ok(runner, config, "apply", job_b)

    global_list = invoke_ok(runner, config, "snapshots", "list")
    master_row = next(
        line
        for line in global_list.output.splitlines()
        if line.startswith(master_hash[:HASH_DISPLAY_LEN])
    )
    tailored_row = next(
        line
        for line in global_list.output.splitlines()
        if line.startswith(tailored_hash[:HASH_DISPLAY_LEN])
    )
    assert "master" in master_row
    assert "used=2" in master_row
    assert "tailored" in tailored_row
    assert "used=1" in tailored_row

    filtered = invoke_ok(runner, config, "snapshots", "list", "--source", "tailored")
    assert tailored_hash[:HASH_DISPLAY_LEN] in filtered.output
    assert master_hash[:HASH_DISPLAY_LEN] not in filtered.output

    per_job = invoke_ok(runner, config, "snapshots", "list", job_a)
    assert "applied:" in per_job.output
    assert f"master:   {master_hash}" in per_job.output
    assert f"tailored: {tailored_hash}" in per_job.output


# ---------------------------------------------------------------------------
# followup golden path
# ---------------------------------------------------------------------------


@pytest.mark.postgres
def test_followup_set_and_needs_followup_round_trip(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """followup sets a date; list --needs-followup shows only past-due jobs."""
    config = write_config(tmp_path, "followup", fresh_pg_dsn)
    runner = CliRunner()
    job_due = seed_job(fresh_pg_dsn, "followup-due")
    job_later = seed_job(fresh_pg_dsn, "followup-later")
    invoke_ok(runner, config, "mark", job_due, job_later, "--status", "scored")

    set_due = invoke_ok(runner, config, "followup", job_due, "--in", "2020-01-01")
    invoke_ok(runner, config, "followup", job_later, "--in", "30d")

    assert f"Follow-up for {job_due} set to 2020-01-01" in set_due.output
    listed = invoke_ok(runner, config, "list", "--needs-followup")
    listed_ids = first_tokens(listed.output)
    assert job_due in listed_ids
    assert job_later not in listed_ids


# ---------------------------------------------------------------------------
# enrich-paste golden path
# ---------------------------------------------------------------------------


@pytest.mark.postgres
def test_enrich_paste_from_file(tmp_path: Path, fresh_pg_dsn: str) -> None:
    """enrich-paste --from-file stores the JD on a scraped stub job."""
    config = write_config(tmp_path, "enrich", fresh_pg_dsn)
    runner = CliRunner()
    canonical_id = "li-4012345678"
    job_id = seed_job(fresh_pg_dsn, canonical_id, platform="linkedin")
    jd_path = tmp_path / "jd.txt"
    jd_text = (
        "We are hiring a backend engineer to build data pipelines in Python.\n"
        "Responsibilities include API design, PostgreSQL schema work, and\n"
        "operating services in production. Requirements: 2+ years Python.\n"
    )
    jd_path.write_text(jd_text, encoding="utf-8")

    result = invoke_ok(
        runner,
        config,
        "enrich-paste",
        canonical_id,
        "--platform",
        "linkedin",
        "--from-file",
        str(jd_path),
    )

    assert f"Enriched job {job_id}" in result.output
    job = run_store(fresh_pg_dsn, lambda store: store.get_job(job_id))
    assert job is not None
    assert job.jd_text == jd_text
    assert job.enrich_source == "manual-paste"


# ---------------------------------------------------------------------------
# companies golden path
# ---------------------------------------------------------------------------


@pytest.mark.postgres
def test_companies_add_list_remove_round_trip(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """companies add --ats (pinned, no probe) / list / remove round-trip."""
    config = write_config(tmp_path, "companies", fresh_pg_dsn)
    runner = CliRunner()

    added = invoke_ok(runner, config, "companies", "add", "acme", "--ats", "greenhouse")
    assert "Added acme (vendor: greenhouse, pinned)" in added.output

    listed = invoke_ok(runner, config, "companies", "list")
    assert "acme" in listed.output
    assert "greenhouse" in listed.output

    removed = invoke_ok(runner, config, "companies", "remove", "acme")
    assert "Removed acme" in removed.output

    # A second remove of the same slug must report not-found, not success.
    removed_again = invoke(runner, config, "companies", "remove", "acme")
    assert removed_again.exit_code != 0, removed_again.output
    assert "company not tracked (unknown or already removed): acme" in (
        removed_again.output
    )

    after = invoke_ok(runner, config, "companies", "list")
    assert "No companies found." in after.output
    including = invoke_ok(runner, config, "companies", "list", "--include-removed")
    assert "acme" in including.output


# ---------------------------------------------------------------------------
# bootstrap-companies golden path (offline)
# ---------------------------------------------------------------------------

_BOOTSTRAP_FIXTURE_MD = "\n".join(
    [
        "| Company | Apply |",
        "| ------- | ----- |",
        "| Acme | https://boards.greenhouse.io/acmeco |",
        "| Beta | https://jobs.ashbyhq.com/betainc |",
        "| Gamma | https://jobs.lever.co/gammaworks |",
    ]
)


@pytest.mark.postgres
def test_bootstrap_companies_dry_run_offline(tmp_path: Path, fresh_pg_dsn: str) -> None:
    """bootstrap-companies dry-run previews slugs and writes nothing."""
    config = write_config(tmp_path, "bootstrap", fresh_pg_dsn)
    runner = CliRunner()

    async def fake_fetch(names: list[str]) -> dict[str, str]:
        return dict.fromkeys(names, _BOOTSTRAP_FIXTURE_MD)

    with patch("jobfeed.cli.bootstrap._fetch_sources", new=fake_fetch):
        result = invoke_ok(
            runner,
            config,
            "bootstrap-companies",
            "--source",
            "simplifyjobs-newgrad",
        )

    assert "simplifyjobs-newgrad: 3 slugs" in result.output
    assert "would add acmeco (greenhouse)" in result.output
    assert "would add betainc (ashby)" in result.output
    assert "would add gammaworks (lever)" in result.output
    assert "Dry-run: 3 new, 0 already tracked." in result.output
    after = invoke_ok(runner, config, "companies", "list")
    assert "No companies found." in after.output
