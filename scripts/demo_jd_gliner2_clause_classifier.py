"""Test the semantic classifier after evidence candidates are separated."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

if __package__:
    from scripts.demo_jd_gliner2_potential import CHOICES, EVAL_CASES, TRAIN_CASES
else:
    from demo_jd_gliner2_potential import CHOICES, EVAL_CASES, TRAIN_CASES

TASKS = {
    "qualification": ("kind", "modality", "subject"),
    "compensation": ("component", "beneficiary"),
}


def expand_records(cases):
    result = []
    for case in cases:
        for structure, records in case["gold"].items():
            for record in records:
                target = record["evidence"]
                result.append(
                    {
                        "case_id": case["case_id"],
                        "structure": structure,
                        "target": target,
                        "input": f"Context: {case['text']} Target: {target}",
                        "labels": {task: record[task] for task in TASKS[structure]},
                    }
                )
    return result


def score_labels(records, predictions):
    if len(records) != len(predictions):
        raise ValueError("prediction count differs from record count")
    exact = correct = total = 0
    details = []
    for record, prediction in zip(records, predictions, strict=True):
        labels = record["labels"]
        matches = {
            task: prediction.get(task) == expected for task, expected in labels.items()
        }
        exact += int(all(matches.values()))
        correct += sum(matches.values())
        total += len(matches)
        details.append(
            {
                "case_id": record["case_id"],
                "target": record["target"],
                "gold": labels,
                "predicted": {task: prediction.get(task) for task in labels},
                "exact": all(matches.values()),
            }
        )
    return {
        "exact_records": exact,
        "total_records": len(records),
        "exact_accuracy": exact / len(records) if records else 0.0,
        "correct_labels": correct,
        "total_labels": total,
        "label_accuracy": correct / total if total else 0.0,
        "details": details,
    }


def make_schema(model, structure):
    schema = model.create_schema()
    for task in TASKS[structure]:
        schema.classification(task, CHOICES[task])
    return schema.build()


def predict(model, records):
    schemas = {structure: make_schema(model, structure) for structure in TASKS}
    model.eval()
    return [
        model.extract(record["input"], schemas[record["structure"]])
        for record in records
    ]


def make_training_examples(records):
    from gliner2.training import Classification, InputExample  # noqa: PLC0415

    return [
        InputExample(
            text=record["input"],
            classifications=[
                Classification(task, CHOICES[task], expected)
                for task, expected in record["labels"].items()
            ],
        )
        for record in records
    ]


def compact(metrics):
    return {key: value for key, value in metrics.items() if key != "details"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="fastino/gliner2.5-base-v1")
    parser.add_argument("--steps", type=int, default=40)
    parser.add_argument("--device", choices=["cpu", "mps", "cuda"], default="cpu")
    parser.add_argument("--baseline-only", action="store_true")
    args = parser.parse_args()
    if args.out.exists():
        parser.error("output path already exists")

    from gliner2 import AutoExtractor  # noqa: PLC0415

    model = AutoExtractor.from_pretrained(args.model).to(args.device)
    train_records = expand_records(TRAIN_CASES)
    eval_records = expand_records(EVAL_CASES)
    baseline = score_labels(eval_records, predict(model, eval_records))
    tuned = training = None

    if not args.baseline_only:
        from gliner2.training import (  # noqa: PLC0415
            ExtractorTrainer,
            TrainingConfig,
        )

        training_dir = args.out.parent / f"{args.out.stem}-checkpoints"
        config = TrainingConfig(
            output_dir=str(training_dir),
            experiment_name="jd-gliner2-clause-classifier",
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
        training = trainer.train(make_training_examples(train_records))
        tuned = score_labels(eval_records, predict(model, eval_records))

    report = {
        "model": args.model,
        "train_records": len(train_records),
        "eval_records": len(eval_records),
        "baseline": baseline,
        "fine_tuned": tuned,
        "training": training,
        "scope": "gold evidence candidate classification; not end-to-end accuracy",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "baseline": compact(baseline),
                "fine_tuned": compact(tuned) if tuned else None,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
