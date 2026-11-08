FROM orgproekt-backend:local
USER root
RUN apt-get update && apt-get install -y --no-install-recommends age postgresql-client-17 && rm -rf /var/lib/apt/lists/*
COPY backend/ops /app/ops
RUN dpkg-query -W age postgresql-client-17 > /app/ops/tool-versions.txt
USER app
ENTRYPOINT ["python", "-m", "ops.backup"]
