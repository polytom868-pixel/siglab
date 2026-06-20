FROM python:3.12-slim

WORKDIR /app

# Layer caching: install dependencies first
COPY pyproject.toml poetry.lock* ./
RUN pip install --no-cache-dir poetry 2>/dev/null; \
    pip install --no-cache-dir gunicorn uvicorn fastapi httpx pandas pyarrow numpy pydantic; \
    pip install --no-cache-dir -e . 2>/dev/null || true

# Copy application source
COPY siglab/ siglab/
COPY config.json* ./

# Production server using gunicorn + uvicorn workers
# $PORT is set automatically by Railway, Render, Fly.io, etc.
CMD gunicorn -k uvicorn.workers.UvicornWorker \
    --bind "0.0.0.0:${PORT:-8080}" \
    --workers "${GUNICORN_WORKERS:-2}" \
    --max-requests 10000 \
    --max-requests-jitter 1000 \
    siglab.dashboard.app:app
