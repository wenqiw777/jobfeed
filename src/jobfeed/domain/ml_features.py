"""Pure rule-based ML-gate feature extraction + deterministic hard-fail rules.

A numpy-free port of the legacy ``jobfeed.ml_gate`` extractor / rules / vocab.
``extract_features`` mirrors the legacy ``extractor.extract`` field-for-field;
``hard_fail_reason`` mirrors ``rules.hard_fail_from_extracted`` (exact reason
strings, legacy order, keyed on ``clearance_status``). The ordered vocab /
layout constants here are the single source of truth for the later numpy
vectorizer and the gate boundary; ``clearance_required`` /
``school_restricted`` stay ``int`` 0/1 to match legacy, while ``is_swe_role``
is surfaced as ``bool`` (int->bool happens here, at the dataclass boundary).
The bulky compiled regex tables live in the private ``_ml_patterns`` sibling
only to respect the domain file-length gate.
"""

from __future__ import annotations

from dataclasses import dataclass

from jobfeed.domain import _ml_patterns as _pat

# ---------------------------------------------------------------------------
# Ordered vocabulary / structured-layout constants (verbatim from legacy
# features.py). The vectorize half (embedding) is intentionally NOT here.
# Structured layout: seniority[0:5] degree[5:9] clearance[9] school[10]
# role[11:16] yoe[16] domains[17:26] techs[26:65] is_swe[65].
# ---------------------------------------------------------------------------

SENIORITY_LEVELS = ["entry", "mid", "senior", "lead", "unknown"]
DEGREE_LEVELS = ["phd", "masters", "bachelors", "none"]
ROLE_TYPES = ["intern", "coop", "new_grad", "fte", "contract"]

# Compact vocab rows keep the layout legible and the module within the domain
# file-length gate; fmt: off stops the formatter exploding them one-per-line.
# fmt: off
DOMAIN_NAMES = [
    "embedded", "robotics", "aerospace", "hardware", "trading",
    "manufacturing", "security", "mobile", "gamedev",
]

TECH_NAMES = [
    "c++", "c#", "typescript", "javascript", "python", "java", "rust",
    "go", "ruby", "scala", "kotlin", "swift", "c", "sql", "postgresql",
    "mysql", "mongodb", "redis", "kafka", "elasticsearch", "docker",
    "kubernetes", "aws", "gcp", "azure", "terraform", "react", "node",
    "django", "flask", "spring", "pytorch", "tensorflow", "cuda",
    "spark", "flink", "airflow", "graphql", "grpc",
]
# fmt: on

assert len(TECH_NAMES) == 39  # noqa: PLR2004 (legacy-pinned vocab width)
assert len(DOMAIN_NAMES) == 9  # noqa: PLR2004 (legacy-pinned vocab width)

# Vocab order is load-bearing for the structured-vector layout: these literal
# name lists MUST stay in lockstep with the pattern tables they index, so a
# future edit that desyncs name-order vs pattern-order fails loudly at import.
assert [name for name, _ in _pat._TECH_PATTERNS] == TECH_NAMES
assert [name for name, _ in _pat._DOMAIN_PATTERNS] == DOMAIN_NAMES

# Per-block widths -> structured dimension. The named sum is asserted equal to
# STRUCTURED_DIM so a vocab edit that desyncs the layout fails loudly.
_DIM_SENIORITY = len(SENIORITY_LEVELS)  # 5
_DIM_DEGREE = len(DEGREE_LEVELS)  # 4
_DIM_CLEARANCE = 1
_DIM_SCHOOL = 1
_DIM_ROLE = len(ROLE_TYPES)  # 5
_DIM_YOE = 1
_DIM_DOMAINS = len(DOMAIN_NAMES)  # 9
_DIM_TECHS = len(TECH_NAMES)  # 39
_DIM_IS_SWE = 1

STRUCTURED_DIM: int = (
    _DIM_SENIORITY
    + _DIM_DEGREE
    + _DIM_CLEARANCE
    + _DIM_SCHOOL
    + _DIM_ROLE
    + _DIM_YOE
    + _DIM_DOMAINS
    + _DIM_TECHS
    + _DIM_IS_SWE
)
assert STRUCTURED_DIM == 5 + 4 + 1 + 1 + 5 + 1 + 9 + 39 + 1
assert STRUCTURED_DIM == 66  # noqa: PLR2004 (legacy-pinned layout width)


@dataclass(frozen=True)
class MLGateFeatures:
    """Structured rule-based features for one job posting (numpy-free)."""

    seniority_level: str
    degree_required: str
    clearance_required: int
    clearance_status: str
    school_restricted: int
    domain_tags: list[str]
    tech_required: list[str]
    role_type: str
    yoe_min: int | None
    is_swe_role: bool


# ---------------------------------------------------------------------------
# Internal helpers (private -> exempt from public-docstring gate).
# ---------------------------------------------------------------------------


def _extract_yoe_values(text: str) -> list[int]:
    values: list[int] = []
    for match in _pat._RE_YOE.finditer(text):
        raw = match.group(1) or match.group(2) or match.group(3) or match.group(4)
        if raw is not None:
            value = int(raw)
            if value <= _pat._MAX_CANDIDATE_YOE:
                values.append(value)
    return sorted(values)


