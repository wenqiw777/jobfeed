"""Offline conservative seniority experiment on the unchanged 100-JD cohort."""

import collections
import csv
import json
import re
from pathlib import Path

MAX_HEADING_LENGTH = 100
MAX_ENTRY_YEARS = 3

NUMBERS = dict(
    zip(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
        ],
        range(13),
        strict=False,
    )
)
YEAR = re.compile(
    (
        "\\b(\\d{1,2})(?:\\s*(?:\\+|plus|or "
        "more)|\\s*(?:-|\u2013|to)\\s*\\d{1,2})?\\s+years?\\b"
    ),
    re.I,
)
EXPERIENCE = re.compile(
    r"\b(experience|programming|specializing|as a|as an|working|work)\b", re.I
)
SENIOR = re.compile(r"\b(senior|sr\.?|staff|principal|lead|manager|director)\b", re.I)
ENTRY = re.compile(
    (
        "\\b(junior|jr\\.?|entry.level|new.grad|graduate|intern"
        "(?:ship)?|early.career)\\b|\\b(?:engineer|developer)\\s"
        "+I\\b"
    ),
    re.I,
)
DEGREE_PATH = re.compile(
    r"\b(bachelor|master|degree|equivalent|in lieu|substitut|education)", re.I
)
PREF = re.compile(r"\b(preferred|desired|nice|bonus|ideally)\b", re.I)


def normalize(text):
    text = (
        re.sub(r"\\([+*#().,\-])", r"\1", text)
        .replace("**", "")
        .strip(" \t*-•#")
        .strip()
    )
    return re.sub(
        r"\b(" + "|".join(NUMBERS) + r")\b",
        lambda m: str(NUMBERS[m[0].lower()]),
        text,
        flags=re.I,
    )


def _section_for_line(line, section):
    low = line.lower().rstrip(":").strip()
    if len(low) < MAX_HEADING_LENGTH:
        if re.search(
            r"^(preferred|desired|nice|bonus|what sets you apart|other desirable)",
            low,
        ):
            section = "preferred"
        elif re.search(
            (
                "^(basic |minimum |required |skill |employment "
                ")?(qualifications|requirements|skills)$"
            ),
            low,
        ) or low in [
            "experience",
            "you have",
            "what you must have",
            "what we're looking for",
            "who you are",
            "required",
        ]:
            section = "required"
        elif re.search(
            (
                "^(about|benefits|compensation|our company|what we "
                "offer|responsibilities)"
            ),
            low,
        ):
            section = "other"
    return section


def _experience_facts(jd):
    section = "unknown"
    facts = []
    issues = []
    for raw in jd.splitlines():
        line = normalize(raw)
        if not line:
            continue
        section = _section_for_line(line, section)
        matches = list(YEAR.finditer(line))
        if not matches:
            continue
        if re.search(
            (
                "\\b(company|founded|history|combined|probationary|res"
                "idency|salary|vacation|age|university|academic "
                "years)\\b"
            ),
            line,
            re.I,
        ):
            continue
        if not EXPERIENCE.search(line):
            continue
        if PREF.search(line) or section == "preferred":
            continue
        if DEGREE_PATH.search(line):
            issues.append("degree_or_equivalent_path")
            facts.append(
                {"source": raw, "kind": "review", "years": [int(m[1]) for m in matches]}
            )
            continue
        strength = section == "required" or bool(
            re.search(r"\b(minimum|at least|must have|required|requires)\b", line, re.I)
        )
        if not strength:
            issues.append("requirement_strength_unclear")
        facts.append(
            {
                "source": raw,
                "kind": "required" if strength else "review",
                "years": [int(m[1]) for m in matches],
            }
        )
    return facts, issues


def judge(title, jd):
    senior = bool(SENIOR.search(title))
    entry = bool(ENTRY.search(title))
    facts, issues = _experience_facts(jd)
    years = [y for f in facts if f["kind"] == "required" for y in f["years"]]
    # Detect alternative paths across separate bullets: never reduce them by max().
    if years and re.search(
        r"\b(in lieu|substitut|or equivalent|equivalent.*experience)\b", jd, re.I
    ):
        issues.append("cross_sentence_alternative")
    any_years = [y for f in facts for y in f["years"]]
    if senior and entry:
        decision = "review"
        reason = "mixed_title_levels"
    elif senior and any_years and max(any_years) <= MAX_ENTRY_YEARS:
        decision = "review"
        reason = "title_experience_conflict"
    elif senior:
        decision = "block"
        reason = "explicit_senior_title"
    elif issues:
        decision = "review"
        reason = ";".join(sorted(set(issues)))
    elif years:
        decision = "block" if max(years) > MAX_ENTRY_YEARS else "keep"
        reason = "explicit_required_experience"
        if entry and decision == "block":
            decision = "review"
            reason = "title_experience_conflict"
    elif entry:
        decision = "keep"
        reason = "explicit_entry_title"
    else:
        decision = "not_extracted"
        reason = "no_explicit_seniority_evidence_extracted"
    # not_extracted is a parser limit, not proof the JD omits seniority; keep visible.
    return {"decision": decision, "reason": reason, "evidence": facts}


def main():
    root = Path("artifacts/seniority-demo-100")
    out = root / "uncertain-v2"
    out.mkdir(exist_ok=True)
    baseline = json.loads((root / "results.json").read_text())
    rows = [
        dict(
            sample_id=r["sample_id"],
            id=r["id"],
            title=r["title"],
            company=r["company"],
            baseline=r["decision"],
            **judge(r["title"], r["jd_text"]),
        )
        for r in baseline
    ]
    for row, old in zip(rows, baseline, strict=False):
        assert all(f["source"] in old["jd_text"] for f in row["evidence"])
    summary = {
        group: dict(
            collections.Counter(
                r["decision"]
                for r in rows
                if group == "all_100" or r["baseline"] == "uncertain"
            )
        )
        for group in ["all_100", "previous_46_uncertain"]
    }
    (out / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    with (out / "results.csv").open("w") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(json.dumps(summary, indent=2))
    for r in rows:
        if r["baseline"] == "uncertain":
            print(r["sample_id"], r["decision"], r["reason"], r["title"])


if __name__ == "__main__":
    main()
