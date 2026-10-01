"""Provider-side output shape matching the canonical Stage B parser."""


def _object(properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def stage_b_response_schema() -> dict[str, object]:
    """Require every Stage B field without generating missing evaluation content."""
    text = {"type": "string"}
    texts = {"type": "array", "items": text}
    return _object(
        {
            "verdict": _object(
                {
                    "recommendation": {
                        "type": "string",
                        "enum": ["apply", "consider", "skip"],
                    },
                    "confidence": {"type": "integer", "minimum": 1, "maximum": 5},
                    "one_line": text,
                }
            ),
            "jd_summary": _object(
                {
                    "role_in_3_lines": text,
                    "must_haves": texts,
                    "nice_to_haves": texts,
                    "red_flags_in_jd": texts,
                }
            ),
            "fit_analysis": _object(
                {
                    "strong_match": {
                        "type": "array",
                        "items": _object(
                            {
                                "requirement": text,
                                "evidence_from_resume": text,
                            }
                        ),
                    },
                    "gaps": {
                        "type": "array",
                        "items": _object(
                            {
                                "requirement": text,
                                "severity": {
                                    "type": "string",
                                    "enum": ["blocker", "notable", "minor"],
                                },
                                "mitigation": {"type": ["string", "null"]},
                            }
                        ),
                    },
                    "score_0_100": {"type": "integer", "minimum": 0, "maximum": 100},
                }
            ),
            "resume_hooks": _object(
                {
                    "lead_with": text,
                    "supporting": texts,
                    "avoid_mentioning": texts,
                }
            ),
        }
    )
