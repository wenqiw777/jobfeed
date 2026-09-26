"""Company-background feeds used as evidence, never as eligibility gates."""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from jobfeed.domain.models_ops import CompanyIntelligenceMatch

COMPANY_INTELLIGENCE_SOURCES: dict[str, str] = {
    "sec": "https://www.sec.gov/files/company_tickers.json",
    "yc": "https://yc-oss.github.io/api/companies/all.json",
    "startup_portfolios": (
        "https://raw.githubusercontent.com/yigitmeteozcan/startups/main/data/all.json"
    ),
    "ai_startups_hiring": (
        "https://raw.githubusercontent.com/vinitshahdeo/"
        "awesome-ai-startups-hiring/main/README.md"
    ),
}

_CACHE_VERSION = 1
_AI_TABLE_ROW = re.compile(
    r"^\|\s*\d+\s*\|\s*(?:\[([^]]+)\]\((https?://[^)]+)\)|([^|]+))"
    r"\s*\|\s*([^|]+)\|",
    re.MULTILINE,
)
_COMPANY_SUFFIXES = frozenset(
    {
        "co",
        "company",
        "corp",
        "corporation",
        "inc",
        "llc",
        "ltd",
        "limited",
        "technologies",
    }
)
_PUBLIC_COMPANY_ALIASES = {
    "amazon": "amazon com",
    "facebook": "meta platforms",
    "google": "alphabet",
    "meta": "meta platforms",
}


@dataclass(frozen=True, kw_only=True)
class CompanySourceRecord:
    """One normalized fact row from one upstream company catalog."""

    source: str
    name: str
    website: str | None = None
    domain: str | None = None
    accelerator: str | None = None
    accelerator_batch: str | None = None
    yc_top_company: bool = False
    is_hiring: bool | None = None
    team_size: int | None = None
    claimed_unicorn: bool = False
    exited: bool | None = None
    category: str | None = None
    company_status: str | None = None
    public_company: bool = False
    watchlist_only: bool = False


@dataclass(frozen=True, kw_only=True)
class SourceSyncState:
    """Refresh outcome for one source."""

    status: Literal["fresh", "stale", "error"]
    record_count: int
    error: str | None = None


@dataclass(frozen=True, kw_only=True)
class CompanySyncReport:
    """Refresh outcomes plus the deduplicated company count."""

    sources: dict[str, SourceSyncState]
    company_count: int


class CompanyIntelligenceIndex:
    """In-memory exact-match index built from cached source records."""

    def __init__(
        self,
        matches: list[CompanyIntelligenceMatch],
        names: dict[str, CompanyIntelligenceMatch],
        domains: dict[str, CompanyIntelligenceMatch],
    ) -> None:
        self.matches = matches
        self._names = names
        self._domains = domains

    def lookup(
        self, company_name: str, *, website: str | None = None
    ) -> CompanyIntelligenceMatch | None:
        """Return an exact domain match, then an exact normalized-name match.

        Args:
            company_name: Employer name from the job posting.
            website: Optional official employer website.

        Returns:
            Merged evidence when the identity is unambiguous, otherwise None.
        """
        domain = company_domain(website)
        if domain is not None and domain in self._domains:
            return self._domains[domain]
        name = normalize_company_name(company_name)
        name = _PUBLIC_COMPANY_ALIASES.get(name, name)
        return self._names.get(name) if name else None


