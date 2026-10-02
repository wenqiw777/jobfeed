"""Pure target-job ownership and Apply-region extraction from observed HTML."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import unquote, urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from jobfeed.adapters.sources._linkedin_guest_parse import _external_apply_href
from jobfeed.domain.application_route import HopKind
from jobfeed.domain.external_identity import observed_identifier
from jobfeed.domain.models import JobPosting
from jobfeed.domain.normalize import normalize, normalize_company

_MIN_JD_CHARS = 300
_MAX_JD_WORDS = 5000
_MIN_JD_COVERAGE = 0.80
_EXCLUDED = re.compile(r"recommend|related|similar|other.jobs|talent|job.alert", re.I)
_APPLY = re.compile(r"\bapply\b|\bapplication\b|\boriginal job post(?:ing)?\b", re.I)
_REQUISITION = re.compile(
    r"\b(?:requisition|req|job)(?:\s*(?:id|number|#))?\s*[:#]\s*([A-Z0-9-]+)",
    re.I,
)


@dataclass(frozen=True)
class RouteDocument:
    """Current-job facts and observed navigation candidates."""

    title: str
    company: str
    description: str
    requisitions: tuple[str, ...]
    candidates: tuple[tuple[str, HopKind], ...]
    owned: bool
    ambiguous: bool = False
    has_job_facts: bool = False


def _job_blocks(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [block for item in value for block in _job_blocks(item)]
    if not isinstance(value, dict):
        return []
    types = value.get("@type", [])
    if types == "JobPosting" or (isinstance(types, list) and "JobPosting" in types):
        return [value]
    return _job_blocks(value.get("@graph", []))


def _text(value: Any) -> str:
    return (
        BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
        if isinstance(value, str)
        else ""
    )


def _requisition(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("value")
    return str(value).strip().casefold() if isinstance(value, (str, int)) else ""


def _internal_job_label(value: str, job: JobPosting) -> bool:
    """Distinguish a title-like internal label from a conflicting requisition."""
    words = set(normalize(job.title).split())
    return (
        len(words) > 1
        and bool(re.search(r"\s", value))
        and not re.match(r"^(?:req(?:uisition)?|job)(?:\s|[:#])", value, re.I)
        and words.issubset(normalize(value).split())
    )


def _excluded(node: Tag, requisitions: tuple[str, ...] = ()) -> bool:
    """Reject unrelated ancestor regions and conflicting job ID attributes.

    Time complexity: O(d * a) for ancestor depth d and four ID attributes a.
    """
    in_job_region = False
    for ancestor in (node, *node.parents):
        if not isinstance(ancestor, Tag):
            continue
        labels = " ".join(
            str(ancestor.get(name, "")) for name in ("id", "class", "aria-label")
        )
        if _EXCLUDED.search(labels):
            return True
        job_region = bool(re.search(r"job[-_ ]?(?:sidebar|apply|header)", labels, re.I))
        in_job_region = in_job_region or job_region
        if ancestor.name in {"nav", "footer"} or (
            ancestor.name in {"header", "aside"} and not in_job_region
        ):
            return True
        for name in ("data-job-id", "data-jobid", "data-requisition-id", "data-req-id"):
            req = str(ancestor.get(name, "")).casefold()
            if requisitions and req and req not in requisitions:
                return True
    return False


def _company_matches(company: str, job: JobPosting) -> bool:
    return bool(normalize_company(company)) and normalize_company(company) == (
        normalize_company(job.company)
    )


def _application_region(
    soup: BeautifulSoup, fragment: str, requisitions: tuple[str, ...]
) -> Tag | None:
    """Read an Apply anchor's current-job region if it already exists."""
    application = soup.find(id=unquote(fragment))
    if isinstance(application, Tag) and not _excluded(application, requisitions):
        return application
    return None


