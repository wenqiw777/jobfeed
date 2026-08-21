"""Pure rule-based ML-gate feature extraction + deterministic hard-fail rules.

A numpy-free port of the legacy ``jobfeed.ml_gate`` extractor / rules / vocab.
``extract_features`` mirrors the legacy ``extractor.extract`` field-for-field;
``hard_fail_reason`` mirrors ``rules.hard_fail_from_extracted`` (exact reason
strings, legacy order, keyed on ``clearance_status``). The ordered vocab /
layout constants here are the single source of truth for the later numpy
vectorizer and the gate boundary; ``clearance_required`` /
``school_restricted`` stay ``int`` 0/1 to match legacy, while ``is_swe_role``
is surfaced as ``bool`` (int->bool happens here, at the dataclass boundary).

The compiled regex tables live inline below (formerly the private
``_ml_patterns`` sibling, merged back so the vocab name lists and the pattern
tables they index stay in one file — the prior cross-file lockstep asserts are
now in-module invariants). Pattern ORDER is load-bearing: ``_DOMAIN_PATTERNS``
/ ``_TECH_PATTERNS`` mirror ``DOMAIN_NAMES`` / ``TECH_NAMES``, and longer tech
tokens precede shorter ones so partial matches never win. This file is
intentionally exempt from the 300-line gate (see tests/support/code_hygiene.py)
because shredding the vocab/pattern tables across files harms readability.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_IC = re.IGNORECASE
_VB = re.IGNORECASE | re.VERBOSE

# --- numeric thresholds shared with the extractor ---
_MAX_CANDIDATE_YOE = 20
_SENIOR_YOE = 5
_MID_YOE = 2
_HARD_FAIL_YOE = 3
_SWE_JD_SAMPLE_CHARS = 3000
_SWE_JD_MIN_SIGNALS = 3

# --- seniority ---
_RE_LEAD = re.compile(
    r"\b(tech\s+lead|team\s+lead|lead\s+(?:engineer|developer|architect"
    r"|software|backend|frontend))\b",
    _IC,
)
_RE_SENIOR = re.compile(r"\b(senior|sr\.?|staff|principal)\b", _IC)
_RE_MID_NUMERAL = re.compile(r"\bengineer\s+(?:II|III|IV)\b", _IC)
_RE_ENTRY = re.compile(
    r"\b(intern(?:ship)?|new[\s\-]grad(?:uate)?|junior|jr\.?|entry[\s\-]level)\b",
    _IC,
)

# --- YoE (drives yoe_min and the seniority fallback) ---
_RE_YOE = re.compile(
    r"(?:"
    r"(?:minimum|at\s+least|minimum\s+of)\s+(\d+)\s+years?"
    r"|"
    r"(\d+)\s*\+\s*years?"
    r"|"
    r"(\d+)\s*[-–]\s*\d+\s*years?"  # noqa: RUF001 (legacy en-dash range)
    r"|"
    r"(\d+)\s+years?\s+(?:of\s+)?(?:professional\s+)?(?:experience|work)"
    r")",
    _VB,
)

# --- degree ---
_RE_PHD = re.compile(r"\b(ph\.?\s*d\.?|doctoral|doctorate|d\.phil\.?)\b", _IC)
_RE_MASTERS = re.compile(
    r"\b(master(?:'?s)?(?:\s+degree)?|m\.?\s*s\.?|m\.?\s*eng\.?|mba)\b", _IC
)
_RE_BACHELORS = re.compile(
    r"\b(bachelor(?:'?s)?(?:\s+degree)?|b\.?\s*s\.?|b\.?\s*a\.?|b\.?\s*eng\.?"
    r"|undergraduate\s+degree)\b",
    _IC,
)

# --- clearance ---
_RE_CLEARANCE = re.compile(
    r"""
    \b(
        TS/SCI
        | top\s+secret
        | secret\s+clearance
        | security\s+clearance
        | dod\s+clearance
        | department\s+of\s+defense\s+clearance
        | must\s+hold\s+an?\s+active
        | active\s+(?:top\s+secret|secret|ts|clearance)
        | clearance\s+required
        | clearance\s+is\s+required
        | obtain\s+a\s+(?:security\s+)?clearance
    )
    """,
    _VB,
)
_RE_CLEARANCE_OBTAINABLE = re.compile(
    r"""
    \b(
        able\s+to\s+obtain
        | ability\s+to\s+obtain
        | eligible\s+to\s+obtain
        | can\s+obtain
        | obtain\s+and\s+maintain
        | obtain\s+a\s+(?:security\s+)?clearance
        | sponsor(?:ed|ship)?\s+(?:for\s+)?(?:a\s+)?(?:security\s+)?clearance
        | subject\s+to\s+a\s+government\s+security\s+investigation
    )
    """,
    _VB,
)
_RE_CLEARANCE_ACTIVE = re.compile(
    r"""
    \b(
        active\s+(?:top\s+secret|secret|ts/sci|clearance)
        | existing\s+(?:top\s+secret|secret|ts/sci|clearance)
        | current(?:ly)?\s+(?:possess|hold)
        | must\s+(?:currently\s+)?(?:possess|hold)\s+(?:an?\s+)?(?:active\s+)?
          (?:top\s+secret|secret|ts/sci|security\s+clearance|clearance)
        | already\s+(?:possess|hold|have)
        | active\s+and\s+(?:existing|transferable)
    )
    """,
    _VB,
)

# --- school restriction ---
_RE_SCHOOL_RESTRICTED = re.compile(
    r"""
    \b(
        must\s+be\s+enrolled\s+at
        | restricted\s+to\s+students\s+at
        | Grandes?\s+[EÉ]coles?
        | skillbridge
        | veteran[\s\-]only
        | military\s+spouse[\s\-]only
    )
    """,
    _VB,
)

# --- role type ---
_RE_ROLE_INTERN = re.compile(
    r"\b(intern(?:ship)?|summer\s+intern(?:ship)?|internship\s+program)\b", _IC
)
_RE_ROLE_COOP = re.compile(r"\b(co[\s\-]?op)\b", _IC)
_RE_ROLE_NEW_GRAD = re.compile(
    r"\b(new[\s\-]grad(?:uate)?|recent\s+grad(?:uate)?|new\s+graduates?)\b", _IC
)
_RE_ROLE_CONTRACT = re.compile(
    r"\b(contract(?:or)?|contractor|contract\s+position|contract\s+role"
    r"|w2\s+contract|c2c)\b",
    _IC,
)

# --- domain tags (order == DOMAIN_NAMES) ---
# Long regex strings are wrapped via adjacent-literal concatenation (compile-
# time identical to the legacy single-line patterns); fmt: off keeps the table
# compact and inside the 88-col / file-length gates without per-line noqa.
# fmt: off
_DOMAIN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("embedded", re.compile(
        r"\b(embedded|firmware|rtos|bare[\s\-]metal|microcontroller|mcu"
        r"|fpga)\b", _IC)),
    ("robotics", re.compile(
        r"\b(robot(?:ics?)?|ros\b|autonomous\s+(?:vehicle|robot)"
        r"|mechatronics)\b", _IC)),
    ("aerospace", re.compile(
        r"\b(aerospace|avionics|satellite|spacecraft|launch\s+vehicle"
        r"|space\s+systems?)\b", _IC)),
    ("hardware", re.compile(
        r"\b(hardware|asic|vlsi|pcb|chip\s+design|silicon|analog"
        r"|digital\s+circuit)\b", _IC)),
    ("trading", re.compile(
        r"\b(trading|quantitative|quant\b|hft|high[\s\-]frequency"
        r"|market[\s\-]making|algorithmic\s+trading|prop\s+trading)\b", _IC)),
    ("manufacturing", re.compile(
        r"\b(manufacturing|factory\s+automation|industrial\s+control|plc\b"
        r"|scada|mes\b|supply\s+chain\s+systems?)\b", _IC)),
    ("security", re.compile(
        r"\b((?:cyber[\s\-]?)?security|penetration\s+testing|pen\s+test"
        r"|vulnerability|malware|soc\b|threat\s+(?:intel|hunting)|red\s+team"
        r"|blue\s+team|appsec|infosec)\b", _IC)),
    ("mobile", re.compile(
        r"\b(mobile|ios\b|android\b|swift\b|kotlin\b|react\s+native"
        r"|flutter)\b", _IC)),
    ("gamedev", re.compile(
        r"\b(game\s+dev(?:elopment)?|game\s+engine|unity\b|unreal\b"
        r"|unreal\s+engine|gaming\s+(?:studio|software)|aaa\s+game)\b", _IC)),
]
# fmt: on

# --- tech required (order == TECH_NAMES; longer tokens precede shorter) ---
_TECH_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("c++", re.compile(r"(?<!\w)C\+\+(?!\w)")),
    ("c#", re.compile(r"\bC#\b")),
    ("typescript", re.compile(r"\bTypeScript\b", _IC)),
    ("javascript", re.compile(r"\bJavaScript\b|(?<!\w)JS\b", _IC)),
    ("python", re.compile(r"\bPython\b", _IC)),
    ("java", re.compile(r"\bJava\b(?!\s*Script)", _IC)),
    ("rust", re.compile(r"\bRust\b", _IC)),
    ("go", re.compile(r"\b(?:Go|Golang)\b", _IC)),
    ("ruby", re.compile(r"\bRuby\b", _IC)),
    ("scala", re.compile(r"\bScala\b", _IC)),
    ("kotlin", re.compile(r"\bKotlin\b", _IC)),
    ("swift", re.compile(r"\bSwift\b", _IC)),
    ("c", re.compile(r"(?<!\w)C(?!\+\+|#|\w)", _IC)),
    ("sql", re.compile(r"\bSQL\b", _IC)),
    ("postgresql", re.compile(r"\bPostgre(?:SQL|s)\b", _IC)),
    ("mysql", re.compile(r"\bMySQL\b", _IC)),
    ("mongodb", re.compile(r"\bMongoDB\b", _IC)),
    ("redis", re.compile(r"\bRedis\b", _IC)),
    ("kafka", re.compile(r"\bKafka\b", _IC)),
    ("elasticsearch", re.compile(r"\bElasticsearch\b", _IC)),
    ("docker", re.compile(r"\bDocker\b", _IC)),
    ("kubernetes", re.compile(r"\bKubernetes\b|K8s\b", _IC)),
    ("aws", re.compile(r"\bAWS\b", _IC)),
    ("gcp", re.compile(r"\bGCP\b|Google\s+Cloud\b", _IC)),
    ("azure", re.compile(r"\bAzure\b", _IC)),
    ("terraform", re.compile(r"\bTerraform\b", _IC)),
    ("react", re.compile(r"\bReact\b(?!\s+Native)", _IC)),
    ("node", re.compile(r"\bNode(?:\.js)?\b", _IC)),
    ("django", re.compile(r"\bDjango\b", _IC)),
    ("flask", re.compile(r"\bFlask\b", _IC)),
    ("spring", re.compile(r"\bSpring\b(?:\s+Boot)?\b", _IC)),
    ("pytorch", re.compile(r"\bPyTorch\b", _IC)),
    ("tensorflow", re.compile(r"\bTensorFlow\b", _IC)),
    ("cuda", re.compile(r"\bCUDA\b", _IC)),
    ("spark", re.compile(r"\bSpark\b|Apache\s+Spark\b", _IC)),
    ("flink", re.compile(r"\bFlink\b|Apache\s+Flink\b", _IC)),
    ("airflow", re.compile(r"\bAirflow\b|Apache\s+Airflow\b", _IC)),
    ("graphql", re.compile(r"\bGraphQL\b", _IC)),
    ("grpc", re.compile(r"\bgRPC\b", _IC)),
]

# --- is_swe_role title/JD signals ---
_SWE_TITLE_STRONG = re.compile(
    r"\b(software engineer|software developer|software development engineer"
    r"|(back.?end|front.?end|full.?stack)[ /-]*(engineer|developer)|swe|sde"
    r"|web developer|mobile developer|android developer|ios developer"
    r"|devops|sre|site reliability"
    r"|data engineer|ml engineer|machine learning engineer|ai engineer"
    r"|platform engineer|infrastructure engineer|cloud engineer)\b",
    _IC,
)
_SWE_TITLE_POS = re.compile(
    r"\b(software|swe|sde|developer|programmer|coder"
    r"|backend|front.?end|full.?stack|devops|sre|site reliability"
    r"|data engineer|ml engineer|machine learning engineer|ai engineer"
    r"|platform engineer|infrastructure engineer|cloud engineer"
    r"|solutions engineer|applications engineer"
    r"|web developer|mobile developer|android|ios developer)\b",
    _IC,
)
_SWE_TITLE_NEG = re.compile(
    r"\b(sales|marketing|recruiter|hr |human resource|account executive"
    r"|business development|product manager|product management"
    r"|customer (success|experience|support)|operations|analyst"
    r"|writer|editor|designer(?!\s+engineer)|coordinator"
    r"|clerk|admin|assistant(?!\s+engineer)|nurse|therapist|teacher"
    r"|retail|cashier|front.?end associate"
    r"|mechanic|technician|welder|inspector"
    r"|civil|structural|mechanical|electrical(?!\s+software)"
    r"|chemical|environmental|industrial(?!\s+software)"
    r"|construction|architecture(?!\s+software)|hvac|plumb"
    r"|financial|accounting|legal|compliance"
    r"|chef|driver|warehouse|shipping|receiving)\b",
    _IC,
)
_SWE_JD_SIGNALS = re.compile(
    r"\b(write code|software development|programming|codebase"
    r"|git|github|pull request|code review"
    r"|api|rest|graphql|microservice|backend service"
    r"|deploy|ci/cd|docker|kubernetes|terraform"
    r"|database|sql|postgresql|mongodb|redis"
    r"|python|java|javascript|typescript|c\+\+|rust|golang|ruby|scala"
    r"|react|angular|vue|node\.js|django|flask|spring boot"
    r"|unit test|integration test|test.driven"
    r"|agile|scrum|sprint)\b",
    _IC,
)


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
assert [name for name, _ in _TECH_PATTERNS] == TECH_NAMES
assert [name for name, _ in _DOMAIN_PATTERNS] == DOMAIN_NAMES

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
    for match in _RE_YOE.finditer(text):
        raw = match.group(1) or match.group(2) or match.group(3) or match.group(4)
        if raw is not None:
            value = int(raw)
            if value <= _MAX_CANDIDATE_YOE:
                values.append(value)
    return sorted(values)


def _seniority_from_title(title: str) -> str | None:
    if _RE_LEAD.search(title):
        return "lead"
    if _RE_SENIOR.search(title):
        return "senior"
    if _RE_MID_NUMERAL.search(title):
        return "mid"
    if _RE_ENTRY.search(title):
        return "entry"
    return None


def _seniority_from_jd(jd_text: str) -> str | None:
    values = _extract_yoe_values(jd_text)
    if not values:
        return None
    if values[0] >= _SENIOR_YOE:
        return "senior"
    if values[0] >= _MID_YOE:
        return "mid"
    return None


def _seniority_level(title: str, jd_text: str) -> str:
    return _seniority_from_title(title) or _seniority_from_jd(jd_text) or "unknown"


def _degree_required(jd_text: str) -> str:
    if _RE_PHD.search(jd_text):
        return "phd"
    if _RE_MASTERS.search(jd_text):
        return "masters"
    if _RE_BACHELORS.search(jd_text):
        return "bachelors"
    return "none"


def _clearance_required(jd_text: str) -> int:
    return 1 if _RE_CLEARANCE.search(jd_text) else 0


def _clearance_status(jd_text: str) -> str:
    if not _RE_CLEARANCE.search(jd_text):
        return "none"
    if _RE_CLEARANCE_OBTAINABLE.search(jd_text):
        return "obtainable"
    if _RE_CLEARANCE_ACTIVE.search(jd_text):
        return "active_required"
    return "ambiguous"


def _school_restricted(jd_text: str) -> int:
    return 1 if _RE_SCHOOL_RESTRICTED.search(jd_text) else 0


def _domain_tags(title: str, jd_text: str) -> list[str]:
    combined = f"{title} {jd_text}"
    return [tag for tag, pat in _DOMAIN_PATTERNS if pat.search(combined)]


def _tech_required(jd_text: str) -> list[str]:
    return [tech for tech, pat in _TECH_PATTERNS if pat.search(jd_text)]


def _role_type(title: str, jd_text: str) -> str:
    combined = f"{title} {jd_text}"
    if _RE_ROLE_INTERN.search(combined):
        return "intern"
    if _RE_ROLE_COOP.search(combined):
        return "coop"
    if _RE_ROLE_NEW_GRAD.search(combined):
        return "new_grad"
    if _RE_ROLE_CONTRACT.search(combined):
        return "contract"
    return "fte"


def _yoe_min(jd_text: str) -> int | None:
    values = _extract_yoe_values(jd_text)
    return values[0] if values else None


def _is_swe_role(title: str, jd_text: str) -> bool:
    title_lower = title.lower()
    if _SWE_TITLE_STRONG.search(title_lower):
        return True
    if _SWE_TITLE_NEG.search(title_lower):
        return False
    if _SWE_TITLE_POS.search(title_lower):
        return True
    sample = jd_text[:_SWE_JD_SAMPLE_CHARS]
    return len(_SWE_JD_SIGNALS.findall(sample)) >= _SWE_JD_MIN_SIGNALS


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
    reason strings and order: a ``yoe_min`` at/above 3 fails first, then an
    active/ambiguous clearance (keyed on ``clearance_status``), then a
    non-software role. Hard requirements deliberately bypass any model score.

    Args:
        features: Extracted structured features for the posting.

    Returns:
        The first violated rule's reason string, or ``None`` when none fire.
    """
    yoe = features.yoe_min
    if yoe is not None and int(yoe) >= _HARD_FAIL_YOE:
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
