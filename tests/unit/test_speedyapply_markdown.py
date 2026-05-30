"""Unit tests for the SpeedyApply markdown table parser.

Covers the two table schemas the upstream repo ships (6-col with Salary, 5-col
without), the closed-listing 🔒 marker, continuation rows, posted_at derivation
from the Age column, and the canonical_id formula.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from jobfeed.adapters.sources._speedyapply_markdown import (
    SpeedyRow,
    canonical_id_for,
    parse_rows,
)

_NOW = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)
_TWO_ROWS = 2


def test_parses_six_column_table_with_salary() -> None:
    """6-col FAANG+ table (Company|Position|Location|Salary|Posting|Age)."""
    md = """## FAANG+

| Company | Position | Location | Salary | Posting | Age |
|---|---|---|---|---|---|
| <a href="https://adobe.com"><strong>Adobe</strong></a> | 2026 Intern - SDE | San Francisco | $55/hr | <a href="https://adobe.wd5.myworkdayjobs.com/job/abc"><img src="x" alt="Apply"/></a> | 2d |
| <a href="https://nvidia.com"><strong>NVIDIA</strong></a> | SWE Intern - AI Tools | Santa Clara | $62/hr | <a href="https://nvidia.wd5.myworkdayjobs.com/job/xyz"><img src="x"/></a> | 5d |
"""
    rows = parse_rows(md, now=_NOW)
    assert len(rows) == _TWO_ROWS
    assert rows[0].company == "Adobe"
    assert rows[0].title == "2026 Intern - SDE"
    assert rows[0].location == "San Francisco"
    assert rows[0].apply_url == "https://adobe.wd5.myworkdayjobs.com/job/abc"
    # Age 2d, now May 10 -> posted May 8.
    assert rows[0].posted_at == datetime(2026, 5, 8, 12, 0, 0, tzinfo=UTC)
    assert rows[1].company == "NVIDIA"
    assert rows[1].posted_at == datetime(2026, 5, 5, 12, 0, 0, tzinfo=UTC)


def test_parses_five_column_table_without_salary() -> None:
    """5-col Other table drops the Salary column."""
    md = """### Other

| Company | Position | Location | Posting | Age |
|---|---|---|---|---|
| <a href="https://www.copart.com"><strong>Copart</strong></a> | SWE Intern | Dallas, TX | <a href="https://copart.wd12.myworkdayjobs.com/job/123"><img src="x"/></a> | 99d |
"""
    rows = parse_rows(md, now=_NOW)
    assert len(rows) == 1
    assert rows[0].company == "Copart"
    assert rows[0].title == "SWE Intern"
    assert rows[0].location == "Dallas, TX"
    assert rows[0].apply_url == "https://copart.wd12.myworkdayjobs.com/job/123"


def test_skips_closed_listings_with_lock_emoji() -> None:
    """Rows carrying 🔒 (closed) are skipped; live rows kept."""
    md = """| Company | Position | Location | Posting | Age |
|---|---|---|---|---|
| <a href="https://acme.com"><strong>Acme</strong></a> | SWE Intern | NY | <a href="https://acme.com/apply"><img src="x"/></a> | 🔒 |
| <a href="https://live.com"><strong>LiveCo</strong></a> | SWE Intern | NY | <a href="https://live.com/apply"><img src="x"/></a> | 3d |
"""
    rows = parse_rows(md, now=_NOW)
    assert len(rows) == 1
    assert rows[0].company == "LiveCo"


def test_skips_continuation_rows_with_empty_company() -> None:
    """Continuation rows (empty Company cell) are skipped."""
    md = """| Company | Position | Location | Posting | Age |
|---|---|---|---|---|
| <a href="https://acme.com"><strong>Acme</strong></a> | SWE Intern | NY | <a href="https://acme.com/a"><img src="x"/></a> | 3d |
|  | SWE Intern (remote) | Remote | <a href="https://acme.com/b"><img src="x"/></a> | 3d |
"""
    rows = parse_rows(md, now=_NOW)
    assert len(rows) == 1
    assert rows[0].company == "Acme"


def test_handles_two_tables_in_one_document() -> None:
    """Column count is tracked per header so 6-col then 5-col both parse."""
    md = """### FAANG+

| Company | Position | Location | Salary | Posting | Age |
|---|---|---|---|---|---|
| <a><strong>Big</strong></a> | Big Role | NY | $99/hr | <a href="https://big.co/a"><img src="x"/></a> | 1d |

### Other

| Company | Position | Location | Posting | Age |
|---|---|---|---|---|
| <a><strong>Small</strong></a> | Small Role | LA | <a href="https://small.co/a"><img src="x"/></a> | 7d |
"""
    rows = parse_rows(md, now=_NOW)
    assert [r.company for r in rows] == ["Big", "Small"]


def test_posted_at_none_for_unparseable_age() -> None:
    """Ages that are not 'Nd' (e.g. 'yesterday') leave posted_at None."""
    md = """| Company | Position | Location | Posting | Age |
|---|---|---|---|---|
| <a><strong>Acme</strong></a> | SWE | NY | <a href="https://acme.co/a"><img src="x"/></a> | yesterday |
"""
    rows = parse_rows(md, now=_NOW)
    assert len(rows) == 1
    assert rows[0].posted_at is None


def test_posted_at_from_nd_age() -> None:
    """posted_at = now - N days for an 'Nd' age."""
    md = """| Company | Position | Location | Posting | Age |
|---|---|---|---|---|
| <a><strong>Acme</strong></a> | SWE | NY | <a href="https://acme.co/a"><img src="x"/></a> | 4d |
"""
    rows = parse_rows(md, now=_NOW)
    assert rows[0].posted_at == datetime(2026, 5, 6, 12, 0, 0, tzinfo=UTC)


def test_canonical_id_formula() -> None:
    """canonical_id == 'sa-' + sha256(apply_url)[:16]."""
    url = "https://example.com/apply/123"
    expected = "sa-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    assert canonical_id_for(url) == expected
    assert (
        SpeedyRow(
            company="C", title="T", location="L", apply_url=url, posted_at=None
        ).canonical_id
        == expected
    )


def test_canonical_id_stable_and_distinct() -> None:
    """Same URL -> same id (upsert collapse); different URL -> different id."""
    a = canonical_id_for("https://example.com/a")
    b = canonical_id_for("https://example.com/a")
    c = canonical_id_for("https://example.com/b")
    assert a == b
    assert a != c
    assert a.startswith("sa-")
    assert len(a) == len("sa-") + 16
