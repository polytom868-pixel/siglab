FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry

COPY pyproject.toml README.md ./
RUN poetry config virtualenvs.create false && poetry install --no-root --no-interaction --no-ansi

COPY siglab/ siglab/

ENV PYTHONUNBUFFERED=1
ENV PORT=8080
EXPOSE 8080
CMD ["uvicorn", "siglab.dashboard.routes:app", "--host", "0.0.0.0", "--port", "8080"]
