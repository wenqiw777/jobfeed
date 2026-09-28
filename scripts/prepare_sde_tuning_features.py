"""Cache existing features for the fresh review sample and measure each stage."""

import json
import time
from pathlib import Path

import scipy.sparse as sp

from jobfeed.adapters.ml._embedder import FastEmbedEmbedder
from jobfeed.adapters.ml._vectorize import featurize_sde_batch
from jobfeed.domain.ml_features import extract_features

OUT = Path("data/sde-xgb-tuning-v1")


def main():
    rows = [
        json.loads(x)
        for x in (OUT / "fresh-review-input.jsonl").read_text().splitlines()
    ]
    embedder = FastEmbedEmbedder()
    start = time.monotonic()
    features = [extract_features(r["title"], r["jd_text"]) for r in rows]
    structured_seconds = time.monotonic() - start
    start = time.monotonic()
    embeddings = embedder.embed_batch(
        [embedder.format_input(r["title"], r["jd_text"]) for r in rows]
    )
    embedding_seconds = time.monotonic() - start
    start = time.monotonic()
    matrix = featurize_sde_batch(
        features, embeddings, [r["title"] for r in rows], [r["jd_text"] for r in rows]
    )
    vector_seconds = time.monotonic() - start
    sp.save_npz(OUT / "fresh-features.npz", matrix)
    (OUT / "fresh-feature-ids.json").write_text(json.dumps([r["job_id"] for r in rows]))
    timing = {
        "rows": len(rows),
        "structured_seconds": structured_seconds,
        "embedding_seconds": embedding_seconds,
        "vector_seconds": vector_seconds,
        "includes_embedder_startup": True,
    }
    (OUT / "fresh-feature-timing.json").write_text(json.dumps(timing, indent=2))
    print(timing, flush=True)


if __name__ == "__main__":
    main()
