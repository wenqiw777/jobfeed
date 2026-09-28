"""Offline semantic contract and policy projection; never a production gate.

Experience comparisons describe the 0-3-year search policy, not a candidate's
work history. Real candidate eligibility needs a separately verified profile.
"""

from __future__ import annotations

import calendar
import itertools
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

State = Literal["present", "not_stated", "ambiguous", "conflicting", "unavailable"]
SEARCH_YEARS_MAX = 3.0
CURRENCY_CODE_LENGTH = 3
THOUSANDS_GROUP_DIGITS = 3
YEAR_TEXT_LENGTH = 4
MONTH_TEXT_LENGTH = 7
KINDS = (
    "degree",
    "experience",
    "graduation",
    "enrollment",
    "start_date",
    "seniority",
    "skill",
    "domain",
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Document(StrictModel):
    job_id: str
    url: str
    title: str
    text: str
    completeness: Literal["complete", "partial", "unavailable"]


class Evidence(StrictModel):
    quote: str = Field(min_length=1)
    block_id: str


class Fact(StrictModel):
    id: str = Field(min_length=1)
    kind: Literal[
        "degree",
        "experience",
        "graduation",
        "enrollment",
        "start_date",
        "seniority",
        "skill",
        "domain",
    ]
    state: State
    modality: Literal["required", "preferred", "context", "waived", "ambiguous"]
    subject: Literal["candidate", "company", "product", "unknown"]
    predicate: Literal["eq", "gte", "gt", "lte", "lt", "exists", "waived"]
    value: str | float | None
    unit: Literal["none", "years", "months", "date"]
    scope: str
    as_of: str | None
    experience_domain: str | None
    internship_policy: Literal["included", "excluded", "unspecified"]
    post_degree: Literal["bachelor", "master", "phd", "graduation", "unspecified"]
    evidence: list[Evidence] = Field(min_length=1)

    @model_validator(mode="after")
    def check_value(self):
        allowed = {
            "seniority": {"eq", "exists", "waived"},
            "enrollment": {"eq", "exists", "waived"},
            "degree": {"eq", "gte", "exists", "waived"},
        }
        if self.kind in allowed and self.predicate not in allowed[self.kind]:
            raise ValueError("unsupported predicate for this kind of fact")
        if self.kind == "experience":
            if self.value is not None and (
                not isinstance(self.value, float) or self.value < 0
            ):
                raise ValueError("experience must be a non-negative number or unknown")
            if self.value is not None and self.unit not in ("years", "months"):
                raise ValueError("experience must retain its period")
        status_only = (
            self.kind == "graduation"
            and self.value == "graduated"
            and self.predicate in ("eq", "exists")
            and self.unit == "none"
        )
        if (
            self.kind in ("graduation", "start_date")
            and self.value is not None
            and not status_only
        ):
            if not isinstance(self.value, str) or self.unit != "date":
                raise ValueError("date fact must retain date precision")
            date_interval(self.value)
        if self.as_of is not None:
            date_interval(self.as_of)
        if self.predicate == "waived" and self.modality != "waived":
            raise ValueError("absence of a requirement is a waiver, not a prohibition")
        return self


class Node(StrictModel):
    op: Literal["fact", "all_of", "any_of"]
    ref: str | None
    children: list[Node]

    @model_validator(mode="after")
    def shape(self):
        if self.op == "fact" and (not self.ref or self.children):
            raise ValueError("leaf requires a fact reference and no children")
        if self.op != "fact" and self.ref is not None:
            raise ValueError("group cannot have a fact reference")
        if self.op == "any_of" and not self.children:
            raise ValueError("empty OR is not a requirement")
        return self


def refs(node: Node) -> list[str]:
    if node.op == "fact":
        return [node.ref]
    return [ref for child in node.children for ref in refs(child)]


class Qualification(StrictModel):
    schema_version: Literal["1", "2"]
    state: State
    coverage: dict[str, State]
    facts: list[Fact]
    required: Node
    preferred: Node

    @model_validator(mode="after")
    def contract(self):
        if set(self.coverage) != set(KINDS):
            raise ValueError("coverage must explicitly cover every requested field")
        by_id = {fact.id: fact for fact in self.facts}
        if len(by_id) != len(self.facts):
            raise ValueError("duplicate fact IDs")
        referenced = set(refs(self.required))
        expected = {
            f.id
            for f in self.facts
            if f.modality == "required" and f.subject == "candidate"
        }
        if referenced != expected:
            raise ValueError("tree must reference exactly all required candidate facts")
        preferred = {
            f.id
            for f in self.facts
            if f.modality == "preferred" and f.subject == "candidate"
        }
        if set(refs(self.preferred)) != preferred:
            raise ValueError(
                "preferred tree must preserve all preferred candidate facts"
            )
        if self.state == "not_stated" and self.facts:
            raise ValueError("not_stated cannot contain facts")
        for fact in self.facts:
            if self.coverage[fact.kind] in ("not_stated", "unavailable"):
                raise ValueError("coverage contradicts extracted facts")
        for kind, state in self.coverage.items():
            if state == "present" and not any(f.kind == kind for f in self.facts):
                raise ValueError("present coverage needs at least one fact")
        return self


class SalaryAttributes(StrictModel):
    minimum: list[Evidence]
    maximum: list[Evidence]
    currency: list[Evidence]
    interval: list[Evidence]
    kind: list[Evidence]
    location: list[Evidence]
    level: list[Evidence]


class Salary(StrictModel):
    minimum: float | None = Field(ge=0)
    maximum: float | None = Field(ge=0)
    currency: str | None
    interval: Literal["hour", "month", "year", "one_time", "unknown"]
    kind: Literal["base", "bonus", "equity", "ote", "total_compensation", "unspecified"]
    location: str
    level: str
    state: State
    evidence: list[Evidence] = Field(min_length=1)
    attributes: SalaryAttributes | None = None

    @model_validator(mode="after")
    def ordered(self):
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("reversed salary interval")
        if self.currency is not None and (
            len(self.currency) != CURRENCY_CODE_LENGTH or not self.currency.isupper()
        ):
            raise ValueError("currency must be an explicit ISO code or null")
        return self


class SalaryOutput(StrictModel):
    schema_version: Literal["1", "2"]
    state: State
    records: list[Salary]

    @model_validator(mode="after")
    def contract(self):
        if self.state == "not_stated" and self.records:
            raise ValueError("not_stated salary has no records")
        if self.state == "present" and not self.records:
            raise ValueError("present salary needs records")
        return self


class CandidateScenario(StrictModel):
    name: str
    degree: Literal["high_school", "associate", "bachelor", "master", "phd"] | None
    credential_status: Literal["completed", "planned", "unknown"]
    graduation_start: str | None
    graduation_end: str | None

    @model_validator(mode="after")
    def dates(self):
        if (self.graduation_start is None) != (self.graduation_end is None):
            raise ValueError("provide both candidate date bounds")
        if self.graduation_start is not None:
            lo, hi = (
                date.fromisoformat(self.graduation_start),
                date.fromisoformat(self.graduation_end),
            )
            if lo > hi:
                raise ValueError("candidate date range reversed")
        return self


class Truth(StrEnum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


def combine(op: str, values: list[Truth]) -> Truth:
    if op == "all_of":
        if Truth.FALSE in values:
            return Truth.FALSE
        return Truth.UNKNOWN if Truth.UNKNOWN in values else Truth.TRUE
    if Truth.TRUE in values:
        return Truth.TRUE
    return Truth.UNKNOWN if Truth.UNKNOWN in values else Truth.FALSE


def date_interval(value: str) -> tuple[int, int]:
    """Retain uncertainty for year-only and month-only source dates."""
    if len(value) == YEAR_TEXT_LENGTH:
        lo, hi = date(int(value), 1, 1), date(int(value), 12, 31)
    elif len(value) == MONTH_TEXT_LENGTH:
        year, month = map(int, value.split("-"))
        lo, hi = (
            date(year, month, 1),
            date(year, month, calendar.monthrange(year, month)[1]),
        )
    else:
        lo = hi = date.fromisoformat(value)
    return lo.toordinal(), hi.toordinal()


def interval_compare(
    candidate: tuple[float, float], predicate: str, target: tuple[float, float]
) -> Truth:
    lo, hi = candidate
    start, end = target
    if predicate == "eq":
        return (
            Truth.TRUE
            if start <= lo <= hi <= end
            else (Truth.FALSE if hi < start or lo > end else Truth.UNKNOWN)
        )
    if predicate == "lte":
        return (
            Truth.TRUE if hi <= start else (Truth.FALSE if lo > end else Truth.UNKNOWN)
        )
    if predicate == "lt":
        return (
            Truth.TRUE if hi < start else (Truth.FALSE if lo >= end else Truth.UNKNOWN)
        )
    if predicate == "gte":
        return (
            Truth.TRUE if lo >= end else (Truth.FALSE if hi < start else Truth.UNKNOWN)
        )
    if predicate == "gt":
        return (
            Truth.TRUE if lo > end else (Truth.FALSE if hi <= start else Truth.UNKNOWN)
        )
    return Truth.UNKNOWN


def graduation_interval(scenario: CandidateScenario):
    if scenario.graduation_start is None:
        return None
    return date.fromisoformat(
        scenario.graduation_start
    ).toordinal(), date.fromisoformat(scenario.graduation_end).toordinal()


def degree_truth(fact: Fact, scenario: CandidateScenario) -> Truth:
    if fact.predicate == "exists" and fact.value == "degree" and scenario.degree:
        return (
            Truth.TRUE if scenario.credential_status == "completed" else Truth.UNKNOWN
        )
    levels = ["high_school", "associate", "bachelor", "master", "phd"]
    if (
        scenario.degree is None
        or fact.value not in levels
        or fact.predicate not in ("eq", "gte")
    ):
        return Truth.UNKNOWN
    if levels.index(scenario.degree) < levels.index(fact.value):
        return Truth.FALSE
    if fact.predicate == "eq" and scenario.degree != fact.value:
        # A higher credential does not establish possession of this exact one.
        return Truth.UNKNOWN
    if scenario.credential_status != "completed":
        interval = graduation_interval(scenario)
        if not fact.as_of or interval is None:
            return Truth.UNKNOWN
        return interval_compare(interval, "lte", date_interval(fact.as_of))
    return Truth.TRUE


def temporal_truth(fact: Fact, scenario: CandidateScenario) -> Truth:
    if fact.kind == "graduation":
        if fact.value == "graduated" and fact.unit == "none":
            return (
                Truth.TRUE
                if scenario.credential_status == "completed"
                else Truth.UNKNOWN
            )
        interval = graduation_interval(scenario)
        return (
            Truth.UNKNOWN
            if interval is None or fact.value is None
            else interval_compare(interval, fact.predicate, date_interval(fact.value))
        )
    if fact.kind == "enrollment":
        if fact.predicate != "eq":
            return Truth.UNKNOWN
        interval = graduation_interval(scenario)
        if (
            interval is None
            or not fact.as_of
            or fact.value not in ("enrolled", "not_graduated", "graduated")
        ):
            return Truth.UNKNOWN
        if fact.value == "enrolled":
            # Neither current nor future enrollment follows from a graduation date.
            # Policy explicitly preserves unconfirmed return-to-school as uncertain.
            return Truth.UNKNOWN
        return interval_compare(
            interval,
            "gt" if fact.value == "not_graduated" else "lte",
            date_interval(fact.as_of),
        )
    return Truth.UNKNOWN


def fact_truth(
    fact: Fact, scenario: CandidateScenario, policy_years: float = SEARCH_YEARS_MAX
) -> Truth:
    if (
        fact.kind in ("skill", "domain")
        or fact.modality != "required"
        or fact.subject != "candidate"
    ):
        return Truth.TRUE
    if fact.state != "present":
        return Truth.UNKNOWN
    if fact.kind == "experience":
        if fact.scope != "professional" or fact.value is None:
            return Truth.UNKNOWN
        years = fact.value / 12 if fact.unit == "months" else fact.value
        return interval_compare(
            (policy_years, policy_years), fact.predicate, (years, years)
        )
    if fact.kind == "degree":
        return degree_truth(fact, scenario)
    if fact.kind in ("graduation", "enrollment"):
        return temporal_truth(fact, scenario)
    if fact.kind == "seniority":
        return seniority_truth(fact)
    # Start availability, qualitative levels and unsupported scopes require facts.
    return Truth.UNKNOWN


def seniority_truth(fact: Fact) -> Truth:
    if fact.predicate != "eq":
        return Truth.UNKNOWN
    if fact.value in ("senior", "staff", "principal"):
        return Truth.FALSE
    if fact.value in ("new_grad", "entry", "junior", "early_career"):
        return Truth.TRUE
    return Truth.UNKNOWN


@dataclass(frozen=True)
class Evaluation:
    truth: Truth
    scenario_results: dict[str, Truth]


def experience_witnesses(output: Qualification):
    # Witnesses for all scalar comparisons; not synthetic candidate employment.
    boundaries = {0.0, SEARCH_YEARS_MAX}
    for fact in output.facts:
        if fact.kind == "experience" and isinstance(fact.value, float):
            boundaries.add(fact.value / 12 if fact.unit == "months" else fact.value)
    ordered = sorted(boundaries | {max(boundaries) + 1})
    witnesses = sorted(
        set(ordered + [(a + b) / 2 for a, b in itertools.pairwise(ordered)])
    )
    return witnesses, [v for v in witnesses if 0 <= v <= SEARCH_YEARS_MAX]


def evaluate(output: Qualification, scenarios: list[CandidateScenario]) -> Evaluation:
    by_id = {f.id: f for f in output.facts}
    witnesses, policy_witnesses = experience_witnesses(output)

    def conjunctive_facts(node):
        if node.op == "fact":
            return [by_id[node.ref]]
        if node.op == "all_of":
            return [f for child in node.children for f in conjunctive_facts(child)]
        return []

    def visit(node, scenario, years):
        if node.op == "fact":
            return fact_truth(by_id[node.ref], scenario, years)
        if node.op == "all_of":
            numeric = [
                f
                for f in conjunctive_facts(node)
                if f.kind == "experience" and f.scope == "professional"
            ]
            if numeric and all(
                combine("all_of", [fact_truth(f, scenario, v) for f in numeric])
                == Truth.FALSE
                for v in witnesses
            ):
                return Truth.UNKNOWN
        return combine(
            node.op, [visit(child, scenario, years) for child in node.children]
        )

    results = {
        s.name: combine(
            "any_of", [visit(output.required, s, years) for years in policy_witnesses]
        )
        for s in scenarios
    }
    if len(results) != len(scenarios) or not scenarios:
        raise ValueError("candidate scenarios need unique names and cannot be empty")
    truth = combine("any_of", list(results.values()))
    if output.state in ("ambiguous", "conflicting", "unavailable") or any(
        state in ("ambiguous", "conflicting", "unavailable")
        for kind, state in output.coverage.items()
        if kind not in ("skill", "domain")
    ):
        truth = Truth.UNKNOWN
    return Evaluation(truth, results)


def substantive_signature(output: Qualification):
    """Ignore generated IDs/order, never erase AND/OR structure or qualifiers."""
    facts = {f.id: f.model_dump(exclude={"id", "evidence"}) for f in output.facts}

    def visit(node):
        if node.op == "fact":
            return ("fact", tuple(sorted(facts[node.ref].items())))
        return (node.op, tuple(sorted((visit(c) for c in node.children), key=repr)))

    return output.state, tuple(sorted(output.coverage.items())), visit(output.required)


def finalize(output, scenarios, *, review=None, complete=True, review_failed=False):
    if (
        not complete
        or review_failed
        or output.state == "unavailable"
        or "unavailable" in output.coverage.values()
    ):
        return {
            "status": "Pending",
            "review_required": True,
            "reason": "evaluation_incomplete",
        }
    result = evaluate(output, scenarios)
    if review is not None:
        if review.state == "unavailable" or "unavailable" in review.coverage.values():
            return {
                "status": "Pending",
                "review_required": True,
                "reason": "review_incomplete",
            }
        if substantive_signature(output) != substantive_signature(review):
            return {
                "status": "Apply",
                "review_required": True,
                "reason": "unresolved_disagreement",
            }
    if result.truth == Truth.FALSE:
        if review is None:
            return {
                "status": "Pending",
                "review_required": True,
                "reason": "proposed_block_needs_review",
            }
        return {
            "status": "Blocked",
            "review_required": False,
            "reason": "confirmed_policy_conflict",
        }
    return {
        "status": "Apply",
        "review_required": result.truth == Truth.UNKNOWN,
        "reason": "uncertain"
        if result.truth == Truth.UNKNOWN
        else "no_confirmed_blocker",
    }


def resolve_evidence(evidence: Evidence, document: Document):
    """Recover source offsets; allow whitespace differences, never token rewrites."""
    source = {"body": document.text, "title": document.title}.get(evidence.block_id)
    if source is None or not evidence.quote.strip():
        return None

    def normalized_with_positions(text):
        characters, positions = [], []
        for index, character in enumerate(text):
            if character.isspace():
                if characters and characters[-1] != " ":
                    characters.append(" ")
                    positions.append(index)
            else:
                characters.append(character)
                positions.append(index)
        if characters and characters[-1] == " ":
            characters.pop()
            positions.pop()
        return "".join(characters), positions

    normalized, positions = normalized_with_positions(source)
    quote, _ = normalized_with_positions(evidence.quote)
    index = normalized.find(quote)
    if index < 0:
        return None
    start, end = positions[index], positions[index + len(quote) - 1] + 1
    return {
        "block_id": evidence.block_id,
        "start": start,
        "end": end,
        "source_quote": source[start:end],
    }


def normalize_qualifications(output: Qualification, document: Document):
    """Apply the user's reviewed convention separately from extracted source facts."""
    return [
        {
            "fact_id": fact.id,
            "value": "bachelor",
            "basis": "user_policy",
            "rule": "reviewed_college_university_graduate",
        }
        for fact in output.facts
        if fact.kind == "degree"
        and fact.subject == "candidate"
        and fact.value == "college_university_graduate"
        and any(
            resolve_evidence(e, document) is not None
            and "college or university graduated" in " ".join(e.quote.lower().split())
            for e in fact.evidence
        )
    ]


def salary_attribute_supported(attribute, value, quotes):
    """Validate model-selected atoms, never search the JD for candidate sentences."""
    atoms = {" ".join(q.lower().split()).strip() for q in quotes}
    if attribute in ("minimum", "maximum"):
        for atom in atoms:
            number = atom.removeprefix("$").strip()
            if "," in number:
                groups = number.split(".", 1)[0].split(",")
                if not 1 <= len(groups[0]) <= THOUSANDS_GROUP_DIGITS or any(
                    len(group) != THOUSANDS_GROUP_DIGITS or not group.isdigit()
                    for group in groups[1:]
                ):
                    continue
            number = number.replace(",", "")
            multiplier = 1000 if number.endswith("k") else 1
            if number.endswith("k"):
                number = number[:-1]
            if (
                number.replace(".", "", 1).isdigit()
                and float(number) * multiplier == value
            ):
                return True
        return False
    allowed = {
        "interval": {
            "year": {
                "year",
                "annual",
                "annually",
                "per year",
                "/year",
                "/yr",
                "yr",
                "per annum",
                "yearly",
            },
            "month": {"month", "monthly", "per month", "/month", "/mo"},
            "hour": {"hour", "hourly", "per hour", "/hour", "/hr"},
            "one_time": {"one-time", "one time", "sign-on", "signing bonus"},
        },
        "kind": {
            "base": {"base", "base salary", "base pay"},
            "bonus": {
                "bonus",
                "bonuses",
                "signing bonus",
                "sign-on bonus",
                "sales bonus",
                "sales bonuses",
                "company bonus",
            },
            "equity": {"equity", "stock", "stock options", "rsus"},
            "ote": {"ote", "on-target earnings", "on target earnings"},
            "total_compensation": {"total compensation", "total remuneration"},
        },
    }
    if attribute in allowed:
        return bool(atoms & allowed[attribute].get(value, set()))
    if attribute == "currency":
        aliases = {
            "USD": {"us$", "us dollars", "u.s. dollars"},
            "CAD": {"c$", "ca$", "canadian dollars"},
            "EUR": {"€", "euros"},
            "GBP": {"£", "pounds sterling"},
        }
        return bool(atoms & ({value.lower()} | aliases.get(value, set())))
    return str(value).lower() in atoms


def validate_salary_attributes(record: Salary, document: Document):
    if record.attributes is None:
        return ["salary.attributes.missing"]
    issues = []
    for attribute in SalaryAttributes.model_fields:
        proofs = getattr(record.attributes, attribute)
        if any(resolve_evidence(e, document) is None for e in proofs):
            issues.append(f"salary.{attribute}.evidence_not_in_document")
        value = getattr(record, attribute)
        if value not in (
            None,
            "unknown",
            "unspecified",
        ) and not salary_attribute_supported(
            attribute, value, [e.quote for e in proofs]
        ):
            issues.append(f"salary.{attribute}.unsupported_value")
    return issues


def validate_grounding(
    output: Qualification | SalaryOutput, document: Document
) -> list[str]:
    issues = []
    records = output.facts if isinstance(output, Qualification) else output.records
    for record in records:
        for evidence in record.evidence:
            if resolve_evidence(evidence, document) is None:
                issues.append("evidence_not_in_document")
        if isinstance(output, SalaryOutput) and output.schema_version == "2":
            issues.extend(validate_salary_attributes(record, document))
    if document.completeness != "complete":
        states = (
            list(output.coverage.values())
            if isinstance(output, Qualification)
            else [output.state]
        )
        if "not_stated" in states:
            issues.append("absence_not_supported_by_partial_document")
    return sorted(set(issues))


def salary_selection(output: SalaryOutput):
    records = [r.model_dump() for r in output.records]
    result = {"state": output.state, "records": records, "observed_score": None}
    if output.state != "present":
        return result
    scopes = {}
    for record in output.records:
        if record.state != "present":
            result["state"] = record.state
            return result
        if (
            record.currency is None
            or record.interval == "unknown"
            or record.kind == "unspecified"
            or record.location in ("unknown", "unspecified")
            or (record.minimum is None and record.maximum is None)
        ):
            result["state"] = "ambiguous"
            return result
        key = (
            record.location,
            record.level,
            record.kind,
            record.currency,
            record.interval,
        )
        bounds = record.minimum, record.maximum
        if key in scopes and scopes[key] != bounds:
            result["state"] = "conflicting"
            return result
        scopes[key] = bounds
    result["state"] = "multiple_ranges" if len(scopes) > 1 else "single_record"
    return result
