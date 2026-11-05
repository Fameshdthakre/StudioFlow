FROM postgres:18-bookworm@sha256:5c4783a8cc3f0074912164f94dfa1be4e043438f0f707cf968d7af9a0d31a2c8 AS postgres-tools
FROM restic/restic:0.19.1@sha256:136600b6ff6843d61d355f7f71f460a166429f35de6fd11b568fece3c9a4d510 AS restic-tools
FROM python:3.12-slim-bookworm AS runtime

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install dependency versions verified by the regression suite.
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

# Copy only application sources. Secrets and data stay outside image layers.
COPY app.py .
COPY static/ ./static/
COPY studioflow/ ./studioflow/
COPY resources/ ./resources/

RUN groupadd --gid 10001 studioflow \
    && useradd --uid 10001 --gid studioflow --no-create-home --shell /usr/sbin/nologin studioflow \
    && mkdir -p data/uploads data/outputs data/brand_logos \
    && chown -R studioflow:studioflow /app/data

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).read()"]

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS operations
USER 0:0
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 liblz4-1 libzstd1 tzdata \
    && rm -rf /var/lib/apt/lists/*
COPY --from=postgres-tools /usr/lib/postgresql/18/bin/pg_dump /usr/local/bin/pg_dump
COPY --from=postgres-tools /usr/lib/postgresql/18/bin/pg_restore /usr/local/bin/pg_restore
COPY --from=postgres-tools /usr/lib/x86_64-linux-gnu/libpq.so.5* /usr/lib/x86_64-linux-gnu/
COPY --from=restic-tools /usr/bin/restic /usr/local/bin/restic
RUN pg_dump --version && pg_restore --version && restic version
USER 10001:10001
HEALTHCHECK NONE
ENTRYPOINT ["python", "-m", "studioflow.operations.cli"]
CMD ["preflight"]

