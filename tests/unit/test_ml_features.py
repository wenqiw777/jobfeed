"""Parity battery for the pure ML-gate feature extractor + hard-fail rules.

Each case pins the EXACT field-for-field output of the legacy
``jobfeed.ml_gate.extractor.extract`` / ``rules.hard_fail_from_extracted``
(captured by running the legacy code), so this module is a faithful,
numpy-free port. ``is_swe_role`` is exposed as ``bool`` here while the
legacy returns ``int`` 0/1; ``clearance_required``/``school_restricted``
stay ``int`` 0/1 to match legacy.
"""

from __future__ import annotations

import dataclasses

from jobfeed.domain.ml_features import (
    DEGREE_LEVELS,
    DOMAIN_NAMES,
    ROLE_TYPES,
    SENIORITY_LEVELS,
    STRUCTURED_DIM,
    TECH_NAMES,
    MLGateFeatures,
    extract_features,
    hard_fail_reason,
)

# ---------------------------------------------------------------------------
# Expected-feature records: (title, jd, expected-fields-dict).
# ``is_swe_role`` is the bool form of the legacy int.
# ---------------------------------------------------------------------------


def _feat(**over: object) -> dict[str, object]:
    """Return a clean baseline feature dict overlaid with case-specific fields.

    Args:
        over: Field overrides for the case being described.

    Returns:
        Expected feature mapping for one parity case.
    """
    base: dict[str, object] = {
        "seniority_level": "unknown",
        "degree_required": "none",
        "clearance_required": 0,
        "clearance_status": "none",
        "school_restricted": 0,
        "domain_tags": [],
        "tech_required": [],
        "role_type": "fte",
        "yoe_min": None,
        "is_swe_role": True,
    }
    base.update(over)
    return base


# (title, jd, expected_features, expected_hard_fail)
PARITY_CASES: list[tuple[str, str, dict[str, object], str | None]] = [
    # --- seniority from title ---
    (
        "Senior Software Engineer",
        "Build backend services.",
        _feat(seniority_level="senior"),
        None,
    ),
    (
        "Staff Software Engineer",
        "Build backend services.",
        _feat(seniority_level="senior"),
        None,
    ),
    (
        "Software Engineer III",
        "Write code for our platform.",
        _feat(seniority_level="mid"),
        None,
    ),
    (
        "Junior Software Engineer",
        "Write code.",
        _feat(seniority_level="entry"),
        None,
    ),
    (
        "Software Engineer Intern",
        "Write code.",
        _feat(seniority_level="entry", role_type="intern"),
        None,
    ),
    # --- seniority from JD fallback ---
    (
        "Software Engineer",
        "We require 5+ years of experience writing code.",
        _feat(seniority_level="senior", yoe_min=5),
        "yoe_min >= 5",
    ),
    (
        "Software Engineer",
        "Minimum 3 years of professional experience.",
        _feat(seniority_level="mid", yoe_min=3),
        "yoe_min >= 3",
    ),
    (
        "Software Engineer",
        "Join our team and write code.",
        _feat(),
        None,
    ),
    # --- degree ---
    (
        "Software Engineer",
        "PhD in CS required. Write code.",
        _feat(degree_required="phd"),
        None,
    ),
    (
        "Software Engineer",
        "Master's degree preferred. Write code.",
        _feat(degree_required="masters"),
        None,
    ),
    (
        "Software Engineer",
        "Bachelor's degree in CS. Write code.",
        _feat(degree_required="bachelors"),
        None,
    ),
    (
        "Software Engineer",
        "Write code for the platform.",
        _feat(degree_required="none"),
        None,
    ),
    # --- clearance status (keyed on clearance_status, not _required) ---
    (
        "Software Engineer",
        "Must hold an active TS/SCI clearance. Write code.",
        _feat(clearance_required=1, clearance_status="active_required"),
        "active clearance required",
    ),
    (
        "Software Engineer",
        "Clearance required; must be able to obtain a security clearance. Write code.",
        _feat(
            clearance_required=1,
            clearance_status="obtainable",
            domain_tags=["security"],
        ),
        None,
    ),
    (
        "Software Engineer",
        "Security clearance required. Write code.",
        _feat(
            clearance_required=1,
            clearance_status="ambiguous",
            domain_tags=["security"],
        ),
        "active clearance required",
    ),
    # --- school restricted ---
    (
        "Software Engineer",
        "Must be enrolled at a partner university. Write code.",
        _feat(school_restricted=1),
        None,
    ),
    # --- yoe ---
    (
        "Software Engineer",
        "3+ years of experience required. Write code.",
        _feat(seniority_level="mid", yoe_min=3),
        "yoe_min >= 3",
    ),
    (
        "Software Engineer",
        "3-5 years experience. Write code.",
        _feat(seniority_level="mid", yoe_min=3),
        "yoe_min >= 3",
    ),
    (
        "Software Engineer",
        "At least 7 years writing code.",
        _feat(seniority_level="senior", yoe_min=7),
        "yoe_min >= 7",
    ),
    (
        "Software Engineer",
        "25 years of experience. Write code.",
        _feat(),
        None,
    ),
    # --- role type ---
    (
        "Software Engineer Co-op",
        "Co-op role writing code.",
        _feat(role_type="coop"),
        None,
    ),
    (
        "New Grad Software Engineer",
        "Recent graduate role writing code.",
        _feat(seniority_level="entry", role_type="new_grad"),
        None,
    ),
    (
        "Software Engineer",
        "Contract position. Write code. W2 contract.",
        _feat(role_type="contract"),
        None,
    ),
    (
        "Software Engineer",
        "Full-time role writing code.",
        _feat(role_type="fte"),
        None,
    ),
    # --- is_swe_role strong / neg / pos / jd-signal ---
    (
        "Backend Engineer, Retail",
        "Some text.",
        _feat(is_swe_role=True),
        None,
    ),
    (
        "Sales Manager",
        "Sell products to clients.",
        _feat(is_swe_role=False),
        "not software engineering role",
    ),
    (
        "Programmer",
        "Some role.",
        _feat(is_swe_role=True),
        None,
    ),
    (
        "Graphic Designer",
        "Design marketing assets.",
        _feat(is_swe_role=False),
        "not software engineering role",
    ),
    (
        "Specialist",
        "We write code using Python and Java. Deploy with Docker and "
        "Kubernetes. Use git and code review.",
        _feat(
            is_swe_role=True,
            tech_required=["python", "java", "docker", "kubernetes"],
        ),
        None,
    ),
    (
        "Specialist",
        "We write code using Python.",
        _feat(is_swe_role=False, tech_required=["python"]),
        "not software engineering role",
    ),
    (
        "Tech Lead",
        "Lead the backend team writing code.",
        _feat(seniority_level="lead", is_swe_role=False),
        "not software engineering role",
    ),
    # --- clean entry-level SWE: no hard fail ---
    (
        "Software Engineer",
        "Entry-level role. Write code in Python. Join us.",
        _feat(tech_required=["python"]),
        None,
    ),
]


