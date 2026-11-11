ARG APP_BASE=orgproekt-backend:local
FROM ${APP_BASE}
ARG APP_IMAGE_ID=unknown
LABEL orgproekt.base-image-id="${APP_IMAGE_ID}"
USER root
RUN apt-get update && apt-get install -y --no-install-recommends age postgresql-client-17 && rm -rf /var/lib/apt/lists/*
COPY backend/ops /app/ops
RUN dpkg-query -W age postgresql-client-17 > /app/ops/tool-versions.txt
USER app
ENTRYPOINT ["python", "-m", "ops.backup"]
