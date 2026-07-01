"""ARQ worker settings and job functions.

Three jobs:
  - ``reconcile_installation``: fan out — enqueue one ``reconcile_repo`` per managed repo
    (so failures stay isolated) and, for Organization accounts, one ``reconcile_org``.
  - ``reconcile_repo``: reconcile (or dry-run) a single repository's tiers.
  - ``reconcile_org``: reconcile the installation's organization-scoped settings once.
  - ``update_admin_prs``: re-base the admin repo's open PRs after a settings-file change
    on the default branch (so each proposal is re-tested against the new baseline).

Each job builds a *fresh* per-installation githubkit client (tokens are never shared
across installations or reused across jobs).
"""

from __future__ import annotations

import os
from typing import Any, ClassVar

from arq.connections import RedisSettings
from arq.cron import cron

from caldrith.audit.logging import bind_context, configure_logging, get_logger
from caldrith.auth.client import GitHubClientFactory
from caldrith.config.loader import load_admin_config
from caldrith.reconcile.org import run_org_reconcile
from caldrith.reconcile.overlay import has_overlays
from caldrith.reconcile.planner import list_target_repos
from caldrith.reconcile.pr_update import update_open_prs
from caldrith.reconcile.runner import REPO_TIERS, run_reconcile
from caldrith.reconcile.selection import select_targets
from caldrith.settings import get_config
from caldrith.worker.installations import paginate_installations
from caldrith.worker.queue import ARQ_QUEUE_NAME

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
    config = get_config()
    log = bind_context(_log, installation_id=installation_id)

    async with factory.for_installation(installation_id) as client:
        settings_config = await load_admin_config(
            client,
            owner=owner,
            admin_repo=config.admin_repo,
            config_path=config.config_path,
            settings_file=config.settings_file_path,
        )
        # Organization-scoped settings apply once per account (not per repo) — enqueue a
        # single org reconcile when an organization block is declared.
        if settings_config.organization is not None:
            await arq_redis.enqueue_job(
                "reconcile_org",
                installation_id=installation_id,
                owner=owner,
                _queue_name=ARQ_QUEUE_NAME,
            )

        # Fan out one repo job per managed repo if ANY repo-scoped tier (or an overlay
        # that may introduce one) is declared; otherwise there's nothing to enforce.
        repo_work = has_overlays(settings_config) or any(
            tier.configured(settings_config) for tier in REPO_TIERS
        )
        if not repo_work:
            log.info("reconcile_installation.no_repo_tiers", owner=owner)
            return 0
        all_targets = await list_target_repos(client)
        targets = select_targets(
            all_targets,
            admin_repo=config.admin_repo,
            restricted=settings_config.restricted_repos,
        )

    for target in targets:
        await arq_redis.enqueue_job(
            "reconcile_repo",
            installation_id=installation_id,
            owner=target.owner,
            repo=target.name,
            dry_run=False,
            head_sha=None,
            _queue_name=ARQ_QUEUE_NAME,
        )
    log.info("reconcile_installation.fanned_out", count=len(targets))
    return len(targets)


async def reconcile_org(
    ctx: dict[str, Any],
    *,
    installation_id: int,
    owner: str,
) -> bool:
    """Reconcile the installation's organization-scoped settings. Returns if changed."""
    factory: GitHubClientFactory = ctx["client_factory"]
    async with factory.for_installation(installation_id) as client:
        summary = await run_org_reconcile(client, installation_id=installation_id, owner=owner)
    return summary.any_changed


async def update_admin_prs(
    ctx: dict[str, Any],
    *,
    installation_id: int,
    owner: str,
) -> int:
    """Re-base the admin repo's open PRs onto the new default-branch baseline.

    Fired when a push modifies the admin settings file on the default branch: each open
    config PR is a proposed change whose dry-run preview now diffs against a stale base,
    so any branch behind its base is merged forward (GitHub's "Update branch"). Returns
    the number of PRs updated.
    """
    factory: GitHubClientFactory = ctx["client_factory"]
    config = get_config()
    async with factory.for_installation(installation_id) as client:
        summary = await update_open_prs(client, owner=owner, repo=config.admin_repo)
    return len(summary.updated)


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
    return summary.any_changed


