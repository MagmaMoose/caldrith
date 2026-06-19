"""Rate limiting for the webhook ingest endpoint.

Backed by slowapi. The key combines the client IP and (when present) the GitHub App
installation id from the payload, so a single noisy installation cannot exhaust the
budget for others. Storage is Redis-backed with an automatic in-memory fallback so a
transient Redis outage degrades gracefully rather than rejecting all traffic.
"""

from __future__ import annotations

from collections.abc import Callable

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from caldrith.settings import get_config

# Default budget. Generous for a webhook firehose; tune per deployment.
# Typed as the slowapi-expected list element type to satisfy invariance.
_DEFAULT_LIMITS: list[str | Callable[..., str]] = ["600/minute"]
_FALLBACK_LIMITS: list[str | Callable[..., str]] = ["120/minute"]


def installation_key(request: Request) -> str:
    """Rate-limit key: ``<ip>:<installation_id>`` when available, else just the IP.

    The installation id is stashed on ``request.state`` by the webhook handler once
    the payload is parsed; before that we fall back to the remote address.
    """
    ip = get_remote_address(request)
    installation_id = getattr(request.state, "installation_id", None)
    return f"{ip}:{installation_id}" if installation_id is not None else ip


def build_limiter() -> Limiter:
    """Construct the slowapi limiter with Redis storage + in-memory fallback."""
    config = get_config()
    return Limiter(
        key_func=installation_key,
        default_limits=_DEFAULT_LIMITS,
        storage_uri=config.redis_url,
        in_memory_fallback=_FALLBACK_LIMITS,
        in_memory_fallback_enabled=True,
        swallow_errors=True,
        key_prefix="caldrith",
    )
