# syntax=docker/dockerfile:1

# ---- Builder ---------------------------------------------------------------
# Resolve and install the locked runtime dependencies into a self-contained
# virtualenv at /app/.venv using uv. A BuildKit cache mount keeps uv's wheel
# cache warm across builds without baking it into a layer.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install dependencies first (cached) using only the lockfile + manifest, then
# the project itself. --frozen fails the build if uv.lock is stale; --no-dev
# excludes the dev dependency group from the runtime image.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---- Runtime ---------------------------------------------------------------
FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Non-root runtime user. No writable app state is required (readOnlyRootFilesystem
# is enforced in k8s); the process only needs to read its venv + source.
RUN useradd --create-home --home-dir /home/app --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder --chown=app:app /app /app

USER app
ENV HOME=/home/app
EXPOSE 8000

# Web app (FastAPI ingest). The ARQ worker runs the same image with its command
# overridden, e.g. in k8s:
#   command: ["arq", "caldrith.worker.worker.WorkerSettings"]
CMD ["uvicorn", "caldrith.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