class CompanyIntelligenceCache:
    """Atomic local JSON cache retaining source-level refresh boundaries."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    def read_source_records(self) -> dict[str, list[CompanySourceRecord]]:
        """Read valid source rows; a missing cache is an empty cache.

        Returns:
            Source-keyed normalized company rows.

        Raises:
            ValueError: If the cache version or payload shape is invalid.
        """
        if not self.path.exists():
            return {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("version") != _CACHE_VERSION:
            raise ValueError("unsupported company intelligence cache version")
        result: dict[str, list[CompanySourceRecord]] = {}
        raw_sources = payload.get("source_records", {})
        if not isinstance(raw_sources, dict):
            raise ValueError("company intelligence source_records must be an object")
        for source, rows in raw_sources.items():
            if not isinstance(source, str) or not isinstance(rows, list):
                raise ValueError("invalid company intelligence source records")
            result[source] = [CompanySourceRecord(**row) for row in rows]
        return result

    def load_index(self) -> CompanyIntelligenceIndex:
        """Build the lookup index from the current cache.

        Returns:
            Exact-match lookup index over every cached source.
        """
        records = [
            record
            for source_rows in self.read_source_records().values()
            for record in source_rows
        ]
        return build_company_index(records)

    def write_source_records(
        self,
        records: dict[str, list[CompanySourceRecord]],
        *,
        fetched_at: datetime,
    ) -> None:
        """Atomically replace the cache after a successful partial refresh.

        Args:
            records: Complete source-keyed rows, including retained stale rows.
            fetched_at: Timestamp of this refresh attempt.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _CACHE_VERSION,
            "updated_at": fetched_at.astimezone(UTC).isoformat(),
            "source_records": {
                source: [asdict(record) for record in source_records]
                for source, source_records in sorted(records.items())
            },
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


class CompanyIntelligenceResolver:
    """Reload the local cache only when its file modification time changes."""

    def __init__(self, cache: CompanyIntelligenceCache) -> None:
        self._cache = cache
        self._mtime_ns: int | None = None
        self._index = CompanyIntelligenceIndex([], {}, {})

    def lookup(self, company_name: str) -> CompanyIntelligenceMatch | None:
        """Resolve one company against the latest locally synced snapshot.

        Args:
            company_name: Employer name from a job posting.

        Returns:
            Merged evidence when found, otherwise None.
        """
        try:
            mtime_ns = self._cache.path.stat().st_mtime_ns
        except FileNotFoundError:
            return None
        if mtime_ns != self._mtime_ns:
            self._index = self._cache.load_index()
            self._mtime_ns = mtime_ns
        return self._index.lookup(company_name)