# Each tech vocab term -> (jd snippet, expected tech_required list). Some
# snippets legitimately trip extra signals (Node.js -> javascript, kotlin/
# swift -> mobile domain), captured verbatim from the legacy extractor.
TECH_PARITY: list[tuple[str, list[str], list[str]]] = [
    ("We use C++ heavily. Write code.", ["c++"], []),
    ("We use C# heavily. Write code.", [], []),
    ("We use TypeScript. Write code.", ["typescript"], []),
    ("We use JavaScript. Write code.", ["javascript"], []),
    ("We use Python. Write code.", ["python"], []),
    ("We use Java. Write code.", ["java"], []),
    ("We use Rust. Write code.", ["rust"], []),
    ("We use Golang. Write code.", ["go"], []),
    ("We use Ruby. Write code.", ["ruby"], []),
    ("We use Scala. Write code.", ["scala"], []),
    ("We use Kotlin. Write code.", ["kotlin"], ["mobile"]),
    ("We use Swift. Write code.", ["swift"], ["mobile"]),
    ("We program in C. Write code.", ["c"], []),
    ("We use SQL. Write code.", ["sql"], []),
    ("We use PostgreSQL. Write code.", ["postgresql"], []),
    ("We use MySQL. Write code.", ["mysql"], []),
    ("We use MongoDB. Write code.", ["mongodb"], []),
    ("We use Redis. Write code.", ["redis"], []),
    ("We use Kafka. Write code.", ["kafka"], []),
    ("We use Elasticsearch. Write code.", ["elasticsearch"], []),
    ("We use Docker. Write code.", ["docker"], []),
    ("We use Kubernetes. Write code.", ["kubernetes"], []),
    ("We use AWS. Write code.", ["aws"], []),
    ("We use GCP. Write code.", ["gcp"], []),
    ("We use Azure. Write code.", ["azure"], []),
    ("We use Terraform. Write code.", ["terraform"], []),
    ("We use React. Write code.", ["react"], []),
    ("We use Node.js. Write code.", ["javascript", "node"], []),
    ("We use Django. Write code.", ["django"], []),
    ("We use Flask. Write code.", ["flask"], []),
    ("We use Spring Boot. Write code.", ["spring"], []),
    ("We use PyTorch. Write code.", ["pytorch"], []),
    ("We use TensorFlow. Write code.", ["tensorflow"], []),
    ("We use CUDA. Write code.", ["cuda"], []),
    ("We use Apache Spark. Write code.", ["spark"], []),
    ("We use Apache Flink. Write code.", ["flink"], []),
    ("We use Apache Airflow. Write code.", ["airflow"], []),
    ("We use GraphQL. Write code.", ["graphql"], []),
    ("We use gRPC. Write code.", ["grpc"], []),
]


