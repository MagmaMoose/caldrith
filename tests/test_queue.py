"""Tests for the dedup + enqueue helpers."""

from __future__ import annotations

from typing import Any

import fakeredis.aioredis

from caldrith.worker.queue import (
    ARQ_QUEUE_NAME,
    dedup_delivery,
    enqueue_reconcile_installation,
    enqueue_reconcile_repo,
)


async def test_dedup_first_delivery_is_new() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    assert await dedup_delivery(redis, "abc") is True
    await redis.aclose()


async def test_dedup_duplicate_rejected() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    assert await dedup_delivery(redis, "abc") is True
    assert await dedup_delivery(redis, "abc") is False
    await redis.aclose()


async def test_dedup_distinct_ids_independent() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    assert await dedup_delivery(redis, "id-1") is True
    assert await dedup_delivery(redis, "id-2") is True
    await redis.aclose()


class _RecordingPool:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, dict[str, Any]]] = []

    async def enqueue_job(self, name: str, **kwargs: Any) -> None:
        self.jobs.append((name, kwargs))


async def test_enqueue_installation() -> None:
    pool = _RecordingPool()
    await enqueue_reconcile_installation(pool, installation_id=1, owner="acme")
    assert pool.jobs == [
        (
            "reconcile_installation",
            {"installation_id": 1, "owner": "acme", "_queue_name": ARQ_QUEUE_NAME},
        )
    ]


async def test_enqueue_repo_dry_run() -> None:
    pool = _RecordingPool()
    await enqueue_reconcile_repo(
        pool, installation_id=1, owner="acme", repo="widget", dry_run=True, head_sha="sha"
    )
    name, kwargs = pool.jobs[0]
    assert name == "reconcile_repo"
    assert kwargs == {
        "installation_id": 1,
        "owner": "acme",
        "repo": "widget",
        "dry_run": True,
        "head_sha": "sha",
        "_queue_name": ARQ_QUEUE_NAME,
    }


async def test_every_enqueue_targets_caldrith_queue() -> None:
    """Regression guard: jobs must NOT land on ARQ's shared default ``arq:queue`` —
    a co-tenant ARQ worker on the same Redis would otherwise steal and fail them."""
    pool = _RecordingPool()
    await enqueue_reconcile_installation(pool, installation_id=1, owner="acme")
    await enqueue_reconcile_repo(pool, installation_id=1, owner="acme", repo="widget")
    assert ARQ_QUEUE_NAME != "arq:queue"
    assert all(kwargs["_queue_name"] == ARQ_QUEUE_NAME for _, kwargs in pool.jobs)
