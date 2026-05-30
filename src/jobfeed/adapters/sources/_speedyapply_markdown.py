"""Markdown table parser for the speedyapply GitHub job lists.

The speedyapply repo publishes a daily-updated markdown table of SWE intern +
new-grad postings. Each row is ``Company | Position | Location | [Salary] |
Posting | Age`` — the Salary column is present in the 6-col FAANG+ variant and
absent in the 5-col Other variant. The apply URL is embedded in the Posting
cell's anchor; closed listings carry a 🔒 emoji (skipped) and continuation rows
have an empty Company cell (skipped).

This module is pure: it never makes HTTP calls. Behavioral parity with legacy
``speedyapply._parse_markdown_rows``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

# Header/separator/row line shapes. The parser walks each table top-down and the
# header's column count tells us whether the Salary column is present.
_HEADER_RE = re.compile(r"^\|\s*Company\s*\|.*\|\s*Age\s*\|\s*$")
_SEPARATOR_RE = re.compile(r"^\|[\s|:\-]+\|\s*$")
_ROW_RE = re.compile(r"^\|.*\|\s*$")

# Cell-level helpers.
_STRONG_RE = re.compile(r"<strong>(.+?)</strong>", re.DOTALL)
_HREF_RE = re.compile(r'href="([^"]+)"')
_TAG_RE = re.compile(r"<[^>]+>")
_AGE_RE = re.compile(r"^(\d+)\s*d$", re.IGNORECASE)

_SALARY_COLUMN_COUNT = 6


@dataclass(frozen=True, kw_only=True)
class SpeedyRow:
    """One parsed speedyapply table row (pre-JD-enrichment)."""

    company: str
    title: str
    location: str
    apply_url: str
    posted_at: datetime | None

    @property
    def canonical_id(self) -> str:
        """Stable per-posting id derived from the apply URL.

        Hashing the URL collapses cross-table duplicates within one parse and
        keeps re-runs upserting into the same row. 16 hex chars = 64 bits of
        entropy, ample against the few thousand rows the repo lists.

        Returns:
            The ``"sa-"``-prefixed canonical id for this row's apply URL.
        """
        return canonical_id_for(self.apply_url)


def canonical_id_for(apply_url: str) -> str:
    """Derive the SpeedyApply canonical id for an apply URL.

    Args:
        apply_url: The row's apply URL.

    Returns:
        ``"sa-" + sha256(apply_url)[:16]``.
    """
    return "sa-" + hashlib.sha256(apply_url.encode("utf-8")).hexdigest()[:16]


def parse_rows(markdown: str, *, now: datetime) -> list[SpeedyRow]:
    """Parse all postable rows from a speedyapply markdown document.

    Walks the document line-by-line, tracking the active table's column count so
    cells map to fields whether or not the Salary column is present.

    Args:
        markdown: Raw markdown text of a speedyapply list.
        now: Reference time used to derive ``posted_at`` from the Age column.

    Returns:
        Parsed rows, in document order, excluding closed (🔒) and continuation
        rows. Time complexity O(L) over the L lines of the document.
    """
    out: list[SpeedyRow] = []
    column_count: int | None = None
    has_salary = False

    for line in markdown.splitlines():
        if _HEADER_RE.match(line):
            column_count = len(_split_row(line))
            has_salary = column_count == _SALARY_COLUMN_COUNT
            continue
        if _SEPARATOR_RE.match(line) or column_count is None:
            continue
        if not _ROW_RE.match(line):
            continue
        cells = _split_row(line)
        if len(cells) != column_count:
            continue  # malformed / continuation row
        row = _parse_row(cells, has_salary=has_salary, now=now)
        if row is not None:
            out.append(row)
    return out


def _split_row(line: str) -> list[str]:
    """Split a markdown table row into cells, dropping empty edge cells."""
    parts = line.split("|")
    if parts and parts[0].strip() == "":
        parts = parts[1:]
    if parts and parts[-1].strip() == "":
        parts = parts[:-1]
    return [part.strip() for part in parts]


def _parse_row(
    cells: list[str], *, has_salary: bool, now: datetime
) -> SpeedyRow | None:
    """Map one row's cells to a SpeedyRow, or None if closed/unparseable."""
    if has_salary:
        company_cell, position_cell, location_cell, _salary, posting_cell, age_cell = (
            cells
        )
    else:
        company_cell, position_cell, location_cell, posting_cell, age_cell = cells

    # Closed listings carry 🔒 (often in Age, sometimes on the company/position).
    if "🔒" in age_cell or "🔒" in company_cell or "🔒" in position_cell:
        return None

    # Continuation rows ("↳ same role, different location") have empty company.
    company = _extract_company(company_cell)
    if not company:
        return None

    apply_url = _extract_apply_url(posting_cell)
    if not apply_url:
        return None

    return SpeedyRow(
        company=company,
        title=_strip_html(position_cell),
        location=_strip_html(location_cell),
        apply_url=apply_url,
        posted_at=_parse_age(age_cell, now=now),
    )


def _extract_company(cell: str) -> str:
    """Pull the company name from a (usually ``<strong>``-wrapped) cell."""
    match = _STRONG_RE.search(cell)
    if match:
        return _strip_html(match.group(1))
    return _strip_html(cell)


def _strip_html(value: str) -> str:
    """Remove HTML tags and unescape the common ``&amp;`` entity."""
    return _TAG_RE.sub("", value).replace("&amp;", "&").strip()


def _extract_apply_url(cell: str) -> str | None:
    """Return the first ``href`` in the Posting cell anchor, or None."""
    match = _HREF_RE.search(cell)
    return match.group(1) if match else None


def _parse_age(cell: str, *, now: datetime) -> datetime | None:
    """Map the Age column to ``posted_at``: ``Nd`` → now - N days, else None."""
    match = _AGE_RE.match(_strip_html(cell))
    if match is None:
        return None
    return now - timedelta(days=int(match.group(1)))


__all__ = ["SpeedyRow", "canonical_id_for", "parse_rows"]
