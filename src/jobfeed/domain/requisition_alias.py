"""Explicit Workday aliases verified against the official posting API."""

import json
import re
from collections.abc import Mapping

from jobfeed.domain.external_identity import ObservedIdentifier, observed_identifier

PREFIX = "workday-alias:"
Aliases = Mapping[tuple[str, str], str]


def verified_alias(url: str, requisition: str | None) -> tuple[str, str] | None:
    """Validate the shape of an API-attested Workday posting alias.

    Args:
        url: Observed Workday posting URL.
        requisition: Actual jobReqId from that posting's official CXS response.

    Returns:
        State key and canonical requisition, or None for unrelated IDs.
    """
    identity = observed_identifier(url)
    if (
        not identity
        or identity.provider != "workday"
        or not isinstance(requisition, str)
        or not requisition
    ):
        return None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", requisition):
        return None
    if not re.fullmatch(re.escape(requisition) + r"-\d+", identity.native_id):
        return None
    return PREFIX + identity.scope + ":" + identity.native_id, requisition


def receipt_alias(url: str, receipt: str) -> tuple[str, str] | None:
    """Accept an alias only from a verified application-route receipt.

    Args:
        url: ATS URL being committed in the same transaction.
        receipt: Serialized, fact-checked route outcome.

    Returns:
        Validated alias state entry, or None for other route outcomes.
    """
    try:
        data = json.loads(receipt)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("status") != "resolved" or data.get("ats_url") != url:
        return None
    return verified_alias(url, data.get("workday_requisition"))


def alias_id(provider: str, scope: str, native_id: str, aliases: Aliases) -> str:
    """Resolve one verified alias without interpreting arbitrary suffixes.

    Args:
        provider: ATS vendor name.
        scope: Tenant owning the requisition.
        native_id: Observed identifier.
        aliases: Explicit, verified tenant-scoped mappings.

    Returns:
        Canonical ID, or the unmodified observed ID.
    """
    return (
        aliases.get((scope, native_id), native_id)
        if provider == "workday"
        else native_id
    )


def canonical_identifier(
    item: ObservedIdentifier, aliases: Aliases
) -> ObservedIdentifier:
    """Retain observed URL provenance while resolving an attested ID.

    Args:
        item: Observed URL identity.
        aliases: Verified mappings from the store.

    Returns:
        Identity with canonical requisition and original evidence URL.
    """
    return ObservedIdentifier(
        item.provider,
        item.scope,
        alias_id(item.provider, item.scope, item.native_id, aliases),
        item.observed_url,
    )