class CompanyIntelligenceSync:
    """Fetch all company catalogs while preserving stale per-source data."""

    def __init__(
        self,
        cache: CompanyIntelligenceCache,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._cache = cache
        self._client = client

    async def sync(self) -> CompanySyncReport:
        """Refresh all sources concurrently and retain prior rows on failure.

        Returns:
            Per-source freshness states and deduplicated company count.

        Raises:
            RuntimeError: If all sources fail and no prior cache exists.
        """
        previous = self._cache.read_source_records()
        client = self._client or httpx.AsyncClient(timeout=60.0, follow_redirects=True)
        owns_client = self._client is None
        try:
            results = await asyncio.gather(
                *(
                    self._fetch_source(client, source)
                    for source in COMPANY_INTELLIGENCE_SOURCES
                ),
                return_exceptions=True,
            )
        finally:
            if owns_client:
                await client.aclose()

        current = dict(previous)
        states: dict[str, SourceSyncState] = {}
        for source, result in zip(COMPANY_INTELLIGENCE_SOURCES, results, strict=True):
            if isinstance(result, BaseException):
                stale_rows = previous.get(source, [])
                states[source] = SourceSyncState(
                    status="stale" if stale_rows else "error",
                    record_count=len(stale_rows),
                    error=str(result),
                )
                continue
            if not result:
                stale_rows = previous.get(source, [])
                states[source] = SourceSyncState(
                    status="stale" if stale_rows else "error",
                    record_count=len(stale_rows),
                    error="source returned zero usable companies",
                )
                continue
            current[source] = result
            states[source] = SourceSyncState(status="fresh", record_count=len(result))

        if not current:
            raise RuntimeError("all company intelligence sources failed")
        now = datetime.now(UTC)
        self._cache.write_source_records(current, fetched_at=now)
        index = build_company_index(
            [record for rows in current.values() for record in rows]
        )
        return CompanySyncReport(sources=states, company_count=len(index.matches))

    async def _fetch_source(
        self, client: httpx.AsyncClient, source: str
    ) -> list[CompanySourceRecord]:
        headers = (
            {"User-Agent": "Jobfeed personal job search wenqiwang@umich.edu"}
            if source == "sec"
            else None
        )
        response = await client.get(
            COMPANY_INTELLIGENCE_SOURCES[source], headers=headers
        )
        response.raise_for_status()
        if source == "sec":
            return parse_sec_companies(response.json())
        if source == "yc":
            return parse_yc_companies(response.json())
        if source == "startup_portfolios":
            return parse_startup_portfolios(response.json())
        return parse_ai_watchlist(response.text)


def parse_sec_companies(payload: object) -> list[CompanySourceRecord]:
    """Normalize the SEC's official company ticker directory.

    Args:
        payload: Decoded SEC company-ticker JSON object.

    Returns:
        Public-company identity records.

    Raises:
        ValueError: If the payload is not an object.
    """
    if not isinstance(payload, dict):
        raise ValueError("SEC company payload must be an object")
    records: list[CompanySourceRecord] = []
    for value in payload.values():
        if not isinstance(value, dict):
            continue
        name = _text(value.get("title"))
        if name:
            records.append(
                CompanySourceRecord(
                    source="sec",
                    name=name,
                    public_company=True,
                    company_status="SEC listed",
                )
            )
    return records


def parse_yc_companies(payload: object) -> list[CompanySourceRecord]:
    """Normalize the YC company API without inventing missing facts.

    Args:
        payload: Decoded upstream JSON payload.

    Returns:
        Valid YC company rows with explicit source provenance.
    """
    rows = _object_rows(payload)
    return [
        CompanySourceRecord(
            source="yc",
            name=name,
            website=_text(row.get("website")),
            domain=company_domain(_text(row.get("website"))),
            accelerator="Y Combinator",
            accelerator_batch=_text(row.get("batch")),
            yc_top_company=row.get("top_company") is True,
            is_hiring=_optional_bool(row.get("isHiring")),
            team_size=_optional_nonnegative_int(row.get("team_size")),
            category=_text(row.get("subindustry")) or _text(row.get("industry")),
            company_status=_text(row.get("status")),
        )
        for row in rows
        if (name := _text(row.get("name"))) is not None
    ]


def parse_startup_portfolios(payload: object) -> list[CompanySourceRecord]:
    """Normalize accelerator portfolios while labeling unicorn as a claim.

    Args:
        payload: Decoded upstream JSON payload.

    Returns:
        Valid portfolio rows without treating upstream claims as verified facts.
    """
    rows = _object_rows(payload)
    return [
        CompanySourceRecord(
            source="startup_portfolios",
            name=name,
            website=_text(row.get("website")),
            domain=company_domain(_text(row.get("website"))),
            accelerator=_text(row.get("program")) or _text(row.get("source")),
            claimed_unicorn=row.get("isUnicorn") is True,
            exited=_optional_bool(row.get("isExit")),
            category=_first_text(row.get("tags")),
        )
        for row in rows
        if (name := _text(row.get("name"))) is not None
    ]


def parse_ai_watchlist(markdown: str) -> list[CompanySourceRecord]:
    """Parse company/category only; intentionally discard unverified funding.

    Args:
        markdown: Raw watchlist README.

    Returns:
        Low-confidence discovery rows containing no funding values.
    """
    records: list[CompanySourceRecord] = []
    for match in _AI_TABLE_ROW.finditer(markdown):
        linked_name, website, plain_name, category = match.groups()
        name = _text(linked_name or plain_name)
        if name is None:
            continue
        records.append(
            CompanySourceRecord(
                source="ai_startups_hiring",
                name=name,
                website=_text(website),
                domain=company_domain(_text(website)),
                category=_text(category),
                watchlist_only=True,
            )
        )
    return records


def build_company_index(
    records: list[CompanySourceRecord],
) -> CompanyIntelligenceIndex:
    """Merge exact domain/name identities and build deterministic lookup maps.

    Args:
        records: Normalized rows from all successfully loaded sources.

    Returns:
        Index that rejects ambiguous names and prefers domain identity.

    Complexity:
        O(n) expected time and O(n) space for n source records.
    """
    grouped: dict[tuple[str, str], list[CompanySourceRecord]] = {}
    for record in records:
        name_key = normalize_company_name(record.name)
        if not name_key and not record.domain:
            continue
        identity = ("domain", record.domain) if record.domain else ("name", name_key)
        grouped.setdefault(identity, []).append(record)

    matches: list[CompanyIntelligenceMatch] = []
    name_candidates: dict[str, list[CompanyIntelligenceMatch]] = {}
    domains: dict[str, CompanyIntelligenceMatch] = {}
    for group in grouped.values():
        match = _merge_group(group)
        matches.append(match)
        for record in group:
            name = normalize_company_name(record.name)
            if name:
                name_candidates.setdefault(name, []).append(match)
            if record.domain:
                domains[record.domain] = match
    names = {
        name: candidates[0]
        for name, candidates in name_candidates.items()
        if len({id(candidate) for candidate in candidates}) == 1
    }
    return CompanyIntelligenceIndex(matches, names, domains)


def _merge_group(records: list[CompanySourceRecord]) -> CompanyIntelligenceMatch:
    sources = tuple(sorted({record.source for record in records}))
    domains = [record.domain for record in records if record.domain]
    team_sizes = [
        record.team_size for record in records if record.team_size is not None
    ]
    hiring_values = [
        record.is_hiring for record in records if record.is_hiring is not None
    ]
    exit_values = [record.exited for record in records if record.exited is not None]
    has_catalog = any(
        source in {"sec", "yc", "startup_portfolios"} for source in sources
    )
    return CompanyIntelligenceMatch(
        name=records[0].name,
        domain=domains[0] if domains else None,
        sources=sources,
        accelerators=tuple(
            sorted({record.accelerator for record in records if record.accelerator})
        ),
        accelerator_batches=tuple(
            sorted(
                {
                    record.accelerator_batch
                    for record in records
                    if record.accelerator_batch
                }
            )
        ),
        yc_top_company=any(record.yc_top_company for record in records),
        is_hiring=hiring_values[0] if hiring_values else None,
        team_size=max(team_sizes) if team_sizes else None,
        claimed_unicorn=any(record.claimed_unicorn for record in records),
        exited=any(exit_values) if exit_values else None,
        categories=tuple(
            sorted({record.category for record in records if record.category})
        ),
        company_statuses=tuple(
            sorted(
                {record.company_status for record in records if record.company_status}
            )
        ),
        public_company=any(record.public_company for record in records),
        confidence="medium" if has_catalog else "low",
        watchlist_only=not has_catalog,
    )


def normalize_company_name(value: str) -> str:
    """Normalize punctuation and legal suffixes for exact name matching.

    Args:
        value: Raw employer name.

    Returns:
        Lowercase whitespace-separated identity key.
    """
    words = re.findall(r"[a-z0-9]+", value.casefold())
    while len(words) > 1 and words[-1] in _COMPANY_SUFFIXES:
        words.pop()
    return " ".join(words)


def company_domain(value: str | None) -> str | None:
    """Return a lowercase host identity from an HTTP(S) company website.

    Args:
        value: Company website URL or hostname.

    Returns:
        Normalized hostname, or None for a blank value.
    """
    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    parsed = urlparse(candidate if "://" in candidate else f"https://{candidate}")
    host = (parsed.hostname or "").casefold().removeprefix("www.").rstrip(".")
    return host or None


def _object_rows(payload: object) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("company source payload must be a list")
    if not all(isinstance(row, dict) for row in payload):
        raise ValueError("company source rows must be objects")
    return payload


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _first_text(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    return next((cleaned for item in value if (cleaned := _text(item))), None)


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_nonnegative_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


__all__ = [
    "COMPANY_INTELLIGENCE_SOURCES",
    "CompanyIntelligenceCache",
    "CompanyIntelligenceIndex",
    "CompanyIntelligenceMatch",
    "CompanyIntelligenceResolver",
    "CompanyIntelligenceSync",
    "CompanySourceRecord",
    "CompanySyncReport",
    "SourceSyncState",
    "build_company_index",
    "company_domain",
    "normalize_company_name",
    "parse_ai_watchlist",
    "parse_startup_portfolios",
    "parse_yc_companies",
]
