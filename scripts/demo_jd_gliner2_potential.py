"""Test whether a small GLiNER2 fine-tune can learn difficult JD fact structure.

This is an isolated learnability experiment. It does not produce eligibility
verdicts, priority scores, or production artifacts.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _q(case_id, text, records):
    return {"case_id": case_id, "text": text, "gold": {"qualification": records}}


def _c(case_id, text, records):
    return {"case_id": case_id, "text": text, "gold": {"compensation": records}}


def _qr(evidence, kind, modality="required", subject="candidate", route="standalone"):
    return {
        "evidence": evidence,
        "kind": kind,
        "modality": modality,
        "subject": subject,
        "route": route,
    }


def _cr(evidence, amount="", period="", component="base", beneficiary="candidate"):
    return {
        "evidence": evidence,
        "amount": amount,
        "period": period,
        "component": component,
        "beneficiary": beneficiary,
    }


TRAIN_CASES = [
    _q(
        "train-q01",
        "Minimum qualifications: Bachelor's degree and 2 years of software experience.",
        [_qr("Bachelor's degree and 2 years of software experience", "combined")],
    ),
    _q(
        "train-q02",
        "A master's degree is preferred but is not required.",
        [_qr("master's degree", "degree", "preferred")],
    ),
    _q(
        "train-q03",
        "You need at least 5 years of relevant professional experience.",
        [_qr("at least 5 years of relevant professional experience", "experience")],
    ),
    _q(
        "train-q04",
        "Applicants must graduate between December 2025 and June 2026.",
        [_qr("graduate between December 2025 and June 2026", "graduation")],
    ),
    _q(
        "train-q05",
        "Our founders hold PhDs from leading universities.",
        [_qr("PhDs", "degree", "context", "company")],
    ),
    _q(
        "train-q06",
        "Several engineers on the team earned master's degrees.",
        [_qr("master's degrees", "degree", "context", "employee")],
    ),
    _q(
        "train-q07",
        "Requires either a bachelor's degree and 3 years of experience or a master's degree and 1 year of experience.",  # noqa: E501
        [
            _qr("a bachelor's degree and 3 years of experience", "combined", route="A"),
            _qr("a master's degree and 1 year of experience", "combined", route="B"),
        ],
    ),
    _q(
        "train-q08",
        "Bachelor's degree or equivalent practical experience is required.",
        [_qr("Bachelor's degree or equivalent practical experience", "combined")],
    ),
    _q(
        "train-q09",
        "A PhD would be a plus; a bachelor's degree meets the minimum requirement.",
        [_qr("PhD", "degree", "preferred"), _qr("bachelor's degree", "degree")],
    ),
    _q(
        "train-q10",
        "No advanced degree is necessary for this position.",
        [_qr("advanced degree", "degree", "waived")],
    ),
    _q(
        "train-q11",
        "Candidates may qualify with four years of work experience or an MSc.",
        [
            _qr("four years of work experience", "experience", route="A"),
            _qr("MSc", "degree", route="B"),
        ],
    ),
    _q(
        "train-q12",
        "Degree or diploma in computer science, engineering, or a related field.",
        [_qr("Degree or diploma", "degree")],
    ),
    _c(
        "train-c01",
        "The base salary range is $120,000 to $155,000 per year.",
        [_cr("$120,000 to $155,000 per year", "$120,000 to $155,000", "year")],
    ),
    _c(
        "train-c02",
        "This contract pays $62 per hour.",
        [_cr("$62 per hour", "$62", "hour")],
    ),
    _c(
        "train-c03",
        "Employees may receive a $5,000 annual performance bonus.",
        [_cr("$5,000 annual performance bonus", "$5,000", "annual", "bonus")],
    ),
    _c(
        "train-c04",
        "Refer a successful candidate and earn a $2,000 referral bonus.",
        [_cr("$2,000 referral bonus", "$2,000", "", "referral_bonus", "referrer")],
    ),
    _c(
        "train-c05",
        "The package includes base pay plus equity grants.",
        [_cr("equity grants", "", "", "equity")],
    ),
    _c(
        "train-c06",
        "Expected compensation is CAD 95,000-115,000 yearly.",
        [_cr("CAD 95,000-115,000 yearly", "CAD 95,000-115,000", "yearly")],
    ),
    _c(
        "train-c07",
        "New hires receive a one-time $3,500 signing bonus.",
        [_cr("one-time $3,500 signing bonus", "$3,500", "one-time", "bonus")],
    ),
    _c(
        "train-c08",
        "The role offers competitive compensation and benefits.",
        [_cr("competitive compensation", "", "", "qualitative")],
    ),
    _c(
        "train-c09",
        "Base compensation ranges from £70,000 to £85,000 a year.",
        [_cr("£70,000 to £85,000 a year", "£70,000 to £85,000", "year")],
    ),
    _c(
        "train-c10",
        "The monthly stipend for this internship is $4,000.",
        [
            _cr(
                "monthly stipend for this internship is $4,000",
                "$4,000",
                "monthly",
                "allowance",
            )
        ],
    ),
]


EVAL_CASES = [
    _q(
        "eval-q01",
        "Minimum requirement: a BS plus two years in backend development.",
        [_qr("a BS plus two years in backend development", "combined")],
    ),
    _q(
        "eval-q02",
        "We prefer candidates with an MS, although a bachelor's is sufficient.",
        [_qr("MS", "degree", "preferred"), _qr("bachelor's", "degree")],
    ),
    _q(
        "eval-q03",
        "Eligibility requires graduation from May 2026 through August 2027.",
        [_qr("graduation from May 2026 through August 2027", "graduation")],
    ),
    _q(
        "eval-q04",
        "Our chief scientist has a PhD in physics. Applicants need three or more years building production systems.",  # noqa: E501
        [
            _qr("PhD in physics", "degree", "context", "employee"),
            _qr("three or more years building production systems", "experience"),
        ],
    ),
    _q(
        "eval-q05",
        "Qualify through 5+ years of industry work, or through a master's degree with 2 years of industry work.",  # noqa: E501
        [
            _qr("5+ years of industry work", "experience", route="A"),
            _qr(
                "a master's degree with 2 years of industry work", "combined", route="B"
            ),
        ],
    ),
    _q(
        "eval-q06",
        "You do not need a graduate degree; an undergraduate degree is enough.",
        [
            _qr("graduate degree", "degree", "waived"),
            _qr("undergraduate degree", "degree"),
        ],
    ),
    _c(
        "eval-c01",
        "Base pay for this position is between $98,500 and $132,000 annually.",
        [_cr("$98,500 and $132,000 annually", "$98,500 and $132,000", "annually")],
    ),
    _c(
        "eval-c02",
        "We pay €45 an hour for this consulting engagement.",
        [_cr("€45 an hour", "€45", "hour")],
    ),
    _c(
        "eval-c03",
        "A referring employee can collect a $1,500 bonus after the new hire starts.",
        [_cr("$1,500 bonus", "$1,500", "", "referral_bonus", "referrer")],
    ),
    _c(
        "eval-c04",
        "Total rewards include restricted stock units and a discretionary cash bonus.",
        [
            _cr("restricted stock units", "", "", "equity"),
            _cr("discretionary cash bonus", "", "", "bonus"),
        ],
    ),
    _c(
        "eval-c05",
        "This internship provides a stipend of $7,200 for the summer.",
        [_cr("stipend of $7,200 for the summer", "$7,200", "summer", "allowance")],
    ),
    {
        "case_id": "eval-mixed01",
        "text": "A bachelor's degree is required. Compensation details are not listed in this posting.",  # noqa: E501
        "gold": {"qualification": [_qr("bachelor's degree", "degree")]},
    },
]


CHOICES = {
    "kind": ["degree", "experience", "graduation", "combined"],
    "modality": ["required", "preferred", "waived", "context"],
    "subject": ["candidate", "company", "employee"],
    "route": ["standalone", "A", "B"],
    "component": [
        "base",
        "bonus",
        "equity",
        "allowance",
        "referral_bonus",
        "qualitative",
    ],
    "beneficiary": ["candidate", "referrer"],
}


def evidence_values(gold):
    for structure, records in gold.items():
        choice_fields = (
            {"kind", "modality", "subject", "route"}
            if structure == "qualification"
            else {"component", "beneficiary"}
        )
        for record in records:
            for field, value in record.items():
                if field not in choice_fields and value:
                    yield value


def _record_tuple(structure, record):
    fields = (
        ("evidence", "kind", "modality", "subject", "route")
        if structure == "qualification"
        else ("evidence", "amount", "period", "component", "beneficiary")
    )
    return (structure, *(record.get(field) or "" for field in fields))


def score_predictions(cases, predictions):
    if len(cases) != len(predictions):
        raise ValueError("prediction count differs from case count")
    exact = true_positives = false_positives = false_negatives = 0
    details = []
    for case, prediction in zip(cases, predictions, strict=True):
        gold_records = {
            _record_tuple(structure, record)
            for structure, records in case["gold"].items()
            for record in records
        }
        predicted_records = {
            _record_tuple(structure, record)
            for structure, records in prediction.items()
            if structure in {"qualification", "compensation"}
            for record in (records or [])
        }
        is_exact = gold_records == predicted_records
        exact += int(is_exact)
        true_positives += len(gold_records & predicted_records)
        false_positives += len(predicted_records - gold_records)
        false_negatives += len(gold_records - predicted_records)
        details.append(
            {
                "case_id": case.get("case_id"),
                "exact": is_exact,
                "gold": sorted(gold_records),
                "predicted": sorted(predicted_records),
            }
        )
    precision_denominator = true_positives + false_positives
    recall_denominator = true_positives + false_negatives
    return {
        "exact_cases": exact,
        "total_cases": len(cases),
        "exact_accuracy": exact / len(cases) if cases else 0.0,
        "record_true_positives": true_positives,
        "record_false_positives": false_positives,
        "record_false_negatives": false_negatives,
        "record_precision": true_positives / precision_denominator
        if precision_denominator
        else 0.0,
        "record_recall": true_positives / recall_denominator
        if recall_denominator
        else 0.0,
        "details": details,
    }


def build_schema(model):
    return (
        model.create_schema()
        .structure(
            "qualification", mode="natural", anchor="evidence", occurrence_policy="all"
        )
        .field(
            "evidence",
            dtype="str",
            description="Exact qualification clause",
            cardinality="required_one",
        )
        .field("kind", dtype="str", choices=CHOICES["kind"], cardinality="required_one")
        .field(
            "modality",
            dtype="str",
            choices=CHOICES["modality"],
            cardinality="required_one",
        )
        .field(
            "subject",
            dtype="str",
            choices=CHOICES["subject"],
            cardinality="required_one",
        )
        .field(
            "route", dtype="str", choices=CHOICES["route"], cardinality="required_one"
        )
        .structure(
            "compensation", mode="natural", anchor="evidence", occurrence_policy="all"
        )
        .field(
            "evidence",
            dtype="str",
            description="Exact compensation clause",
            cardinality="required_one",
        )
        .field(
            "amount",
            dtype="str",
            description="Exact monetary amount or range",
            cardinality="optional_one",
        )
        .field(
            "period",
            dtype="str",
            description="Exact pay period",
            cardinality="optional_one",
        )
        .field(
            "component",
            dtype="str",
            choices=CHOICES["component"],
            cardinality="required_one",
        )
        .field(
            "beneficiary",
            dtype="str",
            choices=CHOICES["beneficiary"],
            cardinality="required_one",
        )
        .build()
    )


def make_training_examples(cases):
    from gliner2.training import (  # noqa: PLC0415
        ChoiceField,
        InputExample,
        Structure,
    )

    examples = []
    for case in cases:
        structures = []
        for structure, records in case["gold"].items():
            for record in records:
                choice_fields = (
                    {"kind", "modality", "subject", "route"}
                    if structure == "qualification"
                    else {"component", "beneficiary"}
                )
                fields = {
                    field: ChoiceField(value, CHOICES[field])
                    if field in choice_fields
                    else value
                    for field, value in record.items()
                }
                structures.append(
                    Structure(
                        structure,
                        mode="natural",
                        anchor="evidence",
                        occurrence_policy="all",
                        **fields,
                    )
                )
        examples.append(InputExample(text=case["text"], structures=structures))
    return examples


def predict(model, cases, threshold):
    schema = build_schema(model)
    return [model.extract(case["text"], schema, threshold=threshold) for case in cases]


def write_report(  # noqa: PLR0913
    path, model_name, threshold, baseline, tuned=None, training=None
):
    report = {
        "model": model_name,
        "threshold": threshold,
        "train_cases": len(TRAIN_CASES),
        "eval_cases": len(EVAL_CASES),
        "baseline": baseline,
        "fine_tuned": tuned,
        "training": training,
        "scope": "controlled learnability experiment; not production accuracy",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="fastino/gliner2.5-base-v1")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--steps", type=int, default=80)
    parser.add_argument("--device", choices=["cpu", "mps", "cuda"], default="cpu")
    parser.add_argument("--baseline-only", action="store_true")
    args = parser.parse_args()
    if args.out.exists():
        parser.error("output path already exists")

    from gliner2 import AutoExtractor  # noqa: PLC0415

    model = AutoExtractor.from_pretrained(args.model).to(args.device)
    baseline_predictions = predict(model, EVAL_CASES, args.threshold)
    baseline = score_predictions(EVAL_CASES, baseline_predictions)
    if args.baseline_only:
        write_report(args.out, args.model, args.threshold, baseline)
        print(
            json.dumps(
                {"baseline": {k: v for k, v in baseline.items() if k != "details"}},
                indent=2,
            )
        )
        return

    from gliner2.training import ExtractorTrainer, TrainingConfig  # noqa: PLC0415

    training_dir = args.out.parent / f"{args.out.stem}-checkpoints"
    config = TrainingConfig(
        output_dir=str(training_dir),
        experiment_name="jd-gliner2-potential",
        max_steps=args.steps,
        batch_size=2,
        gradient_accumulation_steps=2,
        use_lora=True,
        lora_r=8,
        lora_alpha=16,
        save_adapter_only=True,
        eval_strategy="no",
        logging_steps=5,
        num_workers=0,
        fp16=args.device == "cuda",
        bf16=False,
        max_len=256,
    )
    trainer = ExtractorTrainer(model=model, config=config)
    training = trainer.train(train_data=make_training_examples(TRAIN_CASES))
    tuned_predictions = predict(model, EVAL_CASES, args.threshold)
    tuned = score_predictions(EVAL_CASES, tuned_predictions)
    write_report(args.out, args.model, args.threshold, baseline, tuned, training)
    print(
        json.dumps(
            {
                "baseline": {k: v for k, v in baseline.items() if k != "details"},
                "fine_tuned": {k: v for k, v in tuned.items() if k != "details"},
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
