# Sentinel Gujarat – API image (FastAPI). Build context is the repository root:
#   docker build -f backend/Dockerfile -t sentinel-api .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=UTC

# ffmpeg supplies ffprobe for the active health probe; curl is used by the compose healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl tini \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r sentinel && useradd -r -g sentinel -d /app -s /sbin/nologin sentinel

WORKDIR /app
COPY backend/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY backend/app /app/app
COPY backend/seeds /app/seeds
COPY backend/tests /app/tests
COPY backend/pytest.ini /app/pytest.ini

RUN mkdir -p /data /media /recordings \
    && chown -R sentinel:sentinel /app /data

USER sentinel
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
