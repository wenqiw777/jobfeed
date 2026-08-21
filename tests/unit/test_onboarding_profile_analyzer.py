"""Prompt contract for useful résumé-derived onboarding suggestions."""

from jobfeed.onboarding_profile_analyzer import _SYSTEM_PROMPT, _user_prompt


def test_resume_analysis_prompt_requests_rich_search_profile() -> None:
    """The model gets field-specific guidance instead of defaulting to blanks."""
    prompt = _SYSTEM_PROMPT + _user_prompt("Graduates May 2027. Backend intern.")
    normalized = " ".join(prompt.split())

    assert "Never put a job title in seniority_levels" in normalized
    assert "descending relevance order" in normalized
    assert "Infer 3-6 plausible industries" in normalized
    assert "all three work modes" in normalized
    assert "startup, mid-size, and large" in normalized
    assert "Graduates May 2027. Backend intern." in prompt