def _seniority_from_title(title: str) -> str | None:
    if _pat._RE_LEAD.search(title):
        return "lead"
    if _pat._RE_SENIOR.search(title):
        return "senior"
    if _pat._RE_MID_NUMERAL.search(title):
        return "mid"
    if _pat._RE_ENTRY.search(title):
        return "entry"
    return None


def _seniority_from_jd(jd_text: str) -> str | None:
    values = _extract_yoe_values(jd_text)
    if not values:
        return None
    if values[0] >= _pat._SENIOR_YOE:
        return "senior"
    if values[0] >= _pat._MID_YOE:
        return "mid"
    return None


def _seniority_level(title: str, jd_text: str) -> str:
    return _seniority_from_title(title) or _seniority_from_jd(jd_text) or "unknown"


def _degree_required(jd_text: str) -> str:
    if _pat._RE_PHD.search(jd_text):
        return "phd"
    if _pat._RE_MASTERS.search(jd_text):
        return "masters"
    if _pat._RE_BACHELORS.search(jd_text):
        return "bachelors"
    return "none"


def _clearance_required(jd_text: str) -> int:
    return 1 if _pat._RE_CLEARANCE.search(jd_text) else 0


def _clearance_status(jd_text: str) -> str:
    if not _pat._RE_CLEARANCE.search(jd_text):
        return "none"
    if _pat._RE_CLEARANCE_OBTAINABLE.search(jd_text):
        return "obtainable"
    if _pat._RE_CLEARANCE_ACTIVE.search(jd_text):
        return "active_required"
    return "ambiguous"


def _school_restricted(jd_text: str) -> int:
    return 1 if _pat._RE_SCHOOL_RESTRICTED.search(jd_text) else 0


def _domain_tags(title: str, jd_text: str) -> list[str]:
    combined = f"{title} {jd_text}"
    return [tag for tag, pat in _pat._DOMAIN_PATTERNS if pat.search(combined)]


def _tech_required(jd_text: str) -> list[str]:
    return [tech for tech, pat in _pat._TECH_PATTERNS if pat.search(jd_text)]


def _role_type(title: str, jd_text: str) -> str:
    combined = f"{title} {jd_text}"
    if _pat._RE_ROLE_INTERN.search(combined):
        return "intern"
    if _pat._RE_ROLE_COOP.search(combined):
        return "coop"
    if _pat._RE_ROLE_NEW_GRAD.search(combined):
        return "new_grad"
    if _pat._RE_ROLE_CONTRACT.search(combined):
        return "contract"
    return "fte"


def _yoe_min(jd_text: str) -> int | None:
    values = _extract_yoe_values(jd_text)
    return values[0] if values else None


def _is_swe_role(title: str, jd_text: str) -> bool:
    title_lower = title.lower()
    if _pat._SWE_TITLE_STRONG.search(title_lower):
        return True
    if _pat._SWE_TITLE_NEG.search(title_lower):
        return False
    if _pat._SWE_TITLE_POS.search(title_lower):
        return True
    sample = jd_text[: _pat._SWE_JD_SAMPLE_CHARS]
    return len(_pat._SWE_JD_SIGNALS.findall(sample)) >= _pat._SWE_JD_MIN_SIGNALS


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_features(title: str, jd_text: str) -> MLGateFeatures:
    """Extract structured rule-based features from a title and JD body.

    Faithful port of the legacy ``extractor.extract``: seniority falls back
    from title to YoE-derived JD signal to ``"unknown"``; ``is_swe_role`` is
    the boolean form of the legacy 0/1 verdict.

    Args:
        title: Job title (e.g. "Senior Software Engineer").
        jd_text: Full job-description text.

    Returns:
        The structured ``MLGateFeatures`` record for the posting.
    """
    return MLGateFeatures(
        seniority_level=_seniority_level(title, jd_text),
        degree_required=_degree_required(jd_text),
        clearance_required=_clearance_required(jd_text),
        clearance_status=_clearance_status(jd_text),
        school_restricted=_school_restricted(jd_text),
        domain_tags=_domain_tags(title, jd_text),
        tech_required=_tech_required(jd_text),
        role_type=_role_type(title, jd_text),
        yoe_min=_yoe_min(jd_text),
        is_swe_role=_is_swe_role(title, jd_text),
    )


def hard_fail_reason(features: MLGateFeatures) -> str | None:
    """Return a deterministic hard-fail reason, or ``None`` when the role passes.

    Faithful port of legacy ``rules.hard_fail_from_extracted`` with the exact
    reason strings and order: a ``yoe_min`` at/above 2 fails first, then an
    active/ambiguous clearance (keyed on ``clearance_status``), then a
    non-software role. Hard requirements deliberately bypass any model score.

    Args:
        features: Extracted structured features for the posting.

    Returns:
        The first violated rule's reason string, or ``None`` when none fire.
    """
    yoe = features.yoe_min
    if yoe is not None and int(yoe) >= _pat._MID_YOE:
        return f"yoe_min >= {int(yoe)}"
    if features.clearance_status in ("active_required", "ambiguous"):
        return "active clearance required"
    if not features.is_swe_role:
        return "not software engineering role"
    return None


__all__ = [
    "DEGREE_LEVELS",
    "DOMAIN_NAMES",
    "ROLE_TYPES",
    "SENIORITY_LEVELS",
    "STRUCTURED_DIM",
    "TECH_NAMES",
    "MLGateFeatures",
    "extract_features",
    "hard_fail_reason",
]
