ARG APP_BASE=orgproekt-backend:local
FROM ghcr.io/astral-sh/uv@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv
FROM ${APP_BASE} AS runtime
ARG APP_IMAGE_ID=unknown
LABEL orgproekt.base-image-id="${APP_IMAGE_ID}"
USER root
RUN apt-get update && apt-get install -y --no-install-recommends age postgresql-client-17 && rm -rf /var/lib/apt/lists/*
COPY backend/ops /app/ops
RUN dpkg-query -W age postgresql-client-17 > /app/ops/tool-versions.txt
USER app
ENTRYPOINT ["python", "-m", "ops.backup"]

FROM runtime AS test
USER root
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv uv sync --frozen --no-install-project
USER app

FROM runtime AS production
