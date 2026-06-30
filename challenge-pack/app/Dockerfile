# AutoLoan-DocIntel app image.
# Slim Python 3.11 base; builds the FastAPI + HTMX service that serves
# openapi/contract.yaml. Heavy ML libs are installed here but imported lazily
# in code so unit tests / CI can import modules without them.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps: build tools for any C-extension wheels, curl for healthchecks.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Dependency layer first for cache reuse.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Application code. The compose build context is ./app; the broader repo
# (src/, db/, openapi/) is mounted or copied by the candidate's own setup.
COPY . /app

# Non-root runtime.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

# TODO(candidate): expose `app.main:app` (the FastAPI instance implementing
# openapi/contract.yaml). The graph + LLM live in the repo `src/` package.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
