"""Reconcile repository interaction limits.

An interaction limit temporarily restricts who can comment / open issues / open PRs on a
repo (``existing_users`` / ``contributors_only`` / ``collaborators_only``). ``limit: null``
removes any active limit. Drift is detected on the ``limit`` value only — the API reports
an absolute ``expires_at`` timestamp rather than the declared relative ``expiry``, so a
matching ``limit`` is treated as converged (re-applying does not reset the clock).
"""

from __future__ import annotations

from typing import Any

from githubkit import GitHub

from caldrith.config.schema import RepoScoped
from caldrith.github_json import response_json
from caldrith.reconcile.base import RepoTier, TierResult
from caldrith.reconcile.planner import TargetRepo


def _configured(config: RepoScoped) -> bool:
    if config.interaction_limits is None:
        return False
    return config.interaction_limits.model_fields_set != set()


async def reconcile(
    client: GitHub, target: TargetRepo, config: RepoScoped, *, dry_run: bool = False
) -> list[TierResult]:
    """Reconcile the repo interaction limit (set/remove unless dry-run)."""
    if config.interaction_limits is None:
        return []
    desired = config.interaction_limits
    interactions = client.rest.interactions
    live = response_json(
        await interactions.async_get_restrictions_for_repo(owner=target.owner, repo=target.name)
    )
    current_limit = live.get("limit") if isinstance(live, dict) else None
    result = TierResult(tier="interaction_limits", scope=target.full_name)

    if desired.limit is None:
        if current_limit:
            result.changed = True
            result.notes.append("remove interaction limit")
            if not dry_run:
                await interactions.async_remove_restrictions_for_repo(
                    owner=target.owner, repo=target.name
                )
                result.applied = True
        return [result]

    if current_limit != desired.limit:
        result.changed = True
        result.notes.append(f"set interaction limit: {desired.limit}")
        if not dry_run:
            body: dict[str, Any] = {"limit": desired.limit}
            if desired.expiry is not None:
                body["expiry"] = desired.expiry
            await interactions.async_set_restrictions_for_repo(
                owner=target.owner, repo=target.name, data=body
            )
            result.applied = True
    return [result]


TIER = RepoTier(name="interaction_limits", configured=_configured, reconcile=reconcile)
