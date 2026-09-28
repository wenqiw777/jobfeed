"""Repeatable, source-reviewed spot assertions on the expanded experiment.

These targeted assertions are NOT a gold-set accuracy metric. They expose known
failures without changing model outputs, prompts, validation, or production code.
"""

# ruff: noqa: PLR2004 -- explicit source-reviewed expected amounts and years.

import argparse
import json
from pathlib import Path


def probe(root):
    results = []

    def output(sid, task):
        row = json.loads((root / f"{sid}-{task}.json").read_text())
        if not row.get("accepted"):
            raise ValueError(
                f"Sample {sid}/{task} has no accepted output; cannot pass a probe"
            )
        return row["accepted"]["output"]

    def check(name, sample_id, passed, note):
        results.append(
            {
                "case": name,
                "sample_id": sample_id,
                "passed": bool(passed),
                "source_review_basis": note,
            }
        )

    for sid in (50, 157, 164, 254):
        records = output(sid, "salary").get("records", [])
        check(
            "retain_equity_after_repair",
            sid,
            any(r["kind"] == "equity" for r in records),
            "Source explicitly offers or conditionally offers equity/stock; "
            "preserve the record and its condition.",
        )
    records = output(252, "salary").get("records", [])
    check(
        "daily_meal_allowance_is_representable",
        252,
        any(
            r["minimum"] == 70 and r["interval"] == "day" and r["kind"] == "allowance"
            for r in records
        ),
        "Source: $70 per diem for meals. "
        "A daily allowance is not an unknown-period salary.",
    )
    records = output(357, "salary").get("records", [])
    check(
        "referral_condition_preserved_structurally",
        357,
        not any(r["kind"] == "bonus" and r["state"] == "present" for r in records),
        "Only bonus statement is Referral bonuses, "
        "an additional action/eligibility condition.",
    )
    records = output(50, "salary").get("records", [])
    check(
        "three_regional_ranges_not_collapsed",
        50,
        {
            (r["minimum"], r["maximum"], r["currency"], r["interval"])
            for r in records
            if r["minimum"] is not None
        }
        == {
            (97600, 139000, "USD", "unknown"),
            (125320, 142783, "CAD", "unknown"),
            (116965, 133264, "CAD", "unknown"),
        },
        "US, Toronto/Vancouver, and other Canada ranges; no stated salary period.",
    )
    for sid, amount, period in ((54, 29, "hour"), (159, 6000, "month")):
        records = output(sid, "salary").get("records", [])
        check(
            "explicit_period_preserved",
            sid,
            any(r["minimum"] == amount and r["interval"] == period for r in records),
            "Source explicitly says per hour or per month; no annualization.",
        )
    for sid in (247, 455):
        qualification = output(sid, "qualification")
        check(
            "no_invented_candidate_degree",
            sid,
            qualification.get("coverage", {}).get("degree") == "not_stated",
            "Employee PhD background or Research Engineer title "
            "is not a candidate degree requirement.",
        )
    facts = output(161, "qualification").get("facts", [])
    check(
        "eight_years_remains_preferred",
        161,
        any(
            f["kind"] == "experience"
            and f["value"] == 8
            and f["modality"] == "preferred"
            and f["internship_policy"] == "excluded"
            for f in facts
        )
        and not any(
            f["kind"] == "experience"
            and f["value"] == 8
            and f["modality"] == "required"
            for f in facts
        ),
        "8+ years is under Preferred qualifications, excluding internships/co-ops.",
    )
    records = output(248, "salary").get("records", [])
    check(
        "referrer_15000_not_candidate_salary",
        248,
        not any(r["minimum"] == 15000 or r["maximum"] == 15000 for r in records),
        "$15,000 is paid for referring someone else, not this applicant's salary.",
    )
    records = output(49, "salary").get("records", [])
    check(
        "learning_budget_not_salary",
        49,
        not any(r["minimum"] == 2000 or r["maximum"] == 2000 for r in records),
        "USD 2,000 per year is a learning budget, not base salary.",
    )
    qualification = output(447, "qualification")

    def refs(node):
        if node["op"] == "fact":
            return {node["ref"]}
        return {ref for child in node["children"] for ref in refs(child)}

    equivalents = {
        fact["id"]
        for fact in qualification["facts"]
        if fact["kind"] == "degree" and "equivalent" in fact["scope"].lower()
    }
    check(
        "equivalent_qualification_path_not_orphaned",
        447,
        bool(equivalents & refs(qualification["required"])),
        "BS or MS ... or equivalent must retain an alternative path; "
        "an unreferenced waived fact cannot replace that path.",
    )
    facts = output(450, "qualification")["facts"]
    check(
        "minimum_range_not_certain_maximum",
        450,
        not any(
            f["kind"] == "experience"
            and f["modality"] == "required"
            and f["state"] == "present"
            and f["predicate"] in ("lte", "lt")
            and f["value"] == 7
            for f in facts
        ),
        "Minimum 5-7 years supports minimum 5, not a certain hard maximum of 7.",
    )
    return {
        "metric": "targeted_source_reviewed_assertions_not_accuracy",
        "total": len(results),
        "passed": sum(r["passed"] for r in results),
        "failed": sum(not r["passed"] for r in results),
        "cases": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    result = probe(args.run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(1 if result["failed"] else 0)


if __name__ == "__main__":
    main()
