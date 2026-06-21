# syntax=docker/dockerfile:1

# Builder and runtime MUST share the same Python minor: the venv installs into
# lib/python<minor>, so a mismatch makes every dependency import fail at runtime
# (ModuleNotFoundError: No module named 'uvicorn'). One ARG = one source of truth, and
# templated FROM tags keep Dependabot from bumping the two apart.
ARG PYTHON_VERSION=3.12

# ---- Builder ---------------------------------------------------------------
# Resolve and install the locked runtime dependencies into a self-contained
# virtualenv at /app/.venv using uv. A BuildKit cache mount keeps uv's wheel
# cache warm across builds without baking it into a layer.
FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-bookworm-slim AS builder

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
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Non-root runtime user. No writable app state is required (readOnlyRootFilesystem
# is enforced in k8s); the process only needs to read its venv + source.
RUN useradd --create-home --home-dir /home/app --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder --chown=app:app /app /app

USER 1000
ENV HOME=/home/app
EXPOSE 8000

# Liveness probe for non-orchestrated runs (docker run / compose) and image scanners.
# Hits the dependency-free /healthz endpoint via the stdlib (the slim image has no
# curl). Kubernetes ignores Docker HEALTHCHECK and uses the deployment's httpGet
# probes instead; this targets the web CMD below (the ARQ worker overrides CMD and
# has no HTTP listener, so it does not inherit a meaningful check there).
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"]

# Web app (FastAPI ingest). The ARQ worker runs the same image with its command
# overridden, e.g. in k8s:
#   command: ["arq", "caldrith.worker.worker.WorkerSettings"]
CMD ["uvicorn", "caldrith.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
