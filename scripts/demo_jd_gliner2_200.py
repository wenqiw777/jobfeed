"""Run a raw-JD GLiNER2 evidence-candidate baseline on the 200-SDE cohort."""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

FIELD_LABELS = {
    "graduation": "candidate graduation date or enrollment requirement",
    "yoe": "candidate years of work experience requirement",
    "degree": "candidate academic degree requirement",
    "compensation": "salary or compensation amount",
}


def validate_entities(text, extraction):
    entities = extraction.get("entities")
    if not isinstance(entities, dict):
        raise ValueError("extraction does not contain an entities object")
    for label, candidates in entities.items():
        if not isinstance(candidates, list):
            raise ValueError(f"entities for {label!r} are not a list")
        for candidate in candidates:
            start = candidate.get("start")
            end = candidate.get("end")
            evidence = candidate.get("text")
            if (
                not isinstance(start, int)
                or not isinstance(end, int)
                or start < 0
                or end <= start
                or end > len(text)
            ):
                raise ValueError(f"entity span is out of bounds: {candidate!r}")
            if text[start:end] != evidence:
                raise ValueError(f"entity text is not verbatim: {candidate!r}")
    return entities


def _field_stats(results, fields):
    stats = {}
    for field in fields:
        counts = [len(result["entities"][field]) for result in results]
        detected = sum(count > 0 for count in counts)
        stats[field] = {
            "samples_with_candidates": detected,
            "samples_with_no_detection": len(results) - detected,
            "candidate_count": sum(counts),
        }
    return stats


def summarize_results(results, fields=tuple(FIELD_LABELS)):
    required = set(fields)
    grouped = defaultdict(list)
    for result in results:
        present = set(result["entities"])
        if missing := required - present:
            raise ValueError(
                f"sample {result['sample_id']} missing fields: {sorted(missing)}"
            )
        grouped[result["source_group"]].append(result)
    return {
        "samples": len(results),
        "fields": _field_stats(results, fields),
        "source_groups": {
            group: _field_stats(group_results, fields)
            for group, group_results in sorted(grouped.items())
        },
        "interpretation": (
            "no_detection means the model returned no evidence candidate; "
            "it does not mean the requirement is absent"
        ),
    }


def run_extraction(model, samples, options):
    texts = [sample["document"]["text"] for sample in samples]
    started = time.monotonic()
    raw_results = model.batch_extract_entities_long(
        texts,
        list(FIELD_LABELS.values()),
        batch_size=options["batch_size"],
        threshold=options["threshold"],
        include_confidence=True,
        include_spans=True,
        chunk_size=options["chunk_size"],
        chunk_overlap=options["overlap"],
    )
    if len(raw_results) != len(samples):
        raise ValueError("model result count differs from sample count")

    results = []
    for sample, raw in zip(samples, raw_results, strict=True):
        by_label = validate_entities(sample["document"]["text"], raw)
        results.append(
            {
                "sample_id": sample["sample_id"],
                "source_group": sample["source_group"],
                "job_id": sample["document"]["job_id"],
                "title": sample["document"]["title"],
                "source_url": sample["document"]["source_url"],
                "entities": {
                    field: by_label.get(label, [])
                    for field, label in FIELD_LABELS.items()
                },
            }
        )
    return results, time.monotonic() - started


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="fastino/gliner2.5-base-v1")
    parser.add_argument("--device", choices=["cpu", "mps", "cuda"], default="cpu")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--threshold", type=float, default=0.3)
    parser.add_argument("--chunk-size", type=int, default=384)
    parser.add_argument("--overlap", type=int, default=64)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("output path already exists")

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    samples = payload.get("samples")
    if not isinstance(samples, list) or not samples:
        parser.error("input must contain a non-empty samples list")

    from gliner2 import AutoExtractor  # noqa: PLC0415

    model = AutoExtractor.from_pretrained(args.model).to(args.device)
    options = {
        "batch_size": args.batch_size,
        "threshold": args.threshold,
        "chunk_size": args.chunk_size,
        "overlap": args.overlap,
    }
    results, elapsed_seconds = run_extraction(model, samples, options)
    summary = summarize_results(results)
    summary.update(
        {
            "model": args.model,
            "device": args.device,
            "threshold": args.threshold,
            "chunk_size": args.chunk_size,
            "chunk_overlap": args.overlap,
            "elapsed_seconds": elapsed_seconds,
            "documents_per_second": len(results) / elapsed_seconds,
            "scope": "raw-JD evidence-candidate baseline; not fact accuracy",
        }
    )
    report = {"summary": summary, "results": results}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