def _linkedin_job_matches(url: str, job: JobPosting) -> bool:
    """Require the observed LinkedIn detail URL to retain the source native ID."""
    parts = urlsplit(url)
    native = re.search(r"/jobs/view/(?:[^/]*-)?(\d+)/?$", parts.path)
    source = re.fullmatch(r"\D*(\d+)", job.canonical_id)
    return bool(native and source and native[1] == source[1])


def _linkedin_native_title(url: str, region: Tag, job: JobPosting) -> str:
    """Read the SDUI job header before its own Apply control, excluding the JD."""
    host = (urlsplit(url).hostname or "").casefold()
    if (
        host not in {"linkedin.com", "www.linkedin.com"}
        or not _linkedin_job_matches(url, job)
        or region.get("aria-label") != "Primary content"
    ):
        return ""
    header = []
    for node in region.find_all(["p", "a", "h1", "h2"]):
        if _excluded(node):
            continue
        if node.get("aria-label") == "Apply on company website":
            break
        if node.name == "h2":
            return ""
        header.append(node.get_text(" ", strip=True))
    else:
        return ""
    if not any(
        normalize_company(text) == normalize_company(job.company) for text in header
    ):
        return ""
    return next(
        (text for text in header if normalize(text) == normalize(job.title)), ""
    )


def parse_route_document(  # noqa: C901 - explicit job-owned evidence branches
    url: str, html: str, job: JobPosting
) -> RouteDocument:
    """Select only the source job's structured block/Apply region, never siblings.

    Args:
        url: Observed document URL, used to resolve relative navigation edges.
        html: Bounded fetched or rendered document.
        job: Source facts defining the target employer and job title.

    Returns:
        Target ownership facts and actual application navigation candidates.
    """
    soup = BeautifulSoup(html, "html.parser")
    blocks: list[dict[str, Any]] = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            blocks.extend(_job_blocks(json.loads(script.get_text())))
        except (ValueError, TypeError):
            continue
    matching = [
        b
        for b in blocks
        if isinstance(b.get("hiringOrganization"), dict)
        and normalize(str(b.get("title", ""))) == (normalize(job.title))
        and _company_matches(
            str((b.get("hiringOrganization") or {}).get("name", "")), job
        )
    ]
    if len(matching) > 1:
        return RouteDocument("", "", "", (), (), False, True)
    if blocks and not matching:
        return RouteDocument("", "", "", (), (), False, has_job_facts=True)
    block = matching[0] if matching else None
    title = str(block.get("title", "")) if block else ""
    company = str(block["hiringOrganization"].get("name", "")) if block else ""
    region = (
        (
            soup.select_one('section[aria-label="Primary content"]')
            if (urlsplit(url).hostname or "").casefold()
            in {"linkedin.com", "www.linkedin.com"}
            else None
        )
        or soup.select_one("main")
        or soup.select_one('[role="main"]')
        or soup
    )
    heading = region.select_one("h1")
    content = region.get_text(" ", strip=True)
    if not block:
        title = (
            heading.get_text(" ", strip=True)
            if heading
            else (_linkedin_native_title(url, region, job))
        )
        if f" {normalize_company(job.company)} " in f" {normalize(content)} ":
            company = job.company
    linkedin = (urlsplit(url).hostname or "").casefold() in {
        "linkedin.com",
        "www.linkedin.com",
    }
    owned = (
        normalize(title) == normalize(job.title)
        and _company_matches(company, job)
        and (not linkedin or _linkedin_job_matches(url, job))
    )
    description = _text(block.get("description")) if block else content
    req = _requisition(block.get("identifier")) if block else ""
    requisitions = tuple(
        dict.fromkeys(
            [req]
            if req
            else (match.casefold() for match in _REQUISITION.findall(content))
        )
    )
    candidates: list[tuple[str, HopKind]] = []
    base_tag = soup.find("base", href=True)
    base = urljoin(url, str(base_tag["href"])) if isinstance(base_tag, Tag) else url
    if owned:
        regions = [region]
        if block and len(blocks) == 1:
            regions.extend(
                soup.select(
                    ".job-sidebar, .job-apply, .job-header, .hero-content, "
                    "[data-job-id], [data-jobid], [data-requisition-id]"
                )
            )
        anchors = [anchor for owner in regions for anchor in owner.select("a[href]")]
        for anchor in anchors:
            label = " ".join(
                [anchor.get_text(" ", strip=True), str(anchor.get("aria-label", ""))]
            )
            if _excluded(anchor, requisitions) or not _APPLY.search(label):
                continue
            href = urljoin(base, str(anchor["href"]))
            target = _external_apply_href(href) if linkedin else href
            if not target:
                continue
            document, fragment = urldefrag(target)
            if fragment and document == urldefrag(url)[0]:
                # The current document may hold a dynamically loaded ATS.
                application = _application_region(soup, fragment, requisitions)
                regions.extend([application] if application is not None else [])
                continue
            candidates.append((target, "target_apply_link"))
        frames = [frame for owner in regions for frame in owner.select("iframe[src]")]
        for frame in frames:
            if _excluded(frame, requisitions):
                continue
            target = urljoin(base, str(frame["src"]))
            if observed_identifier(target):
                candidates.append((target, "target_embedded_job_url"))
    if owned and block and isinstance(block.get("url"), str):
        target = urljoin(base, block["url"])
        if target != url and observed_identifier(target):
            candidates.append((target, "target_embedded_job_url"))
    # Some careers sites use a human-readable internal JobPosting label and
    # expose the ATS's numeric identifier separately. Only use that metadata
    # when this job's observed application destination is actually Greenhouse.
    meta = soup.find("meta", attrs={"name": "JobIdentifier"})
    native_id = str(meta.get("content", "")).strip() if isinstance(meta, Tag) else ""
    greenhouse = any(
        identity is not None and identity.provider == "greenhouse"
        for target, _kind in candidates
        for identity in (observed_identifier(target),)
    )
    if owned and greenhouse and native_id.isdecimal():
        requisitions = tuple(
            dict.fromkeys(
                [
                    *(
                        value
                        for value in requisitions
                        if not _internal_job_label(value, job)
                    ),
                    native_id,
                ]
            )
        )
    return RouteDocument(
        title,
        company,
        description,
        requisitions,
        tuple(dict.fromkeys(candidates)),
        owned,
        has_job_facts=bool(blocks or heading),
    )