# Each domain vocab term -> (jd snippet, expected domain_tags list).
DOMAIN_PARITY: list[tuple[str, list[str]]] = [
    ("Work on embedded firmware for microcontrollers. Write code.", ["embedded"]),
    ("Build robotics and ROS autonomous systems. Write code.", ["robotics"]),
    ("Develop avionics for satellite systems. Write code.", ["aerospace"]),
    ("Design ASIC and PCB silicon hardware. Write code.", ["hardware"]),
    ("Build HFT algorithmic trading systems. Write code.", ["trading"]),
    ("Factory automation with PLC and SCADA. Write code.", ["manufacturing"]),
    (
        "Penetration testing and threat hunting cybersecurity. Write code.",
        ["security"],
    ),
    ("Build mobile iOS and Android apps. Write code.", ["mobile"]),
    ("Game development with Unity and Unreal engine. Write code.", ["gamedev"]),
]


def _as_dict(features: MLGateFeatures) -> dict[str, object]:
    """Project a feature dataclass into a plain dict for comparison.

    Args:
        features: Extracted feature record.

    Returns:
        Mapping of the 10 feature fields to their values.
    """
    return {
        "seniority_level": features.seniority_level,
        "degree_required": features.degree_required,
        "clearance_required": features.clearance_required,
        "clearance_status": features.clearance_status,
        "school_restricted": features.school_restricted,
        "domain_tags": features.domain_tags,
        "tech_required": features.tech_required,
        "role_type": features.role_type,
        "yoe_min": features.yoe_min,
        "is_swe_role": features.is_swe_role,
    }


def test_extract_features_matches_legacy_battery() -> None:
    """Extraction reproduces legacy output field-for-field across the battery."""
    for title, jd, expected, _ in PARITY_CASES:
        got = _as_dict(extract_features(title, jd))
        assert got == expected, f"mismatch for {title!r} / {jd!r}: {got}"


def test_hard_fail_reason_matches_legacy_battery() -> None:
    """Hard-fail reasons match legacy exact strings and ordering."""
    for title, jd, _, expected_fail in PARITY_CASES:
        features = extract_features(title, jd)
        assert hard_fail_reason(features) == expected_fail, (
            f"hard-fail mismatch for {title!r} / {jd!r}"
        )


def test_each_tech_token_extracted_in_order() -> None:
    """Each tech vocab term is recognized with legacy domain side effects."""
    for jd, expected_tech, expected_domains in TECH_PARITY:
        features = extract_features("Software Engineer", jd)
        assert features.tech_required == expected_tech, jd
        assert features.domain_tags == expected_domains, jd


def test_each_domain_tag_extracted() -> None:
    """Each domain vocab term is recognized from the combined title+JD text."""
    for jd, expected_domains in DOMAIN_PARITY:
        features = extract_features("Software Engineer", jd)
        assert features.domain_tags == expected_domains, jd


def test_clean_entry_level_swe_has_no_hard_fail() -> None:
    """A clean entry-level SWE posting passes every hard-fail rule."""
    features = extract_features(
        "Software Engineer",
        "Entry-level role. Write code in Python. Join us.",
    )
    assert features.is_swe_role is True
    assert features.yoe_min is None
    assert hard_fail_reason(features) is None


def test_hard_fail_keys_on_clearance_status_not_required() -> None:
    """An obtainable clearance sets clearance_required=1 but does not fail."""
    features = extract_features(
        "Software Engineer",
        "Clearance required; must be able to obtain a security clearance. Write code.",
    )
    assert features.clearance_required == 1
    assert features.clearance_status == "obtainable"
    assert hard_fail_reason(features) is None


def test_hard_fail_yoe_threshold_is_two() -> None:
    """yoe_min == 2 trips the hard fail; yoe_min == 1 does not."""
    two = MLGateFeatures(
        seniority_level="mid",
        degree_required="none",
        clearance_required=0,
        clearance_status="none",
        school_restricted=0,
        domain_tags=[],
        tech_required=[],
        role_type="fte",
        yoe_min=2,
        is_swe_role=True,
    )
    one = MLGateFeatures(
        seniority_level="entry",
        degree_required="none",
        clearance_required=0,
        clearance_status="none",
        school_restricted=0,
        domain_tags=[],
        tech_required=[],
        role_type="fte",
        yoe_min=1,
        is_swe_role=True,
    )
    assert hard_fail_reason(two) == "yoe_min >= 2"
    assert hard_fail_reason(one) is None


def test_vocab_counts_and_structured_dim() -> None:
    """Vocab list lengths and the structured layout width match legacy."""
    assert len(TECH_NAMES) == 39
    assert len(DOMAIN_NAMES) == 9
    assert len(SENIORITY_LEVELS) == 5
    assert len(DEGREE_LEVELS) == 4
    assert len(ROLE_TYPES) == 5
    assert STRUCTURED_DIM == 66


def test_features_dataclass_is_frozen() -> None:
    """MLGateFeatures is immutable so extracted records cannot be mutated."""
    features = extract_features("Software Engineer", "Write code in Python.")
    assert dataclasses.is_dataclass(features)
    try:
        features.seniority_level = "senior"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("MLGateFeatures must be frozen")
