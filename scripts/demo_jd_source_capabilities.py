"""Read-only demo for capability-first job-description ingestion.

This demo deliberately stops before eligibility or priority scoring.  It shows
which facts can be trusted as structured source data and which text still needs
a semantic parser.  It never writes to the Jobfeed database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag


@dataclass(frozen=True)
class CompensationFact:
    minimum: int | float | None
    maximum: int | float | None
    currency: str | None
    interval: str | None
    kind: str
    source_path: str


@dataclass
class IngestionResult:
    source_kind: str
    source_url: str
    title: str | None = None
    sections: dict[str, list[str]] = field(default_factory=dict)
    compensation: list[CompensationFact] = field(default_factory=list)
    compensation_state: str = "requires_semantic_review"
    compensation_text_evidence: list[str] = field(default_factory=list)
    provenance: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _nonempty_lines(value: Any) -> list[str]:
    return [_clean(line) for line in str(value or "").splitlines() if _clean(line)]


def extract_ashby_job(job: dict[str, Any], source_url: str) -> IngestionResult:
    """Preserve Ashby's native compensation components without parsing prose."""
    facts: list[CompensationFact] = []
    compensation = job.get("compensation")
    if isinstance(compensation, dict):
        components = compensation.get("summaryComponents")
        if not isinstance(components, list):
            tiers = compensation.get("compensationTiers") or []
            components = [
                component
                for tier in tiers
                if isinstance(tier, dict)
                for component in (tier.get("components") or [])
            ]
        for index, component in enumerate(components or []):
            if not isinstance(component, dict):
                continue
            facts.append(
                CompensationFact(
                    minimum=component.get("minValue"),
                    maximum=component.get("maxValue"),
                    currency=component.get("currencyCode"),
                    interval=component.get("interval"),
                    kind=_clean(component.get("compensationType")) or "unknown",
                    source_path=f"compensation.components[{index}]",
                )
            )

    return IngestionResult(
        source_kind="known_api:ashby",
        source_url=source_url,
        title=_clean(job.get("title")) or None,
        sections={"description": _nonempty_lines(job.get("descriptionPlain"))},
        compensation=facts,
        compensation_state="present" if facts else "requires_semantic_review",
        provenance={
            "description": "descriptionPlain",
            "compensation": "compensation.summaryComponents",
        },
    )


def extract_lever_job(job: dict[str, Any], source_url: str) -> IngestionResult:
    """Preserve Lever's salaryRange and its native description list sections."""
    sections: dict[str, list[str]] = {}
    description = _nonempty_lines(job.get("descriptionPlain"))
    if description:
        sections["description"] = description
    for item in job.get("lists") or []:
        if not isinstance(item, dict):
            continue
        heading = _clean(item.get("text")) or "unnamed"
        body = BeautifulSoup(str(item.get("content") or ""), "html.parser").get_text(
            "\n", strip=True
        )
        lines = _nonempty_lines(body)
        if lines:
            sections.setdefault(heading, []).extend(lines)

    facts: list[CompensationFact] = []
    salary = job.get("salaryRange")
    if isinstance(salary, dict):
        facts.append(
            CompensationFact(
                minimum=salary.get("min"),
                maximum=salary.get("max"),
                currency=salary.get("currency"),
                interval=salary.get("interval"),
                kind="Salary",
                source_path="salaryRange",
            )
        )

    return IngestionResult(
        source_kind="known_api:lever",
        source_url=source_url,
        title=_clean(job.get("text")) or None,
        sections=sections,
        compensation=facts,
        compensation_state="present" if facts else "requires_semantic_review",
        provenance={
            "description": "descriptionPlain+lists",
            "compensation": "salaryRange",
        },
    )


