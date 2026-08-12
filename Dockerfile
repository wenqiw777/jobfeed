FROM python:3.12-slim

# JOBFEED_ML_CACHE_DIR is baked as an image-level default so the build-time
# embedder bake (below) and EVERY runtime read resolve the SAME path — no drift,
# even for a bare `docker run` that doesn't set it. docker-compose reasserts the
# identical value and mounts the `mlcache` volume here.
ENV PATH="/opt/venv/bin:${PATH}" \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    JOBFEED_DB_PATH=/data/jobfeed.sqlite \
    JOBFEED_ML_CACHE_DIR=/cache/jobfeed/fastembed

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends make \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /data \
    && python -m venv /opt/venv

COPY . .

RUN pip install --upgrade pip \
    && pip install --editable ".[dev]"

# Bake the ~87MB ONNX `all-MiniLM-L6-v2` weights into the image at the runtime
# cache path so the canonical `docker compose run --rm jobfeed-cli` does ZERO
# download and works offline. `warm_embedder()` reuses the embedder's own model
# id + cache resolution (reads JOBFEED_ML_CACHE_DIR above), so the baked path
# CANNOT drift from what an evaluation run reads. A fresh `mlcache` named volume
# is seeded from this baked dir on first mount; an upgrader with a pre-existing
# EMPTY mlcache must `docker volume rm jobfeed_mlcache` once to pick it up.
RUN python -c "from jobfeed.adapters.ml._embedder import warm_embedder; print('baked embedder weights into', warm_embedder())"

CMD ["jobfeed"]