def verify_route_target(
    job: JobPosting, doc: RouteDocument, url: str, requisitions: set[str]
) -> str | None:
    """Return a failure reason unless final job facts corroborate this source.

    Args:
        job: Source facts and description requiring corroboration.
        doc: Independently observed final job facts.
        url: Observed concrete ATS destination URL.
        requisitions: Explicit IDs observed on preceding target job pages.

    Returns:
        Machine-readable rejection reason, or None when evidence agrees.
    """
    if not doc.owned or doc.ambiguous:
        return "target_ownership_missing"
    identity = observed_identifier(url)
    if identity is None:
        return "unsupported_ats_identity"
    final_ids = set(doc.requisitions)
    if (
        identity.provider == "workday"
        and final_ids
        and identity.native_id.casefold() not in final_ids
    ):
        return "target_requisition_mismatch"
    if requisitions and final_ids and not requisitions.intersection(final_ids):
        return "requisition_mismatch"
    # Observed wrapper IDs must agree with the explicit ATS requisition too.
    if (
        identity.provider == "workday"
        and requisitions
        and identity.native_id.casefold() not in requisitions
    ):
        return "requisition_mismatch"
    left, right = normalize(job.jd_text), normalize(doc.description)
    if min(len(left), len(right)) < _MIN_JD_CHARS:
        return "target_jd_missing"
    if max(len(left.split()), len(right.split())) > _MAX_JD_WORDS:
        return "target_jd_size_limit"
    matched = sum(
        b.size
        for b in SequenceMatcher(
            None, left.split(), right.split(), autojunk=False
        ).get_matching_blocks()
    )
    if matched / len(left.split()) < _MIN_JD_COVERAGE:
        return "target_jd_mismatch"
    return None