async def reconcile_all_installations(ctx: dict[str, Any]) -> int:
    """Periodic full reconcile: enqueue ``reconcile_installation`` for every install.

    Belt-and-braces against missed webhooks (delivery failures, secret rotation, etc.):
    one ``apps.list_installations`` call (via the App's JWT, no installation needed),
    then a job per installation handled by the existing fan-out. Each enqueue is
    idempotent at the reconcile layer — duplicate runs converge to a no-op.
    """
    factory: GitHubClientFactory = ctx["client_factory"]
    async with factory.for_app() as client:
        installations = await paginate_installations(client)
    arq_redis = ctx["redis"]
    for installation in installations:
        await arq_redis.enqueue_job(
            "reconcile_installation",
            installation_id=installation["id"],
            owner=installation["account"]["login"],
            _queue_name=ARQ_QUEUE_NAME,
        )
    _log.info("reconcile_all_installations.enqueued", source="cron", count=len(installations))
    return len(installations)


async def startup(ctx: dict[str, Any]) -> None:
    """Worker startup: configure logging and a shared client factory."""
    configure_logging()
    ctx["client_factory"] = GitHubClientFactory()
    get_logger(__name__).info("worker.startup")


async def shutdown(ctx: dict[str, Any]) -> None:
    """Worker shutdown hook."""
    get_logger(__name__).info("worker.shutdown")


def _cron_jobs() -> list[Any]:
    """Build the cron-jobs list from ``RECONCILE_CRON_MINUTES`` (0 disables).

    Sub-hour cadences (``minutes < 60``) drive ``minute=`` directly. For ``minutes >=
    60`` we switch to ``hour=`` with ``hours = minutes // 60`` so a value like 120 fires
    every two hours, not hourly. Non-multiples-of-60 above an hour floor to the next
    whole hour (e.g. ``minutes=90`` runs hourly); values inside an hour that don't
    divide 60 fire at every multiple within the hour, then wrap.
    """
    minutes = get_config().reconcile_cron_minutes if _has_config_env() else 0
    if minutes <= 0:
        return []
    cron_kwargs: dict[str, Any]
    if minutes < 60:
        cron_kwargs = {"minute": set(range(0, 60, minutes))}
    else:
        cron_kwargs = {"hour": set(range(0, 24, minutes // 60)), "minute": {0}}
    return [
        cron(
            reconcile_all_installations,
            name="reconcile-all-installations",
            unique=True,
            max_tries=1,
            **cron_kwargs,
        )
    ]


def _has_config_env() -> bool:
    """True when the secrets ``get_config()`` needs are present — so the module stays
    importable by tooling/tests without env (mirrors the ``redis_settings`` rationale)."""
    return all(os.environ.get(k) for k in ("APP_ID", "PRIVATE_KEY", "WEBHOOK_SECRET"))


class WorkerSettings:
    """ARQ worker entrypoint (``arq caldrith.worker.worker.WorkerSettings``)."""

    functions: ClassVar = [
        reconcile_installation,
        reconcile_repo,
        reconcile_org,
        update_admin_prs,
        reconcile_all_installations,
    ]
    cron_jobs: ClassVar = _cron_jobs()
    on_startup = startup
    on_shutdown = shutdown
    # Consume from Caldrith's own queue, not ARQ's shared default ``arq:queue``. Without
    # this, a co-tenant ARQ app on the same Redis pops Caldrith's jobs and fails them
    # ("function not found"), silently stalling all reconciles. See ``ARQ_QUEUE_NAME``.
    queue_name: ClassVar = ARQ_QUEUE_NAME
    # ARQ reads settings off the class ``__dict__``, so ``redis_settings`` MUST be a
    # real class attribute — a metaclass property is invisible to ARQ, which then
    # silently falls back to redis://localhost:6379. Resolve REDIS_URL at import with
    # a localhost default: this needs no secrets (unlike get_config(), which requires
    # APP_ID/PRIVATE_KEY/WEBHOOK_SECRET), so the module stays importable by
    # tooling/tests while the worker process always has REDIS_URL in its env.
    redis_settings = RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379"))
