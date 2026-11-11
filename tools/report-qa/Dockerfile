# Optional, isolated visual QA only; never part of the application runtime.
# Build after the current backend image. Run with --network none and only synthetic files.
FROM ghcr.io/astral-sh/uv@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv
FROM orgproekt-backend:local
USER root
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-writer poppler-utils && rm -rf /var/lib/apt/lists/*
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv uv pip install --python /app/.venv/bin/python pdf2image==1.17.0
USER app
ENTRYPOINT ["/app/.venv/bin/python"]