def _walk_json(
    value: Any, path: tuple[str, ...] = ()
) -> Iterable[tuple[tuple[str, ...], Any]]:
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_json(child, (*path, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_json(child, (*path, str(index)))


def extract_json_ld(html: str, source_url: str) -> IngestionResult | None:
    """Use a JobPosting JSON-LD object when a page publishes one."""
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            payload = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        for path, value in _walk_json(payload):
            if not isinstance(value, dict):
                continue
            types = value.get("@type")
            type_names = types if isinstance(types, list) else [types]
            if "JobPosting" not in type_names:
                continue
            sections = {
                "description": _nonempty_lines(
                    BeautifulSoup(
                        str(value.get("description") or ""), "html.parser"
                    ).get_text("\n", strip=True)
                )
            }
            facts = _json_ld_compensation(
                value.get("baseSalary"), ".".join((*path, "baseSalary"))
            )
            return IngestionResult(
                source_kind="json_ld:JobPosting",
                source_url=source_url,
                title=_clean(value.get("title")) or None,
                sections=sections,
                compensation=facts,
                compensation_state="present" if facts else "requires_semantic_review",
                provenance={"job": ".".join(path) or "$"},
            )
    return None


def _json_ld_compensation(value: Any, source_path: str) -> list[CompensationFact]:
    if not isinstance(value, dict):
        return []
    amount = value.get("value")
    if isinstance(amount, (int, float)):
        minimum = maximum = amount
        interval = None
    elif isinstance(amount, dict):
        minimum = amount.get("minValue", amount.get("value"))
        maximum = amount.get("maxValue", amount.get("value"))
        interval = amount.get("unitText")
    else:
        return []
    return [
        CompensationFact(
            minimum=minimum,
            maximum=maximum,
            currency=value.get("currency"),
            interval=interval,
            kind="Salary",
            source_path=source_path,
        )
    ]


def _parse_json_parse_assignment(script_text: str) -> Any | None:
    marker = "JSON.parse("
    start = script_text.find(marker)
    if start < 0:
        return None
    encoded = script_text[start + len(marker) :].strip()
    if not encoded.endswith(");"):
        return None
    encoded = encoded[:-2]
    try:
        return json.loads(json.loads(encoded))
    except (json.JSONDecodeError, TypeError):
        return None


def extract_embedded_job_json(html: str, source_url: str) -> IngestionResult | None:
    """Find a job-shaped object in page hydration JSON, independent of hostname."""
    soup = BeautifulSoup(html, "html.parser")
    payloads: list[Any] = []
    for script in soup.find_all("script"):
        text = script.string or script.get_text()
        if not text:
            continue
        if script.get("type") == "application/json":
            with suppress(json.JSONDecodeError):
                payloads.append(json.loads(text))
        parsed = _parse_json_parse_assignment(text)
        if parsed is not None:
            payloads.append(parsed)

    for payload in payloads:
        for path, value in _walk_json(payload):
            if not isinstance(value, dict):
                continue
            if not value.get("postingTitle") or not (
                value.get("minimumQualifications") or value.get("description")
            ):
                continue
            sections = {
                key: _nonempty_lines(value.get(key))
                for key in (
                    "jobSummary",
                    "description",
                    "minimumQualifications",
                    "preferredQualifications",
                )
                if value.get(key)
            }
            return IngestionResult(
                source_kind="embedded_job_json",
                source_url=source_url,
                title=_clean(value.get("postingTitle")) or None,
                sections=sections,
                compensation_state="requires_semantic_review",
                provenance={"job": ".".join(path)},
            )
    return None


def extract_structured_html(html: str, source_url: str) -> IngestionResult:
    """Preserve heading boundaries; do not pretend prose is a parsed fact."""
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("h1") or soup.find("title")
    sections: dict[str, list[str]] = {}
    for heading in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        name = _clean(heading.get_text(" ", strip=True))
        if not name:
            continue
        lines: list[str] = []
        for sibling in heading.next_siblings:
            if isinstance(sibling, Tag) and sibling.name in {
                "h1",
                "h2",
                "h3",
                "h4",
                "h5",
                "h6",
            }:
                break
            if isinstance(sibling, Tag):
                lines.extend(_nonempty_lines(sibling.get_text("\n", strip=True)))
        if lines:
            sections.setdefault(name, []).extend(lines)

    evidence: list[str] = []
    for node in soup.find_all(["p", "li"]):
        text = _clean(node.get_text(" ", strip=True))
        lowered = text.lower()
        if text and (
            "$" in text or " usd " in f" {lowered} " or "salary range" in lowered
        ):
            evidence.append(text)

    return IngestionResult(
        source_kind="structured_html",
        source_url=source_url,
        title=_clean(title_tag.get_text(" ", strip=True)) if title_tag else None,
        sections=sections,
        compensation_state="text_present_unparsed"
        if evidence
        else "requires_semantic_review",
        compensation_text_evidence=evidence[:5],
        provenance={
            "sections": "HTML heading boundaries",
            "compensation": "text evidence only",
        },
    )


def extract_page(html: str, source_url: str) -> IngestionResult:
    """Choose the strongest structured capability actually present on the page."""
    return (
        extract_embedded_job_json(html, source_url)
        or extract_json_ld(html, source_url)
        or extract_structured_html(html, source_url)
    )


async def _get_json(client: httpx.AsyncClient, url: str) -> Any:
    response = await client.get(url)
    response.raise_for_status()
    return response.json()


async def _get_text(client: httpx.AsyncClient, url: str) -> str:
    response = await client.get(url)
    response.raise_for_status()
    return response.text


async def run_live_demo() -> list[IngestionResult]:
    """Exercise four current official sources without mutating application state."""
    ashby_url = (
        "https://api.ashbyhq.com/posting-api/job-board/openai?includeCompensation=true"
    )
    lever_url = "https://api.lever.co/v0/postings/shieldai?mode=json"
    apple_url = "https://jobs.apple.com/en-us/details/200664856-3337/software-engineer-siri-app-experiences"
    amazon_url = (
        "https://amazon.jobs/en/jobs/3177934/software-development-engineer-2026-us"
    )
    headers = {"User-Agent": "jobfeed-source-capability-demo/1.0"}
    async with httpx.AsyncClient(
        timeout=30, follow_redirects=True, headers=headers
    ) as client:
        ashby_raw, lever_raw, apple_html, amazon_html = await asyncio.gather(
            _get_json(client, ashby_url),
            _get_json(client, lever_url),
            _get_text(client, apple_url),
            _get_text(client, amazon_url),
        )

    ashby_job = next(job for job in ashby_raw["jobs"] if job.get("compensation"))
    lever_job = next(job for job in lever_raw if job.get("salaryRange"))
    return [
        extract_ashby_job(ashby_job, ashby_url),
        extract_lever_job(lever_job, lever_url),
        extract_page(apple_html, apple_url),
        extract_page(amazon_html, amazon_url),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="fetch four official sample sources"
    )
    args = parser.parse_args()
    if not args.live:
        parser.error("this read-only demo currently requires --live")
    print(
        json.dumps([item.to_dict() for item in asyncio.run(run_live_demo())], indent=2)
    )


if __name__ == "__main__":
    main()
