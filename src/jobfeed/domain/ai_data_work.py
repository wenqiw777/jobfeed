"""Deterministic exclusion of human AI-training and annotation contributor work."""

from __future__ import annotations

import re

AI_DATA_TITLE_PATTERN = (
    r"\b(?:(?:ai|llm|artificial[\s-]+intelligence)[\s-]+"
    r"(?:trainers?|training[\s-]+specialists?|(?:response[\s-]+)?(?:raters?|evaluators?))"
    r"|data[\s-]+(?:annotators?|labell?ers?|"
    r"(?:annotation|labell?ing)[\s-]+specialists?))\b"
)
AI_DATA_COMPANY_PATTERN = r"^\s*data[\s-]*annotation\s*$"
_TITLE = re.compile(AI_DATA_TITLE_PATTERN, re.IGNORECASE)
_COMPANY = re.compile(AI_DATA_COMPANY_PATTERN, re.IGNORECASE)


def ai_data_work_reason(title: str | None, company: str | None) -> str | None:
    """Identify explicit contributor titles and the verified DataAnnotation vendor.

    Args:
        title: Source posting title, including any AI Trainer suffix.
        company: Source employer name; only the exact vendor name is blocked.

    Returns:
        A filter reason for AI-data contributor work, otherwise None. JD mentions
        of training models or building annotation infrastructure are not signals.
    """
    if _COMPANY.fullmatch(company or ""):
        return "AI training/data annotation contributor work (DataAnnotation)"
    if match := _TITLE.search(title or ""):
        return f"AI training/data annotation contributor work ({match.group(0)})"
    return None
