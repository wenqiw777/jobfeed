"""Table parser for curated GitHub job lists.

The speedyapply repo publishes a daily-updated markdown table of SWE intern +
new-grad postings. Each row is ``Company | Position | Location | [Salary] |
Posting | Age`` — the Salary column is present in the 6-col FAANG+ variant and
absent in the 5-col Other variant. The apply URL is embedded in the Posting
cell's anchor; closed listings carry a 🔒 emoji (skipped) and continuation rows
have an empty Company cell (skipped).

The configured source also accepts the HTML table published by SimplifyJobs and
the alternate Markdown schema published by Jobright. This module is pure: it
never makes HTTP calls.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from bs4 import BeautifulSoup, Tag

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
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_JOBRIGHT_HEADER = ["Company", "Job Title", "Location", "Work Model", "Date Posted"]

_SALARY_COLUMN_COUNT = 6
_SIMPLIFY_COLUMN_COUNT = 5


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
    """Parse all postable rows from a supported GitHub list document.

    Walks the document line-by-line, tracking the active table's column count so
    cells map to fields whether or not the Salary column is present.

    Args:
        markdown: Raw markdown text of a speedyapply list.
        now: Reference time used to derive ``posted_at`` from the Age column.

    Returns:
        Parsed rows, in document order, excluding closed (🔒) and continuation
        rows. Time complexity O(L) over the L lines of the document.
    """
    if _is_simplify_document(markdown):
        return _parse_simplify_rows(markdown, now=now)
    if _is_jobright_document(markdown):
        return _parse_jobright_rows(markdown, now=now)

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


def _is_simplify_document(document: str) -> bool:
    """Return whether the document contains Simplify's HTML table schema."""
    return "<table" in document and "<th" in document and "Application</th>" in document


def _parse_simplify_rows(document: str, *, now: datetime) -> list[SpeedyRow]:
    """Parse SimplifyJobs HTML tables, retaining ↳ continuation postings.

    Time complexity is O(T * R), where T is the number of tables and R is the
    number of rows visited across those tables.
    """
    soup = BeautifulSoup(document, "html.parser")
    out: list[SpeedyRow] = []
    for table in soup.find_all("table"):
        if not isinstance(table, Tag):
            continue
        headers = [cell.get_text(" ", strip=True) for cell in table.find_all("th")]
        if headers[:5] != ["Company", "Role", "Location", "Application", "Age"]:
            continue
        previous_company: str | None = None
        for table_row in table.find_all("tr"):
            cells = table_row.find_all("td", recursive=False)
            if len(cells) != _SIMPLIFY_COLUMN_COUNT:
                continue
            company_cell, role_cell, location_cell, application_cell, age_cell = cells
            company_text = company_cell.get_text(" ", strip=True)
            if company_text == "↳":
                company = previous_company
            else:
                company = _strip_company_markers(company_text)
                previous_company = company or previous_company
            if not company or "🔒" in table_row.get_text(" ", strip=True):
                continue
            apply_url = _simplify_apply_url(application_cell)
            if apply_url is None:
                continue
            out.append(
                SpeedyRow(
                    company=company,
                    title=role_cell.get_text(" ", strip=True),
                    location=location_cell.get_text(" ", strip=True),
                    apply_url=apply_url,
                    posted_at=_parse_age(age_cell.get_text(" ", strip=True), now=now),
                )
            )
    return out


def _simplify_apply_url(application_cell: Tag) -> str | None:
    """Return the direct application URL, excluding Simplify's helper link."""
    for anchor in application_cell.find_all("a", href=True):
        image = anchor.find("img")
        if isinstance(image, Tag) and str(image.get("alt", "")).lower() == "apply":
            return str(anchor["href"])
    return None


def _strip_company_markers(company: str) -> str:
    """Remove list legend markers such as 🔥 while preserving the company."""
    return re.sub(r"^[^\w&]+", "", company).strip()


def _is_jobright_document(document: str) -> bool:
    """Return whether the document contains Jobright's Markdown schema."""
    header = "| " + " | ".join(_JOBRIGHT_HEADER) + " |"
    return header in document


def _parse_jobright_rows(document: str, *, now: datetime) -> list[SpeedyRow]:
    """Parse Jobright Markdown rows whose title cell owns the posting URL."""
    out: list[SpeedyRow] = []
    column_count: int | None = None
    for line in document.splitlines():
        if line.strip() == "| " + " | ".join(_JOBRIGHT_HEADER) + " |":
            column_count = len(_JOBRIGHT_HEADER)
            continue
        if column_count is None or _SEPARATOR_RE.match(line) or not _ROW_RE.match(line):
            continue
        cells = _split_row(line)
        if len(cells) != column_count:
            continue
        company_cell, title_cell, location_cell, _work_model, date_cell = cells
        company_match = _MARKDOWN_LINK_RE.search(company_cell)
        title_match = _MARKDOWN_LINK_RE.search(title_cell)
        if company_match is None or title_match is None or "🔒" in line:
            continue
        out.append(
            SpeedyRow(
                company=_strip_company_markers(company_match.group(1)),
                title=title_match.group(1).strip(),
                location=_strip_html(location_cell),
                apply_url=title_match.group(2).strip(),
                posted_at=_parse_calendar_date(date_cell, now=now),
            )
        )
    return out


def _parse_calendar_date(cell: str, *, now: datetime) -> datetime | None:
    """Parse ``Mon DD`` in the current cycle, rolling future dates back a year."""
    try:
        parsed = datetime.strptime(
            f"{_strip_html(cell)} {now.year}", "%b %d %Y"
        ).replace(tzinfo=now.tzinfo)
    except ValueError:
        return None
    if parsed > now:
        parsed = parsed.replace(year=parsed.year - 1)
    return parsed


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
