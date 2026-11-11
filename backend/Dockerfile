FROM ghcr.io/astral-sh/uv@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1
WORKDIR /app
RUN apt-get update && apt-get upgrade -y --no-install-recommends && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 app && mkdir /app/parser-socket && chown app:app /app/parser-socket

FROM base AS dependencies
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv uv sync --frozen --no-dev --no-install-project
COPY backend/embedding-model.json ./
COPY backend/scripts/fetch_embedding_model.py ./scripts/fetch_embedding_model.py
RUN python scripts/fetch_embedding_model.py /opt/orgproekt/embeddings

FROM base AS runtime
COPY --from=dependencies /app/.venv /app/.venv
COPY --from=dependencies /opt/orgproekt/embeddings /opt/orgproekt/embeddings
COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/ops ./ops
COPY backend/alembic.ini backend/embedding-model.json ./
USER app
ENV PATH="/app/.venv/bin:$PATH"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]

# Test dependencies and fixtures never become layers of the production image.
FROM runtime AS test
USER root
COPY backend/ ./
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv uv sync --frozen --no-install-project
USER app

# Preserve the safe default for plain `docker build -f backend/Dockerfile`.
FROM runtime AS production
