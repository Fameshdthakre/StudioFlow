FROM python:3.12-slim

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

RUN groupadd --gid 10001 studioflow \
    && useradd --uid 10001 --gid studioflow --no-create-home --shell /usr/sbin/nologin studioflow \
    && mkdir -p data/uploads data/outputs data/brand_logos \
    && chown -R studioflow:studioflow /app/data

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).read()"]

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]

