"""FastAPI application factory.

Wires the webhook router, rate limiter, and health endpoints, and manages the Redis
connections (dedup client + ARQ enqueue pool) over the app lifespan.

Health:
  - ``GET /healthz``: liveness — no dependencies, always ``200`` if the process is up.
  - ``GET /readyz``: readiness — pings Redis; ``503`` if unreachable.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI
from slowapi.errors import RateLimitExceeded
from slowapi.extension import _rate_limit_exceeded_handler
from slowapi.middleware import SlowAPIMiddleware
from starlette.responses import JSONResponse
from starlette.status import HTTP_503_SERVICE_UNAVAILABLE

from caldrith.api.ratelimit import build_limiter
from caldrith.api.reconcile import router as reconcile_router
from caldrith.api.webhooks import router as webhooks_router
from caldrith.audit.logging import configure_logging, get_logger
from caldrith.settings import get_config

_log = get_logger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open Redis connections on startup, close them on shutdown."""
    config = get_config()
    app.state.redis = aioredis.from_url(config.redis_url, decode_responses=True)
    app.state.arq_redis = await create_pool(RedisSettings.from_dsn(config.redis_url))
    get_logger(__name__).info("api.startup")
    try:
        yield
    finally:
        await app.state.redis.aclose()
        await app.state.arq_redis.aclose()
        get_logger(__name__).info("api.shutdown")


def create_app() -> FastAPI:
    """Construct and return the Caldrith FastAPI application."""
    configure_logging()

    app = FastAPI(
        title="Caldrith",
        description="GitHub configuration-as-code enforcement service.",
        lifespan=_lifespan,
    )

    limiter = build_limiter()
    app.state.limiter = limiter
    # slowapi's handler signature is narrower than Starlette's generic Exception
    # handler type; the pairing with RateLimitExceeded is correct at runtime.
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
    app.add_middleware(SlowAPIMiddleware)

    app.include_router(webhooks_router)
    app.include_router(reconcile_router)

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        """Liveness probe — no dependencies."""
        return {"status": "ok"}

    @app.get("/readyz", tags=["health"])
    async def readyz() -> JSONResponse:
        """Readiness probe — verifies Redis is reachable."""
        try:
            await app.state.redis.ping()
        except Exception:  # any Redis failure means not-ready
            return JSONResponse(
                {"status": "redis unreachable"},
                status_code=HTTP_503_SERVICE_UNAVAILABLE,
            )
        return JSONResponse({"status": "ready"})

    return app
