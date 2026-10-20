"""Vendor-native posting identities shared by source URL aliases."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

_UUID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"


@dataclass(frozen=True)
class ObservedIdentifier:
    """A vendor requisition observed at one concrete URL."""

    provider: str
    scope: str
    native_id: str
    observed_url: str


def observed_identifier(url: str) -> ObservedIdentifier | None:
    """Parse only URLs with a known concrete vendor requisition contract.

    Args:
        url: Observed application URL.

    Returns:
        Normalized observed source identifier, or None.
    """
    identity = external_identity(url)
    if not identity:
        return None
    provider, _, rest = identity.partition(":")
    if provider in {
        "workday",
        "phenom",
        "eightfold",
        "avature",
        "smartrecruiters",
        "talentbrew",
    }:
        scope, _, native_id = rest.rpartition(":")
        if not scope or not native_id:
            return None
    else:
        scope, native_id = "", rest
    return ObservedIdentifier(provider, scope, native_id, url)


def external_identity(url: str) -> str | None:  # noqa: C901 - explicit vendor URL contracts
    """Extract an explicit posting ID; unsupported URLs remain unclassified.

    Args:
        url: Observed application URL.

    Returns:
        Explicit vendor identity parsed from the URL, or None.
    """
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme in {"http", "https"}
            and parsed.hostname == "ad.doubleclick.net"
            and parsed.path.startswith("/ddm/clk/")
        ):
            # The observed click URL contains the destination after its first ?.
            # Unwrap once; never infer an identity from arbitrary query text.
            parsed = urlsplit(unquote(parsed.query))
        host = (parsed.hostname or "").lower()
        query = parse_qs(parsed.query)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    gh_id = query.get("gh_jid", [""])[0]
    if gh_id.isdecimal():
        return f"greenhouse:{gh_id}"
    if host in {
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
        "boards.eu.greenhouse.io",
    }:
        token = query.get("token", [""])[0]
        match = re.search(r"/jobs/(\d+)(?:/|$)", parsed.path)
        if match or token.isdecimal():
            return f"greenhouse:{match[1] if match else token}"
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        match = re.search(r"/jobs/view/(?:[^/]*-)?(\d+)(?:/|$)", parsed.path)
        if match:
            return f"linkedin:{match[1]}"
    for vendor, domain in (("ashby", "jobs.ashbyhq.com"), ("lever", "jobs.lever.co")):
        if host == domain:
            match = re.search(rf"/({_UUID})(?:/|$)", parsed.path)
            if match:
                return f"{vendor}:{match[1].lower()}"
    if host.endswith(".myworkdayjobs.com"):
        match = re.search(r"_([^/]+)(?:/apply)?/?$", parsed.path)
        if match and "/job/" in parsed.path:
            return f"workday:{host.split('.')[0]}:{match[1]}"
    if host in {"careers.southwestair.com", "jobs-us.pwc.com", "careers.serco-na.com"}:
        match = re.search(r"/us/en/job/([A-Za-z0-9]+)(?:/|$)", parsed.path)
        if match:
            return f"phenom:{host}:{match[1]}"
    if host.endswith(".eightfold.ai"):
        pid = query.get("pid", [""])[0]
        if parsed.path.rstrip("/") == "/careers" and pid.isdecimal():
            return f"eightfold:{host}:{pid}"
    destination = _application_destination_identity(host, parsed.path, query)
    if destination:
        return destination
    if host in {"jobright.ai", "www.jobright.ai"}:
        match = re.search(r"/jobs/info/([a-zA-Z0-9-]+)(?:/|$)", parsed.path)
        if match:
            return f"jobright:{match[1]}"
    if host == "joinhandshake.com" or host.endswith(".joinhandshake.com"):
        match = re.search(r"/jobs/(\d+)(?:/|$)", parsed.path)
        if match:
            return f"handshake:{match[1]}"
    return None


def _application_destination_identity(
    host: str, path: str, query: dict[str, list[str]]
) -> str | None:
    """Recognize tenant-scoped concrete applications, excluding career homepages."""
    if host.endswith(".avature.net") and path.rstrip("/") == "/Careers/RegisterMethod":
        folder_ids = query.get("folderId", [])
        if len(folder_ids) == 1 and folder_ids[0].isdecimal():
            return f"avature:{host}:{folder_ids[0]}"
    if host == "jobs.smartrecruiters.com":
        match = re.fullmatch(r"/([^/]+)/(\d+)(?:-[^/]+)?/?", path)
        if match:
            return f"smartrecruiters:{match[1].lower()}:{match[2]}"
    if host == "www.synchronycareers.com":
        match = re.fullmatch(r"/job/[^/]+/[^/]+/(\d+)/(\d+)/?", path)
        if match:
            return f"talentbrew:{host}:{match[1]}/{match[2]}"
    return None
