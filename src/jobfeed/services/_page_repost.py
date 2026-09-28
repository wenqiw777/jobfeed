"""Conservative repost interpretation using existing page identity evidence."""

import re
from typing import Any
from urllib.parse import urlsplit

_EXPLICIT = re.compile(
    r"^Reposted (?:\d+ (?:minute|hour|day|week|month|year)s? ago"
    r"|yesterday|today|just now)$",
    re.I,
)
_MIN_REGION_DEPTH = 2
_MAX_REGION_BLOCKS = 12


def _posting_id(value: str) -> str | None:
    url = urlsplit(value)
    if url.hostname not in {"www.linkedin.com", "linkedin.com"}:
        return None
    match = re.fullmatch(r"/jobs/view/(\d+)/?", url.path)
    return match[1] if match else None


def target_region(
    target: dict[str, Any], snapshot: dict[str, Any]
) -> dict[str, Any] | None:
    """Scope only an unambiguous, bounded title/company header, never a whole page."""
    identity = _posting_id(str(target.get("url", "")))
    title, company = target.get("title"), target.get("company")
    blocks = snapshot.get("blocks", [])
    if (
        not identity
        or identity != _posting_id(str(snapshot.get("url", "")))
        or snapshot.get("truncated")
        or not title
        or not company
        or (target.get("id") is not None and str(target["id"]) != identity)
    ):
        return None
    anchors = [
        b
        for b in blocks
        if any(_posting_id(link) == identity for link in b.get("links", []))
    ]
    employers = [
        b
        for b in blocks
        if b.get("text", "").strip() == company.strip()
        and any(
            urlsplit(link).path.startswith("/company/")
            and urlsplit(link).hostname == "www.linkedin.com"
            for link in b.get("links", [])
        )
    ]
    if len(anchors) != 1 or len(employers) != 1:
        return None
    if anchors[0].get("text", "").strip() != title.strip():
        return None
    left, right = anchors[0].get("path", []), employers[0].get("path", [])
    common = []
    for a, b in zip(left, right, strict=False):
        if a != b:
            break
        common.append(a)
    if len(common) < _MIN_REGION_DEPTH:
        return None
    region = [b for b in blocks if b.get("path", [])[: len(common)] == common]
    # A broad ancestor containing the whole document is not identity ownership.
    if not _MIN_REGION_DEPTH <= len(region) <= _MAX_REGION_BLOCKS or len(region) == len(
        blocks
    ):
        return None
    if _contradicts(region, identity, title, company):
        return None
    return _normalize_region(region, common, {**snapshot, "title": title})


def _contradicts(
    region: list[dict[str, Any]], identity: str, title: str, company: str
) -> bool:
    for block in region:
        if (
            block.get("kind") == "heading"
            and block.get("text", "").strip() != title.strip()
        ):
            return True
        for link in block.get("links", []):
            other_id = _posting_id(link)
            if other_id and other_id != identity:
                return True
            if (
                urlsplit(link).path.startswith("/company/")
                and block.get("text", "").strip() != company.strip()
            ):
                return True
    return False


def _normalize_region(
    region: list[dict[str, Any]], common: list[int], snapshot: dict[str, Any]
) -> dict[str, Any]:
    # Stable local IDs/path tokens let unrelated recommendation DOM changes reuse
    # a model decision; all target-region text and links remain in the comparison.
    nodes: dict[int, int] = {}
    normalized = []
    for index, block in enumerate(region):
        path = []
        for node in block.get("path", [])[len(common) :]:
            if node not in nodes:
                nodes[node] = len(nodes)
            path.append(nodes[node])
        normalized.append({**block, "id": index, "path": path})
    return {
        "url": snapshot["url"],
        "title": snapshot.get("title", ""),
        "blocks": normalized,
        "truncated": False,
    }


def explicit_repost(region: dict[str, Any]) -> str | None:
    """Accept only a standalone metadata segment inside a proven target region."""
    evidence = [
        b["text"]
        for b in region["blocks"]
        if len(b.get("path", [])) == 1 and not b.get("links")
        if any(_EXPLICIT.fullmatch(part.strip()) for part in b["text"].split("·"))
    ]
    return evidence[0] if len(evidence) == 1 else None


def has_repost(snapshot: dict[str, Any]) -> bool:
    return any(
        re.search(r"\breposted\b", str(b.get("text", "")), re.I)
        for b in snapshot.get("blocks", [])
    )
