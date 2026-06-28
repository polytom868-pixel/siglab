FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*

# Install build deps
RUN pip install --no-cache-dir poetry

# Copy only dep files first for layer caching
COPY pyproject.toml ./
RUN poetry config virtualenvs.create false && poetry install --no-root --no-interaction --no-ansi

# Copy source
COPY siglab/ siglab/
RUN poetry install --no-interaction --no-ansi

ENV PYTHONUNBUFFERED=1
ENV PORT=8080
EXPOSE 8080
CMD uvicorn siglab.dashboard.routes:app --host 0.0.0.0 --port $PORT
