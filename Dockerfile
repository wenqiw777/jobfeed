FROM python:3.11-slim

ENV PATH="/opt/venv/bin:${PATH}" \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends make \
    && rm -rf /var/lib/apt/lists/* \
    && python -m venv /opt/venv

COPY . .

RUN pip install --upgrade pip \
    && pip install --editable ".[dev]"

CMD ["jobfeed"]
