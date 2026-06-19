"""ARQ worker settings and job functions.

Two jobs:
  - ``reconcile_installation``: fan out — list the installation's repos and enqueue
    one ``reconcile_repo`` per repo so failures stay isolated.
  - ``reconcile_repo``: reconcile (or dry-run) a single repository.

Each job builds a *fresh* per-installation githubkit client (tokens are never shared
across installations or reused across jobs).
"""

from __future__ import annotations

import os
from typing import Any, ClassVar

from arq.connections import RedisSettings

from caldrith.audit.logging import bind_context, configure_logging, get_logger
from caldrith.auth.client import GitHubClientFactory
from caldrith.reconcile.planner import list_target_repos
from caldrith.reconcile.runner import run_reconcile

_log = get_logger(__name__)


async def reconcile_installation(
    ctx: dict[str, Any],
    *,
    installation_id: int,
    owner: str,
) -> int:
    """Fan out: enqueue one ``reconcile_repo`` job per accessible repo.

    Returns the number of repos fanned out.
    """
    factory: GitHubClientFactory = ctx["client_factory"]
    arq_redis = ctx["redis"]
    log = bind_context(_log, installation_id=installation_id)

    async with factory.for_installation(installation_id) as client:
        targets = await list_target_repos(client)

    for target in targets:
        await arq_redis.enqueue_job(
            "reconcile_repo",
            installation_id=installation_id,
            owner=target.owner,
            repo=target.name,
            dry_run=False,
            head_sha=None,
        )
    log.info("reconcile_installation.fanned_out", count=len(targets))
    return len(targets)


async def reconcile_repo(
    ctx: dict[str, Any],
    *,
    installation_id: int,
    owner: str,
    repo: str,
    dry_run: bool = False,
    head_sha: str | None = None,
) -> bool:
    """Reconcile (or dry-run) a single repository. Returns whether changes occurred."""
    factory: GitHubClientFactory = ctx["client_factory"]
    async with factory.for_installation(installation_id) as client:
        summary = await run_reconcile(
            client,
            installation_id=installation_id,
            owner=owner,
            repos=[repo],
            dry_run=dry_run,
            head_sha=head_sha,
        )
    return bool(summary.changed)


async def startup(ctx: dict[str, Any]) -> None:
    """Worker startup: configure logging and a shared client factory."""
    configure_logging()
    ctx["client_factory"] = GitHubClientFactory()
    get_logger(__name__).info("worker.startup")


async def shutdown(ctx: dict[str, Any]) -> None:
    """Worker shutdown hook."""
    get_logger(__name__).info("worker.shutdown")


class WorkerSettings:
    """ARQ worker entrypoint (``arq caldrith.worker.worker.WorkerSettings``)."""

    functions: ClassVar = [reconcile_installation, reconcile_repo]
    on_startup = startup
    on_shutdown = shutdown
    # ARQ reads settings off the class ``__dict__``, so ``redis_settings`` MUST be a
    # real class attribute — a metaclass property is invisible to ARQ, which then
    # silently falls back to redis://localhost:6379. Resolve REDIS_URL at import with
    # a localhost default: this needs no secrets (unlike get_config(), which requires
    # APP_ID/PRIVATE_KEY/WEBHOOK_SECRET), so the module stays importable by
    # tooling/tests while the worker process always has REDIS_URL in its env.
    redis_settings = RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379"))
