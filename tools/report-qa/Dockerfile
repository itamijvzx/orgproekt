# Optional, isolated visual QA only; never part of the application runtime.
# Build after the current backend image. Run with --network none and only synthetic files.
FROM orgproekt-backend:local
USER root
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-writer poppler-utils && rm -rf /var/lib/apt/lists/*
RUN uv pip install --python /app/.venv/bin/python pdf2image==1.17.0
USER app
ENTRYPOINT ["/app/.venv/bin/python"]
