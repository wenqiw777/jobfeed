"""Compiled regex tables for the ML-gate feature extractor (pure stdlib).

Verbatim port of the legacy ``jobfeed.ml_gate.extractor`` patterns, split out
of ``ml_features.py`` only to respect the domain file-length gate. Pattern
ORDER is load-bearing: ``_DOMAIN_PATTERNS`` / ``_TECH_PATTERNS`` mirror the
``DOMAIN_NAMES`` / ``TECH_NAMES`` vocab order, and longer tech tokens precede
shorter ones so partial matches never win. Edits here change extraction parity.
"""

from __future__ import annotations

import re

_IC = re.IGNORECASE
_VB = re.IGNORECASE | re.VERBOSE

# --- numeric thresholds shared with the extractor ---
_MAX_CANDIDATE_YOE = 20
_SENIOR_YOE = 5
_MID_YOE = 2
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
