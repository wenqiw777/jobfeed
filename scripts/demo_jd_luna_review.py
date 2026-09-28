"""Run selective Luna review, optionally replaying saved primary demo outputs.

No scoring, production writes, second retry, or hidden independent model calls.
"""

import argparse
import asyncio
import json
from pathlib import Path

from scripts.demo_jd_contract import call_model, extraction_schema
from scripts.jd_luna_review import review_prompt, review_reasons, review_schema
from scripts.jd_semantic_contract import Document


async def review_task(  # noqa: PLR0913 -- explicit routing flags and testable transport
    document,
    task,
    primary,
    *,
    previous=None,
    audit=False,
    proposed_block=False,
    model="gpt-5.6-luna",
    timeout=240,
    caller=None,
):
    caller = caller or call_model
    reasons = review_reasons(
        primary,
        task,
        document,
        previous=previous,
        audit=audit,
        proposed_block=proposed_block,
    )
    review = None
    if reasons:
        review = await caller(
            document,
            task,
            model,
            timeout=timeout,
            review_context={
                "reasons": reasons,
                "prior_output": (previous or primary).get("output", {}),
                "candidate_output": primary.get("output", {}),
                "validation_errors": primary.get("validation_errors", []),
                "execution_error": primary.get("error"),
            },
        )
    if not reasons:
        accepted, state = primary, "not_reviewed"
    elif review["state"] == "valid" and review.get("review_decision") == "resolved":
        accepted, state = review, "reviewed"
    else:
        accepted, state = None, "review_required"
    return {
        "task": task,
        "first": primary,
        "previous": previous,
        "review_reasons": reasons,
        "semantic_review": review,
        "accepted": accepted,
        "resolution_state": state,
        "proposed_block": proposed_block,
        "production_verdict": None,
        "observed_score": None,
        "human_accuracy": None,
    }


def load_inputs(args):
    entries = json.loads(args.inputs.read_text())
    ids = [r["sample_id"] for r in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate input sample IDs")
    if not set(args.audit_ids) <= set(ids):
        raise ValueError("audit IDs must exist in input documents")
    if args.ids:
        if len(args.ids) != len(set(args.ids)) or not set(args.ids) <= set(ids):
            raise ValueError("selected IDs must be unique and present")
        entries = [r for r in entries if r["sample_id"] in args.ids]
    if not set(args.audit_ids) <= {r["sample_id"] for r in entries}:
        raise ValueError("audit IDs must be included in selected samples")
    if len(args.tasks) != len(set(args.tasks)):
        raise ValueError("task types must be unique")
    if args.baseline:
        saved = json.loads((args.baseline / "inputs.json").read_text())
        by_id = {r["sample_id"]: r["document"] for r in saved}
        for entry in entries:
            if by_id.get(entry["sample_id"]) != entry["document"]:
                raise ValueError("baseline and current document differ")
    return entries


async def run(args):
    if args.output.exists():
        raise ValueError("output directory already exists; choose a new one")
    entries = load_inputs(args)
    args.output.mkdir(parents=True)
    (args.output / "inputs.json").write_text(
        json.dumps(entries, ensure_ascii=False, indent=2)
    )
    (args.output / "review_prompts.json").write_text(
        json.dumps({task: review_prompt(task) for task in args.tasks}, indent=2)
    )
    for task in args.tasks:
        (args.output / f"{task}.schema.json").write_text(
            json.dumps(review_schema(extraction_schema(task)), indent=2)
        )
    semaphore = asyncio.Semaphore(args.concurrency)

    async def execute(entry, task):
        sid = entry["sample_id"]
        document = Document.model_validate(entry["document"])
        async with semaphore:
            previous = None
            if args.baseline:
                saved = json.loads((args.baseline / f"{sid}-{task}.json").read_text())
                primary = saved.get("accepted") or saved.get("repair") or saved["first"]
                if saved.get("repair"):
                    previous = saved["first"]
            else:
                primary = await call_model(
                    document, task, args.model, timeout=args.timeout
                )
            row = await review_task(
                document,
                task,
                primary,
                previous=previous,
                audit=sid in args.audit_ids,
                model=args.review_model,
                timeout=args.timeout,
            )
        row["sample_id"] = sid
        (args.output / f"{sid}-{task}.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=2)
        )
        print(
            json.dumps(
                {
                    "sample_id": sid,
                    "task": task,
                    "reasons": row["review_reasons"],
                    "resolution": row["resolution_state"],
                }
            ),
            flush=True,
        )
        return row

    rows = await asyncio.gather(
        *(execute(entry, task) for entry in entries for task in args.tasks)
    )
    summary = {
        "sample_count": len(entries),
        "task_count": len(rows),
        "primary_calls": 0 if args.baseline else len(rows),
        "review_calls": sum(r["semantic_review"] is not None for r in rows),
        "reviewed_tasks": sum(r["resolution_state"] == "reviewed" for r in rows),
        "not_reviewed_tasks": sum(
            r["resolution_state"] == "not_reviewed" for r in rows
        ),
        "review_required_tasks": sum(
            r["resolution_state"] == "review_required" for r in rows
        ),
        "audit_ids": args.audit_ids,
        "review_model": args.review_model,
        "human_accuracy": None,
        "limitations": [
            "Stored-source development replay, not an independent gold test.",
            "Model review and quote validation do not prove semantic accuracy.",
            "Unknown and unrepresentable facts remain unresolved; "
            "no production scoring.",
        ],
    }
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ids", type=int, nargs="*")
    parser.add_argument("--audit-ids", type=int, nargs="*", default=[])
    parser.add_argument(
        "--tasks",
        choices=("qualification", "salary"),
        nargs="+",
        default=["qualification", "salary"],
    )
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--review-model", default="gpt-5.6-luna")
    parser.add_argument("--concurrency", type=int, choices=range(1, 5), default=4)
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
