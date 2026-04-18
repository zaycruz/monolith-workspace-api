# syntax=docker/dockerfile:1.7
# =============================================================================
# monolith-workspace-api — production Docker image
#
# Multi-stage build:
#   1. builder  — install uv, resolve deps from uv.lock, build .venv
#   2. runtime  — slim image that only carries the venv + app source
#
# Target: AWS App Runner (us-east-1) or any OCI runtime. Exposes 8080 and
# ships a curl-based healthcheck that hits /health. Runs as non-root uid 10001.
# =============================================================================

# ---- Stage 1: builder --------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

# Build deps — trimmed the moment the venv is assembled.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv (pinned to a known-good release series).
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /build

# Copy only the manifest + lock + readme (hatchling reads readme during wheel
# build) so the dep layer is cached independently of app source changes.
COPY pyproject.toml uv.lock README.md ./

# Resolve + install pinned dependencies into /opt/venv without dev extras.
RUN uv sync --frozen --no-dev --no-install-project

# Now copy the actual app source and install the project itself into the venv.
COPY app ./app
COPY alembic ./alembic
RUN uv sync --frozen --no-dev

# ---- Stage 2: runtime --------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8080

# curl is only kept in runtime because HEALTHCHECK uses it. Everything else
# (build-essential, etc.) stays behind in the builder stage.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd  --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app

# Pull the prebuilt venv and app source from the builder stage.
COPY --from=builder --chown=app:app /opt/venv /opt/venv
COPY --from=builder --chown=app:app /build/app    /app/app
COPY --from=builder --chown=app:app /build/alembic /app/alembic

USER app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
