"""Fine-tune ModernBERT as a local high-recall Block candidate detector.

The labels are exploratory Luna decisions, not human gold.  This experiment can
measure teacher imitation and escalation rate; it cannot establish accuracy.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import numpy as np

from scripts.demo_local_blocker_candidate import choose_recall_threshold, load_samples

SEED = 20260905


def split_name(sample_id):
    remainder = sample_id % 5
    if remainder == 0:
        return "test"
    if remainder == 1:
        return "development"
    return "train"


def max_document_logits(logits):
    if not logits:
        raise ValueError("at least one chunk logit is required")
    return [max(row[column] for row in logits) for column in range(len(logits[0]))]


def _tokenize_documents(samples, tokenizer, max_tokens):
    special = tokenizer.num_special_tokens_to_add(pair=False)
    capacity = max_tokens - special
    documents = []
    source_tokens = 0
    for sample in samples:
        text = f"{sample['title']}\n{sample['location']}\n\n{sample['text']}"
        token_ids = tokenizer.encode(text, add_special_tokens=False, truncation=False)
        parts = [
            token_ids[start : start + capacity]
            for start in range(0, len(token_ids), capacity)
        ]
        if not parts:
            parts = [[]]
        rebuilt = [token for part in parts for token in part]
        if rebuilt != token_ids:
            raise ValueError(f"token coverage failed for sample {sample['sample_id']}")
        documents.append(
            {
                "sample_id": sample["sample_id"],
                "parts": [
                    tokenizer.build_inputs_with_special_tokens(part) for part in parts
                ],
                "content_lengths": [len(part) for part in parts],
            }
        )
        source_tokens += len(token_ids)
    return documents, {
        "documents": len(documents),
        "source_tokens": source_tokens,
        "processed_content_tokens": source_tokens,
        "dropped_tokens": 0,
        "chunks": sum(len(document["parts"]) for document in documents),
        "multi_chunk_documents": sum(
            len(document["parts"]) > 1 for document in documents
        ),
        "max_chunk_tokens": max_tokens,
    }


def _tensors(parts, pad_id, torch):
    width = max(len(part) for part in parts)
    input_ids = torch.full((len(parts), width), pad_id, dtype=torch.long, device="cuda")
    attention_mask = torch.zeros_like(input_ids)
    for row, part in enumerate(parts):
        input_ids[row, : len(part)] = torch.tensor(part, device="cuda")
        attention_mask[row, : len(part)] = 1
    return {"input_ids": input_ids, "attention_mask": attention_mask}


def _document_logits(model, document, pad_id, torch):
    output = model(**_tensors(document["parts"], pad_id, torch)).logits.float()
    return output.max(dim=0, keepdim=True).values


def _predict(model, documents, indices, pad_id, torch):
    model.eval()
    scores = {}
    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
        for position, index in enumerate(indices, start=1):
            logits = _document_logits(model, documents[index], pad_id, torch)
            scores[documents[index]["sample_id"]] = float(logits.softmax(-1)[0, 1])
            if position % 50 == 0:
                print(f"scored {position}/{len(indices)}", flush=True)
    return scores


def _metrics(scores, labels, threshold):
    predicted = {sample_id: score >= threshold for sample_id, score in scores.items()}
    true_positive = sum(predicted[key] and labels[key] == 1 for key in predicted)
    false_positive = sum(predicted[key] and labels[key] == 0 for key in predicted)
    positive = sum(labels[key] == 1 for key in predicted)
    proposed = true_positive + false_positive
    return {
        "documents": len(predicted),
        "teacher_blocks": positive,
        "proposed": proposed,
        "recall_vs_teacher": true_positive / positive if positive else None,
        "precision_vs_teacher": true_positive / proposed if proposed else None,
        "escalation_rate": proposed / len(predicted) if predicted else None,
    }


def main():  # noqa: C901
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model-out", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--accumulation", type=int, default=8)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if args.out.exists() or args.model_out.exists():
        parser.error("output path already exists")

    import torch  # noqa: PLC0415
    from torch.nn import functional  # noqa: PLC0415
    from transformers import (  # noqa: PLC0415
        AutoModelForSequenceClassification,
        AutoTokenizer,
    )

    if not torch.cuda.is_available():
        raise RuntimeError("this experiment must run on CUDA")
    torch.manual_seed(SEED)
    torch.cuda.manual_seed_all(SEED)
    np.random.seed(SEED)
    random.seed(SEED)

    samples = load_samples(args.input, args.manifest)
    comparison = json.loads(args.comparison.read_text())
    teacher = comparison["strategies"]["luna"]
    labels = {row["sample_id"]: int(row["decision"] == "block") for row in teacher}
    tokenizer = AutoTokenizer.from_pretrained("answerdotai/ModernBERT-base")
    documents, coverage = _tokenize_documents(samples, tokenizer, args.max_tokens)
    model = AutoModelForSequenceClassification.from_pretrained(
        "answerdotai/ModernBERT-base",
        num_labels=2,
        attn_implementation="sdpa",
        reference_compile=False,
        id2label={0: "NO_BLOCK_CANDIDATE", 1: "BLOCK_CANDIDATE"},
        label2id={"NO_BLOCK_CANDIDATE": 0, "BLOCK_CANDIDATE": 1},
    ).to("cuda")
    model.gradient_checkpointing_enable(
        gradient_checkpointing_kwargs={"use_reentrant": False}
    )
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=2e-5, weight_decay=0.01, foreach=False
    )
    train_indices = [
        index
        for index, row in enumerate(documents)
        if split_name(row["sample_id"]) == "train"
    ]
    development_indices = [
        index
        for index, row in enumerate(documents)
        if split_name(row["sample_id"]) == "development"
    ]
    test_indices = [
        index
        for index, row in enumerate(documents)
        if split_name(row["sample_id"]) == "test"
    ]
    positives = sum(labels[documents[index]["sample_id"]] for index in train_indices)
    class_weights = torch.tensor(
        [1.0, (len(train_indices) - positives) / max(1, positives)], device="cuda"
    )

    if args.smoke:
        train_indices = [
            train_indices[0],
            max(train_indices, key=lambda i: len(documents[i]["parts"])),
        ]
        epochs = 1
    else:
        epochs = args.epochs

    started = time.monotonic()
    best_state = None
    best_key = None
    epoch_reports = []
    for epoch in range(epochs):
        order = list(train_indices)
        random.Random(SEED + epoch).shuffle(order)
        model.train()
        optimizer.zero_grad(set_to_none=True)
        for position, index in enumerate(order, start=1):
            with torch.autocast("cuda", dtype=torch.bfloat16):
                logits = _document_logits(
                    model, documents[index], tokenizer.pad_token_id, torch
                )
                target = torch.tensor(
                    [labels[documents[index]["sample_id"]]], device="cuda"
                )
                loss = functional.cross_entropy(logits, target, weight=class_weights)
            (loss / args.accumulation).backward()
            if position % args.accumulation == 0 or position == len(order):
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                optimizer.zero_grad(set_to_none=True)
            if position % 25 == 0 or position == len(order):
                print(
                    f"epoch {epoch + 1}/{epochs} trained {position}/{len(order)} "
                    f"loss={float(loss.detach()):.4f}",
                    flush=True,
                )
        development_scores = _predict(
            model, documents, development_indices, tokenizer.pad_token_id, torch
        )
        threshold = choose_recall_threshold(
            development_scores, labels, minimum_recall=1.0
        )
        metrics = _metrics(development_scores, labels, threshold)
        key = (metrics["escalation_rate"], -float(threshold))
        epoch_reports.append(
            {"epoch": epoch + 1, "development_at_full_recall": metrics}
        )
        if best_key is None or key < best_key:
            best_key = key
            best_state = {
                name: value.detach().cpu() for name, value in model.state_dict().items()
            }

    if args.smoke:
        report = {
            "smoke": "passed",
            "gpu": torch.cuda.get_device_name(0),
            "trained_documents": len(train_indices),
            "coverage": coverage,
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        return

    model.load_state_dict(best_state)
    development_scores = _predict(
        model, documents, development_indices, tokenizer.pad_token_id, torch
    )
    test_scores = _predict(
        model, documents, test_indices, tokenizer.pad_token_id, torch
    )
    operating_points = {}
    for target_recall in (1.0, 0.95, 0.9, 0.8):
        threshold = choose_recall_threshold(
            development_scores, labels, minimum_recall=target_recall
        )
        operating_points[str(target_recall)] = {
            "threshold": threshold,
            "development": _metrics(development_scores, labels, threshold),
            "test": _metrics(test_scores, labels, threshold),
        }

    report = {
        "teacher": "gpt-5.6-luna exploratory decisions; not human gold",
        "task": "local high-recall candidate proposal; cannot issue Block",
        "model": "answerdotai/ModernBERT-base end-to-end fine-tune",
        "gpu": torch.cuda.get_device_name(0),
        "epochs": epochs,
        "train_documents": len(train_indices),
        "development_documents": len(development_indices),
        "test_documents": len(test_indices),
        "coverage": coverage,
        "epoch_reports": epoch_reports,
        "training_and_scoring_seconds": time.monotonic() - started,
        "operating_points": operating_points,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.model_out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2))
    model.save_pretrained(args.model_out, safe_serialization=True)
    tokenizer.save_pretrained(args.model_out)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
