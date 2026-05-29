"""Deterministic mock LLM adapter for Phase 0 tests and local smoke runs."""

from __future__ import annotations

import json

from jobfeed.domain.models import LLMRequest, LLMResponse

TOKEN_COUNT = 100
TOKEN_SPLIT = TOKEN_COUNT // 2


class MockLLM:
    """LLMClient implementation that returns canonical deterministic JSON."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Complete a mock Stage A or Stage B request.

        Args:
            request: Adapter-neutral completion request.

        Returns:
            Adapter-neutral mock response with deterministic JSON content.

        Raises:
            ValueError: If the requested model is not a mock stage model.
        """
        if "stage-a" in request.model:
            content = _stage_a_content()
        elif "stage-b" in request.model:
            content = _stage_b_content()
        else:
            raise ValueError(f"unsupported mock model: {request.model}")
        return LLMResponse(
            content=content,
            model=request.model,
            input_tokens=TOKEN_SPLIT,
            output_tokens=TOKEN_SPLIT,
            cost_usd=0.0,
            cached=False,
        )


def _stage_a_content() -> str:
    return json.dumps(
        {
            "score": 75,
            "one_line": "Mock evaluation",
            "timing_eligible": "eligible",
        }
    )


def _stage_b_content() -> str:
    return json.dumps(
        {
            "verdict": {
                "recommendation": "consider",
                "confidence": 3,
                "one_line": "Mock verdict for testing.",
            },
            "jd_summary": {
                "role_in_3_lines": "Mock JD summary",
                "must_haves": ["Python"],
                "nice_to_haves": [],
                "red_flags_in_jd": [],
            },
            "fit_analysis": {
                "score_0_100": 72,
                "strong_match": [
                    {
                        "requirement": "Python backend services",
                        "evidence_from_resume": (
                            "Resume shows production Python work."
                        ),
                    }
                ],
                "gaps": [
                    {
                        "requirement": "Company-specific systems",
                        "severity": "minor",
                        "mitigation": ("Connect prior automation work to the role."),
                    }
                ],
            },
            "resume_hooks": {
                "lead_with": "Mention the local-first job pipeline.",
                "supporting": [],
                "avoid_mentioning": [],
            },
        }
    )


__all__ = ["MockLLM"]
