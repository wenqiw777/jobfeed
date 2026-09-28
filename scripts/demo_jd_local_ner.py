"""Offline GLiNER span baseline; no eligibility, scoring, or LLM calls."""

import argparse
import json
import time
from pathlib import Path

LABELS = [
    "graduation date",
    "years of experience",
    "education degree",
    "salary amount",
    "salary currency",
    "salary payment period",
]


def windows(text, width=1000, overlap=300):
    if width <= 0 or not 0 <= overlap < width:
        raise ValueError("require width > overlap >= 0")
    result = []
    start = 0
    while start < len(text):
        result.append((start, text[start : start + width]))
        if start + width >= len(text):
            break
        start += width - overlap
    return result


def merge_entities(text, predictions):
    result = {}
    for offset, entities in predictions:
        for entity in entities:
            start, end = offset + entity["start"], offset + entity["end"]
            if not 0 <= start < end <= len(text) or text[start:end] != entity["text"]:
                raise ValueError("model evidence does not match input")
            key = (start, end, entity["label"])
            restored = {**entity, "start": start, "end": end}
            if key not in result or result[key]["score"] < entity["score"]:
                result[key] = restored
    return sorted(result.values(), key=lambda x: (x["start"], x["end"], x["label"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--device", default="cpu", choices=["cpu", "mps", "cuda"])
    parser.add_argument("--batch-size", type=int, default=4)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("batch size must be positive")
    if args.out.exists():
        parser.error("output directory already exists; select a new run directory")

    # Optional runtime stays outside the project's test environment.
    import torch  # noqa: PLC0415
    from gliner import GLiNER  # noqa: PLC0415

    torch.set_num_threads(4)
    started = time.perf_counter()
    model_name = "urchade/gliner_small-v2.1"
    model = GLiNER.from_pretrained(model_name).to(args.device).eval()
    loaded_seconds = time.perf_counter() - started
    samples = json.loads(args.input.read_text())
    args.out.mkdir(parents=True)
    results = []
    for sample in samples:
        began = time.perf_counter()
        document = sample["document"]
        blocks = {}
        window_count = 0
        for block in ("title", "text"):
            source = document[block]
            chunks = windows(source)
            # Fail visibly rather than silently discard a long window's tail.
            for _, chunk in chunks:
                if (
                    len(list(model.data_processor.words_splitter(chunk)))
                    > model.config.max_len
                ):
                    raise ValueError(
                        f"sample {sample['sample_id']} exceeds model word budget"
                    )
            predictions = []
            for at in range(0, len(chunks), args.batch_size):
                batch = chunks[at : at + args.batch_size]
                entities = model.batch_predict_entities(
                    [chunk for _, chunk in batch],
                    LABELS,
                    threshold=0.3,
                    flat_ner=False,
                    multi_label=True,
                )
                if len(entities) != len(batch):
                    raise ValueError("prediction count differs from input count")
                predictions.extend(
                    (offset, spans)
                    for (offset, _), spans in zip(batch, entities, strict=True)
                )
            blocks[block] = merge_entities(source, predictions)
            window_count += len(chunks)
        result = {
            "sample_id": sample["sample_id"],
            "job_id": document["job_id"],
            "source_completeness": document["completeness"],
            "entities": blocks,
            "field_states": {
                label: "detected_unverified"
                if any(e["label"] == label for spans in blocks.values() for e in spans)
                else "no_detection"
                for label in LABELS
            },
            "window_count": window_count,
            "seconds": round(time.perf_counter() - began, 3),
        }
        (args.out / f"{sample['sample_id']}.json").write_text(
            json.dumps(result, indent=2)
        )
        results.append(result)
        print(
            f"{len(results)}/{len(samples)} sample={sample['sample_id']} "
            f"seconds={result['seconds']}",
            flush=True,
        )
    summary = {
        "model": model_name,
        "device": args.device,
        "threshold": 0.3,
        "labels": LABELS,
        "window_chars": 1000,
        "overlap_chars": 300,
        "batch_size": args.batch_size,
        "samples": len(results),
        "model_load_seconds": round(loaded_seconds, 3),
        "inference_seconds": round(sum(r["seconds"] for r in results), 3),
        "windows": sum(r["window_count"] for r in results),
        "detected_jobs_by_label": {
            label: sum(
                r["field_states"][label] == "detected_unverified" for r in results
            )
            for label in LABELS
        },
        "limitations": [
            "NER spans only; no modality, subject, AND/OR or salary association model",
            "No detection is not a verified absence; scores are uncalibrated",
            "No production verdicts or priority scores; no generative model calls",
            "Saved source text, not a new live website extraction test",
        ],
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
