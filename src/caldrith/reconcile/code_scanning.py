"""Reconcile CodeQL default setup (code scanning without a committed workflow).

GitHub's *default setup* turns CodeQL on via the API instead of a workflow file. It is
free on public repos; on private/internal repos it needs GitHub Code Security, so the
update is rejected (403/422) without it — the runner isolates that per tier.

``state`` mirrors the API (``configured`` / ``not-configured``). Drift is detected on
``state`` and, when declared, on ``query_suite`` / ``languages`` (omit ``languages`` to
let GitHub auto-detect, in which case languages are not diffed). A ``GET`` that 404/403s —
default setup unavailable for the repo (empty / no analysable code) or not licensed — is
treated as *not configured*.
"""

from __future__ import annotations

from typing import Any

from githubkit import GitHub
from githubkit.exception import RequestFailed

from caldrith.config.schema import RepoScoped
from caldrith.github_json import response_json
from caldrith.reconcile.base import RepoTier, TierResult
from caldrith.reconcile.planner import TargetRepo


async def reconcile(
    client: GitHub, target: TargetRepo, config: RepoScoped, *, dry_run: bool = False
) -> list[TierResult]:
    """Reconcile CodeQL default setup for one repo (enable/refine/disable unless dry-run)."""
    if config.code_scanning is None:
        return []
    desired = config.code_scanning
    code_scanning = client.rest.code_scanning
    result = TierResult(tier="code_scanning", scope=target.full_name)

    try:
        live = response_json(
            await code_scanning.async_get_default_setup(owner=target.owner, repo=target.name)
        )
    except RequestFailed as exc:
        if exc.response.status_code not in (403, 404):
            raise
        live = {}  # unavailable / unlicensed here -> treat as not configured
    if not isinstance(live, dict):
        live = {}

    drift = live.get("state") != desired.state
    if desired.state == "configured":
        if desired.query_suite is not None and desired.query_suite != live.get("query_suite"):
            drift = True
        if desired.languages is not None and sorted(desired.languages) != sorted(
            live.get("languages") or []
        ):
            drift = True
    if not drift:
        return [result]

    result.changed = True
    result.notes.append(f"code scanning default setup: {desired.state}")
    if not dry_run:
        body: dict[str, Any] = {"state": desired.state}
        if desired.state == "configured":
            for field in ("query_suite", "languages", "runner_type", "runner_label"):
                value = getattr(desired, field)
                if value is not None:
                    body[field] = value
        await code_scanning.async_update_default_setup(
            owner=target.owner, repo=target.name, data=body
        )
        result.applied = True
    return [result]


TIER = RepoTier(
    name="code_scanning",
    configured=lambda c: c.code_scanning is not None,
    reconcile=reconcile,
)
