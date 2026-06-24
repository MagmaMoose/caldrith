"""Enqueue helpers and delivery deduplication.

GitHub may redeliver a webhook (retries, manual replays). We dedup on the
``X-GitHub-Delivery`` id using Redis ``SETNX`` with a TTL: the first delivery wins and
enqueues a job; redeliveries within the TTL are dropped.
"""

from __future__ import annotations

from typing import Any, Protocol

# Default dedup window. Comfortably longer than GitHub's redelivery retry window
# while bounding Redis memory.
DEDUP_TTL_SECONDS = 24 * 60 * 60
_DEDUP_KEY_PREFIX = "caldrith:delivery:"

# ARQ queue name — MUST be Caldrith-specific. ARQ defaults every app to the queue
# ``arq:queue``; when another ARQ app shares the same Redis (e.g. a sibling service on a
# central Valkey), the two workers poll the *same* default queue and pop each other's
# jobs, failing them with ``JobExecutionFailed("function '...' not found")``. Namespacing
# the queue isolates Caldrith's jobs to Caldrith's worker. The producer (API pool), the
# consumer (``WorkerSettings.queue_name``), and every ``enqueue_job`` MUST agree on this
# name — including the worker's own fan-out, whose pool does not inherit the name.
ARQ_QUEUE_NAME = "caldrith:arq:queue"


class SupportsSetNX(Protocol):
    """Minimal Redis surface used for dedup (satisfied by redis.asyncio + fakeredis)."""

    async def set(  # protocol method
        self,
        name: str,
        value: Any,
        *,
        nx: bool = ...,
        ex: int | None = ...,
    ) -> Any: ...


async def dedup_delivery(
    redis: SupportsSetNX,
    delivery_id: str,
    *,
    ttl_seconds: int = DEDUP_TTL_SECONDS,
) -> bool:
    """Return ``True`` if this delivery is new (and should be processed).

    Atomically sets ``caldrith:delivery:<id>`` only if absent (``SET NX``). Returns
    ``True`` on first sight, ``False`` for a duplicate within the TTL.
    """
    key = f"{_DEDUP_KEY_PREFIX}{delivery_id}"
    result = await redis.set(key, "1", nx=True, ex=ttl_seconds)
    return bool(result)


async def enqueue_reconcile_installation(
    arq_redis: Any,
    *,
    installation_id: int,
    owner: str,
) -> None:
    """Enqueue a full-account reconcile for an installation."""
    await arq_redis.enqueue_job(
        "reconcile_installation",
        installation_id=installation_id,
        owner=owner,
        _queue_name=ARQ_QUEUE_NAME,
    )


async def enqueue_reconcile_repo(
    arq_redis: Any,
    *,
    installation_id: int,
    owner: str,
    repo: str,
    dry_run: bool = False,
    head_sha: str | None = None,
) -> None:
    """Enqueue a single-repo reconcile (or dry-run)."""
    await arq_redis.enqueue_job(
        "reconcile_repo",
        installation_id=installation_id,
        owner=owner,
        repo=repo,
        dry_run=dry_run,
        head_sha=head_sha,
        _queue_name=ARQ_QUEUE_NAME,
    )


async def enqueue_reconcile_org(
    arq_redis: Any,
    *,
    installation_id: int,
    owner: str,
) -> None:
    """Enqueue an organization-scoped reconcile for an installation."""
    await arq_redis.enqueue_job(
        "reconcile_org",
        installation_id=installation_id,
        owner=owner,
        _queue_name=ARQ_QUEUE_NAME,
    )
